"""Isolated end-of-day event study using the frozen detector, never live delivery."""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, date, datetime, timedelta
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
from statistics import mean, median

from accuflow.detectors.unified import canonical, evaluate
from accuflow.features.bars import coverage, instant, minute_map
from accuflow.scoring.rules import RULE_HASH, RULE_VERSION
from accuflow.services.calendar import calendar_for, sessions
from accuflow.services.quality import valid_bar


def make_plan(start: date, end: date, now: datetime):
    if start > end:
        raise ValueError("start must not follow end")
    if (end - start).days > 366:
        raise ValueError("a run is limited to one calendar year")
    latest = sessions(now, 1)[-1]
    if end.isoformat() > latest["date"]:
        raise ValueError("end must be a completed trading date or earlier")
    cal = calendar_for(end.year)
    labels = cal.sessions_in_range(start, end)
    if len(labels) == 0:
        raise ValueError("range contains no trading sessions")
    first_open = cal.session_open(labels[0]).to_pydatetime()
    warm = sessions(first_open - timedelta(seconds=1), 65)
    targets = [{"date": x.date().isoformat(), "open": cal.session_open(x).to_pydatetime(),
                "close": cal.session_close(x).to_pydatetime()} for x in labels]
    return warm + targets, targets


def load_bars(path: Path, symbols):
    """Read an existing cache without migrations or changes to production state."""
    result = {s: {"daily": [], "minute": []} for s in symbols}
    if not path.exists():
        return result
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        for symbol in symbols:
            rows = conn.execute("SELECT * FROM market_bars WHERE symbol=? ORDER BY timestamp", (symbol,))
            ids = set()
            for raw in rows:
                row = dict(raw)
                if row["source"] != "IBKR" or not row["use_rth"]:
                    raise ValueError("backtest requires IBKR regular-session TRADES data")
                ids.add(row["con_id"])
                key = {"1 day": "daily", "1 min": "minute"}.get(row["bar_size"])
                if key:
                    result[symbol][key].append(row)
            if len(ids) > 1:
                raise ValueError("ambiguous contract identity in history cache")
    return result


def review_context(review, session, captured):
    verified = bool(review.get("verification_notes", "").strip())
    context = {"known_at": captured.isoformat(), "effective_session": session["date"],
               "units_verified": verified and review.get("units_verified") is True,
               "adjustment_verified": verified and review.get("adjustment_verified") is True,
               "market_symbol": "SPY", "sector_symbol": None,
               "event_notes": "Historical event background has not been reviewed."}
    event = review.get("events", {}).get(session["date"])
    if event and instant(event["known_at"]) <= session["close"]:
        context.update(event_review_through=event["review_through"],
                       major_event=event.get("major_event") is True,
                       event_notes=event.get("notes", ""))
    return context


def build_snapshot(data, symbol, labels, index, captured, review, state=None, prior_days=()):
    window = labels[max(0, index - 65):index + 1]
    session = labels[index]
    start, end = window[0]["open"], session["close"]
    own = data[symbol]
    con_id = next((r["con_id"] for r in own["daily"] + own["minute"] if r.get("con_id")), None)
    snapshot = {"schema_version": 1, "symbol": symbol, "con_id": con_id,
                "instrument_key": f"conid:{con_id}" if con_id else f"symbol:{symbol}",
                "rule_version": RULE_VERSION, "rule_hash": RULE_HASH,
                "as_of": end.isoformat(), "captured_at": captured.isoformat(),
                "sessions": [{k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in s.items()} for s in window],
                "context": review_context(review, session, captured), "state_before": state,
                "prior_days": list(prior_days)[-4:], "flow_windows": [], "sector_daily": [], "sector_minute": []}
    for prefix, name in (("", symbol), ("market_", "SPY")):
        source = data[name]
        snapshot[prefix + "daily"] = [r for r in source["daily"] if window[0]["date"] <= r["timestamp"][:10] < session["date"]]
        snapshot[prefix + "minute"] = [r for r in source["minute"] if start <= instant(r["timestamp"]) < end]
    return snapshot


def forward_performance(signal_date, own_daily, market_daily, latest_date, horizons=(1, 5, 10)):
    """Signal is known after close; measure from the NEXT session's open."""
    cal = calendar_for(date.fromisoformat(signal_date).year)
    next_days = cal.sessions_in_range(date.fromisoformat(signal_date) + timedelta(days=1),
                                      date.fromisoformat(signal_date) + timedelta(days=40))
    dates = [x.date().isoformat() for x in next_days]
    own = {r["timestamp"][:10]: r for r in own_daily if valid_bar(r)}
    market = {r["timestamp"][:10]: r for r in market_daily if valid_bar(r)}
    outcomes = {}
    for horizon in horizons:
        required = dates[:horizon]
        reason = "right_censored" if required[-1] > latest_date else None
        if reason is None and any(d not in own for d in required):
            reason = "missing_stock_bars"
        if reason:
            outcomes[str(horizon)] = {"available": False, "reason": reason}
            continue
        entry = float(own[required[0]]["open"])
        ret = (float(own[required[-1]]["close"]) / entry - 1) * 100
        reference = None
        if all(d in market for d in required):
            reference = (float(market[required[-1]]["close"]) / float(market[required[0]]["open"]) - 1) * 100
        outcomes[str(horizon)] = {"available": True, "entry_date": required[0], "exit_date": required[-1],
                                 "entry_price": entry, "return_pct": ret, "spy_return_pct": reference,
                                 "excess_return_pp": ret - reference if reference is not None else None,
                                 "max_favorable_pct": (max(float(own[d]["high"]) for d in required) / entry - 1) * 100,
                                 "max_adverse_pct": (min(float(own[d]["low"]) for d in required) / entry - 1) * 100}
    return outcomes


def summarize_performance(rows):
    result = {}
    for horizon in ("1", "5", "10"):
        outcomes = [r["forward"][horizon] for r in rows if r["forward"][horizon]["available"]]
        values = [r["return_pct"] for r in outcomes]
        excess = [r["excess_return_pp"] for r in outcomes if r["excess_return_pp"] is not None]
        result[horizon] = {"samples": len(values), "excluded": len(rows) - len(values),
                           "mean_return_pct": mean(values) if values else None,
                           "median_return_pct": median(values) if values else None,
                           "positive_return_fraction": mean(v > 0 for v in values) if values else None,
                           "excess_samples": len(excess), "mean_excess_return_pp": mean(excess) if excess else None}
    return result


def run_study(data, symbol, labels, targets, captured, review, output: Path, progress=None):
    """output must already be a reserved empty directory; preserve every input snapshot."""
    target_dates = {s["date"] for s in targets}
    latest_date = sessions(captured, 1)[-1]["date"]
    state = None
    prior_days = []
    rows = []
    seen_episodes = set()
    # Five pre-period evaluations initialize the five-day persistence state.
    with gzip.open(output / "snapshots.jsonl.gz", "wt", encoding="utf-8", compresslevel=1) as snapshots:
        for index in range(60, len(labels)):
            snapshot = build_snapshot(data, symbol, labels, index, captured, review, state, prior_days)
            result = evaluate(snapshot)
            if progress and (index == 60 or index % 20 == 0 or index == len(labels) - 1):
                progress(f"Evaluated {symbol} {result['session_date']} ({index + 1}/{len(labels)})")
            snapshots.write(canonical({"snapshot": snapshot, "result": result}) + "\n")
            state = result["state_after"]
            prior_days.append(result["daily_record"])
            positive = any(e["event_type"] in {"new_anomaly", "strengthened"} for e in result["events"])
            episode = state.get("episode_id")
            first_signal = bool(positive and episode and episode not in seen_episodes)
            if positive and episode:
                seen_episodes.add(episode)
            if result["session_date"] not in target_dates:
                continue
            rows.append({"session_date": result["session_date"], "score": result["score"],
                         "status": result["status"], "quality_valid": result["quality"]["valid"],
                         "reasons": result["quality"]["reasons"], "first_episode_signal": first_signal,
                         "episode_id": episode, "events": [e["event_type"] for e in result["events"]],
                         "dominant_evidence": result["dominant_evidence"], "evidence_coverage": result["evidence_coverage"],
                         "core_established": result["families"]["core_behavior"]["established"],
                         "price_response_confirmed": result["families"]["price_response"]["confirmed"],
                         "family_values": {key: value["value"] for key, value in result["families"].items()},
                         "evidence_modes": result["features"]["modes"],
                         "snapshot_id": result["snapshot_id"], "counter_evidence": result["counter_evidence"],
                         "forward": forward_performance(result["session_date"], data[symbol]["daily"], data["SPY"]["daily"], latest_date)})
    valid = [r for r in rows if r["quality_valid"]]
    signals = [r for r in valid if r["first_episode_signal"]]
    digest = hashlib.sha256()
    with (output / "snapshots.jsonl.gz").open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    summary = {"status": "completed" if len(valid) == len(rows) else "needs_review",
               "symbol": symbol, "start": targets[0]["date"], "end": targets[-1]["date"],
               "mode": "end_of_day_event_study", "data_captured_at": captured.isoformat(), "rule_version": RULE_VERSION, "rule_hash": RULE_HASH,
               "target_sessions": len(rows), "valid_sessions": len(valid), "signal_episodes": len(signals),
               "event_reviewed_sessions": sum(bool((ctx := review_context(review, day, captured)).get("event_review_through")) and instant(ctx["event_review_through"]) >= day["close"] for day in targets),
               "quality_reasons": dict(Counter(reason for r in rows for reason in r["reasons"])),
               "signal_performance": summarize_performance(signals),
               "all_valid_session_performance": summarize_performance(valid),
               "snapshot_archive_sha256": digest.hexdigest(), "verification": review,
               "limitations": ["Historical bars downloaded now are a revised data vintage, not point-in-time archives.",
                   "End-of-day checkpoints only; intraday alerts and intraday waning are not evaluated.",
                   "No historical live trade/quote stream: only price-volume evidence is tested.",
                   "Unknown historical event background blocks the strengthened state.",
                   "Five warm-up checkpoints initialize state; earlier episodes are not reconstructed.",
                   "Next-session open to horizon close price returns; no dividends, costs or execution model.",
                   "Episodes are counted once; overlapping return windows are not independent samples.",
                   "Performance is not evidence of account identity or an optimized trading strategy."]}
    (output / "daily.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary(output, summary)
    return summary


def write_summary(output, summary):
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {summary['symbol']} historical event study", "", f"Status: {summary['status']}",
             f"Period: {summary['start']} to {summary['end']}", ""]
    if "blocked_reason" in summary:
        lines.append(summary["blocked_reason"])
    else:
        lines += [f"Valid sessions: {summary['valid_sessions']}/{summary['target_sessions']}",
                  f"First signal episodes: {summary['signal_episodes']}", "",
                  "| Holding sessions | Samples | Mean price return % | Mean excess over SPY (pp) |",
                  "|---|---:|---:|---:|"]
        for h, row in summary["signal_performance"].items():
            def number(v): return "N/A" if v is None else f"{v:.3f}"
            lines.append(f"| {h} | {row['samples']} | {number(row['mean_return_pct'])} | {number(row['mean_excess_return_pp'])} |")
    lines += ["", "Limitations:"] + [f"- {s}" for s in summary.get("limitations", [])]
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


async def download_history(settings, cache, symbols, labels, *, pause_seconds=2.0, progress=print, outcome_sessions=10):
    if not 1 <= outcome_sessions <= 20:
        raise ValueError("outcome_sessions must be between 1 and 20")
    from accuflow.providers.ibkr_async.client import IBKRClient
    from accuflow.storage.database import Database
    # Existing production databases are never migrated by this command.
    if cache.exists():
        with sqlite3.connect(cache.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT value FROM system_state WHERE key='backtest_cache'").fetchone()
            if not row or row[0] != "v1":
                raise ValueError("download requires a dedicated backtest cache")
    db = Database(cache)
    await db.connect()
    client = IBKRClient(settings, db)
    try:
        await db.set_system_state("backtest_cache", "v1")
        await client.connect()
        latest = sessions(datetime.now(UTC), 1)[-1]
        following = calendar_for(date.fromisoformat(labels[-1]["date"]).year).sessions_in_range(
            date.fromisoformat(labels[-1]["date"]) + timedelta(days=1),
            date.fromisoformat(labels[-1]["date"]) + timedelta(days=40))
        outcome_end = following[min(outcome_sessions - 1, len(following) - 1)].date()
        daily_end = min(latest["close"], calendar_for(outcome_end.year).session_close(outcome_end.isoformat()).to_pydatetime())
        for symbol in symbols:
            if await db.get_stock(symbol) is None:
                await db.create_stock(symbol)
            qualified = await client.qualify_stock(symbol)
            await db.save_qualified_contract(symbol, company_name=qualified["company_name"], con_id=qualified["con_id"],
                                             primary_exchange=qualified["primary_exchange"], currency=qualified["currency"])
            await asyncio.sleep(pause_seconds)
            daily = await client.historical_bars(contract=qualified["contract"], duration="2 Y", bar_size="1 day", end_date_time=daily_end)
            if not daily:
                raise RuntimeError("IBKR returned no daily data")
            await db.upsert_bars(symbol=symbol, con_id=qualified["con_id"], bar_size="1 day", bars=daily, use_rth=True)
            for offset in range(0, len(labels), 5):
                batch = labels[offset:offset + 5]
                missing = []
                for session in batch:
                    existing = await db.bars_between(symbol, "1 min", session["open"].isoformat(), session["close"].isoformat())
                    mapping, _ = minute_map(existing, session["open"], session["close"])
                    if not coverage(mapping, session["open"], session["close"])["valid"]:
                        missing.append(session)
                if not missing:
                    continue
                # Keep each response around 2,000 bars, including holidays/weekends.
                duration_days = (date.fromisoformat(missing[-1]["date"]) - date.fromisoformat(missing[0]["date"])).days + 1
                await asyncio.sleep(pause_seconds)
                bars = await client.historical_bars(contract=qualified["contract"], duration=f"{duration_days} D", bar_size="1 min", end_date_time=missing[-1]["close"])
                if not bars:
                    raise RuntimeError(f"IBKR returned no minute data: {symbol} {missing[-1]['date']}")
                bars = [b for b in bars if missing[0]["open"] <= instant(b["timestamp"]) < missing[-1]["close"]]
                if not bars:
                    raise RuntimeError("IBKR returned data outside the requested range")
                await db.upsert_bars(symbol=symbol, con_id=qualified["con_id"], bar_size="1 min", bars=bars, use_rth=True)
                progress(f"Downloaded {symbol} through {batch[-1]['date']} ({min(offset + 5, len(labels))}/{len(labels)})")
    finally:
        await client.disconnect()
        await client.flush_errors()
        await db.close()


async def run_backtest(settings, *, symbol, start, end, cache, output, review=None, download=False):
    captured = datetime.now(UTC)
    labels, targets = make_plan(start, end, captured)
    if symbol == "SPY":
        raise ValueError("target symbol must differ from SPY benchmark")
    output.mkdir(parents=True, exist_ok=False)
    base = {"symbol": symbol, "start": targets[0]["date"], "end": targets[-1]["date"],
            "target_sessions": len(targets), "required_history_start": labels[0]["date"],
            "rule_version": RULE_VERSION, "rule_hash": RULE_HASH}
    try:
        if download:
            await download_history(settings, cache, (symbol, "SPY"), labels)
        data = load_bars(cache, (symbol, "SPY"))
        if any(not data[s][key] for s in (symbol, "SPY") for key in ("daily", "minute")):
            summary = base | {"status": "blocked", "blocked_reason": "GOOG/target and SPY both require real daily and one-minute IBKR history including warm-up; no substitute or synthetic data was used."}
            write_summary(output, summary)
            return summary
        captured = datetime.now(UTC)
        summary = await asyncio.to_thread(run_study, data, symbol, labels, targets, captured, review or {}, output, lambda message: print(message, flush=True))
        return summary
    except Exception as exc:
        summary = base | {"status": "blocked", "blocked_reason": f"Historical backtest could not complete ({type(exc).__name__}). Cache is retained for resuming; no live messages were sent."}
        write_summary(output, summary)
        raise
