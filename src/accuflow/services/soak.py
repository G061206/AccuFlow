"""Bounded acceptance runner; never enables SMTP or changes monitoring."""
import asyncio
import json
import time
from contextlib import suppress
from pathlib import Path
from accuflow.services.telemetry import TelemetryService, summarize
from accuflow.storage.database import Database
from accuflow.storage.signals import SignalStore


async def run_soak(settings,seconds,interval,output,replay_latest=False):
    if not 10<=seconds<=86400 or not 5<=interval<=300: raise ValueError('duration 10..86400 seconds; interval 5..300 seconds')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    if output.exists(): raise ValueError('acceptance output already exists')
    db=Database(settings.database_path);await db.connect()
    sampler=TelemetryService(db,settings);samples=[];replays=0;errors=[]
    deadline=time.monotonic()+seconds
    loop_stats={'lag':0.0}
    async def pulse():
        while True:
            expected=time.monotonic()+0.1
            await asyncio.sleep(0.1)
            loop_stats['lag']=max(loop_stats['lag'],time.monotonic()-expected)
    heartbeat=asyncio.create_task(pulse())
    try:
        with output.with_suffix('.jsonl').open('x',encoding='utf-8') as stream:
            while True:
                started=time.monotonic()
                if replay_latest:
                    rows=await SignalStore(db).list(limit=1)
                    if rows:
                        try:
                            result=await SignalStore(db).replay(rows[0]['snapshot_id'])
                            if not result['matches']: errors.append('replay mismatch')
                            replays+=1
                        except Exception as exc:
                            if len(errors)<100: errors.append(type(exc).__name__)
                sample=await sampler.sample(loop_stats["lag"])
                loop_stats["lag"]=0.0
                sample['replay_iteration_seconds']=time.monotonic()-started
                samples.append(sample);stream.write(json.dumps(sample)+'\n');stream.flush()
                remaining=deadline-time.monotonic()
                if remaining<=0: break
                await asyncio.sleep(min(interval,remaining))
        result=summarize(samples)
        result.update(mode='replay_load' if replay_latest else 'resource_observation',replays=replays,
                      replay_errors=errors,max_iteration_seconds=max(s['replay_iteration_seconds'] for s in samples))
        if errors: result['status']='failed'
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result
    finally:
        heartbeat.cancel()
        with suppress(asyncio.CancelledError): await heartbeat
        await db.close()
