from datetime import UTC, datetime
from types import SimpleNamespace

from ib_async import StartupFetchNONE

from accuflow.config import Settings
from accuflow.providers.ibkr_async.client import IBKRClient


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self


class FakeIB:
    def __init__(self):
        self.disconnectedEvent = FakeEvent()
        self.errorEvent = FakeEvent()
        self.connected = False
        self.connect_kwargs = None
        self.market_data_type = None
        self.cancelled_tick_types = []

    async def connectAsync(self, **kwargs):
        self.connect_kwargs = kwargs
        self.connected = True

    def reqMarketDataType(self, market_data_type):
        self.market_data_type = market_data_type

    async def reqCurrentTimeAsync(self):
        return datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    def isConnected(self):
        return self.connected

    def disconnect(self):
        self.connected = False
    async def qualifyContractsAsync(self, contract):
        contract.conId = 265598
        contract.description = "Apple Inc."
        contract.primaryExchange = "NASDAQ"
        return [contract]

    def reqMktData(self, *args):
        return SimpleNamespace(marketDataType=1, bid=230.1, ask=230.2, last=230.15, close=229.8)

    def cancelMktData(self, contract):
        self.cancelled_snapshot = True

    async def reqTickersAsync(self, contract):
        return [
            SimpleNamespace(
                marketDataType=1,
                bid=230.1,
                ask=230.2,
                last=230.15,
                close=229.8,
            )
        ]

    async def reqHistoricalDataAsync(self, *args, **kwargs):
        return [
            SimpleNamespace(
                date=datetime(2026, 9, 11, 20, 0, tzinfo=UTC),
                open=228,
                high=231,
                low=227,
                close=230,
                volume=1000,
                average=229.5,
                barCount=42,
            )
        ]

    async def reqHistoricalTicksAsync(self, *args, **kwargs):
        return [
            SimpleNamespace(
                time=datetime(2026, 9, 11, 19, 59, tzinfo=UTC)
            )
        ]

    def reqTickByTickData(
        self, contract, tick_type, numberOfTicks=0, ignoreSize=False
    ):
        return SimpleNamespace(tickByTicks=[], updateEvent=FakeEvent())

    def cancelTickByTickData(self, contract, tick_type):
        self.cancelled_tick_types.append(tick_type)
        return True



async def test_ibkr_connection_is_readonly_and_skips_account_startup_fetch(tmp_path):
    settings = Settings(
        database_path=tmp_path / "adapter.db",
        ibkr_host="127.0.0.1",
        ibkr_port=4002,
        ibkr_client_id=27,
        ibkr_market_data_type=1,
    )
    fake_ib = FakeIB()
    client = IBKRClient(settings, SimpleNamespace(), ib=fake_ib)

    status = await client.connect()

    assert status["connected"] is True
    assert fake_ib.connect_kwargs["readonly"] is True
    assert fake_ib.connect_kwargs["fetchFields"] == StartupFetchNONE
    assert fake_ib.connect_kwargs["clientId"] == 27
    assert fake_ib.market_data_type == 1


async def test_capability_probe_reports_each_ibkr_data_path(tmp_path):
    settings = Settings(
        database_path=tmp_path / "adapter.db",
        ibkr_request_timeout=2,
    )
    fake_ib = FakeIB()
    client = IBKRClient(settings, SimpleNamespace(), ib=fake_ib)
    await client.connect()

    report = await client.probe_capabilities(
        "AAPL", sample_wait_seconds=0
    )

    assert report["symbol"] == "AAPL"
    assert report["snapshot_status"] == "available"
    assert report["market_data_type"] == 1
    assert report["historical_bars_status"] == "available"
    assert report["historical_ticks_status"] == "available"
    assert report["tick_by_tick_last_status"] == "requested_no_sample"
    assert report["tick_by_tick_bidask_status"] == "requested_no_sample"
    assert fake_ib.cancelled_tick_types == ["Last", "BidAsk"]
