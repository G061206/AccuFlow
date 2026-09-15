import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from accuflow.config import Settings
from accuflow.services.market_data import MarketDataService
from accuflow.services.reports import ReportService
from accuflow.services.workflow import WorkflowService
from accuflow.storage.database import Database
from accuflow.storage.workflow import WorkflowStore
from test_api import make_client
from test_regressions import AS_OF, seed_history


class Collector:
    def __init__(self):
        self.calls = []
        self.failures = {}
        self.ibkr = SimpleNamespace(status=lambda: {"connected":True})

    async def backfill(self, symbol, **kwargs):
        self.calls.append(symbol)
        if symbol in self.failures: raise self.failures[symbol]
        return {"daily":0,"minute":0}


async def test_checkpoint_idempotency_outbox_and_replay_after_source_changes(tmp_path):
    db=Database(tmp_path/"workflow.db"); await db.connect()
    try:
        await seed_history(db)
        collector=Collector()
        reports=ReportService(db)
        flow=WorkflowService(db,collector,reports,Settings())
        result=await flow.run_checkpoint(AS_OF,"收盘报告")
        again=await flow.run_checkpoint(AS_OF,"收盘报告")
        assert again["status"] == "already_claimed_or_completed"
        assert collector.calls == ["AAPL"]
        store=WorkflowStore(db)
        assert len(await store.outbox()) == 1
        assert (await store.jobs())[0]["status"] == "completed"
        assert (await reports.replay(result["report_id"]))["matches"]
        async with db.transaction() as conn:
            await conn.execute("DELETE FROM market_bars")
        assert (await reports.replay(result["report_id"]))["matches"]
        # Reopening the application keeps the original report and dedupe key.
    finally: await db.close()
    await db.connect()
    try:
        resumed=WorkflowService(db,collector,ReportService(db),Settings())
        assert (await resumed.run_checkpoint(AS_OF,"收盘报告"))["status"] == "already_claimed_or_completed"
        assert len(await WorkflowStore(db).outbox()) == 1
    finally: await db.close()


async def test_cancelled_checkpoint_resumes_only_unfinished_symbols(tmp_path):
    db=Database(tmp_path/"resume.db"); await db.connect()
    try:
        await db.create_stock("AAPL"); await db.create_stock("MSFT")
        collector=Collector(); collector.failures["MSFT"]=asyncio.CancelledError()
        flow=WorkflowService(db,collector,ReportService(db),Settings())
        with pytest.raises(asyncio.CancelledError): await flow.run_checkpoint(AS_OF,"收盘报告")
        assert (await WorkflowStore(db).jobs())[0]["status"] == "failed"
        assert await db.list_reports() == []
        collector.failures.clear()
        flow=WorkflowService(db,collector,ReportService(db),Settings())
        result=await flow.run_checkpoint(AS_OF,"收盘报告")
        assert result["status"] == "completed"
        assert collector.calls == ["AAPL","MSFT","MSFT"]
        assert len(await WorkflowStore(db).outbox()) == 1
    finally: await db.close()


async def test_collection_failure_isolated_and_not_reported_as_no_signal(tmp_path):
    db=Database(tmp_path/"failure.db"); await db.connect()
    try:
        await db.create_stock("AAPL"); await db.create_stock("MSFT")
        collector=Collector(); collector.failures["AAPL"]=ConnectionError("gateway unavailable")
        flow=WorkflowService(db,collector,ReportService(db),Settings())
        result=await flow.run_checkpoint(AS_OF,"收盘报告")
        report=await db.get_report(result["report_id"])
        assert collector.calls == ["AAPL","MSFT"]
        assert "采集失败：gateway unavailable" in " ".join(report["counter_evidence"])
        assert "不输出建仓强度分数" in report["judgment"]
        assert "无异动" not in report["judgment"]
    finally: await db.close()


async def test_outbox_failure_rolls_back_report_and_snapshot(tmp_path):
    db=Database(tmp_path/"atomic.db"); await db.connect()
    try:
        await db.create_stock("AAPL")
        async with db.transaction() as conn:
            await conn.execute("""CREATE TEMP TRIGGER reject_preview BEFORE INSERT ON notification_outbox
                BEGIN SELECT RAISE(ABORT,'outbox failed'); END""")
        with pytest.raises(Exception, match="outbox failed"):
            await ReportService(db).generate("收盘报告",as_of=AS_OF)
        assert await db.list_reports() == []
        row=await (await db.connection.execute("SELECT count(*) AS n FROM report_inputs")).fetchone()
        assert row["n"] == 0
        assert await WorkflowStore(db).outbox() == []
    finally: await db.close()


async def test_lease_fences_old_owner_across_connections(tmp_path):
    first=Database(tmp_path/"lease.db"); second=Database(tmp_path/"lease.db")
    await first.connect(); await second.connect()
    try:
        a,b=WorkflowStore(first),WorkflowStore(second)
        assert await a.claim("job",AS_OF,"收盘报告","first",["AAPL"],now=AS_OF) == ["AAPL"]
        assert await b.claim("job",AS_OF,"收盘报告","second",["MSFT"],now=AS_OF+timedelta(seconds=60)) is None
        assert await b.claim("job",AS_OF,"收盘报告","second",["MSFT"],now=AS_OF+timedelta(seconds=121)) == ["AAPL"]
        with pytest.raises(RuntimeError,match="lease lost"):
            await a.save_input("job","first","AAPL",{})
        assert not await a.renew("job","first")
    finally:
        await first.close(); await second.close()


async def test_completed_history_not_redownloaded(tmp_path):
    db=Database(tmp_path/"incremental.db"); await db.connect()
    try:
        await seed_history(db)
        class Gateway:
            async def qualify_stock(self,symbol):
                return dict(company_name=symbol,con_id=1,primary_exchange="NASDAQ",currency="USD",contract=object())
            async def historical_bars(self,**kwargs):
                pytest.fail("complete historical sessions should not be downloaded again")
        service=MarketDataService(db,Gateway())
        assert await service.backfill("AAPL",include_daily=False,include_minute=True,as_of=AS_OF) == {"daily":0,"minute":0}
        assert (await db.get_stock("AAPL"))["data_status"] == "ready"
    finally: await db.close()


def test_generated_report_snapshot_is_immutable_and_preview_preference_applies(tmp_path):
    with make_client(tmp_path) as client:
        client.post("/api/stocks",json={"symbol":"AAPL"})
        client.patch("/api/settings",json={"daily_report":False})
        generated=client.post("/api/reports/generate",json={}).json()
        assert client.get("/api/notifications/outbox").json() == []
        replay=client.get(f"/api/reports/{generated['id']}/replay").json()
        assert replay["matches"] is True
        assert client.post("/api/reports",json=replay["report"]).status_code == 409
        assert client.get("/api/workflow/jobs").json() == []


async def test_disabled_scheduler_makes_no_provider_calls(tmp_path):
    db=Database(tmp_path/"disabled.db"); await db.connect()
    try:
        await db.create_stock("AAPL")
        collector=Collector()
        flow=WorkflowService(db,collector,ReportService(db),Settings())
        await flow.tick(AS_OF)
        assert collector.calls == [] and await WorkflowStore(db).jobs() == []
        await db.set_system_state("preferences",json.dumps({"monitoring_enabled":True}))
        await flow.tick(AS_OF)
        assert collector.calls == ["AAPL"]
        await flow.tick(AS_OF)
        assert collector.calls == ["AAPL"]
    finally: await db.close()
