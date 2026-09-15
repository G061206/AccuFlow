from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from accuflow.domain.models import ReportCreate
from accuflow.services.quality import QualityService
from accuflow.storage.workflow import WorkflowStore

from accuflow.scoring.rules import RULE_VERSION
from accuflow.services.signals import SignalService

LEGACY_VERSION = "unified-v1-readiness"


class ReportService:
    def __init__(self, database):
        self.database = database
        self.store = WorkflowStore(database)
        self.signals = SignalService(database)
        self.generation_lock = asyncio.Lock()

    async def capture(self, symbol, as_of, collection_error=None):
        quality = await QualityService(self.database).evaluate(symbol, as_of)
        metrics = self._daily_metrics(quality.pop("daily"))
        quality.pop("minute")
        if collection_error:
            quality["ready"] = False
            quality["reasons"].append(f"采集失败：{collection_error}")
        capabilities = await self.database.list_capability_reports(symbol=symbol, limit=1)
        capability = capabilities[0] if capabilities else None
        return {"symbol":symbol,"quality":quality,"metrics":metrics,"capability":capability,
                "detection":await self.signals.capture(symbol,as_of,collection_error)}

    @staticmethod
    def build_legacy(inputs, report_type, as_of, report_id):
        if not inputs: raise ValueError("没有启用中的跟踪股票")
        evidence, counter, quality_text, eligible = [], [], [], []
        for item in inputs:
            symbol, quality = item["symbol"], item["quality"]
            evidence.append(f"{symbol}：日线窗口 {quality['daily_count']} 个交易日、1分钟线 "
                f"{quality['minute_count']} 根（{quality['baseline_days']} 个合格交易日）{item['metrics']}")
            quality_text.append(f"{symbol} 日线={quality['daily_count']}，分钟线={quality['minute_count']}，"
                f"分钟基线交易日={quality['baseline_days']}，数据截至={quality['last_minute'] or '无'}")
            reasons = list(quality["reasons"])
            if quality["ready"]: eligible.append(symbol)
            capability = item.get("capability")
            if not capability:
                reasons.append("尚未生成 IBKR 行情能力报告")
            else:
                age = as_of - datetime.fromisoformat(capability["checked_at"])
                if age > timedelta(hours=24) or age < timedelta(0):
                    reasons.append("实时能力报告不在本检查点的有效时间范围内")
                elif capability["market_data_type"] != 1 or any(capability[name] != "available" for name in
                    ("snapshot_status","tick_by_tick_last_status","tick_by_tick_bidask_status")):
                    reasons.append("实时双流质量尚不可用于评分")
            if reasons: counter.append(f"{symbol}：" + "；".join(reasons))
        judgment = (f"{', '.join(eligible)} 已达到基础样本数量门槛；统一特征与评分引擎尚未启用，本报告不输出建仓强度。"
            if eligible else "当前股票尚未满足历史基线与数据新鲜度门槛，本次只保存可复核的数据就绪结论，不输出建仓强度分数。")
        return ReportCreate(id=report_id, report_time=as_of, report_type=report_type,
            symbols=[x["symbol"] for x in inputs], summary=f"{len(inputs)} 只股票完成真实数据质量检查；{len(eligible)} 只达到基础样本数量门槛",
            judgment=judgment,evidence=evidence,counter_evidence=counter,data_quality="；".join(quality_text),rule_version=LEGACY_VERSION)

    @staticmethod
    def build(inputs, report_type, as_of, report_id):
        if not inputs: raise ValueError("没有启用中的跟踪股票")
        if not all("detection" in x for x in inputs):
            return ReportService.build_legacy(inputs,report_type,as_of,report_id)
        evidence=[]; counter=[]; quality=[]; judgments=[]
        names={"core_behavior":"核心行为","cross_day":"跨日持续","price_response":"价格响应",
               "relative_strength":"相对强弱","distribution":"分布与VWAP"}
        for item in inputs:
            r=item["detection"]["result"]; symbol=item["symbol"]
            score=f"{r['score']:.1f}" if r['score'] is not None else "不输出建仓强度分数"
            judgments.append(f"{symbol}：{score} · {r['status_label']}")
            evidence.append(f"{symbol}："+"，".join(
                f"{names[k]} {v['value']:.2f}" if v['available'] else f"{names[k]} 不可用"
                for k in names for v in [r['families'][k]]))
            evidence.append(f"{symbol}：支持 {r['families']['cross_day']['support_days']}/5 个独立交易日；快照 {r['snapshot_id']}")
            counter.extend(f"{symbol}：{reason}" for reason in r['counter_evidence'])
            quality.append(f"{symbol}：{'合格' if r['quality']['valid'] else '不足'}；{item['quality']['baseline_days']} 个历史分钟基线日")
        return ReportCreate(id=report_id,report_time=as_of,report_type=report_type,
            symbols=[x['symbol'] for x in inputs],summary=f"{len(inputs)} 只股票完成统一检测与数据质量检查",
            judgment="；".join(judgments),evidence=evidence,counter_evidence=counter,
            data_quality="；".join(quality),rule_version=RULE_VERSION)

    async def generate(self, report_type, *, as_of=None):
        async with self.generation_lock:
            as_of = as_of or datetime.now(UTC)
            stocks = [s for s in await self.database.list_stocks() if s["active"]]
            if not stocks: raise ValueError("没有启用中的跟踪股票")
            if len(stocks) > 100: raise ValueError("单次报告最多支持 100 只股票")
            inputs = [await self.capture(stock["symbol"], as_of) for stock in stocks]
            report_id = f"m2-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
            report = self.build(inputs, report_type, as_of, report_id)
            return await self.store.save_bundle(report, inputs)

    async def replay(self, report_id):
        saved = await self.database.get_report(report_id)
        snapshot = await self.store.report_input(report_id)
        if not saved or not snapshot: raise KeyError(report_id)
        if saved["rule_version"] not in {RULE_VERSION,LEGACY_VERSION}:
            raise ValueError("不支持此规则版本的回放")
        report = self.build(snapshot["inputs"], saved["report_type"], datetime.fromisoformat(saved["report_time"]), report_id)
        detection_matches = True
        for item in snapshot["inputs"]:
            if "detection" in item:
                replay = await self.signals.store.replay(item["detection"]["result"]["snapshot_id"])
                detection_matches = detection_matches and replay["matches"]
        matches = detection_matches and all(getattr(report, key) == saved[key] for key in
            ("summary","judgment","evidence","counter_evidence","data_quality","symbols"))
        return {"report_id":report_id,"matches":matches,"sha256":snapshot["sha256"],"rule_version":saved["rule_version"],
                "report":report.model_dump(mode="json")}

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
