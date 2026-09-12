from __future__ import annotations

import logging
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware

from accuflow.config import Settings, get_settings
from accuflow.domain.models import (
    BackfillRequest,
    ReportCreate,
    StockActiveUpdate,
    StockCreate,
)
from accuflow.providers.ibkr_async.client import (
    IBKRClient,
    IBKRContractError,
    IBKRNotConnectedError,
)
from accuflow.services.market_data import MarketDataService
from accuflow.storage.database import Database

logger = logging.getLogger(__name__)


def stock_response(stock: dict[str, Any]) -> dict[str, Any]:
    if not stock["active"]:
        display_status = "paused"
    elif stock["data_status"] in {"pending", "error"}:
        display_status = "incomplete"
    else:
        display_status = "tracking"
    return {
        "symbol": stock["symbol"],
        "company": stock["company_name"] or stock["symbol"],
        "status": display_status,
        "active": bool(stock["active"]),
        "conId": stock["con_id"],
        "primaryExchange": stock["primary_exchange"],
        "currency": stock["currency"],
        "coverage": "完整" if stock["data_status"] == "ready" else stock["data_status_detail"],
        "coverageDetail": (
            "实时 · 1分钟 · 1小时 · 日线"
            if stock["data_status"] == "ready"
            else stock["data_status_detail"]
        ),
        "score": stock["score"],
        "signal": stock["signal"],
        "checkedAt": stock["last_checked_at"],
        "reportType": stock["last_report_type"],
        "dataStatus": stock["data_status"],
    }


def report_response(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": report["id"],
        "time": report["report_time"],
        "type": report["report_type"],
        "symbols": report["symbols"],
        "summary": report["summary"],
        "conclusion": report["judgment"],
        "evidence": report["evidence"],
        "counter": report["counter_evidence"],
        "quality": report["data_quality"],
        "ruleVersion": report["rule_version"],
    }


def create_app(
    settings: Settings | None = None,
    *,
    ibkr_factory: Callable[[Settings, Database], IBKRClient] = IBKRClient,
) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(resolved_settings.database_path)
        await database.connect()
        ibkr = ibkr_factory(resolved_settings, database)
        app.state.database = database
        app.state.ibkr = ibkr
        app.state.market_data = MarketDataService(database, ibkr)
        if resolved_settings.ibkr_connect_on_startup:
            try:
                await ibkr.connect()
            except Exception:
                logger.exception("IBKR startup connection failed; API remains available")
        try:
            yield
        finally:
            await ibkr.disconnect()
            await database.close()

    app = FastAPI(
        title="AccuFlow API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:4173", "http://127.0.0.1:4173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    def database(request: Request) -> Database:
        return request.app.state.database

    def ibkr(request: Request) -> IBKRClient:
        return request.app.state.ibkr

    def market_data(request: Request) -> MarketDataService:
        return request.app.state.market_data

    @app.get("/api/health")
    async def health(request: Request):
        db = database(request)
        gateway = ibkr(request)
        return {
            "service": "ok",
            "database": "ok" if await db.ping() else "error",
            "ibkr": gateway.status(),
        }

    @app.get("/api/stocks")
    async def list_stocks(request: Request):
        return [stock_response(item) for item in await database(request).list_stocks()]

    @app.post("/api/stocks", status_code=status.HTTP_201_CREATED)
    async def create_stock(payload: StockCreate, request: Request):
        db = database(request)
        try:
            created = await db.create_stock(payload.symbol)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail=f"{payload.symbol} 已在跟踪列表中") from exc
        gateway = ibkr(request)
        if gateway.status()["connected"]:
            try:
                created = await market_data(request).qualify_and_persist(payload.symbol)
            except (IBKRContractError, TimeoutError) as exc:
                created = await db.mark_stock_data_status(payload.symbol, "error", str(exc))
        return stock_response(created)

    @app.patch("/api/stocks/{symbol}")
    async def update_stock(symbol: str, payload: StockActiveUpdate, request: Request):
        updated = await database(request).set_stock_active(symbol.upper(), payload.active)
        if updated is None:
            raise HTTPException(status_code=404, detail="股票不存在")
        return stock_response(updated)

    @app.delete("/api/stocks/{symbol}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_stock(symbol: str, request: Request):
        deleted = await database(request).delete_stock(symbol.upper())
        if not deleted:
            raise HTTPException(status_code=404, detail="股票不存在")

    @app.post("/api/stocks/{symbol}/qualify")
    async def qualify_stock(symbol: str, request: Request):
        try:
            stock = await market_data(request).qualify_and_persist(symbol.upper())
            return stock_response(stock)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="股票不存在") from exc
        except IBKRNotConnectedError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (IBKRContractError, TimeoutError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/stocks/{symbol}/backfill")
    async def backfill_stock(
        symbol: str, payload: BackfillRequest, request: Request
    ):
        try:
            counts = await market_data(request).backfill(
                symbol.upper(),
                include_daily=payload.include_daily,
                include_minute=payload.include_minute,
            )
            return {"symbol": symbol.upper(), "stored": counts}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="股票不存在") from exc
        except IBKRNotConnectedError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (IBKRContractError, TimeoutError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/stocks/{symbol}/bars")
    async def list_bars(
        symbol: str,
        request: Request,
        bar_size: str = Query(default="1 day", pattern=r"^(1 day|1 min)$"),
        limit: int = Query(default=500, ge=1, le=5000),
    ):
        return await database(request).list_bars(symbol.upper(), bar_size, limit)

    @app.get("/api/reports")
    async def list_reports(
        request: Request,
        query: str = Query(default="", max_length=100),
        report_type: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ):
        rows = await database(request).list_reports(
            query=query,
            report_type=report_type,
            limit=limit,
            offset=offset,
        )
        return [report_response(row) for row in rows]

    @app.get("/api/reports/{report_id}")
    async def get_report(report_id: str, request: Request):
        report = await database(request).get_report(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        return report_response(report)

    @app.post("/api/reports", status_code=status.HTTP_201_CREATED)
    async def save_report(payload: ReportCreate, request: Request):
        report = await database(request).save_report(payload)
        return report_response(report)

    @app.post("/api/ibkr/connect")
    async def connect_ibkr(request: Request):
        try:
            return await ibkr(request).connect()
        except (ConnectionError, OSError, TimeoutError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"无法连接 IBKR Gateway/TWS：{exc}",
            ) from exc

    @app.delete("/api/ibkr/connect")
    async def disconnect_ibkr(request: Request):
        return await ibkr(request).disconnect()

    return app


app = create_app()

