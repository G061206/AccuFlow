from datetime import UTC, datetime, timedelta
import math

from accuflow.services.calendar import sessions


def valid_bar(bar):
    try:
        values = [float(bar[key]) for key in ("open", "high", "low", "close", "volume")]
        o, h, low, close, volume = values
        return (all(math.isfinite(x) for x in values) and min(o, h, low, close) > 0
                and volume >= 0 and low <= min(o, close) <= max(o, close) <= h)
    except (ValueError, TypeError, KeyError):
        return False


class QualityService:
    def __init__(self, database):
        self.database = database

    async def evaluate(self, symbol, as_of=None):
        as_of = as_of or datetime.now(UTC)
        completed = sessions(as_of, 65)
        baseline = completed[-20:]
        recent = sessions(as_of, 21, complete=False)
        daily = await self.database.bars_between(symbol, "1 day", completed[0]["date"], completed[-1]["date"] + "T23:59:59+00:00")
        minute = await self.database.bars_between(symbol, "1 min", recent[0]["open"].isoformat(), as_of.astimezone(UTC).isoformat())
        daily_dates = {x["date"] for x in completed}
        valid_daily = [b for b in daily if valid_bar(b) and b["timestamp"][:10] in daily_dates]
        daily_days = {b["timestamp"][:10] for b in valid_daily}
        actual = set()
        for bar in minute:
            if valid_bar(bar):
                try:
                    time = datetime.fromisoformat(bar["timestamp"])
                    if time.tzinfo and time + timedelta(minutes=1) <= as_of:
                        actual.add(time.astimezone(UTC))
                except ValueError:
                    pass
        coverage = []
        for session in baseline:
            expected = int((session["close"] - session["open"]).total_seconds() // 60)
            found = sum(session["open"] + timedelta(minutes=n) in actual for n in range(expected))
            coverage.append({"date": session["date"], "expected": expected, "received": found, "ratio": found / expected})
        baseline_days = sum(x["ratio"] >= 0.95 for x in coverage)
        latest = recent[-1]
        end = min(as_of.replace(second=0, microsecond=0), latest["close"])
        expected_last = end - timedelta(minutes=1)
        # During the first minute after open, use the preceding completed session.
        if end <= latest["open"]:
            expected_last = completed[-1]["close"] - timedelta(minutes=1)
        last_minute = max(actual) if actual else None
        minute_fresh = last_minute is not None and timedelta(0) <= expected_last - last_minute <= timedelta(minutes=5)
        daily_fresh = completed[-1]["date"] in daily_days
        reasons = []
        if len(daily_days) < 60: reasons.append(f"日线仅 {len(daily_days)}/60 个交易日")
        if baseline_days < 20: reasons.append(f"合格分钟基线仅 {baseline_days}/20 个交易日")
        if not daily_fresh: reasons.append("缺少最近完整交易日日线")
        if not minute_fresh: reasons.append("分钟数据落后或缺失")
        return {"ready": not reasons, "daily_count": len(daily_days), "minute_count": len(actual),
                "baseline_days": baseline_days, "sessions": coverage, "reasons": reasons,
                "daily_fresh": daily_fresh, "minute_fresh": minute_fresh,
                "last_minute": last_minute.isoformat() if last_minute else None,
                "as_of": as_of.isoformat(), "daily": valid_daily, "minute": minute}

    async def refresh(self, symbol, as_of=None):
        quality = await self.evaluate(symbol, as_of)
        detail = ("日线与分钟历史基线合格；实时逐笔能力另行探测" if quality["ready"] else "；".join(quality["reasons"]))
        return await self.database.mark_stock_data_status(symbol, "ready" if quality["ready"] else "incomplete", detail)
