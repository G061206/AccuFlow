from __future__ import annotations

from datetime import UTC, datetime
from statistics import median
from typing import Any

from accuflow.domain.models import ReportCreate
from accuflow.storage.database import Database


class ReportService:
    def __init__(self, database: Database):
        self.database = database

    async def generate(self, report_type: str) -> dict[str, Any]:
        stocks = [
            stock
            for stock in await self.database.list_stocks()
            if stock["active"]
        ]
        if not stocks:
            raise ValueError("没有启用中的跟踪股票")

        evidence: list[str] = []
        counter_evidence: list[str] = []
        quality: list[str] = []
        eligible_symbols: list[str] = []

        for stock in stocks:
            symbol = stock["symbol"]
            daily = await self.database.list_bars(symbol, "1 day", 65)
            minute = await self.database.list_bars(symbol, "1 min", 5000)
            session_days = {
                str(bar["timestamp"])[:10]
                for bar in minute
                if bar.get("timestamp")
            }
            daily_count = len(daily)
            minute_count = len(minute)
            intraday_days = len(session_days)

            metrics = self._daily_metrics(daily)
            evidence.append(
                f"{symbol}：日线窗口 {daily_count} 根、1分钟线 "
                f"{minute_count} 根（{intraday_days} 个交易日）"
                f"{metrics}"
            )
            quality.append(
                f"{symbol} 日线={daily_count}，分钟线={minute_count}，"
                f"分钟基线交易日={intraday_days}"
            )

            if daily_count >= 60 and intraday_days >= 20:
                eligible_symbols.append(symbol)
            else:
                missing: list[str] = []
                if daily_count < 60:
                    missing.append(f"日线仅 {daily_count}/60")
                if intraday_days < 20:
                    missing.append(f"分钟基线仅 {intraday_days}/20 个交易日")
                counter_evidence.append(
                    f"{symbol}：{'，'.join(missing)}，暂不进入正式评分"
                )

            capabilities = await self.database.list_capability_reports(
                symbol=symbol,
                limit=1,
            )
            if capabilities:
                capability = capabilities[0]
                unavailable = [
                    name
                    for name, state in (
                        ("实时快照", capability["snapshot_status"]),
                        (
                            "逐笔成交",
                            capability["tick_by_tick_last_status"],
                        ),
                        (
                            "逐笔BidAsk",
                            capability["tick_by_tick_bidask_status"],
                        ),
                    )
                    if state != "available"
                ]
                if unavailable:
                    counter_evidence.append(
                        f"{symbol}：{', '.join(unavailable)}尚不可用于评分"
                    )
                quality.append(
                    f"{symbol} 请求行情类型="
                    f"{capability['details'].get('market_data_type_label', 'unknown')}，"
                    f"历史bars={capability['historical_bars_status']}，"
                    f"历史ticks={capability['historical_ticks_status']}"
                )
            else:
                counter_evidence.append(
                    f"{symbol}：尚未生成 IBKR 行情能力报告"
                )

        symbols = [stock["symbol"] for stock in stocks]
        if eligible_symbols:
            judgment = (
                f"{', '.join(eligible_symbols)} 已达到基础样本数量门槛；"
                "统一特征与评分引擎尚未启用，本报告不输出建仓强度。"
            )
        else:
            judgment = (
                "当前股票尚未同时满足分钟基线和实时逐笔质量门槛，"
                "本次只保存可复核的数据就绪结论，不输出建仓强度分数。"
            )

        now = datetime.now(UTC)
        report = ReportCreate(
            id=(
                f"readiness-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
            ),
            report_time=now,
            report_type=report_type,
            symbols=symbols,
            summary=(
                f"{len(symbols)} 只股票完成真实数据质量检查；"
                f"{len(eligible_symbols)} 只达到基础样本数量门槛"
            ),
            judgment=judgment,
            evidence=evidence,
            counter_evidence=counter_evidence,
            data_quality="；".join(quality),
            rule_version="unified-v1-readiness",
        )
        return await self.database.save_report(report)

    @staticmethod
    def _daily_metrics(daily: list[dict[str, Any]]) -> str:
        if not daily:
            return "，无可用日线指标"
        parts = [f"，最近收盘 {float(daily[0]['close']):.2f}"]
        if len(daily) >= 6 and float(daily[5]["close"]) != 0:
            change = float(daily[0]["close"]) / float(daily[5]["close"]) - 1
            parts.append(f"，近5日变化 {change:+.2%}")
        prior_volumes = [
            float(bar["volume"])
            for bar in daily[1:21]
            if float(bar["volume"]) > 0
        ]
        if prior_volumes:
            baseline = median(prior_volumes)
            if baseline > 0:
                ratio = float(daily[0]["volume"]) / baseline
                parts.append(f"，当日量/前20日中位数 {ratio:.2f}x")
        return "".join(parts)
