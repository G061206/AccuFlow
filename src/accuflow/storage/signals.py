import asyncio
import base64
import gzip
import json
from datetime import UTC, datetime

from accuflow.detectors.unified import canonical, evaluate, snapshot_id
from accuflow.scoring.rules import RULE_VERSION, RULE_HASH


class SignalStore:
    def __init__(self, database): self.db=database

    async def context(self, instrument, as_of):
        rows=await (await self.db.read_connection.execute(
            "SELECT payload_json FROM analysis_contexts WHERE instrument_key=? AND known_at<=? ORDER BY known_at DESC,id DESC LIMIT 1",
            (instrument,as_of))).fetchall()
        return json.loads(rows[0][0]) if rows else None

    async def save_context(self, instrument, context):
        async with self.db.transaction() as conn:
            await conn.execute("INSERT INTO analysis_contexts(instrument_key,known_at,payload_json) VALUES(?,?,?)",
                (instrument,context["known_at"],canonical(context)))
        return context

    async def at(self, instrument, as_of):
        row=await (await self.db.read_connection.execute(
            "SELECT result_json FROM signal_evaluations WHERE instrument_key=? AND as_of=? AND rule_version=?",
            (instrument,as_of,RULE_VERSION))).fetchone()
        result=json.loads(row[0]) if row else None
        if result and result.get('rule_hash')!=RULE_HASH:
            raise ValueError("checkpoint uses a different frozen implementation; publish a new rule version")
        return result

    async def history(self, instrument, as_of, date):
        row=await (await self.db.read_connection.execute(
            "SELECT result_json FROM signal_evaluations WHERE instrument_key=? AND as_of<? AND rule_version=? ORDER BY as_of DESC LIMIT 1",
            (instrument,as_of,RULE_VERSION))).fetchone()
        days=await (await self.db.read_connection.execute(
            "SELECT payload_json FROM signal_days WHERE instrument_key=? AND session_date<? AND as_of<? AND rule_version=? ORDER BY session_date DESC LIMIT 4",
            (instrument,date,as_of,RULE_VERSION))).fetchall()
        previous=json.loads(row[0]) if row else None
        if previous and previous.get('rule_hash')!=RULE_HASH:
            raise ValueError("prior state uses a different frozen implementation; publish a new rule version")
        return (previous["state_after"] if previous else None),[json.loads(x[0]) for x in days]

    async def commit(self, conn, prepared, hourly_alert):
        result=prepared["result"]; snapshot=prepared.get("snapshot")
        if result.get('rule_hash')!=RULE_HASH or result.get('rule_version')!=RULE_VERSION:
            raise ValueError("cannot commit an obsolete rule result")
        if snapshot is None and prepared.get("snapshot_gzip"):
            snapshot=json.loads(gzip.decompress(base64.b64decode(prepared["snapshot_gzip"])))
        existing=await (await conn.execute("SELECT result_json FROM signal_evaluations WHERE id=?",(result["snapshot_id"],))).fetchone()
        if existing:
            if json.loads(existing[0])!=result: raise ValueError("immutable evaluation mismatch")
            return
        if snapshot is None or snapshot_id(snapshot)!=result["snapshot_id"]:
            raise ValueError("signal snapshot checksum mismatch")
        instrument=snapshot["instrument_key"]; as_of=snapshot["as_of"]
        latest=await (await conn.execute("SELECT as_of,payload_json FROM signal_states WHERE instrument_key=? AND rule_version=?",
            (instrument,RULE_VERSION))).fetchone()
        current=not latest or latest["as_of"]<as_of
        if current and latest and json.loads(latest["payload_json"])!=snapshot.get("state_before"):
            raise ValueError("signal state changed during capture; retry checkpoint")
        await conn.execute("INSERT INTO signal_evaluations VALUES(?,?,?,?,?,?,?)",
            (result["snapshot_id"],instrument,snapshot["symbol"],as_of,RULE_VERSION,
             gzip.compress(canonical(snapshot).encode()),canonical(result)))
        await conn.execute("""INSERT INTO signal_days VALUES(?,?,?,?,?) ON CONFLICT(instrument_key,session_date,rule_version)
            DO UPDATE SET as_of=excluded.as_of,payload_json=excluded.payload_json WHERE signal_days.as_of<excluded.as_of""",
            (instrument,result["session_date"],RULE_VERSION,as_of,canonical(result["daily_record"])))
        if current:
            await conn.execute("""INSERT INTO signal_states VALUES(?,?,?,?) ON CONFLICT(instrument_key,rule_version)
                DO UPDATE SET as_of=excluded.as_of,payload_json=excluded.payload_json""",
                (instrument,RULE_VERSION,as_of,canonical(result["state_after"])))
        for event in result["events"]:
            await conn.execute("INSERT INTO signal_events VALUES(?,?,?)",(event["event_id"],result["snapshot_id"],canonical(event)))
            if current and hourly_alert and event["notifiable"]:
                labels={'new_anomaly':'新异动','strengthened':'持续迹象增强','enhanced':'新增增强证据',
                        'waning':'迹象减弱','invalidated':'结构失效'}
                title=f"[AccuFlow][{labels.get(event['event_type'],event['event_type'])}] {snapshot['symbol']}"
                body="\n".join([f"数据截至：{event['as_of']}",f"统一规则分：{event['score']}（前次 {event['previous_score']}）",
                    "支持证据："+"；".join(event['supporting_evidence']),
                    "反证与限制："+"；".join(event['counter_evidence']),
                    "事件背景："+(event['known_events'] or '尚未完整核验'),
                    "仅供本地预览，尚未发送邮件。"])
                await conn.execute("INSERT INTO signal_previews VALUES(?,'preview',?,?,?)",
                    (event["event_id"],title,body,datetime.now(UTC).isoformat()))

    async def get(self, identity):
        row=await (await self.db.read_connection.execute("SELECT * FROM signal_evaluations WHERE id=?",(identity,))).fetchone()
        if not row: raise KeyError(identity)
        snapshot=json.loads(gzip.decompress(row["snapshot_gzip"]))
        if snapshot_id(snapshot)!=identity: raise ValueError("signal snapshot checksum mismatch")
        return snapshot,json.loads(row["result_json"])

    async def replay(self, identity):
        snapshot,saved=await self.get(identity)
        result=await asyncio.to_thread(evaluate,snapshot)
        return {"id":identity,"matches":result==saved,"rule_version":RULE_VERSION,"result":result}

    async def list(self, symbol=None, limit=20):
        rows=await (await self.db.read_connection.execute(
            "SELECT result_json FROM signal_evaluations "+("WHERE symbol=? " if symbol else "")+"ORDER BY as_of DESC LIMIT ?",
            (symbol,limit) if symbol else (limit,))).fetchall()
        return [{k:v for k,v in json.loads(row[0]).items() if k not in {"features","state_after"}} for row in rows]
