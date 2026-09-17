"""Daily v2 episodes, isolated by manifest and robust to missing observations."""
from copy import deepcopy
import hashlib


def initial(config):
    return {"manifest_hash":config["manifest_hash"], "status":"normal", "episode_id":None,
            "as_of":None, "initial_sent":False, "upgraded":False, "low_closes":0,
            "invalid_closes":0, "missing_closes":0, "coverage":None, "last_date":None,
            "closed_on":None, "support_at_signal":[], "floor":None, "residual_since_entry":0.}


def transition(previous, result, days, config):
    if previous and previous.get("manifest_hash") != config["manifest_hash"]:
        raise ValueError("v2 episode manifest mismatch")
    state = deepcopy(previous or initial(config))
    if state["as_of"] and state["as_of"] >= result["as_of"]:
        return state, []
    day = days[-1]
    date = day["date"]
    state["as_of"] = result["as_of"]
    if state["last_date"] == date:
        return state, []
    state["last_date"] = date
    events = []
    def emit(kind):
        key = f"{result['instrument_key']}:{config['manifest_hash']}:{date}:{kind}"
        events.append({"event_id":hashlib.sha256(key.encode()).hexdigest()[:32],
                       "episode_id":state["episode_id"], "event_type":kind,
                       "as_of":result["as_of"], "available_at":result["available_at"],
                       "notifiable":False})
    if not result["quality"]["valid"]:
        state["missing_closes"] += 1
        state["stale"] = state["missing_closes"] >= 5
        return state, events
    state["missing_closes"] = 0
    state["stale"] = False
    signature = result["quality"]["comparison_signature"]
    comparable = state["coverage"] is None or state["coverage"] == signature
    state["coverage"] = signature
    score, tau = result["score_lower"], config["threshold"]
    support = [d["date"] for d in days if d.get("supported")]
    dates = [d["date"] for d in days]
    cooled = state["closed_on"] is None or sum(d > state["closed_on"] for d in dates) >= config["cooldown_sessions"]
    fresh = state["closed_on"] is None or any(d > state["closed_on"] for d in support)
    if not state["episode_id"] and score >= tau-10 and cooled and fresh:
        state["episode_id"] = hashlib.sha256(f"{result['instrument_key']}:{date}:{config['manifest_hash']}".encode()).hexdigest()[:24]
        state.update(status="observing", initial_sent=False, upgraded=False, floor=day["invalidation_floor"],
                     residual_since_entry=0., support_at_signal=[], low_closes=0, invalid_closes=0)
    if not state["episode_id"]:
        return state, events
    rr = day["raw"]["retention"][0]
    if rr is not None:
        state["residual_since_entry"] += rr
    if comparable:
        state["low_closes"] = state["low_closes"]+1 if score<tau-10 else 0
        failure = (state["floor"] is not None and day["close"] is not None and day["close"]<state["floor"]
                   and state["residual_since_entry"]<=0 and result["reverse_percentile"] is not None
                   and result["reverse_percentile"]>=.8)
        state["invalid_closes"] = state["invalid_closes"]+1 if failure else 0
    else:
        state["low_closes"] = state["invalid_closes"] = 0
    if state["invalid_closes"]>=config["invalidation_closes"]:
        state["status"]="invalidated"
        emit("invalidated")
        state["episode_id"]=None
        state["closed_on"]=date
        return state,events
    if state["low_closes"]>=config["close_low_closes"]:
        emit("episode_closed")
        replacement = initial(config)
        replacement.update(as_of=state["as_of"], last_date=date, closed_on=date, coverage=signature)
        return replacement,events
    if state["initial_sent"] and state["low_closes"]>=config["waning_closes"]:
        if state["status"]!="waning":
            state["status"]="waning"
            emit("waning")
        return state,events
    enough_days = sum(d.get("supported",False) for d in days[-5:])>=3 or sum(d.get("supported",False) for d in days[-10:])>=6
    family_count = sum(f["available"] and f["value"]>config["family_gate"] for k,f in result["families"].items() if k!="persistence")
    candidate = score>=tau and enough_days and family_count>=2
    if candidate and not state["initial_sent"]:
        state.update(status="candidate", initial_sent=True, support_at_signal=support)
        emit("candidate")
    elif state["initial_sent"] and not state["upgraded"] and score>=min(tau+10,95) and candidate:
        new_days=set(support)-set(state["support_at_signal"])
        if len(new_days)>=2 and result["event_context"]["checked"] and not result["event_context"]["major_event"] and result["flow_verdict"]["status"]!="contradicts":
            state.update(status="strengthened",upgraded=True)
            emit("strengthened")
    elif state["status"]=="waning" and candidate:
        state["status"]="candidate"
    return state,events
