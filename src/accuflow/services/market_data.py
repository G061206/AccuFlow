from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from accuflow.providers.ibkr_async.client import IBKRClient
from accuflow.services.calendar import sessions
from accuflow.services.quality import QualityService
from accuflow.storage.database import Database


class MarketDataService:
    def __init__(self, database: Database, ibkr: IBKRClient):
        self.database = database
        self.ibkr = ibkr
        self.quality = QualityService(database)
        self._backfill_lock = asyncio.Lock()

    async def qualify_and_persist(self, symbol: str) -> dict[str, Any]:
        if await self.database.get_stock(symbol) is None:
            raise KeyError(symbol)
        qualified = await self.ibkr.qualify_stock(symbol)
        return await self.database.save_qualified_contract(
            symbol, company_name=qualified["company_name"], con_id=qualified["con_id"],
            primary_exchange=qualified["primary_exchange"], currency=qualified["currency"],
        )

    async def probe_and_persist(self, symbol: str) -> dict[str, Any]:
        if await self.database.get_stock(symbol) is None:
            raise KeyError(symbol)
        report = await self.ibkr.probe_capabilities(symbol)
        return await self.database.save_capability_report(report)

    async def backfill(self, symbol: str, *, include_daily: bool, include_minute: bool,
                       as_of: datetime | None = None) -> dict[str, int]:
        if not include_daily and not include_minute:
            raise ValueError("至少选择一种补数周期")
        async with self._backfill_lock:
            if await self.database.get_stock(symbol) is None:
                raise KeyError(symbol)
            as_of = as_of or datetime.now(UTC)
            counts = {"daily": set(), "minute": set()}
            try:
                qualified = await self.ibkr.qualify_stock(symbol)
                await self.database.save_qualified_contract(
                    symbol, company_name=qualified["company_name"], con_id=qualified["con_id"],
                    primary_exchange=qualified["primary_exchange"], currency=qualified["currency"],
                )
                await self.database.mark_stock_data_status(symbol, "syncing", "正在串行补齐历史数据")
                quality = await self.quality.evaluate(symbol, as_of)
                requests = []
                if include_daily:
                    requests.append(("daily", "1 day", "1 Y", as_of))
                if include_minute:
                    completed = {x["date"] for x in quality["sessions"] if x["ratio"] >= 0.95}
                    # Include twenty completed sessions and the current session. Persist
                    # each slice immediately so an interrupted run resumes missing slices.
                    targets = {x["date"]: x for x in sessions(as_of, 20)}
                    targets.update({x["date"]: x for x in sessions(as_of, 1, complete=False)})
                    for day, session in sorted(targets.items()):
                        if day not in completed:
                            end = min(as_of, session["close"])
                            requests.append(("minute", "1 min", "1 D", end))
                for kind, size, duration, end in requests:
                    bars = await self.ibkr.historical_bars(contract=qualified["contract"],
                        duration=duration, bar_size=size, end_date_time=end)
                    if not bars:
                        raise TimeoutError(f"IBKR {size} 返回空数据，补数未完成")
                    await self.database.upsert_bars(symbol=symbol, con_id=qualified["con_id"],
                        bar_size=size, bars=bars, use_rth=True)
                    counts[kind].update(str(bar["timestamp"]) for bar in bars)
                await self.quality.refresh(symbol, as_of)
                return {kind: len(values) for kind, values in counts.items()}
            except BaseException as exc:
                if await self.database.get_stock(symbol) is not None:
                    await self.database.mark_stock_data_status(symbol, "error",
                        "补数已取消，可重新同步" if isinstance(exc, asyncio.CancelledError) else str(exc))
                raise
