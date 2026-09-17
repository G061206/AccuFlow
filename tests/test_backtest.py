"""Synthetic fixtures validate mechanics only, never GOOG market performance."""
import copy
import gzip
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from accuflow.config import Settings
from accuflow.detectors.unified import evaluate
from accuflow.services.backtest import (build_snapshot, forward_performance, load_bars,
    make_plan, review_context, run_backtest, run_study, summarize_performance, download_history)
from accuflow.services.calendar import sessions
from accuflow.storage.database import Database
from m2_fixtures import make_snapshot


def dataset():
    fixture = make_snapshot(minutes=390)
    data = {"GOOG": {"daily": fixture["daily"], "minute": fixture["minute"]},
            "SPY": {"daily": fixture["market_daily"], "minute": fixture["market_minute"]}}
    labels = [{"date": s["date"], "open": datetime.fromisoformat(s["open"]),
               "close": datetime.fromisoformat(s["close"])} for s in fixture["sessions"]]
    return data, labels, datetime.fromisoformat(fixture["captured_at"])


def bar(day, opening=100, close=110):
    return {"timestamp": day, "open": opening, "high": max(opening, close) + 1,
            "low": min(opening, close) - 1, "close": close, "volume": 1000}


def test_forward_starts_next_open_and_never_skips_missing_sessions():
    own = [bar("2026-09-11", close=500), bar("2026-09-14", opening=100, close=110)]
    benchmark = [bar("2026-09-14", opening=200, close=210)]
    result = forward_performance("2026-09-11", own, benchmark, "2026-10-01")
    assert result["1"]["entry_date"] == "2026-09-14"
    assert result["1"]["return_pct"] == pytest.approx(10)
    assert result["1"]["excess_return_pp"] == pytest.approx(5)
    assert result["5"] == {"available": False, "reason": "missing_stock_bars"}
    censored = forward_performance("2026-09-11", own, benchmark, "2026-09-11")
    assert censored["1"]["reason"] == "right_censored"


def test_summary_distinguishes_no_samples_from_zero_return():
    result = summarize_performance([])
    assert result["1"]["samples"] == 0
    assert result["1"]["mean_return_pct"] is None
    assert result["1"]["positive_return_fraction"] is None


def test_snapshot_excludes_future_and_current_daily_bar():
    data, labels, captured = dataset()
    review = {"units_verified": True, "adjustment_verified": True, "verification_notes": "synthetic fixture"}
    before = build_snapshot(data, "GOOG", labels, len(labels) - 1, captured, review)
    result = evaluate(before)
    assert result["quality"]["valid"]
    assert not result["quality"]["event_checked"]
    mutated = copy.deepcopy(data)
    mutated["GOOG"]["daily"] += [bar(labels[-1]["date"], close=10000), bar("2026-09-15", close=1)]
    future = copy.deepcopy(mutated["GOOG"]["minute"][-1])
    future.update(timestamp=(labels[-1]["close"] + timedelta(minutes=1)).isoformat(), close=10000)
    mutated["GOOG"]["minute"].append(future)
    after = build_snapshot(mutated, "GOOG", labels, len(labels) - 1, captured, review)
    assert before == after
    assert evaluate(after) == result


def test_review_never_fabricates_unit_or_event_verification():
    _, labels, captured = dataset()
    session = labels[-1]
    context = review_context({}, session, captured)
    assert not context["units_verified"] and not context["adjustment_verified"]
    review = {"events": {session["date"]: {"known_at": (session["close"] + timedelta(days=1)).isoformat(),
                                          "review_through": session["close"].isoformat()}}}
    assert "event_review_through" not in review_context(review, session, captured)


def test_plan_handles_leap_year_warmup_and_unfinished_dates():
    now = datetime(2026, 9, 15, 21, tzinfo=UTC)
    labels, targets = make_plan(date(2025, 9, 15), date(2026, 9, 15), now)
    assert len(labels) - len(targets) == 65
    assert labels[65] == targets[0]
    assert targets[-1]["date"] == "2026-09-15"
    with pytest.raises(ValueError, match="completed"):
        make_plan(date(2025, 9, 16), date(2026, 9, 16), now)


def test_walk_forward_preserves_replay_and_does_not_create_live_state(tmp_path):
    data, labels, captured = dataset()
    review = {"units_verified": True, "adjustment_verified": True, "verification_notes": "synthetic fixture only"}
    result = run_study(data, "GOOG", labels, labels[-1:], captured, review, tmp_path)
    assert result["valid_sessions"] == 1
    assert result["status"] == "completed"
    assert result["signal_episodes"] == 1
    assert result["signal_performance"]["1"]["samples"] == 0
    with gzip.open(tmp_path / "snapshots.jsonl.gz", "rt", encoding="utf-8") as handle:
        records = [json.loads(line) for line in handle]
    assert len(records) == 6  # Five state warm-up sessions plus one target session.
    for row in records:
        assert evaluate(row["snapshot"]) == row["result"]
    assert not list(tmp_path.glob("*.db"))


async def test_missing_real_data_is_blocked_not_a_zero_signal_backtest(tmp_path):
    output = tmp_path / "result"
    result = await run_backtest(Settings(_env_file=None), symbol="GOOG", start=date(2025, 9, 12),
                               end=date(2026, 9, 11), cache=tmp_path / "absent.db", output=output)
    assert result["status"] == "blocked"
    assert "signal_performance" not in result
    assert not (output / "snapshots.jsonl.gz").exists()
    with pytest.raises(FileExistsError):
        await run_backtest(Settings(_env_file=None), symbol="GOOG", start=date(2025, 9, 12),
                           end=date(2026, 9, 11), cache=tmp_path / "absent.db", output=output)


async def test_download_rejects_non_backtest_database_without_mutating_it(tmp_path):
    path = tmp_path / "business.db"
    db = Database(path)
    await db.connect()
    await db.close()
    before = path.read_bytes()
    with pytest.raises(ValueError, match="dedicated"):
        await download_history(Settings(_env_file=None), path, ("GOOG", "SPY"), [])
    assert path.read_bytes() == before
    assert load_bars(path, ("GOOG", "SPY"))["GOOG"]["minute"] == []


async def test_batched_download_is_bounded_and_resumes(tmp_path, monkeypatch):
    from accuflow.providers.ibkr_async import client as client_module
    labels, _ = make_plan(date(2026, 9, 8), date(2026, 9, 14), datetime(2026, 9, 15, 21, tzinfo=UTC))
    labels = labels[-5:]
    calls = []

    class FakeClient:
        def __init__(self, settings, database): pass
        async def connect(self): pass
        async def disconnect(self): pass
        async def flush_errors(self): pass
        async def qualify_stock(self, symbol):
            return {"company_name": symbol, "con_id": 1 if symbol == "GOOG" else 2,
                    "primary_exchange": "TEST", "currency": "USD", "contract": symbol}
        async def historical_bars(self, **kwargs):
            calls.append(kwargs)
            if kwargs["bar_size"] == "1 day":
                return [bar(day["date"]) for day in labels]
            assert kwargs["duration"] == "7 D"  # Five sessions crossing a weekend.
            return [bar((day["open"] + timedelta(minutes=i)).isoformat()) for day in labels
                    for i in range(int((day["close"] - day["open"]).total_seconds() // 60))]

    monkeypatch.setattr(client_module, "IBKRClient", FakeClient)
    cache = tmp_path / "history.db"
    messages = []
    settings = Settings(_env_file=None)
    await download_history(settings, cache, ("GOOG", "SPY"), labels, pause_seconds=0, progress=messages.append)
    assert len([c for c in calls if c["bar_size"] == "1 min"]) == 2
    assert len(load_bars(cache, ("GOOG", "SPY"))["GOOG"]["minute"]) == 1950
    assert len(messages) == 2
    calls.clear()
    await download_history(settings, cache, ("GOOG", "SPY"), labels, pause_seconds=0, progress=messages.append)
    assert all(c["bar_size"] == "1 day" for c in calls)


async def test_download_extends_daily_data_to_requested_twenty_session_horizon(tmp_path,monkeypatch):
    from accuflow.providers.ibkr_async import client as client_module
    from accuflow.services.calendar import calendar_for
    cal=calendar_for(2026)
    day=cal.sessions_in_range('2026-01-05','2026-01-05')[0]
    labels=[{'date':'2026-01-05','open':cal.session_open(day).to_pydatetime(),'close':cal.session_close(day).to_pydatetime()}]
    endpoints=[]
    class FakeClient:
        def __init__(self,settings,database): pass
        async def connect(self): pass
        async def disconnect(self): pass
        async def flush_errors(self): pass
        async def qualify_stock(self,symbol):
            return {'company_name':symbol,'con_id':1,'primary_exchange':'TEST','currency':'USD','contract':symbol}
        async def historical_bars(self,**kwargs):
            if kwargs['bar_size']=='1 day':
                endpoints.append(kwargs['end_date_time'])
                return [bar('2026-01-05')]
            return [bar((labels[0]['open']+timedelta(minutes=i)).isoformat()) for i in range(390)]
    monkeypatch.setattr(client_module,'IBKRClient',FakeClient)
    await download_history(Settings(_env_file=None),tmp_path/'cache.db',('GOOG',),labels,pause_seconds=0,outcome_sessions=20)
    assert endpoints==[cal.session_close(cal.session_offset(day,20)).to_pydatetime()]
