"""Single-process checkpoint runner; leases fence concurrent/restarted workers.

Delivery is preview-only. This module never sends email; signal events remain local previews.
"""
import asyncio
from contextlib import suppress
from datetime import UTC, datetime, timedelta
import logging
import time
from uuid import uuid4

from accuflow.services.calendar import checkpoints
from accuflow.services.reports import RULE_VERSION
from accuflow.storage.workflow import WorkflowStore

logger = logging.getLogger(__name__)


class WorkflowService:
    def __init__(self, database, market_data, reports, settings):
        self.db, self.market_data, self.reports, self.settings = database, market_data, reports, settings
        self.store = WorkflowStore(database)
        self._lock = asyncio.Lock()
        self.last_error = None
        self.running_key = None
        self.last_tick = None

    def status(self):
        return {"running_key":self.running_key,"last_error":self.last_error,"last_tick":self.last_tick,
                "delivery_mode":"smtp" if self.settings.smtp_enabled else "preview","scoring_enabled":True,"rule_version":RULE_VERSION}

    async def _heartbeat(self, key, owner, parent):
        try:
            while True:
                await asyncio.sleep(30)
                if not await self.store.renew(key, owner):
                    raise RuntimeError("checkpoint lease lost")
        except asyncio.CancelledError:
            raise
        except Exception:
            parent.cancel()
            raise

    async def run_checkpoint(self, checkpoint, report_type):
        if checkpoint.tzinfo is None: raise ValueError("检查点必须包含时区")
        if report_type not in {"小时报告","收盘报告"}: raise ValueError("未知报告类型")
        async with self._lock, self.reports.generation_lock:
            key = f"{RULE_VERSION}:{checkpoint.astimezone(UTC).isoformat()}:{report_type}"
            owner = uuid4().hex
            stocks = [s["symbol"] for s in await self.db.list_stocks() if s["active"]]
            existing = await (await self.db.read_connection.execute("SELECT 1 FROM job_runs WHERE job_key=?", (key,))).fetchone()
            if not stocks and not existing: raise ValueError("没有启用中的跟踪股票")
            if len(stocks) > 100: raise ValueError("自动检查最多支持 100 只股票")
            symbols = await self.store.claim(key, checkpoint, report_type, owner, stocks)
            if symbols is None:
                return {"job_key":key,"status":"already_claimed_or_completed"}
            self.running_key = key
            started=time.monotonic()
            heartbeat = asyncio.create_task(self._heartbeat(key, owner, asyncio.current_task()))
            try:
                async with asyncio.timeout(self.settings.workflow_job_timeout):
                    inputs = []
                    for symbol in symbols:
                        saved = await self.store.input(key, symbol)
                        if saved:
                            inputs.append(saved)
                            continue
                        error = None
                        try:
                            stock = await self.db.get_stock(symbol)
                            if not stock or not stock["active"]: raise ValueError("股票已暂停或移除")
                            async with asyncio.timeout(self.settings.workflow_stock_timeout):
                                await self.market_data.backfill(symbol,include_daily=True,include_minute=True,as_of=checkpoint)
                        except Exception as exc:
                            error = str(exc) or type(exc).__name__
                        captured = await self.reports.capture(symbol, checkpoint, error)
                        await self.store.save_input(key, owner, symbol, captured)
                        inputs.append(captured)
                    report_id = f"checkpoint-{checkpoint.strftime('%Y%m%dT%H%M%SZ')}-{'close' if report_type == '收盘报告' else 'hour'}-m2"
                    report = self.reports.build(inputs,report_type,checkpoint,report_id)
                    stored = await self.store.save_bundle(report,inputs,key=key,owner=owner)
                    self.last_error = None
                    return {"job_key":key,"status":"completed","report_id":stored["id"]}
            except BaseException as exc:
                self.last_error = str(exc) or type(exc).__name__
                await self.store.fail(key,owner,self.last_error)
                raise
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError, Exception): await heartbeat
                self.db.workflow_metrics={"duration_seconds":time.monotonic()-started,"checkpoint":checkpoint.isoformat(),"failed":self.last_error is not None}
                self.running_key = None

    async def tick(self, as_of=None):
        now = as_of or datetime.now(UTC)
        self.last_tick = now.isoformat()
        preferences = await self.store.preferences()
        if not preferences.monitoring_enabled: return
        due = checkpoints(now)
        # Recover the most recent close plus the latest hour only; expired intraday
        # alerts are not replayed into the live notification stream.
        closing = [x for x in due if x[1] == "收盘报告"]
        hourly = [x for x in due if x[1] == "小时报告" and now-x[0] <= timedelta(hours=1)]
        targets = closing[-1:] + hourly[-1:]
        jobs = await self.store.jobs(100)
        by_key = {job["job_key"]: job for job in jobs}
        # Resume interrupted jobs using their frozen watchlist and completed inputs.
        targets += [(datetime.fromisoformat(job["checkpoint"]),job["report_type"]) for job in jobs
                    if job["job_key"].startswith(RULE_VERSION+":") and job["status"] != "completed" and job["attempts"] < 3]
        pending = []
        for checkpoint, kind in sorted(set(targets)):
            key = f"{RULE_VERSION}:{checkpoint.astimezone(UTC).isoformat()}:{kind}"
            job = by_key.get(key)
            if job:
                if job["status"] == "completed" or job["attempts"] >= 3: continue
                if job["status"] == "running" and job["lease_until"] > now.isoformat(): continue
                if now - datetime.fromisoformat(job["updated_at"]) < timedelta(seconds=60): continue
            pending.append((checkpoint,kind))
        if not pending: return
        if not jobs and not any(s["active"] for s in await self.db.list_stocks()): return
        if not self.market_data.ibkr.status()["connected"]:
            try:
                await self.market_data.ibkr.connect()
            except Exception as exc:
                # Still produce an explicit failed-data report at the checkpoint.
                self.last_error = str(exc)
        for checkpoint, kind in pending:
            await self.run_checkpoint(checkpoint,kind)

    async def run(self):
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc) or type(exc).__name__
                logger.exception("Checkpoint processing failed")
            await asyncio.sleep(self.settings.workflow_poll_seconds)
