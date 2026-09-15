import asyncio
import base64
import gzip
from datetime import UTC, datetime

from accuflow.detectors.unified import evaluate, canonical
from accuflow.domain.signals import AnalysisContext
from accuflow.features.orderflow import flow_windows
from accuflow.features.bars import instant
from accuflow.scoring.rules import RULE_VERSION, RULE_HASH
from accuflow.services.calendar import sessions
from accuflow.storage.signals import SignalStore


class SignalService:
    def __init__(self, database):
        self.db=database; self.store=SignalStore(database)

    async def instrument(self, symbol):
        stock=await self.db.get_stock(symbol)
        return stock,f"conid:{stock['con_id']}" if stock and stock['con_id'] else f"symbol:{symbol}"

    async def capture(self, symbol, as_of, collection_error=None):
        as_of=as_of.astimezone(UTC)
        stock,instrument=await self.instrument(symbol)
        existing=await self.store.at(instrument,as_of.isoformat())
        if existing: return {"result":existing}
        captured=datetime.now(UTC)
        labels=sessions(as_of,66,complete=False)
        context=await self.store.context(instrument,captured.isoformat()) or {}
        state,days=await self.store.history(instrument,as_of.isoformat(),labels[-1]["date"])
        async def bars(name,kind):
            if not name: return []
            target=await self.db.get_stock(name)
            if not target or not target["con_id"]: return []
            rows=await self.db.bars_between(name,kind,labels[0]["date"],as_of.isoformat())
            return [x for x in rows if x["con_id"]==target["con_id"] and x["source"]=="IBKR"]
        snapshot={"schema_version":1,"symbol":symbol,"con_id":stock["con_id"] if stock else None,
            "instrument_key":instrument,"rule_version":RULE_VERSION,"rule_hash":RULE_HASH,
            "as_of":as_of.isoformat(),"captured_at":captured.isoformat(),
            "sessions":[{k:v.isoformat() if hasattr(v,"isoformat") else v for k,v in day.items()} for day in labels],
            "context":context,"state_before":state,"prior_days":days,"collection_error":collection_error}
        for prefix,name in (("",symbol),("market_",context.get("market_symbol","SPY")),("sector_",context.get("sector_symbol"))):
            snapshot[prefix+"daily"]=await bars(name,"1 day")
            snapshot[prefix+"minute"]=await bars(name,"1 min")
        rows=await (await self.db.read_connection.execute(
            "SELECT payload_json FROM flow_windows WHERE instrument_key=? AND start>=? AND end<=? AND captured_at<=? ORDER BY start",
            (instrument,labels[0]["date"],as_of.isoformat(),captured.isoformat()))).fetchall()
        import json
        snapshot["flow_windows"]=[json.loads(x[0]) for x in rows]
        def prepare():
            return {"snapshot_gzip":base64.b64encode(gzip.compress(canonical(snapshot).encode())).decode(),"result":evaluate(snapshot)}
        return await asyncio.to_thread(prepare)

    async def record_observations(self, symbol, observations, intervals, start, end):
        """Boundary for the IBKR collector; persist classified windows, never fabricated signs."""
        stock,instrument=await self.instrument(symbol)
        if not stock or not stock["con_id"]: raise ValueError("qualified contract required")
        now=datetime.now(UTC)
        if instant(end)>now: raise ValueError("cannot ingest unfinished windows")
        labels=sessions(instant(end),1,complete=False)
        opened,closed=labels[-1]['open'],labels[-1]['close']
        if instant(start)<opened or instant(end)>closed or (instant(start)-opened).total_seconds()%300 or (instant(end)-opened).total_seconds()%300:
            raise ValueError("flow windows must align with regular-session five-minute boundaries")
        if len(observations)>100000 or len(intervals)>10000 or (instant(end)-instant(start)).total_seconds()>3600:
            raise ValueError("ingestion batch exceeds bounded hourly capacity")
        context=await self.store.context(instrument,now.isoformat()) or {}
        # Freeze each complete slot; exact retries are idempotent, revisions require a new dataset.
        windows=await asyncio.to_thread(flow_windows,observations,intervals,start,end,context.get("tick_size",.01))
        async with self.db.transaction() as conn:
            for row in windows:
                old=await (await conn.execute("SELECT payload_json FROM flow_windows WHERE instrument_key=? AND start=?",(instrument,row["start"]))).fetchone()
                if old and old[0]!=canonical(row): raise ValueError("conflicting finalized flow window")
                await conn.execute("INSERT OR IGNORE INTO flow_windows VALUES(?,?,?,?,?)",
                    (instrument,row["start"],row["end"],now.isoformat(),canonical(row)))
        return len(windows)
