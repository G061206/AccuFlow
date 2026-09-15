import asyncio
import hashlib
import json
import logging
import smtplib

from accuflow.notifications.smtp import SMTPChannel, Rejected
from accuflow.notifications.templates import transport_hash
from accuflow.storage.delivery import DeliveryStore

logger=logging.getLogger(__name__)


class DeliveryService:
    def __init__(self,database,settings,channel_factory=SMTPChannel):
        self.db=database;self.settings=settings;self.store=DeliveryStore(database)
        self.channel_factory=channel_factory;self.last_error=None;self.active_id=None

    async def tick(self):
        if not self.settings.smtp_enabled: return
        item=await self.store.claim(self.settings)
        if not item: return
        self.active_id=item['id'];channel=None;data_started=False
        try:
            if hashlib.sha256(item['mime']).hexdigest()!=item['mime_sha256']:
                await self.store.finish(item,'failed','MIME checksum mismatch');return
            if item['transport_hash']!=transport_hash(self.settings):
                await self.store.finish(item,'failed','SMTP configuration changed; review frozen envelope');return
            channel=self.channel_factory(self.settings)
            task=asyncio.create_task(asyncio.to_thread(channel.prepare,json.loads(item['envelope_json'])))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Preparation can still be in a thread, but it must never send DATA.
                def cleanup(done):
                    if not done.cancelled(): done.exception()
                    channel.close()
                task.add_done_callback(cleanup)
                raise
            await self.store.mark_sending(item)
            data_started=True
            await asyncio.to_thread(channel.deliver,item['mime'])
            await self.store.finish(item,'sent')
            self.last_error=None
        except asyncio.CancelledError:
            await self.store.finish(item,'uncertain' if data_started else 'retry','worker interrupted')
            raise
        except (Rejected,smtplib.SMTPResponseException) as exc:
            code=exc.code if isinstance(exc,Rejected) else exc.smtp_code
            status='retry' if 400<=code<500 and item['attempts']<self.settings.delivery_max_attempts else 'failed'
            self.last_error=f'SMTP rejected request ({code})'
            await self.store.finish(item,status,self.last_error)
        except Exception as exc:
            status='uncertain' if data_started else ('retry' if item['attempts']<self.settings.delivery_max_attempts else 'failed')
            # Exception bodies may contain provider echoes or credentials. Persist type only.
            self.last_error=type(exc).__name__
            await self.store.finish(item,status,self.last_error)
        finally:
            if channel: channel.close()
            self.active_id=None

    async def run(self):
        while True:
            try: await self.tick()
            except asyncio.CancelledError: raise
            except Exception as exc:
                self.last_error=type(exc).__name__
                logger.error('delivery loop failed: %s',self.last_error)
            await asyncio.sleep(self.settings.delivery_poll_seconds)
