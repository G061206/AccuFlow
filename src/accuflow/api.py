from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from contextlib import suppress
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Callable, Literal
from pydantic import BaseModel

from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware

from accuflow.domain.signals import AnalysisContext
from accuflow.storage.signals import SignalStore
from accuflow.config import Settings, get_settings
from accuflow.domain.models import (
    BackfillRequest,
    Preferences,
    PreferencesUpdate,
    ReportGenerateRequest,
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
from accuflow.services.reports import ReportService
from accuflow.services.workflow import WorkflowService
from accuflow.services.delivery import DeliveryService
from accuflow.services.telemetry import TelemetryService, summarize
import json
from accuflow.storage.delivery import DeliveryStore
from accuflow.storage.workflow import WorkflowStore
from accuflow.storage.database import Database

logger = logging.getLogger(__name__)
INITIAL_WATCHLIST_STATE_KEY = "initial_watchlist_seeded"


async def seed_initial_watchlist(
    database: Database, symbols: list[str]
) -> None:
    if await database.get_system_state(INITIAL_WATCHLIST_STATE_KEY) is not None:
        return
    for symbol in symbols:
        if await database.get_stock(symbol) is None:
            await database.create_stock(symbol)
    await database.set_system_state(
        INITIAL_WATCHLIST_STATE_KEY, ",".join(symbols)
    )


def stock_response(stock: dict[str, Any]) -> dict[str, Any]:
    if not stock["active"]:
        display_status = "paused"
    elif stock["data_status"] != "ready":
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
        "coverage": "历史基线合格" if stock["data_status"] == "ready" else "数据未就绪",
        "coverageDetail": stock["data_status_detail"],
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


class DeliveryResolution(BaseModel):
    resolution: Literal["confirmed_sent","cancelled"]


def create_app(
    settings: Settings | None = None,
    *,
    ibkr_factory: Callable[[Settings, Database], IBKRClient] = IBKRClient,
) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(resolved_settings.database_path)
        database.delivery_settings = resolved_settings
        await database.connect()
        await seed_initial_watchlist(
            database, resolved_settings.initial_symbol_list
        )
        ibkr = ibkr_factory(resolved_settings, database)
        app.state.database = database
        app.state.ibkr = ibkr
        app.state.market_data = MarketDataService(database, ibkr)
        app.state.report_service = ReportService(database)
        workflow = WorkflowService(database, app.state.market_data, app.state.report_service, resolved_settings)
        app.state.workflow = workflow
        if resolved_settings.ibkr_connect_on_startup:
            try:
                await ibkr.connect()
            except Exception:
                logger.exception("IBKR startup connection failed; API remains available")
        delivery = DeliveryService(database,resolved_settings)
        app.state.delivery = delivery
        delivery_worker = asyncio.create_task(delivery.run())
        telemetry=TelemetryService(database,resolved_settings)
        app.state.telemetry=telemetry
        telemetry_worker=asyncio.create_task(telemetry.run())
        worker = asyncio.create_task(workflow.run())
        try:
            yield
        finally:
            telemetry_worker.cancel()
            with suppress(asyncio.CancelledError):
                await telemetry_worker
            delivery_worker.cancel()
            with suppress(asyncio.CancelledError):
                await delivery_worker
            worker.cancel()
            with suppress(asyncio.CancelledError):
                await worker
            await ibkr.disconnect()
            if hasattr(ibkr, "flush_errors"):
                await ibkr.flush_errors()
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
    def reports_service(request: Request) -> ReportService:
        return request.app.state.report_service


    @app.get("/api/health")
    async def health(request: Request):
        db = database(request)
        gateway = ibkr(request)
        return {
            "service": "ok",
            "database": "ok" if await db.ping() else "error",
            "ibkr": gateway.status(),
            "workflow": request.app.state.workflow.status(),
            "telemetry": {"latest":request.app.state.telemetry.latest,"last_error":request.app.state.telemetry.last_error},
            "delivery": {"mode":"smtp" if resolved_settings.smtp_enabled else "preview", "active_id":request.app.state.delivery.active_id,"last_error":request.app.state.delivery.last_error},
        }

    @app.get("/api/settings")
    async def get_preferences(request: Request):
        saved = await database(request).get_system_state("preferences")
        preferences = Preferences.model_validate_json(saved) if saved else Preferences()
        return {**preferences.model_dump(), "delivery_mode": "smtp" if resolved_settings.smtp_enabled else "preview",
                "hourly_alert_available": True}

    @app.patch("/api/settings")
    async def update_preferences(payload: PreferencesUpdate, request: Request):
        db = database(request)
        # Serialize the read-modify-write together to preserve independent updates.
        async with db.transaction() as conn:
            row = await (await conn.execute("SELECT value FROM system_state WHERE key='preferences'")).fetchone()
            current = Preferences.model_validate_json(row["value"]) if row else Preferences()
            updated = current.model_copy(update=payload.model_dump(exclude_none=True))
            await conn.execute("""INSERT INTO system_state(key,value,updated_at)
                VALUES('preferences',?,datetime('now')) ON CONFLICT(key) DO UPDATE
                SET value=excluded.value,updated_at=excluded.updated_at""", (updated.model_dump_json(),))
        return {**updated.model_dump(), "delivery_mode": "smtp" if resolved_settings.smtp_enabled else "preview", "hourly_alert_available": True}

    @app.get("/api/signals")
    async def list_signals(request: Request, symbol: str | None = None, limit: int = Query(default=20,ge=1,le=100)):
        return await SignalStore(database(request)).list(symbol.upper() if symbol else None,limit)

    @app.get("/api/signals/{identity}/replay")
    async def replay_signal(identity: str, request: Request):
        try:
            return await SignalStore(database(request)).replay(identity)
        except KeyError as exc: raise HTTPException(404,"检测快照不存在") from exc
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc

    @app.get("/api/stocks/{symbol}/analysis-context")
    async def analysis_context(symbol: str, request: Request):
        service=reports_service(request).signals
        stock,instrument=await service.instrument(symbol.upper())
        if not stock: raise HTTPException(404,"股票不存在")
        return await service.store.context(instrument,datetime.now(UTC).isoformat()) or {}

    @app.post("/api/stocks/{symbol}/analysis-context")
    async def update_analysis_context(symbol: str, payload: AnalysisContext, request: Request):
        service=reports_service(request).signals
        stock,instrument=await service.instrument(symbol.upper())
        if not stock or not stock['con_id']: raise HTTPException(409,"请先解析 IBKR 合约")
        # Context availability is server-stamped; clients cannot retroactively authorize signals.
        context=payload.model_dump(mode="json")
        context['known_at']=datetime.now(UTC).isoformat()
        if payload.event_review_through and payload.event_review_through>datetime.now(UTC):
            raise HTTPException(422,"事件核验不能覆盖未来时间")
        return await service.store.save_context(instrument,context)

    @app.get("/api/stocks")
    async def list_stocks(request: Request):
        db = database(request)
        rows = await db.list_stocks()
        result = []
        for item in rows:
            if item["data_status"] == "ready":
                item = await market_data(request).quality.refresh(item["symbol"])
            result.append(stock_response(item))
        return result

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
            except (IBKRContractError, IBKRNotConnectedError, TimeoutError) as exc:
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

    @app.get("/api/ibkr/capabilities")
    async def list_capability_reports(
        request: Request,
        symbol: str | None = Query(default=None, max_length=12),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        normalized = symbol.strip().upper() if symbol else None
        return await database(request).list_capability_reports(
            symbol=normalized,
            limit=limit,
        )

    @app.post("/api/stocks/{symbol}/probe")
    async def probe_stock_capabilities(symbol: str, request: Request):
        try:
            return await market_data(request).probe_and_persist(
                symbol.upper()
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="股票不存在") from exc
        except IBKRNotConnectedError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except (IBKRContractError, TimeoutError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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

    @app.post(
        "/api/reports/generate",
        status_code=status.HTTP_201_CREATED,
    )
    async def generate_report(
        payload: ReportGenerateRequest,
        request: Request,
    ):
        try:
            stored = await reports_service(request).generate(
                payload.report_type
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return report_response(stored)

    @app.get("/api/workflow/jobs")
    async def workflow_jobs(request: Request, limit: int = Query(default=30, ge=1, le=100)):
        return await WorkflowStore(database(request)).jobs(limit)

    @app.get("/api/notifications/outbox")
    async def notification_previews(request: Request, limit: int = Query(default=50, ge=1, le=100)):
        return await WorkflowStore(database(request)).outbox(limit)

    @app.get("/api/metrics")
    async def metrics(request: Request, limit: int = Query(default=1000,ge=1,le=5000)):
        rows=await (await database(request).read_connection.execute('SELECT payload_json FROM runtime_samples ORDER BY at DESC LIMIT ?',(limit,))).fetchall()
        samples=[json.loads(row[0]) for row in reversed(rows)]
        return {'summary':summarize(samples),'latest':samples[-1] if samples else None}

    @app.get("/api/notifications/deliveries")
    async def deliveries(request: Request, limit: int = Query(default=50,ge=1,le=100)):
        return await DeliveryStore(database(request)).list(limit)

    @app.get("/api/notifications/deliveries/{identity}/content")
    async def delivery_content(identity: str, request: Request):
        row=await (await database(request).read_connection.execute('SELECT subject,body,html,message_id FROM delivery_outbox WHERE id=?',(identity,))).fetchone()
        if not row: raise HTTPException(404,"通知不存在")
        return dict(row)

    @app.post("/api/notifications/deliveries/{identity}/resolve")
    async def resolve_delivery(identity: str, payload: DeliveryResolution, request: Request):
        try: return await DeliveryStore(database(request)).resolve(identity,payload.resolution)
        except ValueError as exc: raise HTTPException(409,str(exc)) from exc

    @app.get("/api/reports/{report_id}/replay")
    async def replay_report(report_id: str, request: Request):
        try:
            return await reports_service(request).replay(report_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="报告没有可回放的输入快照") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/reports/{report_id}")
    async def get_report(report_id: str, request: Request):
        report = await database(request).get_report(report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="报告不存在")
        return report_response(report)

    @app.post("/api/reports", status_code=status.HTTP_201_CREATED)
    async def save_report(payload: ReportCreate, request: Request):
        if await WorkflowStore(database(request)).report_input(payload.id):
            raise HTTPException(status_code=409, detail="带快照的报告不可覆盖，请创建新版本")
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
