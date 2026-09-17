"""Pure v2 research detector over causally generated, versioned daily summaries."""
import hashlib
from statistics import mean
from accuflow.features.accumulation_v2 import extract
from accuflow.features.bars import instant
from accuflow.scoring.accumulation_v2 import canonical, percentile, score_interval, verify_manifest
from accuflow.state.accumulation_v2 import transition


def flow_verdict(snapshot):
    # Missing arrival provenance or incomplete dual streams cannot confirm direction.
    as_of, captured = instant(snapshot["as_of"]), instant(snapshot["captured_at"])
    grouped={}
    conflicts=set()
    for row in snapshot.get("flow_windows",[]):
        if not row.get("available_at") or instant(row["available_at"])>captured or instant(row["end"])>as_of:
            continue
        if row.get("feed_type")!="live_tick_by_tick" or row.get("unit")!="shares": continue
        if not row.get("valid") or not row.get("stable") or row.get("sequence_conflict"): continue
        if row.get("connection_coverage",0)<.95 or row.get("classified_coverage",0)<.7: continue
        if row.get("trade_count",0)<5: continue
        group=grouped.setdefault(row["start"][:10],{})
        if row["start"] in group and group[row["start"]]!=row: conflicts.add(row["start"])
        group[row["start"]]=row
    day=snapshot["as_of"][:10]
    daily={}
    for label in snapshot["sessions"]:
        rows=list(grouped.get(label["date"],{}).values())
        opened,closed=instant(label["open"]),instant(label["close"])
        rows=[r for r in rows if r["start"] not in conflicts and opened<=instant(r["start"])<instant(r["end"])<=closed
              and (instant(r["start"])-opened).total_seconds()%300==0
              and (instant(r["end"])-instant(r["start"])).total_seconds()==300]
        expected=int((closed-opened).total_seconds()/300)
        amount=sum(r["amount"] for r in rows)
        if len(rows)>=.95*expected and amount>0:
            daily[label["date"]]={"pi":sum(r["buy"]-r["sell"] for r in rows)/amount,
                "positive":mean(r["pi"]>0 for r in rows), "negative":mean(r["pi"]<0 for r in rows), "slots":expected}
    current=daily.get(day)
    refs=[v["pi"] for d,v in sorted(daily.items()) if d<day and current and v["slots"]==current["slots"]][-60:]
    if current is None or len(refs)<20:
        return {"status":"unavailable", "reason":"continuous_dual_stream_or_history_missing", "samples":len(refs)}
    rank=percentile(current["pi"],refs)
    recent_dates={label["date"] for label in snapshot["sessions"] if label["date"]<=day}
    recent_dates=set(sorted(recent_dates)[-5:])
    recent=[v for d,v in sorted(daily.items()) if d in recent_dates]
    if current["pi"]>0 and rank>=.8 and current["positive"]>=.6 and sum(v["pi"]>0 for v in recent)>=3:
        status="supports"
    elif current["pi"]<0 and rank<=.2 and current["negative"]>=.6 and sum(v["pi"]<0 for v in recent)>=3:
        status="contradicts"
    else:
        status="inconclusive"
    return {"status":status,"pi":current["pi"],"percentile":rank,"samples":len(refs),
            "scope":"estimated_active_direction_only"}


def evaluate_v2(snapshot, config):
    verify_manifest(config)
    if snapshot.get("manifest_hash")!=config["manifest_hash"] or snapshot.get("feature_schema")!="v2-daily-1":
        raise ValueError("v2 feature provenance mismatch")
    as_of,captured=instant(snapshot["as_of"]),instant(snapshot["captured_at"])
    if captured<as_of: raise ValueError("unfinished decision")
    days=[d for d in snapshot["days"] if instant(d["as_of"])<=as_of]
    if not days or instant(days[-1]["as_of"])!=as_of:
        raise ValueError("v2 requires an exact completed-session summary")
    dates=[d["date"] for d in days]
    if dates!=sorted(set(dates)): raise ValueError("unordered or duplicate daily features")
    labels=[s for s in snapshot["sessions"] if instant(s["close"])<=as_of]
    if [s["date"] for s in labels]!=dates or any(instant(d["as_of"])!=instant(s["close"]) for d,s in zip(days,labels)):
        raise ValueError("daily feature/session alignment mismatch")
    families,penalties,sensitivity=extract(days,config)
    lower,upper=score_interval(families,penalties,config)
    context=snapshot.get("context",{})
    known=bool(context.get("known_at") and instant(context["known_at"])<=captured)
    reasons=list(days[-1]["quality"]["reasons"])
    if not known or not context.get("units_verified"): reasons.append("units_unverified")
    if not known or not context.get("adjustment_verified"): reasons.append("adjustment_unverified")
    available=sorted(k for k,f in families.items() if f["available"])
    proportion=sum(config["weights"][k] for k in available)/100
    if proportion<config["minimum_coverage"] or not all(k in available for k in ("direction","retention","persistence")):
        reasons.append("feature_warmup_or_coverage")
    event_known=bool(context.get("event_known_at") and instant(context["event_known_at"])<=as_of)
    event_checked=bool(event_known and context.get("event_review_through") and instant(context["event_review_through"])>=as_of)
    refs=[d["raw"]["direction"][0] for d in days[:-1][-config["feature_history"]:] if d["raw"]["direction"][0] is not None]
    current=days[-1]["raw"]["direction"][0]
    reverse=percentile(-current,[-x for x in refs]) if current is not None and len(refs)>=config["min_feature_history"] else None
    if current is not None and current>=0 and reverse is not None: reverse=0.
    quality={"valid":not reasons,"reasons":sorted(set(reasons)),"base_coverage":proportion,
             "comparison_signature":[available,days[-1]["quality"]["reference"]],"reference":days[-1]["quality"]["reference"]}
    result={"model_version":config["version"],"manifest_hash":config["manifest_hash"],"instrument_key":snapshot["instrument_key"],
            "symbol":snapshot["symbol"],"as_of":as_of.isoformat(),"available_at":captured.isoformat(),
            "data_vintage":snapshot.get("data_vintage","unknown"),"calibration":config["calibration"],
            "score_lower":lower if not reasons else None,"score_upper":upper if not reasons else None,"threshold":config["threshold"],
            "families":families,"penalties":penalties,"sensitivity":sensitivity,"quality":quality,
            "flow_verdict":flow_verdict(snapshot),"event_context":{"checked":event_checked,
                "major_event":bool(event_known and context.get("major_event")),"notes":context.get("event_notes","") if event_known else "事件背景未核验"},
            "reverse_percentile":reverse,"pending":days[-1]["pending"],
            "support_days_5":sum(d.get("supported",False) for d in days[-5:]),
            "support_days_10":sum(d.get("supported",False) for d in days[-10:]),
            "evidence_ids":[f"{d['date']}:continuous_support" for d in days[-20:] if d.get("supported")]}
    state,events=transition(snapshot.get("state_before"),result,days,config)
    result.update(state_after=state,status=state["status"],events=events)
    result["snapshot_id"]=hashlib.sha256(canonical(snapshot).encode()).hexdigest()
    canonical(result)
    return result
