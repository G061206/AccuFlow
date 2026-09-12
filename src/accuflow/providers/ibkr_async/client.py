from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from ib_async import IB, Stock, StartupFetchNONE

from accuflow.config import Settings
from accuflow.storage.database import Database

logger = logging.getLogger(__name__)


class IBKRNotConnectedError(RuntimeError):
    pass


class IBKRContractError(RuntimeError):
    pass


@dataclass
class IBKRState:
    connected: bool = False
    connecting: bool = False
    host: str = ""
    port: int = 0
    client_id: int = 0
    server_time: str | None = None
    connected_at: str | None = None
    disconnected_at: str | None = None
    last_error: str | None = None
    market_data_type: int = 1


class IBKRClient:
    def __init__(self, settings: Settings, database: Database, ib: IB | None = None):
        self.settings = settings
        self.database = database
        self.ib = ib or IB()
        self.state = IBKRState(
            host=settings.ibkr_host,
            port=settings.ibkr_port,
            client_id=settings.ibkr_client_id,
            market_data_type=settings.ibkr_market_data_type,
        )
        self._connect_lock = asyncio.Lock()
        self.ib.disconnectedEvent += self._on_disconnected
        self.ib.errorEvent += self._on_error

    async def connect(self) -> dict[str, Any]:
        async with self._connect_lock:
            if self.ib.isConnected():
                return self.status()
            self.state.connecting = True
            self.state.last_error = None
            try:
                await self.ib.connectAsync(
                    host=self.settings.ibkr_host,
                    port=self.settings.ibkr_port,
                    clientId=self.settings.ibkr_client_id,
                    timeout=self.settings.ibkr_connect_timeout,
                    readonly=True,
                    fetchFields=StartupFetchNONE,
                )
                self.ib.reqMarketDataType(self.settings.ibkr_market_data_type)
                server_time = await asyncio.wait_for(
                    self.ib.reqCurrentTimeAsync(),
                    timeout=self.settings.ibkr_connect_timeout,
                )
                self.state.connected = True
                self.state.connected_at = datetime.now(UTC).isoformat()
                self.state.server_time = server_time.isoformat()
                return self.status()
            except BaseException as exc:
                self.state.connected = False
                self.state.last_error = str(exc) or exc.__class__.__name__
                if self.ib.isConnected():
                    self.ib.disconnect()
                raise
            finally:
                self.state.connecting = False

    async def disconnect(self) -> dict[str, Any]:
        if self.ib.isConnected():
            self.ib.disconnect()
        self.state.connected = False
        self.state.disconnected_at = datetime.now(UTC).isoformat()
        return self.status()

    def status(self) -> dict[str, Any]:
        self.state.connected = self.ib.isConnected()
        return asdict(self.state)

    async def qualify_stock(self, symbol: str) -> dict[str, Any]:
        self._ensure_connected()
        contract = Stock(symbol, "SMART", "USD")
        qualified = await asyncio.wait_for(
            self.ib.qualifyContractsAsync(contract),
            timeout=self.settings.ibkr_request_timeout,
        )
        if len(qualified) != 1 or qualified[0] is None:
            raise IBKRContractError(f"IBKR 无法唯一解析股票代码 {symbol}")
        result = qualified[0]
        return {
            "symbol": result.symbol,
            "company_name": result.description or result.symbol,
            "con_id": int(result.conId),
            "primary_exchange": result.primaryExchange or "",
            "currency": result.currency or "USD",
            "contract": result,
        }

    async def historical_bars(
        self,
        *,
        contract: Any,
        duration: str,
        bar_size: str,
        use_rth: bool = True,
    ) -> list[dict[str, Any]]:
        self._ensure_connected()
        bars = await self.ib.reqHistoricalDataAsync(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow="TRADES",
            useRTH=use_rth,
            formatDate=2,
            keepUpToDate=False,
            timeout=self.settings.ibkr_request_timeout,
        )
        return [
            {
                "timestamp": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "average": bar.average,
                "bar_count": bar.barCount,
            }
            for bar in bars
        ]

    def _ensure_connected(self) -> None:
        if not self.ib.isConnected():
            raise IBKRNotConnectedError("IBKR Gateway/TWS 尚未连接")

    def _on_disconnected(self) -> None:
        self.state.connected = False
        self.state.disconnected_at = datetime.now(UTC).isoformat()

    def _on_error(
        self,
        request_id: int,
        error_code: int,
        message: str,
        contract: Any | None,
    ) -> None:
        symbol = getattr(contract, "symbol", None) if contract else None
        if error_code not in {2104, 2106, 2107, 2108, 2158}:
            self.state.last_error = f"{error_code}: {message}"
        logger.warning(
            "IBKR error request_id=%s code=%s symbol=%s message=%s",
            request_id,
            error_code,
            symbol,
            message,
        )
        try:
            asyncio.get_running_loop().create_task(
                self.database.record_ibkr_error(
                    request_id=request_id,
                    error_code=error_code,
                    message=message,
                    symbol=symbol,
                )
            )
        except RuntimeError:
            logger.debug("No running loop available to persist IBKR error")
