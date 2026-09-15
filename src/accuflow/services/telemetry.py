"""Bounded resource sampling for on-host acceptance; never reads process secrets."""
import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
import psutil


class TelemetryService:
    def __init__(self,database,settings):
        self.db=database;self.settings=settings;self.process=psutil.Process()
        self.latest=None;self.last_error=None

    def system_sample(self):
        gateway_rss=0;gateway_count=0
        for process in psutil.process_iter(['name','memory_info']):
            try:
                if process.info['name'] and any(x in process.info['name'].lower() for x in ('java','ibgateway','tws')):
                    # Java is only counted when its launch command identifies IBKR.
                    if any(x in ' '.join(process.cmdline()).lower() for x in ('ibgateway','jts','tws')):
                        gateway_count+=1;gateway_rss+=process.info['memory_info'].rss
            except (psutil.NoSuchProcess,psutil.AccessDenied): continue
        memory=psutil.virtual_memory()
        return {'pid':os.getpid(),'cpu_count':psutil.cpu_count(),'process_cpu_percent':self.process.cpu_percent(),
            'process_rss_bytes':self.process.memory_info().rss,'host_memory_total':memory.total,
            'host_memory_available':memory.available,'host_cpu_percent':psutil.cpu_percent(),
            'gateway_processes':gateway_count,'gateway_rss_bytes':gateway_rss,
            'database_bytes':sum(p.stat().st_size for p in (self.db.path,self.db.path.with_name(self.db.path.name+'-wal'),self.db.path.with_name(self.db.path.name+'-shm')) if p.exists()),
            'disk_free_bytes':psutil.disk_usage(str(self.db.path.parent)).free}

    async def sample(self,lag=0.0):
        result=await asyncio.to_thread(self.system_sample)
        now=datetime.now(UTC)
        counts=await (await self.db.read_connection.execute('SELECT status,count(*) AS n FROM delivery_outbox GROUP BY status')).fetchall()
        jobs=await (await self.db.read_connection.execute("SELECT status,count(*) AS n FROM job_runs WHERE status!='completed' GROUP BY status")).fetchall()
        result.update(at=now.isoformat(),event_loop_lag_seconds=max(0,lag),delivery_queue={x['status']:x['n'] for x in counts},job_queue={x['status']:x['n'] for x in jobs},
            workflow=getattr(self.db,'workflow_metrics',{}))
        async with self.db.transaction() as conn:
            await conn.execute('INSERT OR REPLACE INTO runtime_samples VALUES(?,?)',(result['at'],json.dumps(result)))
            await conn.execute('DELETE FROM runtime_samples WHERE at<?',((now-timedelta(days=self.settings.metrics_retention_days)).isoformat(),))
        self.latest=result;self.last_error=None
        return result

    async def run(self):
        deadline=time.monotonic()
        while True:
            try: await self.sample(time.monotonic()-deadline)
            except asyncio.CancelledError: raise
            except Exception as exc: self.last_error=type(exc).__name__
            deadline=time.monotonic()+self.settings.metrics_interval_seconds
            await asyncio.sleep(self.settings.metrics_interval_seconds)


def summarize(samples):
    if not samples: return {'status':'insufficient','reasons':['no samples']}
    first,last=samples[0],samples[-1]
    elapsed=(datetime.fromisoformat(last['at'])-datetime.fromisoformat(first['at'])).total_seconds()
    reasons=[]
    if elapsed<6.5*3600: reasons.append('less than one 6.5-hour observation period')
    if not all(s['gateway_processes'] for s in samples): reasons.append('Gateway/TWS not observed throughout')
    if first['cpu_count']!=2 or not 1.5*1024**3<=first['host_memory_total']<=2.5*1024**3: reasons.append('not the target 2C2G resource class')
    if max(s['event_loop_lag_seconds'] for s in samples)>5: reasons.append('event loop lag exceeded 5 seconds')
    if min(s['host_memory_available'] for s in samples)<128*1024**2: reasons.append('available host memory below 128 MiB')
    rss_growth=last['process_rss_bytes']-first['process_rss_bytes']
    if elapsed>=3600 and rss_growth>128*1024**2: reasons.append('process RSS growth exceeded 128 MiB')
    if last['job_queue'].get('running',0)>first['job_queue'].get('running',0): reasons.append('unfinished job backlog grew')
    return {'status':'needs_review' if reasons else 'resource_checks_passed','reasons':reasons,
        'duration_seconds':elapsed,'samples':len(samples),'peak_process_rss_bytes':max(s['process_rss_bytes'] for s in samples),
        'peak_gateway_rss_bytes':max(s['gateway_rss_bytes'] for s in samples),'rss_growth_bytes':rss_growth,
        'database_growth_bytes':last['database_bytes']-first['database_bytes'],
        'peak_process_cpu_percent':max(s['process_cpu_percent'] for s in samples),
        'final_delivery_queue':last['delivery_queue'],'final_job_queue':last['job_queue'],
        'max_event_loop_lag_seconds':max(s['event_loop_lag_seconds'] for s in samples),
        'note':'Resource checks alone do not verify real market coverage or SMTP delivery.'}
