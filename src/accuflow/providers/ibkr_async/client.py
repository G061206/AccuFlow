from __future__ import annotations

import asyncio
import math
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from ib_async import IB, Stock, StartupFetchNONE

from accuflow.config import Settings
from accuflow.storage.database import Database

logger = logging.getLogger(__name__)
MARKET_DATA_TYPE_LABELS = {
    1: "live",
    2: "frozen",
    3: "delayed",
    4: "delayed_frozen",
}
INFORMATIONAL_ERROR_CODES = {2104, 2106, 2107, 2108, 2158}
PERMISSION_ERROR_CODES = {354, 10089, 10167, 10168, 10189}



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
        self._recent_errors: list[dict[str, Any]] = []
        self.ib.errorEvent += self._capture_probe_error

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

    async def probe_capabilities(
        self,
        symbol: str,
        *,
        sample_wait_seconds: float = 1.0,
    ) -> dict[str, Any]:
        self._ensure_connected()
        qualified = await self.qualify_stock(symbol)
        contract = qualified["contract"]
        snapshot = await self._probe_snapshot(contract)
        historical_bars = await self._probe_historical_bars(contract)
        historical_ticks = await self._probe_historical_ticks(contract)
        tick_last = await self._probe_live_ticks(
            contract, "Last", sample_wait_seconds
        )
        tick_bidask = await self._probe_live_ticks(
            contract, "BidAsk", sample_wait_seconds
        )
        checked = datetime.now(UTC)
        return {
            "id": (
                f"capability-{symbol}-"
                f"{checked.strftime('%Y%m%dT%H%M%S%fZ')}"
            ),
            "symbol": symbol,
            "con_id": qualified["con_id"],
            "checked_at": checked.isoformat(),
            "snapshot_status": snapshot["status"],
            "market_data_type": snapshot["market_data_type"],
            "historical_bars_status": historical_bars["status"],
            "historical_bars_count": historical_bars["sample_count"],
            "historical_ticks_status": historical_ticks["status"],
            "historical_ticks_count": historical_ticks["sample_count"],
            "tick_by_tick_last_status": tick_last["status"],
            "tick_by_tick_bidask_status": tick_bidask["status"],
            "details": {
                "market_data_type_label": MARKET_DATA_TYPE_LABELS.get(
                    snapshot["market_data_type"], "unknown"
                ),
                "snapshot": snapshot,
                "historical_bars": historical_bars,
                "historical_ticks": historical_ticks,
                "tick_by_tick_last": tick_last,
                "tick_by_tick_bidask": tick_bidask,
            },
        }

    async def _probe_snapshot(self, contract: Any) -> dict[str, Any]:
        error_cursor = len(self._recent_errors)
        try:
            tickers = await asyncio.wait_for(
                self.ib.reqTickersAsync(contract),
                timeout=self.settings.ibkr_request_timeout,
            )
            ticker = tickers[0] if tickers else None
        except BaseException as exc:
            return {
                "status": "error",
                "market_data_type": None,
                "values": {},
                "errors": self._errors_since(error_cursor),
                "reason": str(exc) or exc.__class__.__name__,
            }

        errors = self._errors_since(error_cursor)
        error_status = self._status_from_errors(errors)
        values = {
            name: self._finite_number(getattr(ticker, name, None))
            for name in ("bid", "ask", "last", "close")
        }
        has_sample = any(value is not None for value in values.values())
        return {
            "status": error_status or ("available" if has_sample else "no_sample"),
            "market_data_type": (
                int(ticker.marketDataType)
                if ticker is not None
                and getattr(ticker, "marketDataType", None) is not None
                else None
            ),
            "values": values,
            "errors": errors,
        }

    async def _probe_historical_bars(
        self, contract: Any
    ) -> dict[str, Any]:
        error_cursor = len(self._recent_errors)
        try:
            bars = await self.historical_bars(
                contract=contract,
                duration="5 D",
                bar_size="1 day",
            )
        except BaseException as exc:
            return {
                "status": "error",
                "sample_count": 0,
                "errors": self._errors_since(error_cursor),
                "reason": str(exc) or exc.__class__.__name__,
            }
        errors = self._errors_since(error_cursor)
        error_status = self._status_from_errors(errors)
        return {
            "status": error_status or ("available" if bars else "no_sample"),
            "sample_count": len(bars),
            "errors": errors,
        }

    async def _probe_historical_ticks(
        self, contract: Any
    ) -> dict[str, Any]:
        error_cursor = len(self._recent_errors)
        try:
            ticks = await asyncio.wait_for(
                self.ib.reqHistoricalTicksAsync(
                    contract,
                    startDateTime="",
                    endDateTime=datetime.now(UTC),
                    numberOfTicks=1,
                    whatToShow="TRADES",
                    useRth=True,
                    ignoreSize=True,
                ),
                timeout=self.settings.ibkr_request_timeout,
            )
        except BaseException as exc:
            return {
                "status": "error",
                "sample_count": 0,
                "errors": self._errors_since(error_cursor),
                "reason": str(exc) or exc.__class__.__name__,
            }
        errors = self._errors_since(error_cursor)
        error_status = self._status_from_errors(errors)
        return {
            "status": error_status or ("available" if ticks else "no_sample"),
            "sample_count": len(ticks),
            "errors": errors,
        }

    async def _probe_live_ticks(
        self,
        contract: Any,
        tick_type: str,
        sample_wait_seconds: float,
    ) -> dict[str, Any]:
        error_cursor = len(self._recent_errors)
        ticker = None
        initial_count = 0
        subscribed = False
        permission_denied = False
        try:
            ticker = self.ib.reqTickByTickData(
                contract,
                tick_type,
                numberOfTicks=0,
                ignoreSize=tick_type == "Last",
            )
            subscribed = True
            initial_count = len(ticker.tickByTicks)
            await asyncio.sleep(sample_wait_seconds)
            sample_count = max(0, len(ticker.tickByTicks) - initial_count)
            errors = self._errors_since(error_cursor)
            error_status = self._status_from_errors(errors)
            permission_denied = error_status == "unavailable"
            return {
                "status": error_status
                or ("available" if sample_count else "requested_no_sample"),
                "sample_count": sample_count,
                "errors": errors,
                "note": (
                    None
                    if sample_count
                    else "请求已被接受，但探测窗口内没有新逐笔；非交易时段不能据此确认实时覆盖"
                ),
            }
        except BaseException as exc:
            return {
                "status": "error",
                "sample_count": 0,
                "errors": self._errors_since(error_cursor),
                "reason": str(exc) or exc.__class__.__name__,
            }
        finally:
            if subscribed and not permission_denied:
                self.ib.cancelTickByTickData(contract, tick_type)
            if ticker is not None:
                del ticker.tickByTicks[initial_count:]

    def _errors_since(self, cursor: int) -> list[dict[str, Any]]:
        return [dict(item) for item in self._recent_errors[cursor:]]

    @staticmethod
    def _status_from_errors(
        errors: list[dict[str, Any]]
    ) -> str | None:
        codes = {item["error_code"] for item in errors}
        if codes & PERMISSION_ERROR_CODES:
            return "unavailable"
        if codes - INFORMATIONAL_ERROR_CODES:
            return "error"
        return None

    @staticmethod
    def _finite_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _capture_probe_error(
        self,
        request_id: int,
        error_code: int,
        message: str,
        contract: Any | None,
    ) -> None:
        self._recent_errors.append(
            {
                "observed_at": datetime.now(UTC).isoformat(),
                "request_id": request_id,
                "error_code": error_code,
                "message": message,
                "symbol": (
                    getattr(contract, "symbol", None) if contract else None
                ),
            }
        )
        if len(self._recent_errors) > 200:
            del self._recent_errors[:-200]

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
