import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from accuflow.notifications.templates import render, report_content, transport_hash


class DeliveryStore:
    def __init__(self, database): self.db=database

    async def enqueue(self,conn,key,subject,sections,as_of,*,events=(),expires_at=None,preview_only=False):
        existing=await (await conn.execute('SELECT id FROM delivery_outbox WHERE source_key=?',(key,))).fetchone()
        if existing: return existing[0]
        if events:
            rows=await (await conn.execute('SELECT event_id FROM delivery_events WHERE event_id IN ('+','.join('?' for _ in events)+')',tuple(e['event_id'] for e in events))).fetchall()
            if len(rows)==len(events): return None
        identity=hashlib.sha256(key.encode()).hexdigest()
        settings=getattr(self.db,'delivery_settings',None)
        enabled=bool(settings and settings.smtp_enabled and not preview_only)
        sender=settings.smtp_sender if enabled else ''
        recipients=settings.recipient_list if enabled else []
        content=render(identity,subject,sections,as_of,sender,recipients)
        now=datetime.now(UTC).isoformat()
        status='pending' if enabled else 'preview'
        await conn.execute("""INSERT INTO delivery_outbox(id,source_key,created_at,status,subject,body,html,message_id,
            mime,mime_sha256,envelope_json,transport_hash,next_attempt_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (identity,key,now,status,content['subject'],content['body'],content['html'],content['message_id'],content['mime'],
             hashlib.sha256(content['mime']).hexdigest(),json.dumps({'sender':sender,'recipients':recipients}),
             transport_hash(settings) if enabled else None,now,expires_at))
        for event in events:
            await conn.execute('INSERT OR IGNORE INTO delivery_events VALUES(?,?)',(event['event_id'],identity))
        await conn.execute('INSERT INTO delivery_audit(delivery_id,at,status,detail) VALUES(?,?,?,?)',(identity,now,status,'created'))
        return identity

    async def enqueue_report(self,conn,report,inputs,prefs):
        events=[e for item in inputs for e in item.get('detection',{}).get('result',{}).get('events',[]) if e['event_type'] not in {'data_quality','episode_closed'}]
        closing=report.report_type=='收盘报告'
        if closing and not prefs.daily_report: return
        if not closing:
            events=[e for e in events if e['notifiable']]
            if not prefs.hourly_alert or not events: return
            # One bundle per checkpoint, and event IDs are globally deduplicated.
            known={row[0] for row in await (await conn.execute('SELECT event_id FROM delivery_events WHERE event_id IN ('+','.join('?' for _ in events)+')',tuple(e['event_id'] for e in events))).fetchall()}
            events=[e for e in events if e['event_id'] not in known]
            if not events: return
        subject,sections=report_content(report,inputs,events)
        results=[x.get('detection',{}).get('result') for x in inputs]
        draft=closing and not all(r and r.get('closed') for r in results)
        session_date=next((r['session_date'] for r in results if r),report.report_time.date().isoformat())
        key=(('draft:'+report.id if draft else 'daily:'+session_date+':'+report.rule_version) if closing else 'events:'+':'.join(sorted(e['event_id'] for e in events)))
        if draft: subject='[盘中预览] '+subject
        await self.enqueue(conn,key,subject,sections,report.report_time.isoformat(),events=events if not closing else (),
            expires_at=(report.report_time+timedelta(minutes=15)).isoformat() if not closing else None,preview_only=draft)

    async def failure(self,conn,key,checkpoint,error):
        await self.enqueue(conn,'failure:'+key,'[AccuFlow][运行失败] '+checkpoint,
            [('运行失败',['检查点未完成，无法据此判断有无异动。',error[:2000]])],checkpoint)

    async def claim(self, settings, now=None):
        now=now or datetime.now(UTC); stamp=now.isoformat();owner=uuid4().hex
        async with self.db.transaction() as conn:
            expired=await (await conn.execute("SELECT id,status FROM delivery_outbox WHERE status IN ('preparing','sending') AND lease_until<=?",(stamp,))).fetchall()
            for row in expired:
                status='uncertain' if row['status']=='sending' else 'retry'
                await conn.execute('UPDATE delivery_outbox SET status=?,owner=NULL,last_error=? WHERE id=?',(status,'worker lease expired',row['id']))
                await conn.execute('INSERT INTO delivery_audit(delivery_id,at,status,detail) VALUES(?,?,?,?)',(row['id'],stamp,status,'lease expired'))
            await conn.execute("UPDATE delivery_outbox SET status='cancelled',last_error='stale intraday alert' WHERE status IN ('pending','retry') AND expires_at<=?",(stamp,))
            await conn.execute("UPDATE delivery_outbox SET status='failed',last_error='retry budget exhausted' WHERE status IN ('pending','retry') AND attempts>=?",(settings.delivery_max_attempts,))
            row=await (await conn.execute("SELECT * FROM delivery_outbox WHERE status IN ('pending','retry') AND next_attempt_at<=? ORDER BY created_at,id LIMIT 1",(stamp,))).fetchone()
            if not row: return None
            lease=now+timedelta(seconds=settings.smtp_timeout*20+60)
            await conn.execute("UPDATE delivery_outbox SET status='preparing',owner=?,lease_until=?,attempts=attempts+1 WHERE id=?",(owner,lease.isoformat(),row['id']))
            result=dict(row);result.update(owner=owner,attempts=row['attempts']+1)
            return result

    async def mark_sending(self,item):
        async with self.db.transaction() as conn:
            cursor=await conn.execute("UPDATE delivery_outbox SET status='sending' WHERE id=? AND owner=? AND status='preparing' AND lease_until>?",
                (item['id'],item['owner'],datetime.now(UTC).isoformat()))
            if cursor.rowcount!=1: raise RuntimeError('delivery lease lost before DATA')

    async def finish(self,item,status,detail=''):
        now=datetime.now(UTC)
        async with self.db.transaction() as conn:
            cursor=await conn.execute("""UPDATE delivery_outbox SET status=?,last_error=?,sent_at=?,next_attempt_at=?,owner=NULL,lease_until=NULL
                WHERE id=? AND owner=? AND status IN ('preparing','sending')""",
                (status,detail or None,now.isoformat() if status=='sent' else None,
                 (now+timedelta(seconds=min(3600,30*2**(item['attempts']-1)))).isoformat(),item['id'],item['owner']))
            if cursor.rowcount:
                await conn.execute('INSERT INTO delivery_audit(delivery_id,at,status,detail) VALUES(?,?,?,?)',(item['id'],now.isoformat(),status,detail))

    async def resolve(self,identity,resolution):
        target={'confirmed_sent':'sent','cancelled':'cancelled'}[resolution]
        async with self.db.transaction() as conn:
            cursor=await conn.execute("UPDATE delivery_outbox SET status=?,last_error='operator resolved uncertainty',sent_at=? WHERE id=? AND status='uncertain'",
                (target,datetime.now(UTC).isoformat() if target=='sent' else None,identity))
            if cursor.rowcount!=1: raise ValueError('only uncertain delivery can be resolved')
            await conn.execute('INSERT INTO delivery_audit(delivery_id,at,status,detail) VALUES(?,?,?,?)',(identity,datetime.now(UTC).isoformat(),target,'operator resolution'))
        return {'id':identity,'status':target}

    async def list(self,limit=50):
        rows=await (await self.db.read_connection.execute('SELECT id,created_at,status,subject,body,message_id,attempts,last_error,sent_at FROM delivery_outbox ORDER BY created_at DESC LIMIT ?',(limit,))).fetchall()
        return [dict(row) for row in rows]
