import hashlib
import json
from datetime import UTC, datetime, timedelta

from accuflow.domain.models import Preferences
from accuflow.storage.signals import SignalStore
from accuflow.storage.delivery import DeliveryStore


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class WorkflowStore:
    def __init__(self, database):
        self.db = database

    async def preferences(self):
        saved = await self.db.get_system_state("preferences")
        return Preferences.model_validate_json(saved) if saved else Preferences()

    async def claim(self, key, checkpoint, kind, owner, symbols, *, now=None):
        now = now or datetime.now(UTC)
        async with self.db.transaction() as conn:
            row = await (await conn.execute("SELECT * FROM job_runs WHERE job_key=?", (key,))).fetchone()
            if row and (row["status"] == "completed" or (row["status"] == "running" and row["lease_until"] > now.isoformat())):
                return None
            if row:
                symbols = json.loads(row["symbols_json"])
            await conn.execute("""INSERT INTO job_runs
                (job_key,checkpoint,report_type,status,owner,lease_until,attempts,symbols_json,updated_at)
                VALUES(?,?,?,'running',?,?,1,?,?) ON CONFLICT(job_key) DO UPDATE SET
                status='running',owner=excluded.owner,lease_until=excluded.lease_until,
                attempts=job_runs.attempts+1,error=NULL,updated_at=excluded.updated_at""",
                (key, checkpoint.isoformat(), kind, owner, (now+timedelta(seconds=120)).isoformat(), encode(symbols), now.isoformat()))
        return symbols

    async def renew(self, key, owner):
        now = datetime.now(UTC)
        async with self.db.transaction() as conn:
            cursor = await conn.execute("""UPDATE job_runs SET lease_until=?,updated_at=?
                WHERE job_key=? AND owner=? AND status='running'""",
                ((now+timedelta(seconds=120)).isoformat(),now.isoformat(),key,owner))
        return cursor.rowcount > 0

    async def fail(self, key, owner, error):
        async with self.db.transaction() as conn:
            await conn.execute("""UPDATE job_runs SET status='failed',error=?,updated_at=?
                WHERE job_key=? AND owner=? AND status='running'""",
                (str(error)[:2000],datetime.now(UTC).isoformat(),key,owner))
            job=await (await conn.execute("SELECT checkpoint FROM job_runs WHERE job_key=? AND owner=? AND status='failed'",(key,owner))).fetchone()
            if job: await DeliveryStore(self.db).failure(conn,key,job['checkpoint'],str(error))

    async def input(self, key, symbol):
        row = await (await self.db.read_connection.execute(
            "SELECT payload_json FROM checkpoint_inputs WHERE job_key=? AND symbol=?",(key,symbol))).fetchone()
        return json.loads(row["payload_json"]) if row else None

    async def save_input(self, key, owner, symbol, payload):
        async with self.db.transaction() as conn:
            owned = await (await conn.execute("SELECT 1 FROM job_runs WHERE job_key=? AND owner=? AND status='running'", (key,owner))).fetchone()
            if not owned: raise RuntimeError("checkpoint lease lost")
            await conn.execute("INSERT OR IGNORE INTO checkpoint_inputs VALUES(?,?,?)",(key,symbol,encode(payload)))

    async def save_bundle(self, report, inputs, *, key=None, owner=None):
        raw = encode([({**item,"detection":{"result":item["detection"]["result"]}} if "detection" in item else item) for item in inputs])
        now = datetime.now(UTC).isoformat()
        async with self.db.transaction() as conn:
            if key:
                owned = await (await conn.execute("SELECT 1 FROM job_runs WHERE job_key=? AND owner=? AND status='running'",(key,owner))).fetchone()
                if not owned: raise RuntimeError("checkpoint lease lost")
            await self.db.write_report(report)
            await conn.execute("INSERT INTO report_inputs VALUES(?,?,?,?)",
                (report.id,raw,hashlib.sha256(raw.encode()).hexdigest(),now))
            row = await (await conn.execute("SELECT value FROM system_state WHERE key='preferences'")).fetchone()
            prefs = Preferences.model_validate_json(row["value"]) if row else Preferences()
            for item in inputs:
                if "detection" in item:
                    await SignalStore(self.db).commit(conn,item["detection"],prefs.hourly_alert)
            if prefs.daily_report and report.report_type == "收盘报告":
                body = "\n\n".join([report.summary,report.judgment,"\n".join(report.evidence),
                    "\n".join(report.counter_evidence),report.data_quality,
                    "仅供本地预览，尚未发送邮件。"])
                await conn.execute("INSERT INTO notification_outbox VALUES(?,?,'preview',?,?,?)",
                    (f"preview:{report.id}",report.id,f"[AccuFlow][检测日报] {report.report_time.date()}",body,now))
            await DeliveryStore(self.db).enqueue_report(conn,report,inputs,prefs)
            for item in inputs:
                # An older recovered checkpoint must not replace the latest visible check.
                await conn.execute("""UPDATE tracked_stocks SET last_checked_at=?,last_report_type=?,
                    score=?,signal=? WHERE symbol=? AND (last_checked_at IS NULL OR last_checked_at<=?)""",
                    (report.report_time.isoformat(),report.report_type,
                    item.get("detection",{}).get("result",{}).get("score"),
                    item.get("detection",{}).get("result",{}).get("status_label", "数据不足，暂不评分"),
                    item["symbol"],report.report_time.isoformat()))
            if key:
                await conn.execute("UPDATE job_runs SET status='completed',report_id=?,updated_at=? WHERE job_key=? AND owner=?",
                    (report.id,now,key,owner))
        return await self.db.get_report(report.id)

    async def report_input(self, report_id):
        row = await (await self.db.read_connection.execute("SELECT * FROM report_inputs WHERE report_id=?",(report_id,))).fetchone()
        if not row: return None
        result = dict(row)
        if hashlib.sha256(result["payload_json"].encode()).hexdigest() != result["sha256"]:
            raise ValueError("report input checksum mismatch")
        result["inputs"] = json.loads(result.pop("payload_json"))
        return result

    async def jobs(self, limit=30):
        rows = await (await self.db.read_connection.execute("SELECT * FROM job_runs ORDER BY checkpoint DESC LIMIT ?",(limit,))).fetchall()
        return [{k:v for k,v in dict(row).items() if k not in {"owner","symbols_json"}} for row in rows]

    async def outbox(self, limit=50):
        rows = await (await self.db.read_connection.execute("SELECT * FROM notification_outbox ORDER BY created_at DESC LIMIT ?",(limit,))).fetchall()
        signals=await (await self.db.read_connection.execute("SELECT * FROM signal_previews ORDER BY created_at DESC LIMIT ?",(limit,))).fetchall()
        return sorted([dict(row) for row in rows]+[dict(row) for row in signals],key=lambda x:x['created_at'],reverse=True)[:limit]
