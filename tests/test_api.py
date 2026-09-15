from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from accuflow.api import create_app
from accuflow.config import Settings


class FakeIBKRClient:
    def __init__(self, settings, database):
        self.connected = False

    def status(self):
        return {
            "connected": self.connected,
            "connecting": False,
            "host": "127.0.0.1",
            "port": 4002,
            "client_id": 17,
            "server_time": "2026-09-13T13:32:00+00:00" if self.connected else None,
            "connected_at": None,
            "disconnected_at": None,
            "last_error": None,
            "market_data_type": 1,
        }

    async def connect(self):
        self.connected = True
        return self.status()

    async def disconnect(self):
        self.connected = False
        return self.status()

    async def qualify_stock(self, symbol):
        if not self.connected:
            from accuflow.providers.ibkr_async.client import IBKRNotConnectedError

            raise IBKRNotConnectedError("IBKR Gateway/TWS 尚未连接")
        contract = SimpleNamespace(symbol=symbol)
        return {
            "symbol": symbol,
            "company_name": f"{symbol} Corporation",
            "con_id": 1000 + sum(map(ord, symbol)),
            "primary_exchange": "NASDAQ",
            "currency": "USD",
            "contract": contract,
        }

    async def probe_capabilities(self, symbol):
        con_id = 1000 + sum(map(ord, symbol))
        return {
            "id": f"capability-{symbol}-test",
            "symbol": symbol,
            "con_id": con_id,
            "checked_at": "2026-09-13T13:32:00+00:00",
            "snapshot_status": "available",
            "market_data_type": 1,
            "historical_bars_status": "available",
            "historical_bars_count": 5,
            "historical_ticks_status": "available",
            "historical_ticks_count": 1,
            "tick_by_tick_last_status": "requested_no_sample",
            "tick_by_tick_bidask_status": "requested_no_sample",
            "details": {
                "market_data_type_label": "live",
                "snapshot": {"values": {"last": 102}},
            },
        }

    async def historical_bars(self, *, contract, duration, bar_size, use_rth=True, end_date_time=""):
        return [
            {
                "timestamp": datetime(2026, 9, 11, 20, 0, tzinfo=UTC),
                "open": 100,
                "high": 103,
                "low": 99,
                "close": 102,
                "volume": 1000,
                "average": 101.5,
                "bar_count": 42,
            }
        ]


def make_client(tmp_path):
    settings = Settings(
        database_path=tmp_path / "accuflow-test.db",
        ibkr_connect_on_startup=False,
        initial_symbols="",
    )
    app = create_app(settings, ibkr_factory=FakeIBKRClient)
    return TestClient(app)


def test_stock_lifecycle_and_ibkr_backfill(tmp_path):
    with make_client(tmp_path) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ibkr"]["connected"] is False

        created = client.post("/api/stocks", json={"symbol": "aapl"})
        assert created.status_code == 201
        assert created.json()["symbol"] == "AAPL"
        assert created.json()["dataStatus"] == "pending"

        duplicate = client.post("/api/stocks", json={"symbol": "AAPL"})
        assert duplicate.status_code == 409

        disconnected_backfill = client.post(
            "/api/stocks/AAPL/backfill",
            json={"include_daily": True, "include_minute": True},
        )
        assert disconnected_backfill.status_code == 503

        connected = client.post("/api/ibkr/connect")
        assert connected.status_code == 200
        assert connected.json()["connected"] is True

        qualified = client.post("/api/stocks/AAPL/qualify")
        assert qualified.status_code == 200
        assert qualified.json()["conId"] is not None
        capability = client.post("/api/stocks/AAPL/probe")
        assert capability.status_code == 200
        assert capability.json()["snapshot_status"] == "available"
        assert capability.json()["market_data_type"] == 1

        capabilities = client.get(
            "/api/ibkr/capabilities", params={"symbol": "aapl"}
        )
        assert capabilities.status_code == 200
        assert [item["symbol"] for item in capabilities.json()] == ["AAPL"]
        assert capabilities.json()[0]["details"]["market_data_type_label"] == "live"


        backfill = client.post(
            "/api/stocks/AAPL/backfill",
            json={"include_daily": True, "include_minute": True},
        )
        assert backfill.status_code == 200
        assert backfill.json()["stored"] == {"daily": 1, "minute": 1}

        daily_bars = client.get(
            "/api/stocks/AAPL/bars", params={"bar_size": "1 day"}
        )
        assert daily_bars.status_code == 200
        assert len(daily_bars.json()) == 1
        assert daily_bars.json()[0]["source"] == "IBKR"
        generated = client.post(
            "/api/reports/generate",
            json={"report_type": "收盘报告"},
        )
        assert generated.status_code == 201
        assert generated.json()["symbols"] == ["AAPL"]
        assert generated.json()["ruleVersion"] == "unified-v1-m2.1"
        assert "不输出建仓强度分数" in generated.json()["conclusion"]


        paused = client.patch("/api/stocks/AAPL", json={"active": False})
        assert paused.status_code == 200
        assert paused.json()["status"] == "paused"

        deleted = client.delete("/api/stocks/AAPL")
        assert deleted.status_code == 204
        assert client.get("/api/stocks").json() == []


def test_reports_are_persisted_and_searchable(tmp_path):
    report = {
        "id": "closing-20260911",
        "report_time": "2026-09-11T20:00:00Z",
        "report_type": "收盘报告",
        "symbols": ["AAPL", "NVDA"],
        "summary": "2 只股票完成收盘检测",
        "judgment": "NVDA 的持续需求迹象较强。",
        "evidence": ["成交压力持续"],
        "counter_evidence": ["样本日期仍有限"],
        "data_quality": "数据完整",
        "rule_version": "unified-v1",
    }

    with make_client(tmp_path) as client:
        created = client.post("/api/reports", json=report)
        assert created.status_code == 201
        assert created.json()["symbols"] == ["AAPL", "NVDA"]

        by_symbol = client.get("/api/reports", params={"query": "NVDA"})
        assert by_symbol.status_code == 200
        assert [item["id"] for item in by_symbol.json()] == ["closing-20260911"]

        by_type = client.get(
            "/api/reports", params={"report_type": "小时报告"}
        )
        assert by_type.status_code == 200
        assert by_type.json() == []

        detail = client.get("/api/reports/closing-20260911")
        assert detail.status_code == 200
        assert detail.json()["quality"] == "数据完整"

    with make_client(tmp_path) as restarted_client:
        persisted = restarted_client.get("/api/reports")
        assert persisted.status_code == 200
        assert len(persisted.json()) == 1
        assert persisted.json()[0]["id"] == "closing-20260911"


def test_initial_watchlist_is_seeded_only_once(tmp_path):
    database_path = tmp_path / "watchlist.db"
    settings = Settings(
        database_path=database_path,
        initial_symbols="AAPL,NVDA",
        ibkr_connect_on_startup=False,
    )
    app = create_app(settings, ibkr_factory=FakeIBKRClient)

    with TestClient(app) as client:
        assert [item["symbol"] for item in client.get("/api/stocks").json()] == [
            "AAPL",
            "NVDA",
        ]
        assert client.delete("/api/stocks/AAPL").status_code == 204
        assert client.delete("/api/stocks/NVDA").status_code == 204

    restarted_app = create_app(settings, ibkr_factory=FakeIBKRClient)
    with TestClient(restarted_app) as restarted_client:
        assert restarted_client.get("/api/stocks").json() == []
