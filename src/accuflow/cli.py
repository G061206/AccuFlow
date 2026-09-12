from __future__ import annotations

import argparse

import uvicorn

from accuflow.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="AccuFlow service")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="启动 Web API")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "serve":
        uvicorn.run(
            "accuflow.api:app",
            host=settings.api_host,
            port=settings.api_port,
            log_level=settings.log_level.lower(),
        )


if __name__ == "__main__":
    main()

