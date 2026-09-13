from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Iterable

import aiosqlite

from accuflow.domain.models import ReportCreate


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._connection: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("database is not connected")
        return self._connection

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.execute("PRAGMA journal_mode = WAL")
        await self._connection.execute("PRAGMA synchronous = NORMAL")
        await self._connection.execute("PRAGMA foreign_keys = ON")
        schema_path = Path(__file__).with_name("schema.sql")
        await self._connection.executescript(schema_path.read_text(encoding="utf-8"))
        await self._connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(1, ?)",
            (utc_now(),),
        )
        await self._connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES(2, ?)",
            (utc_now(),),
        )
        await self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def ping(self) -> bool:
        row = await (await self.connection.execute("SELECT 1 AS ok")).fetchone()
        return bool(row and row["ok"] == 1)

    async def list_stocks(self) -> list[dict[str, Any]]:
        rows = await (
            await self.connection.execute(
                "SELECT * FROM tracked_stocks ORDER BY created_at, symbol"
            )
        ).fetchall()
        return [dict(row) for row in rows]

    async def get_stock(self, symbol: str) -> dict[str, Any] | None:
        row = await (
            await self.connection.execute(
                "SELECT * FROM tracked_stocks WHERE symbol = ?", (symbol,)
            )
        ).fetchone()
        return dict(row) if row else None

    async def create_stock(self, symbol: str) -> dict[str, Any]:
        now = utc_now()
        async with self._write_lock:
            await self.connection.execute(
                """
                INSERT INTO tracked_stocks(symbol, created_at, updated_at)
                VALUES(?, ?, ?)
                """,
                (symbol, now, now),
            )
            await self.connection.commit()
        stock = await self.get_stock(symbol)
        assert stock is not None
        return stock

    async def set_stock_active(self, symbol: str, active: bool) -> dict[str, Any] | None:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "UPDATE tracked_stocks SET active = ?, updated_at = ? WHERE symbol = ?",
                (int(active), utc_now(), symbol),
            )
            await self.connection.commit()
        return await self.get_stock(symbol) if cursor.rowcount else None

    async def delete_stock(self, symbol: str) -> bool:
        async with self._write_lock:
            cursor = await self.connection.execute(
                "DELETE FROM tracked_stocks WHERE symbol = ?", (symbol,)
            )
            await self.connection.commit()
        return cursor.rowcount > 0

    async def save_qualified_contract(
        self,
        symbol: str,
        *,
        company_name: str,
        con_id: int,
        primary_exchange: str,
        currency: str,
    ) -> dict[str, Any]:
        async with self._write_lock:
            await self.connection.execute(
                """
                UPDATE tracked_stocks
                SET company_name = ?, con_id = ?, primary_exchange = ?, currency = ?,
                    data_status = 'qualified', data_status_detail = 'IBKR 合约已解析',
                    updated_at = ?
                WHERE symbol = ?
                """,
                (
                    company_name,
                    con_id,
                    primary_exchange,
                    currency,
                    utc_now(),
                    symbol,
                ),
            )
            await self.connection.commit()
        stock = await self.get_stock(symbol)
        if stock is None:
            raise KeyError(symbol)
        return stock

    async def mark_stock_data_status(
        self, symbol: str, status: str, detail: str
    ) -> dict[str, Any]:
        async with self._write_lock:
            await self.connection.execute(
                """
                UPDATE tracked_stocks
                SET data_status = ?, data_status_detail = ?, updated_at = ?
                WHERE symbol = ?
                """,
                (status, detail, utc_now(), symbol),
            )
            await self.connection.commit()
        stock = await self.get_stock(symbol)
        if stock is None:
            raise KeyError(symbol)
        return stock

    async def upsert_bars(
        self,
        *,
        symbol: str,
        con_id: int,
        bar_size: str,
        bars: Iterable[dict[str, Any]],
        use_rth: bool,
    ) -> int:
        received_at = utc_now()
        rows = [
            (
                symbol,
                con_id,
                bar_size,
                self._serialize_timestamp(bar["timestamp"]),
                float(bar["open"]),
                float(bar["high"]),
                float(bar["low"]),
                float(bar["close"]),
                float(bar["volume"]),
                self._optional_float(bar.get("average")),
                self._optional_int(bar.get("bar_count")),
                int(use_rth),
                "IBKR",
                received_at,
            )
            for bar in bars
        ]
        if not rows:
            return 0
        async with self._write_lock:
            await self.connection.executemany(
                """
                INSERT INTO market_bars(
                    symbol, con_id, bar_size, timestamp, open, high, low, close,
                    volume, average, bar_count, use_rth, source, received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(con_id, bar_size, timestamp) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    volume = excluded.volume,
                    average = excluded.average,
                    bar_count = excluded.bar_count,
                    received_at = excluded.received_at
                """,
                rows,
            )
            await self.connection.commit()
        return len(rows)

    async def list_bars(
        self, symbol: str, bar_size: str, limit: int = 500
    ) -> list[dict[str, Any]]:
        rows = await (
            await self.connection.execute(
                """
                SELECT * FROM market_bars
                WHERE symbol = ? AND bar_size = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (symbol, bar_size, limit),
            )
        ).fetchall()
        return [dict(row) for row in rows]

    async def save_capability_report(
        self, report: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._write_lock:
            await self.connection.execute(
                """
                INSERT INTO capability_reports(
                    id, symbol, con_id, checked_at, snapshot_status,
                    market_data_type, historical_bars_status,
                    historical_bars_count, historical_ticks_status,
                    historical_ticks_count, tick_by_tick_last_status,
                    tick_by_tick_bidask_status, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report["id"],
                    report["symbol"],
                    report["con_id"],
                    report["checked_at"],
                    report["snapshot_status"],
                    report["market_data_type"],
                    report["historical_bars_status"],
                    report["historical_bars_count"],
                    report["historical_ticks_status"],
                    report["historical_ticks_count"],
                    report["tick_by_tick_last_status"],
                    report["tick_by_tick_bidask_status"],
                    json.dumps(report["details"], ensure_ascii=False),
                ),
            )
            await self.connection.commit()
        stored = await self.get_capability_report(report["id"])
        assert stored is not None
        return stored

    async def list_capability_reports(
        self,
        *,
        symbol: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if symbol:
            rows = await (
                await self.connection.execute(
                    """
                    SELECT * FROM capability_reports
                    WHERE symbol = ?
                    ORDER BY checked_at DESC
                    LIMIT ?
                    """,
                    (symbol, limit),
                )
            ).fetchall()
        else:
            rows = await (
                await self.connection.execute(
                    """
                    SELECT * FROM capability_reports
                    ORDER BY checked_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            ).fetchall()
        return [self._deserialize_capability_report(dict(row)) for row in rows]

    async def get_capability_report(
        self, report_id: str
    ) -> dict[str, Any] | None:
        row = await (
            await self.connection.execute(
                "SELECT * FROM capability_reports WHERE id = ?",
                (report_id,),
            )
        ).fetchone()
        return self._deserialize_capability_report(dict(row)) if row else None

    async def save_report(self, report: ReportCreate) -> dict[str, Any]:
        now = utc_now()
        async with self._write_lock:
            await self.connection.execute("BEGIN")
            try:
                await self.connection.execute(
                    """
                    INSERT INTO reports(
                        id, report_time, report_type, summary, judgment,
                        evidence_json, counter_evidence_json, data_quality,
                        rule_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        report_time = excluded.report_time,
                        report_type = excluded.report_type,
                        summary = excluded.summary,
                        judgment = excluded.judgment,
                        evidence_json = excluded.evidence_json,
                        counter_evidence_json = excluded.counter_evidence_json,
                        data_quality = excluded.data_quality,
                        rule_version = excluded.rule_version
                    """,
                    (
                        report.id,
                        report.report_time.isoformat(),
                        report.report_type,
                        report.summary,
                        report.judgment,
                        json.dumps(report.evidence, ensure_ascii=False),
                        json.dumps(report.counter_evidence, ensure_ascii=False),
                        report.data_quality,
                        report.rule_version,
                        now,
                    ),
                )
                await self.connection.execute(
                    "DELETE FROM report_symbols WHERE report_id = ?", (report.id,)
                )
                await self.connection.executemany(
                    """
                    INSERT INTO report_symbols(report_id, symbol, position)
                    VALUES(?, ?, ?)
                    """,
                    [
                        (report.id, symbol, position)
                        for position, symbol in enumerate(report.symbols)
                    ],
                )
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise
        stored = await self.get_report(report.id)
        assert stored is not None
        return stored

    async def list_reports(
        self,
        *,
        query: str = "",
        report_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if report_type:
            conditions.append("r.report_type = ?")
            params.append(report_type)
        if query:
            conditions.append(
                """
                (
                    lower(r.summary) LIKE lower(?)
                    OR lower(r.judgment) LIKE lower(?)
                    OR EXISTS (
                        SELECT 1 FROM report_symbols rsq
                        WHERE rsq.report_id = r.id
                          AND lower(rsq.symbol) LIKE lower(?)
                    )
                )
                """
            )
            pattern = f"%{query}%"
            params.extend([pattern, pattern, pattern])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        rows = await (
            await self.connection.execute(
                f"""
                SELECT r.*,
                       COALESCE((
                           SELECT group_concat(ordered.symbol, ',')
                           FROM (
                               SELECT symbol
                               FROM report_symbols
                               WHERE report_id = r.id
                               ORDER BY position
                           ) AS ordered
                       ), '') AS symbols_csv
                FROM reports r
                {where}
                ORDER BY r.report_time DESC
                LIMIT ? OFFSET ?
                """,
                params,
            )
        ).fetchall()
        return [self._deserialize_report(dict(row)) for row in rows]

    async def get_report(self, report_id: str) -> dict[str, Any] | None:
        row = await (
            await self.connection.execute(
                """
                SELECT r.*,
                       COALESCE((
                           SELECT group_concat(ordered.symbol, ',')
                           FROM (
                               SELECT symbol
                               FROM report_symbols
                               WHERE report_id = r.id
                               ORDER BY position
                           ) AS ordered
                       ), '') AS symbols_csv
                FROM reports r
                WHERE r.id = ?
                """,
                (report_id,),
            )
        ).fetchone()
        return self._deserialize_report(dict(row)) if row else None

    async def record_ibkr_error(
        self,
        *,
        request_id: int,
        error_code: int,
        message: str,
        symbol: str | None,
    ) -> None:
        async with self._write_lock:
            await self.connection.execute(
                """
                INSERT INTO ibkr_errors(
                    occurred_at, request_id, error_code, message, symbol
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (utc_now(), request_id, error_code, message, symbol),
            )
            await self.connection.commit()

    async def get_system_state(self, key: str) -> str | None:
        row = await (
            await self.connection.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            )
        ).fetchone()
        return str(row["value"]) if row else None

    async def set_system_state(self, key: str, value: str) -> None:
        async with self._write_lock:
            await self.connection.execute(
                """
                INSERT INTO system_state(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, utc_now()),
            )
            await self.connection.commit()

    @staticmethod
    def _serialize_timestamp(value: datetime | date | str) -> str:
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        return None if value is None else float(value)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        return None if value is None else int(value)

    @staticmethod
    def _deserialize_capability_report(
        row: dict[str, Any]
    ) -> dict[str, Any]:
        row["details"] = json.loads(row.pop("details_json"))
        return row

    @staticmethod
    def _deserialize_report(row: dict[str, Any]) -> dict[str, Any]:
        row["symbols"] = [value for value in row.pop("symbols_csv").split(",") if value]
        row["evidence"] = json.loads(row.pop("evidence_json"))
        row["counter_evidence"] = json.loads(row.pop("counter_evidence_json"))
        return row
