from datetime import UTC, datetime, timedelta
from math import sin

from accuflow.services.calendar import sessions
from accuflow.scoring.rules import RULE_VERSION, RULE_HASH


def make_snapshot(*, minutes=180, current_date=None, references=True):
    as_of=current_date or datetime(2026,9,14,16,30,tzinfo=UTC)
    labels=sessions(as_of,66,complete=False)
    current=labels[-1]
    as_of=current["open"]+timedelta(minutes=minutes)
    minute=[]; market_minute=[]; daily=[]; market_daily=[]
    for i,day in enumerate(labels):
        today=day["date"]==current["date"]
        if not today:
            daily.append(dict(timestamp=day["date"],open=100.5,high=102.5,low=98,close=100.5,volume=40000,source="IBKR",use_rth=1))
            market_daily.append(dict(timestamp=day["date"],open=100,high=101,low=99,close=100,volume=40000,source="IBKR",use_rth=1))
        duration=minutes if today else int((day["close"]-day["open"]).total_seconds()//60)
        for n in range(duration):
            price=100.5+0.004*n if today else 100.5+0.03*sin(n/20+i)
            row=dict(timestamp=(day["open"]+timedelta(minutes=n)).isoformat(),open=price,high=price+0.12,
                low=price-0.12,close=price+0.02,volume=300 if today else 90+i%17+n%7,
                average=price,source="IBKR",use_rth=1)
            if today and n in (25,85): row["low"]=98.2
            minute.append(row)
            market_minute.append(dict(timestamp=row["timestamp"],open=100,high=100.1,low=99.9,close=100,
                volume=100,average=100,source="IBKR",use_rth=1))
    return {"schema_version":1,"symbol":"AAPL","con_id":1,"instrument_key":"conid:1",
        "rule_version":RULE_VERSION,"rule_hash":RULE_HASH,"as_of":as_of.isoformat(),
        "captured_at":(as_of+timedelta(minutes=2)).isoformat(),
        "sessions":[{k:v.isoformat() if hasattr(v,"isoformat") else v for k,v in day.items()} for day in labels],
        "daily":daily,"minute":minute,"market_daily":market_daily if references else [],
        "market_minute":market_minute if references else [],"sector_daily":[],"sector_minute":[],
        "flow_windows":[],"prior_days":[],"state_before":None,
        "context":{"known_at":(current["open"]-timedelta(minutes=1)).isoformat(),
            "effective_session":current["date"],"units_verified":True,"adjustment_verified":True,
            "event_review_through":as_of.isoformat(),"major_event":False,"event_notes":"fixture reviewed"}}


def add_prior_days(snapshot, supported=3):
    snapshot["prior_days"]=[{"session_date":s["date"],"evaluated":True,"supported":i<supported,
        "closed":True,"rule_version":RULE_VERSION} for i,s in enumerate(snapshot["sessions"][-5:-1][::-1])]
    return snapshot


def add_flow(snapshot, *, side="buy"):
    rows=[]
    for index,day in enumerate(snapshot["sessions"]):
        opened=datetime.fromisoformat(day["open"])
        stop=min(datetime.fromisoformat(day["close"]),datetime.fromisoformat(snapshot["as_of"]))
        today=index==len(snapshot["sessions"])-1
        for slot in range(int((stop-opened).total_seconds()//300)):
            amount=(3000 if today else 1000+index%20*40) if side=="sell" else (1000 if today else (500 if slot%3 else 3000))
            pi=(-0.8 if side=="sell" else 0.9) if today else (-0.6 if side=="sell" else 0.1+index%7*.02)
            start=opened+timedelta(minutes=5*slot)
            rows.append(dict(start=start.isoformat(),end=(start+timedelta(minutes=5)).isoformat(),amount=amount,
                buy=amount*(1+pi)/2,sell=amount*(1-pi)/2,unknown=0,pi=pi,trade_count=10,
                connection_coverage=1.0,classified_coverage=1.0,stable=True,valid=True,
                mid_start=100,mid_end=100 if today else 99.5,spread=.002,
                feed_type="live_tick_by_tick",unit="shares"))
    snapshot["flow_windows"]=rows
    return snapshot
