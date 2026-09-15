import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from accuflow.config import Settings
from accuflow.domain.models import BackfillRequest
from accuflow.providers.ibkr_async.client import IBKRClient
from accuflow.services.calendar import sessions, checkpoints
from accuflow.services.quality import QualityService
from accuflow.services.reports import ReportService
from accuflow.storage.database import Database
from test_api import make_client
from test_ibkr_client import FakeIB

AS_OF = datetime(2026, 9, 12, 12, tzinfo=UTC)


def bar(timestamp, **overrides):
    return dict(timestamp=timestamp, open=100, high=102, low=99, close=101, volume=100, **overrides)


async def seed_history(db):
    await db.create_stock("AAPL")
    days = sessions(AS_OF, 60)
    await db.upsert_bars(symbol="AAPL", con_id=1, bar_size="1 day", use_rth=True,
        bars=[bar(day["date"]) for day in days])
    minutes = [bar(day["open"] + timedelta(minutes=i)) for day in days[-20:]
        for i in range(int((day["close"] - day["open"]).total_seconds() // 60))]
    await db.upsert_bars(symbol="AAPL", con_id=1, bar_size="1 min", use_rth=True, bars=minutes)
    return minutes


def test_duplicate_stock_does_not_poison_next_report(tmp_path):
    with make_client(tmp_path) as client:
        assert client.post("/api/stocks", json={"symbol": "AAPL"}).status_code == 201
        assert client.post("/api/stocks", json={"symbol": "AAPL"}).status_code == 409
        assert client.post("/api/reports/generate", json={}).status_code == 201


async def test_partial_bar_batch_is_rolled_back(tmp_path):
    db = Database(tmp_path / "batch.db"); await db.connect()
    try:
        await db.create_stock("AAPL")
        with pytest.raises(sqlite3.IntegrityError):
            await db.upsert_bars(symbol="AAPL", con_id=1, bar_size="1 min", use_rth=True,
                bars=[bar(AS_OF), {**bar(AS_OF + timedelta(minutes=1)), "close": float("nan")}])
        assert await db.list_bars("AAPL", "1 min") == []
        await db.create_stock("MSFT")
        assert await db.get_stock("MSFT")
    finally: await db.close()


async def test_twenty_full_sessions_not_truncated_and_stale_data_fails(tmp_path):
    db = Database(tmp_path / "quality.db"); await db.connect()
    try:
        minutes = await seed_history(db)
        q = await QualityService(db).evaluate("AAPL", AS_OF)
        assert len(minutes) > 5000
        assert q["baseline_days"] == 20
        assert q["minute_count"] == len(minutes)
        assert q["ready"]
        report = await ReportService(db).generate("收盘报告", as_of=AS_OF)
        assert "1 只股票完成统一检测" in report["summary"]
        stale = await QualityService(db).evaluate("AAPL", AS_OF + timedelta(days=5))
        assert not stale["ready"] and not stale["minute_fresh"]
        async with db.transaction() as conn:
            await conn.execute("DELETE FROM market_bars WHERE bar_size='1 min' AND timestamp < ?",
                (minutes[-1]["timestamp"].isoformat(),))
        sparse = await QualityService(db).evaluate("AAPL", AS_OF)
        assert sparse["baseline_days"] == 0 and not sparse["ready"]
    finally: await db.close()


def test_noop_backfill_rejected_and_empty_never_ready(tmp_path):
    with pytest.raises(ValueError): BackfillRequest(include_daily=False, include_minute=False)
    with make_client(tmp_path) as client:
        client.post("/api/stocks", json={"symbol": "AAPL"})
        assert client.post("/api/stocks/AAPL/backfill", json={"include_daily": False, "include_minute": False}).status_code == 422
        client.post("/api/ibkr/connect")
        async def empty(**kwargs): return []
        client.app.state.ibkr.historical_bars = empty
        assert client.post("/api/stocks/AAPL/backfill", json={}).status_code == 422
        stock = client.get("/api/stocks").json()[0]
        assert stock["dataStatus"] == "error"
        assert stock["coverage"] != "完整"


def test_preferences_persist_and_signal_preview_can_be_enabled(tmp_path):
    with make_client(tmp_path) as client:
        assert client.patch("/api/settings", json={"daily_report": False}).status_code == 200
        assert client.patch("/api/settings", json={"hourly_alert": True}).status_code == 200
    with make_client(tmp_path) as client:
        settings = client.get("/api/settings").json()
        assert settings["daily_report"] is False and settings["delivery_mode"] == "preview"


def test_error_cursor_survives_eviction_and_filters_other_symbols():
    client = IBKRClient(Settings(), SimpleNamespace(), ib=FakeIB())
    for n in range(250): client._capture_probe_error(n, 2104, "info", None)
    cursor = client._error_sequence
    client._capture_probe_error(251, 354, "denied", SimpleNamespace(symbol="MSFT"))
    client._capture_probe_error(252, 354, "denied", SimpleNamespace(symbol="AAPL"))
    errors = client._errors_since(cursor, SimpleNamespace(symbol="AAPL"))
    assert [item["request_id"] for item in errors] == [252]
    assert client._status_from_errors(errors) == "unavailable"


async def test_cancel_snapshot_and_live_ticks_release_resources():
    fake = FakeIB()
    fake.reqMktData = lambda *a: SimpleNamespace(bid=None, ask=None, last=None, close=None)
    client = IBKRClient(Settings(), SimpleNamespace(), ib=fake)
    snapshot = asyncio.create_task(client._probe_snapshot(SimpleNamespace(symbol="AAPL")))
    await asyncio.sleep(0.01); snapshot.cancel()
    with pytest.raises(asyncio.CancelledError): await snapshot
    assert fake.cancelled_snapshot
    ticks = asyncio.create_task(client._probe_live_ticks(SimpleNamespace(symbol="AAPL"), "Last", 30))
    await asyncio.sleep(0.01); ticks.cancel()
    with pytest.raises(asyncio.CancelledError): await ticks
    assert fake.cancelled_tick_types == ["Last"]


def test_calendar_holiday_early_close_dst():
    thanksgiving = datetime(2026, 11, 26, 23, tzinfo=UTC)
    assert sessions(thanksgiving, 1)[0]["date"] == "2026-11-25"
    friday = sessions(datetime(2026, 11, 27, 23, tzinfo=UTC), 1)[0]
    assert friday["close"].hour == 18
    before = sessions(datetime(2026, 3, 6, 23, tzinfo=UTC), 1)[0]
    after = sessions(datetime(2026, 3, 9, 23, tzinfo=UTC), 1)[0]
    assert before["open"].hour == 14 and after["open"].hour == 13
    assert (friday["close"], "收盘报告") in checkpoints(datetime(2026, 11, 27, 18, 20, tzinfo=UTC))


async def test_readers_do_not_observe_uncommitted_writes(tmp_path):
    db = Database(tmp_path / "isolation.db"); await db.connect()
    try:
        async with db.transaction() as conn:
            await conn.execute("INSERT INTO system_state VALUES('isolation','pending','now')")
            assert await db.get_system_state("isolation") is None
        assert await db.get_system_state("isolation") == "pending"
    finally: await db.close()
