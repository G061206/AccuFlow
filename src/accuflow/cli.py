from __future__ import annotations

import argparse
import asyncio
import json

import uvicorn

from accuflow.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="AccuFlow service")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="启动 Web API")
    soak=subparsers.add_parser("soak",help="记录资源并生成验收摘要，不开启发信")
    soak.add_argument("--seconds",type=int,default=25200)
    soak.add_argument("--interval",type=int,default=30)
    soak.add_argument("--output",required=True)
    soak.add_argument("--replay-latest",action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "soak":
        from accuflow.services.soak import run_soak
        print(json.dumps(asyncio.run(run_soak(settings,args.seconds,args.interval,args.output,args.replay_latest)),ensure_ascii=False,indent=2))
    if args.command == "serve":
        uvicorn.run(
            "accuflow.api:app",
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
        )


if __name__ == "__main__":
    main()

