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


class FakeIB:
    def __init__(self):
        self.disconnectedEvent = FakeEvent()
        self.errorEvent = FakeEvent()
        self.connected = False
        self.connect_kwargs = None
        self.market_data_type = None

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
