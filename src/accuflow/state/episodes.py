"""Pure episode transition function; missing data never implies selling."""
import copy
import hashlib
from datetime import timedelta

from accuflow.features.bars import instant
from accuflow.scoring.rules import RULES, RULE_VERSION


STATUS_LABELS={"normal":"正常","observing":"观察","new_anomaly":"新异动",
    "strengthened":"持续迹象增强","waning":"衰减","invalidated":"结构失效"}


def initial_state():
    return {"status":"normal","episode_id":None,"rule_version":RULE_VERSION,
        "as_of":None,"last_valid_score":None,"last_valid_coverage":[],"last_valid_dominant":None,
        "initial_sent":False,"upgraded":False,"invalidated":False,"low_hours":0,"last_low_checkpoint":None,
        "low_close_dates":[],"last_notified_score":None,"last_notified_at":None,
        "notified_evidence":[],"enhancement_date":None,"enhancement_count":0,"quality_valid":None,"invalidation_reference":None}


def cross_day(features, prior_days, sessions):
    dates=[row["date"] for row in sessions[-5:]][::-1]
    records={x["session_date"]:x for x in prior_days if x.get("rule_version")==RULE_VERSION}
    current_supported=features["core_behavior"]["established"] and features["price_response"]["confirmed"]
    current={"session_date":features["session_date"],"evaluated":features["quality"]["valid"],
             "supported":current_supported and features["quality"]["independent_hours"]>=2,
             "closed":features["closed"],"rule_version":RULE_VERSION}
    records[current["session_date"]]=current
    support_count=closed_count=evaluable=0; weighted=0.0; timeline=[]
    for date,weight in zip(dates,RULES["cross_day_weights"]):
        record=records.get(date)
        valid=bool(record and record.get("evaluated") and (date==features["session_date"] or record.get("closed")))
        supported=bool(valid and record.get("supported"))
        if valid: evaluable+=1
        if supported:
            support_count+=1; weighted+=weight
            if record["closed"]: closed_count+=1
        timeline.append({"date":date,"evaluated":valid,"supported":supported if valid else None,
                         "closed":bool(record and record.get("closed")),"weight":weight})
    return {"available":True,"value":weighted/sum(RULES["cross_day_weights"]),
        "support_days":support_count,"closed_support_days":closed_count,"evaluable_days":evaluable,
        "timeline":timeline,"current_record":current}


def transition(previous, result, snapshot):
    state=copy.deepcopy(previous or initial_state())
    if state.get("rule_version")!=RULE_VERSION:
        state=initial_state()
    before=copy.deepcopy(state)
    if before.get("as_of") and instant(before["as_of"])>=instant(snapshot["as_of"]):
        return state,[]
    as_of=instant(snapshot["as_of"]); captured=instant(snapshot["captured_at"])
    date=result["session_date"]; score=result["score"]; quality=result["quality"]
    state["as_of"]=as_of.isoformat()
    state["quality_valid"]=quality["valid"]
    events=[]
    def emit(kind, notifiable=False):
        event_id=hashlib.sha256(f"{snapshot['instrument_key']}:{as_of.isoformat()}:{RULE_VERSION}:{kind}".encode()).hexdigest()[:32]
        events.append({"event_id":event_id,"episode_id":state["episode_id"],"event_type":kind,
            "severity":"high" if kind in {"strengthened","invalidated"} else "info",
            "symbol":snapshot["symbol"],"con_id":snapshot.get("con_id"),"as_of":as_of.isoformat(),
            "available_at":captured.isoformat(),"score":score,"previous_score":before.get("last_valid_score"),
            "dominant_evidence":result["dominant_evidence"],"supporting_evidence":result["evidence_ids"],
            "counter_evidence":result["counter_evidence"],"evidence_coverage":result["evidence_coverage"],
            "quality":quality,"known_events":quality.get("event_notes",""),
            "invalidation_reference":result["invalidation_reference"],"config_version":RULE_VERSION,
            "notifiable":notifiable and not result["closed"] and captured-as_of<=timedelta(minutes=15)})
    if before.get("quality_valid") is not None and before["quality_valid"]!=quality["valid"]:
        emit("data_quality")
    if not quality["valid"]:
        return state,events
    coverage=set(result["evidence_coverage"])
    comparable=(before.get("last_valid_score") is not None and
                set(before.get("last_valid_coverage",[]))<=coverage and
                before.get("last_valid_dominant")==result["dominant_evidence"])
    core=result["families"]["core_behavior"]["established"]
    response=result["families"]["price_response"]["confirmed"]
    if not state["episode_id"] and core and score>=55:
        state=initial_state() | {"as_of":as_of.isoformat(),"quality_valid":True,
            "status":"observing","invalidation_reference":result["support"],"episode_id":hashlib.sha256(f"{snapshot['instrument_key']}:{as_of.isoformat()}:{RULE_VERSION}".encode()).hexdigest()[:24]}
    cross=result["families"]["cross_day"]
    strong=(core and response and score>=78 and cross["support_days"]>=3 and cross["closed_support_days"]>=2
        and cross["evaluable_days"]>=4 and quality["independent_hours"]>=2
        and quality["event_checked"] and not quality["major_event"])
    family_count=sum(f["available"] and f["value"]>0 for f in result["families"].values())
    is_new=core and response and score>=65 and family_count>=2
    emitted=False
    if state["episode_id"] and result["invalidation"] and not state["invalidated"]:
        state["invalidated"]=True; state["status"]="invalidated"
        emit("invalidated",True); emitted=True
    elif state["episode_id"] and not state["invalidated"]:
        if strong and not state["upgraded"]:
            state["status"]="strengthened"; state["upgraded"]=True; state["initial_sent"]=True
            emit("strengthened",True); emitted=True
        elif is_new and not state["initial_sent"]:
            state["status"]="new_anomaly"; state["initial_sent"]=True
            emit("new_anomaly",True); emitted=True
        elif state["initial_sent"] and comparable and core and response and state["last_notified_score"] is not None:
            if state["enhancement_date"]!=date:
                state["enhancement_date"]=date; state["enhancement_count"]=0
            fresh=set(result["evidence_ids"])-set(state["notified_evidence"])
            cooled=state["last_notified_at"] is None or as_of-instant(state["last_notified_at"])>=timedelta(hours=2)
            if score-state["last_notified_score"]>=10 and fresh and cooled and state["enhancement_count"]<2:
                state["enhancement_count"]+=1
                emit("enhanced",True); emitted=True
        checkpoint=f"{date}:{quality['independent_hours']}"
        if state["initial_sent"] and not result["closed"] and comparable and state.get("last_low_checkpoint")!=checkpoint:
            preceding=f"{date}:{quality['independent_hours']-1}"
            state["low_hours"]=(state["low_hours"]+1 if state.get("last_low_checkpoint")==preceding else 1) if score<55 else 0
            state["last_low_checkpoint"]=checkpoint
            if state["low_hours"]==2:
                old_status=state["status"]; state["status"]="waning"
                emit("waning",old_status=="strengthened")
    if emitted:
        state["last_notified_score"]=score; state["last_notified_at"]=as_of.isoformat()
        state["notified_evidence"]=sorted(set(state["notified_evidence"])|set(result["evidence_ids"]))
    if result["closed"] and state["episode_id"]:
        if score<55 and comparable:
            previous_date=snapshot["sessions"][-2]["date"] if len(snapshot["sessions"])>1 else None
            dates=state["low_close_dates"]
            if date not in dates:
                state["low_close_dates"]=(dates+[date])[-2:] if dates and dates[-1]==previous_date else [date]
            if len(state["low_close_dates"])>=2:
                emit("episode_closed")
                state=initial_state() | {"as_of":as_of.isoformat(),"quality_valid":True}
        elif score>=55:
            state["low_close_dates"]=[]
    state["last_valid_score"]=score; state["last_valid_coverage"]=sorted(coverage)
    state["last_valid_dominant"]=result["dominant_evidence"]
    return state,events
