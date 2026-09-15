from datetime import UTC, datetime, timedelta
from math import isfinite
from statistics import median

from accuflow.services.quality import valid_bar
from accuflow.scoring.rules import RULES


def instant(value):
    value = datetime.fromisoformat(value) if isinstance(value,str) else value
    if value.tzinfo is None: raise ValueError("timestamp must include timezone")
    return value.astimezone(UTC)


def minute_map(bars, start, end, captured_at=None):
    result, invalid = {}, 0
    for bar in bars:
        try:
            time = instant(bar["timestamp"])
            if not start <= time < end or time+timedelta(minutes=1)>end: continue
            if captured_at and bar.get("received_at") and instant(bar["received_at"])>captured_at: continue
            if time.second or time.microsecond or not valid_bar(bar):
                invalid += 1; continue
            if time in result and result[time] != bar:
                # Unresolved revisions are not silently selected by input order.
                result[time] = None
            else:
                result[time] = bar
        except (ValueError,TypeError,KeyError):
            invalid += 1
    return {t:b for t,b in result.items() if b is not None}, invalid


def coverage(mapping, start, end):
    total = int((end-start).total_seconds()//60)
    longest = gap = found = 0
    for i in range(total):
        if start+timedelta(minutes=i) in mapping:
            found += 1; gap = 0
        else:
            gap += 1; longest = max(longest,gap)
    ratio = found/total if total else 0.0
    return {"expected":total,"received":found,"ratio":ratio,"max_gap":longest,
        "valid":total>0 and ratio>=RULES["minute_coverage"] and longest<=RULES["max_gap_minutes"]}


def aggregate(mapping, start, end, minutes):
    result = []
    current = start
    while current+timedelta(minutes=minutes)<=end:
        stop = current+timedelta(minutes=minutes)
        quality = coverage(mapping,current,stop)
        rows = [mapping[current+timedelta(minutes=n)] for n in range(minutes)
                if current+timedelta(minutes=n) in mapping]
        if rows:
            volume = sum(float(b["volume"]) for b in rows)
            wap_known = all(b.get("average") is not None and isfinite(float(b["average"]))
                            and float(b["average"])>0 for b in rows)
            wap = sum(float(b["average"])*float(b["volume"]) for b in rows)/volume if volume and wap_known else None
            result.append({"start":current.isoformat(),"end":stop.isoformat(),
                "slot":int((current-start).total_seconds()//60),"open":float(rows[0]["open"]),
                "high":max(float(b["high"]) for b in rows),"low":min(float(b["low"]) for b in rows),
                "close":float(rows[-1]["close"]),"volume":volume,"wap":wap,"quality":quality})
        current = stop
    return result


def effective_count(values):
    values = [max(0.0,float(x)) for x in values]
    squares = sum(x*x for x in values)
    return sum(values)**2/squares if squares else 0.0


def frozen_support(daily, session_date):
    # Daily records of the current session are never used to draw its support.
    rows = sorted([b for b in daily if b["timestamp"][:10]<session_date and valid_bar(b)],key=lambda b:b["timestamp"])
    if len(rows)<15: return None
    ranges = [max(float(b["high"])-float(b["low"]),abs(float(b["high"])-float(a["close"])),
                  abs(float(b["low"])-float(a["close"]))) for a,b in zip(rows[-15:-1],rows[-14:])]
    atr = median(ranges)
    if atr<=0: return None
    level = min(float(b["low"]) for b in rows[-5:])
    return {"lower":level-RULES["support_atr_width"]*atr,
            "upper":level+RULES["support_atr_width"]*atr,"atr":atr,
            "based_on_through":rows[-1]["timestamp"][:10],"frozen_for":session_date}


def support_events(slots, support):
    if not support: return [], False
    events, armed = [], True
    for index,slot in enumerate(slots):
        if not slot["quality"]["valid"]: continue
        if slot["low"]>support["upper"]+RULES["support_exit_atr"]*support["atr"]:
            armed=True
        if not armed or slot["low"]>support["upper"]: continue
        armed=False
        following=slots[index+1:index+4]
        if len(following)<3: continue
        if not all(b["quality"]["valid"] for b in following): continue
        if instant(following[-1]["end"])-instant(slot["end"])!=timedelta(minutes=15): continue
        floor=support["lower"]-RULES["invalidation_atr"]*support["atr"]
        if min([slot["low"]]+[b["low"] for b in following])>=floor and following[-1]["close"]>=support["upper"]:
            events.append({"at":slot["start"],"confirmed_at":following[-1]["end"],"price":following[-1]["close"]})
    floor=support["lower"]-RULES["invalidation_atr"]*support["atr"]
    last=slots[-2:]
    invalidated=len(last)==2 and all(b["quality"]["valid"] and b["close"]<floor for b in last)
    return events, invalidated
