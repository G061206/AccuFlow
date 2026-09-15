import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from email import policy
from email.parser import BytesParser

import pytest

from accuflow.config import Settings
from accuflow.notifications.smtp import Rejected, SMTPChannel
from accuflow.notifications.templates import render, report_content
from accuflow.services.delivery import DeliveryService
from accuflow.services.telemetry import TelemetryService, summarize
from accuflow.storage.database import Database
from accuflow.storage.delivery import DeliveryStore
from accuflow.services.reports import ReportService
from test_api import make_client
from m2_fixtures import make_snapshot
from accuflow.detectors.unified import evaluate


def settings(enabled=True):
    return Settings(_env_file=None,smtp_enabled=enabled,smtp_host='smtp.example.invalid',smtp_sender='from@example.invalid',smtp_recipients='to@example.invalid',smtp_password='never-log-secret')


class Channel:
    calls=[]; prepare_error=None; data_error=None
    def __init__(self,settings): pass
    def prepare(self,envelope):
        self.calls.append('prepare')
        if self.prepare_error: raise self.prepare_error
    def deliver(self,mime):
        self.calls.append(mime)
        if self.data_error: raise self.data_error
    def close(self): pass


@pytest.fixture
async def db(tmp_path):
    db=Database(tmp_path/'delivery.db');await db.connect();db.delivery_settings=settings()
    Channel.calls=[];Channel.prepare_error=None;Channel.data_error=None
    yield db
    await db.close()


async def enqueue(db,key='one'):
    async with db.transaction() as conn:
        return await DeliveryStore(db).enqueue(conn,key,'测试日报',[('结论',['正常'])],datetime.now(UTC).isoformat())


def test_deterministic_multipart_and_html_escaping():
    args=('a'*64,'测试 <subject>',[('判断',['<script>alert(1)</script>'])],'2026-09-15T20:00:00+00:00','from@example.invalid',['to@example.invalid'])
    a,b=render(*args),render(*args)
    assert a==b and '<script>' not in a['html']
    parsed=BytesParser(policy=policy.default).parsebytes(a['mime'])
    assert parsed['Message-ID']==a['message_id']
    assert parsed.get_body(preferencelist=('plain',)) and parsed.get_body(preferencelist=('html',))


async def test_preview_never_promotes_after_smtp_enable(db):
    db.delivery_settings=settings(False);identity=await enqueue(db)
    db.delivery_settings=settings()
    await DeliveryService(db,settings(),Channel).tick()
    assert not Channel.calls
    assert (await DeliveryStore(db).list())[0]['status']=='preview'


async def test_sent_and_repeated_tick_are_idempotent(db):
    identity=await enqueue(db);assert await enqueue(db)==identity
    service=DeliveryService(db,settings(),Channel)
    await service.tick();await service.tick()
    assert len(Channel.calls)==2
    assert (await service.store.list())[0]['status']=='sent'


@pytest.mark.parametrize('phase,error,expected',[
    ('prepare',ConnectionError('secret'),'retry'),('prepare',Rejected(535),'failed'),
    ('data',Rejected(451),'retry'),('data',Rejected(550),'failed'),
    ('data',ConnectionError('secret'),'uncertain')])
async def test_failure_boundaries(db,phase,error,expected):
    await enqueue(db)
    setattr(Channel,phase+'_error',error)
    service=DeliveryService(db,settings(),Channel);await service.tick()
    result=(await service.store.list())[0]
    assert result['status']==expected and 'secret' not in (result['last_error'] or '')
    before=len(Channel.calls);await service.tick();assert len(Channel.calls)==before


async def test_retry_preserves_message_id_and_exact_bytes(db):
    await enqueue(db);Channel.data_error=Rejected(451)
    service=DeliveryService(db,settings(),Channel);await service.tick()
    async with db.transaction() as conn: await conn.execute("UPDATE delivery_outbox SET next_attempt_at='2000-01-01'")
    Channel.data_error=None;await service.tick()
    assert Channel.calls[1]==Channel.calls[3]
    assert (await service.store.list())[0]['attempts']==2


async def test_crash_after_data_is_uncertain_and_resolution_never_resends(db):
    identity=await enqueue(db);store=DeliveryStore(db)
    item=await store.claim(settings());await store.mark_sending(item)
    async with db.transaction() as conn: await conn.execute("UPDATE delivery_outbox SET lease_until='2000-01-01'")
    assert await store.claim(settings()) is None
    assert (await store.list())[0]['status']=='uncertain'
    await store.resolve(identity,'confirmed_sent')
    await DeliveryService(db,settings(),Channel).tick();assert not Channel.calls
    with pytest.raises(ValueError): await store.resolve(identity,'cancelled')


async def test_claim_fencing_and_stale_alert(db):
    await enqueue(db);store=DeliveryStore(db);item=await store.claim(settings())
    assert await store.claim(settings()) is None
    async with db.transaction() as conn: await conn.execute("UPDATE delivery_outbox SET lease_until='2000-01-01'")
    successor=await store.claim(settings());assert successor['owner']!=item['owner']
    with pytest.raises(RuntimeError): await store.mark_sending(item)
    async with db.transaction() as conn:
        await store.enqueue(conn,'stale','过期',[('结论',['过期'])],'2000-01-01T00:00:00+00:00',expires_at='2000-01-01T00:15:00+00:00')
    assert await store.claim(settings()) is None
    assert any(x['status']=='cancelled' for x in await store.list())


async def test_report_and_delivery_are_atomic(db):
    await db.create_stock('AAPL')
    async with db.transaction() as conn:
        await conn.execute("CREATE TEMP TRIGGER reject_delivery BEFORE INSERT ON delivery_outbox BEGIN SELECT RAISE(ABORT,'delivery failed'); END")
    with pytest.raises(Exception,match='delivery failed'): await ReportService(db).generate('收盘报告')
    assert not await db.list_reports() and not await DeliveryStore(db).list()


def test_report_four_outcome_paths():
    snapshot=make_snapshot();result=evaluate(snapshot)
    item={'symbol':'AAPL','quality':{'baseline_days':60},'detection':{'result':result}}
    report=ReportService.build([item],'收盘报告',datetime.fromisoformat(snapshot['as_of']),'four')
    assert '正常异动' in report_content(report,[item],result['events'])[0]
    assert '无新异动' in report_content(report,[item],[])[0]
    result['quality']['valid']=False
    assert '数据不足' in report_content(report,[item],[])[0]
    report.counter_evidence=['采集失败：gateway unavailable']
    assert '运行失败' in report_content(report,[item],[])[0]


async def test_sampling_and_short_run_cannot_claim_full_day(db):
    telemetry=TelemetryService(db,settings(False))
    one=await telemetry.sample();two=await telemetry.sample()
    summary=summarize([one,two])
    assert summary['status']=='needs_review'
    assert 'less than one 6.5-hour observation period' in summary['reasons']
    assert one['process_rss_bytes']>0


def test_delivery_api_and_safe_defaults(tmp_path):
    with make_client(tmp_path) as client:
        client.post('/api/stocks',json={'symbol':'AAPL'})
        assert client.post('/api/reports/generate',json={}).status_code==201
        rows=client.get('/api/notifications/deliveries').json()
        assert rows[0]['status']=='preview'
        assert client.post(f"/api/notifications/deliveries/{rows[0]['id']}/resolve",json={'resolution':'confirmed_sent'}).status_code==409
        assert client.get('/api/metrics').status_code==200


def test_smtp_does_not_send_to_partial_recipient_set(monkeypatch):
    calls=[]
    class FakeSMTP:
        def __init__(self,*a,**k): pass
        def ehlo(self): calls.append('ehlo')
        def starttls(self,**kw): calls.append('tls')
        def mail(self,address): return 250,b'ok'
        def rcpt(self,address): return (250,b'ok') if address.startswith('first') else (550,b'no')
        def close(self): calls.append('close')
    import accuflow.notifications.smtp as module
    monkeypatch.setattr(module.smtplib,'SMTP',FakeSMTP)
    channel=SMTPChannel(settings())
    with pytest.raises(Rejected): channel.prepare({'sender':'from@example.invalid','recipients':['first@example.invalid','second@example.invalid']})
    assert calls==['ehlo','tls','ehlo','close']


async def test_intraday_close_button_cannot_send_premature_daily_email(db):
    snapshot=make_snapshot();result=evaluate(snapshot)
    item={'symbol':'AAPL','quality':{'baseline_days':60},'detection':{'result':result}}
    report=ReportService.build([item],'收盘报告',datetime.fromisoformat(snapshot['as_of']),'draft-close')
    from accuflow.domain.models import Preferences
    async with db.transaction() as conn:
        await DeliveryStore(db).enqueue_report(conn,report,[item],Preferences())
    row=(await DeliveryStore(db).list())[0]
    assert row['status']=='preview' and '盘中预览' in row['subject']
    await DeliveryService(db,settings(),Channel).tick()
    assert not Channel.calls


async def test_corrupt_payload_or_changed_transport_never_sends(db):
    await enqueue(db)
    async with db.transaction() as conn: await conn.execute("UPDATE delivery_outbox SET mime=x'00'")
    service=DeliveryService(db,settings(),Channel);await service.tick()
    assert (await service.store.list())[0]['status']=='failed' and not Channel.calls
    await enqueue(db,'changed')
    altered=settings().model_copy(update={'smtp_host':'other.example.invalid'})
    await DeliveryService(db,altered,Channel).tick()
    assert all(row['status']=='failed' for row in await service.store.list())
    assert not Channel.calls
