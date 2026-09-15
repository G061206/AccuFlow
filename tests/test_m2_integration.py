import copy
import json
from datetime import datetime

import pytest

from accuflow.detectors.unified import evaluate
from accuflow.services.reports import ReportService
from accuflow.storage.database import Database
from accuflow.storage.signals import SignalStore
from accuflow.storage.workflow import WorkflowStore
from m2_fixtures import make_snapshot
from test_api import make_client


async def seed(db,snapshot):
    for symbol,conid,prefix in [('AAPL',1,''),('SPY',2,'market_')]:
        await db.create_stock(symbol)
        await db.save_qualified_contract(symbol,company_name=symbol,con_id=conid,primary_exchange='NASDAQ',currency='USD')
        for size,key in [('1 day','daily'),('1 min','minute')]:
            await db.upsert_bars(symbol=symbol,con_id=conid,bar_size=size,bars=snapshot[prefix+key],use_rth=True)
    await db.set_stock_active('SPY',False)
    await SignalStore(db).save_context('conid:1',snapshot['context'])


async def test_real_storage_to_report_state_preview_and_restart_replay(tmp_path):
    db=Database(tmp_path/'m2.db'); await db.connect()
    snapshot=make_snapshot()
    try:
        await seed(db,snapshot)
        await db.set_system_state('preferences',json.dumps({'hourly_alert':True}))
        service=ReportService(db)
        report=await service.generate('小时报告',as_of=datetime.fromisoformat(snapshot['as_of']))
        results=await SignalStore(db).list('AAPL')
        assert len(results)==1 and results[0]['score']>=65
        identity=results[0]['snapshot_id']
        assert (await db.get_stock('AAPL'))['score']==results[0]['score']
        assert (await service.replay(report['id']))['matches']
        await service.generate('小时报告',as_of=datetime.fromisoformat(snapshot['as_of']))
        assert len(await SignalStore(db).list('AAPL'))==1
        async with db.transaction() as conn:
            await conn.execute('DELETE FROM market_bars')
        assert (await service.replay(report['id']))['matches']
    finally: await db.close()
    await db.connect()
    try:
        assert (await SignalStore(db).replay(identity))['matches']
        assert (await ReportService(db).replay(report['id']))['matches']
    finally: await db.close()


async def test_signal_event_preview_atomicity_and_dedup(tmp_path):
    db=Database(tmp_path/'atomic.db');await db.connect()
    try:
        snapshot=make_snapshot(); result=evaluate(snapshot)
        await db.create_stock('AAPL')
        await db.set_system_state('preferences',json.dumps({'hourly_alert':True}))
        item={'symbol':'AAPL','quality':{'baseline_days':60},'detection':{'snapshot':snapshot,'result':result}}
        report=ReportService.build([item],'小时报告',datetime.fromisoformat(snapshot['as_of']),'atomic')
        async with db.transaction() as conn:
            await conn.execute("CREATE TEMP TRIGGER reject_signal BEFORE INSERT ON signal_previews BEGIN SELECT RAISE(ABORT,'preview failed'); END")
        with pytest.raises(Exception,match='preview failed'): await WorkflowStore(db).save_bundle(report,[item])
        assert not await SignalStore(db).list() and not await db.list_reports()
        async with db.transaction() as conn: await conn.execute('DROP TRIGGER reject_signal')
        await WorkflowStore(db).save_bundle(report,[item])
        assert len(await WorkflowStore(db).outbox())==1
        async with db.transaction() as conn: await SignalStore(db).commit(conn,item['detection'],True)
        assert len(await WorkflowStore(db).outbox())==1
        assert (await SignalStore(db).replay(result['snapshot_id']))['matches']
    finally: await db.close()


def test_context_api_cannot_backdate_availability(tmp_path):
    with make_client(tmp_path) as client:
        client.post('/api/stocks',json={'symbol':'AAPL'})
        client.post('/api/ibkr/connect');client.post('/api/stocks/AAPL/qualify')
        context=make_snapshot()['context']
        context['known_at']='2020-01-01T00:00:00Z'
        response=client.post('/api/stocks/AAPL/analysis-context',json=context)
        assert response.status_code==200
        assert response.json()['known_at']>context['known_at']
        generated=client.post('/api/reports/generate',json={})
        assert generated.status_code==201
        result=client.get('/api/signals?symbol=AAPL').json()[0]
        assert result['score'] is None
        assert client.get(f"/api/signals/{result['snapshot_id']}/replay").json()['matches']


async def test_legacy_quality_report_still_replays(tmp_path):
    db=Database(tmp_path/'legacy.db');await db.connect()
    try:
        await db.create_stock('AAPL')
        service=ReportService(db)
        as_of=datetime.fromisoformat(make_snapshot()['as_of'])
        item=await service.capture('AAPL',as_of)
        item.pop('detection')
        report=service.build_legacy([item],'收盘报告',as_of,'legacy')
        await service.store.save_bundle(report,[item])
        replay=await service.replay('legacy')
        assert replay['matches'] and replay['rule_version']=='unified-v1-readiness'
    finally: await db.close()
