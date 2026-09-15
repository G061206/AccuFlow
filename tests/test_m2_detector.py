import copy
from datetime import datetime, timedelta

import pytest
from accuflow.detectors.unified import evaluate
from accuflow.features.bars import minute_map, aggregate, instant
from accuflow.scoring.rules import unified_score, RULES
from m2_fixtures import make_snapshot, add_prior_days, add_flow


def test_proxy_detector_produces_one_explainable_score_without_tick_fabrication():
    snapshot=make_snapshot()
    result=evaluate(snapshot)
    assert result["quality"]["valid"]
    assert result["dominant_evidence"]=="price_volume"
    assert result["families"]["core_behavior"]["established"]
    assert result["families"]["price_response"]["confirmed"]
    assert result["score"]>=65
    assert not result["quality"]["flow_available"]
    assert result["state_after"]["status"]=="new_anomaly"
    assert len(result["families"])==5
    assert result==evaluate(copy.deepcopy(snapshot))


def test_cross_day_upgrade_and_event_background_gate():
    snapshot=add_prior_days(make_snapshot())
    result=evaluate(snapshot)
    assert result["families"]["cross_day"]["support_days"]==4
    assert result["state_after"]["status"]=="strengthened"
    snapshot["context"]["event_review_through"]=None
    missing=evaluate(snapshot)
    assert missing["score"]==result["score"]
    assert missing["state_after"]["status"]!="strengthened"
    snapshot["context"]["event_review_through"]=snapshot["as_of"]
    snapshot["context"]["major_event"]=True
    assert evaluate(snapshot)["state_after"]["status"]!="strengthened"


def test_future_rows_and_current_daily_do_not_change_frozen_features():
    snapshot=make_snapshot()
    first=evaluate(snapshot)
    future=copy.deepcopy(snapshot["minute"][-1]);future["timestamp"]=(instant(snapshot["as_of"])+timedelta(minutes=10)).isoformat();future["volume"]=1e12
    snapshot["minute"].append(future)
    snapshot["daily"].append(dict(timestamp=snapshot["sessions"][-1]["date"],open=1,high=1000,low=.01,close=500,volume=1e12))
    later=evaluate(snapshot)
    assert first["score"]==later["score"]
    assert first["support"]==later["support"]
    assert first["families"]["relative_strength"]["coefficients"]==later["families"]["relative_strength"]["coefficients"]


def test_gap_over_three_minutes_disables_score_but_preserves_episode():
    snapshot=make_snapshot();before=evaluate(snapshot)
    snapshot["state_before"]=before["state_after"]
    snapshot["as_of"]=(instant(snapshot["as_of"])+timedelta(minutes=5)).isoformat()
    snapshot["captured_at"]=(instant(snapshot["as_of"])+timedelta(minutes=2)).isoformat()
    result=evaluate(snapshot)
    assert result["score"] is None
    assert result["state_after"]["episode_id"]==before["state_after"]["episode_id"]
    assert result["state_after"]["status"]==before["state_after"]["status"]
    assert all(e["event_type"]!="waning" for e in result["events"])


def test_missing_fields_do_not_redistribute_weights():
    snapshot=make_snapshot();first=evaluate(snapshot)
    snapshot["market_minute"]=[]
    for b in snapshot["minute"]: b.pop("average",None)
    missing=evaluate(snapshot)
    assert missing["score"]<first["score"]
    assert not missing["families"]["relative_strength"]["available"]
    assert missing["families"]["distribution"]["value"]<=0.5
    assert unified_score({"core_behavior":{"available":True,"value":1}},[])[0]==30
    assert unified_score({k:{"available":True,"value":1} for k in RULES["weights"]},list(RULES["penalties"]))==(70,30)


def test_active_flow_candidate_and_pending_response_are_distinct():
    result=evaluate(add_flow(make_snapshot()))
    assert result["features"]["modes"]["active_buying"]["available"]
    assert result["features"]["modes"]["active_buying"]["candidate"]
    assert result["features"]["modes"]["active_buying"]["response"]
    first_hour=evaluate(add_flow(make_snapshot(minutes=60)))
    assert first_hour["features"]["modes"]["active_buying"]["pending"]
    assert not first_hour["features"]["modes"]["active_buying"]["response"]


def test_passive_flow_needs_matched_history_and_finished_recovery():
    snapshot=add_flow(make_snapshot(),side="sell")
    result=evaluate(snapshot)
    passive=result["features"]["modes"]["passive_absorption"]
    assert passive["available"] and passive["candidate"] and passive["response"]
    assert passive["matched_samples"]==30
    snapshot["flow_windows"]=[r for r in snapshot["flow_windows"] if instant(r["start"])>=instant(snapshot["sessions"][-1]["open"])]
    assert not evaluate(snapshot)["features"]["modes"]["passive_absorption"]["available"]


def test_incomplete_bars_never_enter_aggregation():
    snapshot=make_snapshot(minutes=62)
    start=instant(snapshot["sessions"][-1]["open"]);end=instant(snapshot["as_of"])
    rows,_=minute_map(snapshot["minute"],start,end)
    assert len(aggregate(rows,start,end,5))==12
    assert len(aggregate(rows,start,end,60))==1


def test_unverified_units_or_changed_rules_fail_closed():
    snapshot=make_snapshot();snapshot["context"]["units_verified"]=False
    assert evaluate(snapshot)["score"] is None
    snapshot["rule_hash"]="changed"
    with pytest.raises(ValueError,match="frozen rule"): evaluate(snapshot)
