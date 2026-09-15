"""Pure M2 evaluation: snapshot -> features -> one score -> one episode."""
import hashlib
import json

from accuflow.features.extract import extract
from accuflow.scoring.rules import RULES, RULE_HASH, RULE_VERSION, unified_score
from accuflow.state.episodes import cross_day, transition, STATUS_LABELS


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)


def snapshot_id(snapshot):
    return hashlib.sha256(canonical(snapshot).encode()).hexdigest()


def evaluate(snapshot):
    if snapshot.get("rule_version")!=RULE_VERSION or snapshot.get("rule_hash")!=RULE_HASH:
        raise ValueError("unsupported or changed frozen rule version")
    features=extract(snapshot)
    cross=cross_day(features,snapshot.get("prior_days",[]),snapshot["sessions"])
    families={name:features[name] for name in ("core_behavior","price_response","relative_strength","distribution")}
    families["cross_day"]=cross
    score,penalty=unified_score(families,features["penalties"])
    quality=features["quality"]
    if not quality["valid"]: score=None
    coverage=[name for name,family in families.items() if family["available"]]
    if quality["flow_available"]: coverage.append("trade_quote_direction")
    if features["distribution"]["wap_available"]: coverage.append("wap")
    counter=list(quality["reasons"])
    if not quality["flow_available"]: counter.append("逐笔方向与承接证据不可用，缺失权重未重新分配")
    if not quality["event_checked"]: counter.append("事件背景未完整核验，禁止升级为较强迹象")
    if quality["major_event"]: counter.append("事件附近异动，禁止升级为较强建仓迹象")
    if not features["relative_strength"]["available"]: counter.append("大盘/行业参照不足，相对强弱分项不可用")
    if not features["distribution"]["wap_available"]: counter.append("WAP 缺失，VWAP 分项不可用")
    counter += [f"反证扣分：{name}" for name in features["penalties"]]
    result={"rule_version":RULE_VERSION,"rule_hash":RULE_HASH,"snapshot_id":snapshot_id(snapshot),
        "symbol":snapshot["symbol"],"con_id":snapshot.get("con_id"),"as_of":snapshot["as_of"],
        "session_date":features["session_date"],"closed":features["closed"],"score":score,
        "families":families,"quality":quality,"dominant_evidence":features["dominant_evidence"],
        "evidence_coverage":sorted(coverage),"evidence_ids":features["evidence_ids"],
        "counter_evidence":counter,"pending_evidence":features["pending"],
        "contradiction_penalty":penalty,"support":features["support"],"invalidation":features["invalidation"],"invalidation_reference":features["invalidation_reference"],
        "features":{"modes":features["modes"],"five_minute":features["slots"],"hourly":features["hours"]}}
    state,events=transition(snapshot.get("state_before"),result,snapshot)
    result["state_after"]=state
    result["status"]=state["status"]
    result["status_label"]=STATUS_LABELS[state["status"]] if quality["valid"] else "数据不足（保留原信号状态）"
    result["events"]=events
    result["daily_record"]=cross["current_record"] | {"as_of":snapshot["as_of"],"score":score}
    for event in events: event["evidence_snapshot_id"]=result["snapshot_id"]
    # Validate serializability, including every diagnostic value.
    canonical(result)
    return result
