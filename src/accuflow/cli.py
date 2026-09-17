from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path

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
    backtest = subparsers.add_parser("backtest", help="隔离的历史收盘信号回测，不触发邮件")
    backtest.add_argument("--model", choices=("v1", "v2"), default="v1")
    backtest.add_argument("--threshold", type=int, choices=(50,55,60,65,70,75,80), help="v2 固定实验阈值，默认 65，未经校准")
    backtest.add_argument("--symbol", default="GOOG")
    backtest.add_argument("--start", type=date.fromisoformat)
    backtest.add_argument("--end", type=date.fromisoformat)
    backtest.add_argument("--cache", type=Path, default=Path("data/backtests/goog/history.db"))
    backtest.add_argument("--output", type=Path, required=True)
    backtest.add_argument("--review", type=Path, help="成交量、复权及历史事件核验 JSON")
    backtest.add_argument("--download", action="store_true", help="通过已登录的 IBKR 下载至专用缓存")
    backtest.add_argument("--host")
    backtest.add_argument("--port", type=int)
    backtest.add_argument("--client-id", type=int, default=71)
    replay = subparsers.add_parser("replay-v2", help="从原始行情重建 v2 特征并回放隔离研究")
    replay.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "replay-v2":
        from accuflow.services.backtest_v2 import replay_v2
        print(json.dumps(replay_v2(args.input), ensure_ascii=False, indent=2))
        return
    if args.command == "backtest":
        if args.model == "v1" and args.threshold is not None:
            parser.error("--threshold only applies to --model v2")
        from accuflow.services.backtest import run_backtest
        from accuflow.services.calendar import sessions
        latest = date.fromisoformat(sessions(datetime.now(UTC), 1)[-1]["date"])
        end = args.end or latest
        try:
            default_start = end.replace(year=end.year - 1)
        except ValueError:
            default_start = end.replace(year=end.year - 1, day=28)
        options = {"smtp_enabled": False, "ibkr_connect_on_startup": False, "ibkr_client_id": args.client_id}
        if args.host: options["ibkr_host"] = args.host
        if args.port: options["ibkr_port"] = args.port
        settings = settings.model_copy(update=options)
        review = json.loads(args.review.read_text(encoding="utf-8")) if args.review else {}
        runner = run_backtest
        extra = {}
        if args.model == "v2":
            from accuflow.services.backtest_v2 import run_backtest_v2
            runner = run_backtest_v2
            extra["threshold"] = args.threshold if args.threshold is not None else 65
        result = asyncio.run(runner(settings, symbol=args.symbol.upper(), start=args.start or default_start,
            end=end, cache=args.cache, output=args.output, review=review, download=args.download, **extra))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result["status"] != "completed":
            raise SystemExit(2)
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

