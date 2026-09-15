"""Explicit synthetic-data benchmark for the isolated M3 acceptance directory."""
import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from accuflow.config import Settings
from accuflow.services.reports import ReportService
from accuflow.services.soak import run_soak
from accuflow.storage.database import Database
from m2_fixtures import make_snapshot
from test_m2_integration import seed


async def main(seconds,output):
    path=Path(output).with_suffix('.db')
    if path.exists(): raise ValueError('benchmark database already exists')
    snapshot=make_snapshot(minutes=390)
    db=Database(path);await db.connect()
    try:
        await seed(db,snapshot)
        await ReportService(db).generate('收盘报告',as_of=datetime.fromisoformat(snapshot['as_of']))
    finally: await db.close()
    settings=Settings(_env_file=None,database_path=path,smtp_enabled=False,ibkr_connect_on_startup=False)
    result=await run_soak(settings,seconds,5,output,True)
    import json
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=int,default=120)
    parser.add_argument('--output',default='data/m3-replay-soak.json')
    args=parser.parse_args()
    asyncio.run(main(args.seconds,args.output))
