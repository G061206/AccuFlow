from __future__ import annotations

import asyncio
from typing import Any

from accuflow.providers.ibkr_async.client import IBKRClient
from accuflow.storage.database import Database


class MarketDataService:
    def __init__(self, database: Database, ibkr: IBKRClient):
        self.database = database
        self.ibkr = ibkr
        self._backfill_lock = asyncio.Lock()

    async def qualify_and_persist(self, symbol: str) -> dict[str, Any]:
        qualified = await self.ibkr.qualify_stock(symbol)
        return await self.database.save_qualified_contract(
            symbol,
            company_name=qualified["company_name"],
            con_id=qualified["con_id"],
            primary_exchange=qualified["primary_exchange"],
            currency=qualified["currency"],
        )

    async def backfill(
        self,
        symbol: str,
        *,
        include_daily: bool,
        include_minute: bool,
    ) -> dict[str, int]:
        async with self._backfill_lock:
            stock = await self.database.get_stock(symbol)
            if stock is None:
                raise KeyError(symbol)
            qualified = await self.ibkr.qualify_stock(symbol)
            await self.database.save_qualified_contract(
                symbol,
                company_name=qualified["company_name"],
                con_id=qualified["con_id"],
                primary_exchange=qualified["primary_exchange"],
                currency=qualified["currency"],
            )

            counts = {"daily": 0, "minute": 0}
            if include_daily:
                daily = await self.ibkr.historical_bars(
                    contract=qualified["contract"],
                    duration="1 Y",
                    bar_size="1 day",
                )
                counts["daily"] = await self.database.upsert_bars(
                    symbol=symbol,
                    con_id=qualified["con_id"],
                    bar_size="1 day",
                    bars=daily,
                    use_rth=True,
                )
            if include_minute:
                minute = await self.ibkr.historical_bars(
                    contract=qualified["contract"],
                    duration="2 D",
                    bar_size="1 min",
                )
                counts["minute"] = await self.database.upsert_bars(
                    symbol=symbol,
                    con_id=qualified["con_id"],
                    bar_size="1 min",
                    bars=minute,
                    use_rth=True,
                )
            await self.database.mark_stock_data_status(
                symbol,
                "ready",
                f"IBKR 补数完成：日线 {counts['daily']}，1分钟 {counts['minute']}",
            )
            return counts
