"""Conservative trade/quote classification. No inferred account identity."""
from bisect import bisect_right
from datetime import timedelta
from statistics import median

from accuflow.domain.signals import Observation, StreamInterval
from accuflow.features.bars import instant
from accuflow.scoring.rules import RULES


def sign(value):
    return 1 if value>1e-12 else -1 if value< -1e-12 else 0


def union_seconds(intervals, start, end):
    segments=sorted((max(start,x.start),min(end,x.end)) for x in intervals
        if x.trades_connected and x.quotes_connected and x.end>start and x.start<end)
    total=0.0; cursor=start
    for left,right in segments:
        left=max(left,cursor)
        if right>left: total+=(right-left).total_seconds()
        cursor=max(cursor,right)
    return total


def classify(trade, quotes, quote_times, intervals, age, tick_size, *, loose=False):
    if trade.unit!="shares" or trade.conditions or trade.timestamp_precision_seconds>1:
        return 0, "unverified_trade"
    if not any(x.connection_session==trade.connection_session and x.trades_connected and x.quotes_connected
               and x.start<=trade.event_time<x.end for x in intervals):
        return 0,"disconnected"
    index=bisect_right(quote_times,trade.event_time)
    for position in range(index-1,-1,-1):
        quote=quotes[position]
        delta=(trade.event_time-quote.event_time).total_seconds()
        if delta>age: break
        if quote.connection_session!=trade.connection_session or quote.received_at>trade.received_at: continue
        if quote.unit!="shares" or quote.conditions or quote.timestamp_precision_seconds>1: continue
        same_second=trade.event_time.replace(microsecond=0)==quote.event_time.replace(microsecond=0)
        if not loose and (quote.event_time>=trade.event_time or
                          (max(trade.timestamp_precision_seconds,quote.timestamp_precision_seconds)>=1 and same_second)):
            continue
        if quote.bid>=quote.ask: return 0,"locked_or_crossed"
        midpoint=(quote.bid+quote.ask)/2
        if abs(trade.price-midpoint)<=tick_size*0.05: return 0,"midpoint"
        if trade.price>=quote.ask-tick_size*0.1: return 1,"ask"
        if trade.price<=quote.bid+tick_size*0.1: return -1,"bid"
        return (1 if trade.price>midpoint else -1),"inside_spread_estimate"
    return 0,"no_prior_fresh_quote"


def flow_windows(observations, intervals, start, end, tick_size=0.01):
    """Consume complete intervals. Stable callback identity only deduplicates replay overlap."""
    start,end=instant(start),instant(end)
    parsed=[Observation.model_validate(x) if isinstance(x,dict) else x for x in observations]
    spans=[StreamInterval.model_validate(x) if isinstance(x,dict) else x for x in intervals]
    identities={}; conflicted=False
    for item in parsed:
        key=(item.connection_session,item.kind,item.sequence)
        if key in identities and identities[key]!=item: conflicted=True
        identities[key]=item
    trades=sorted([x for x in identities.values() if x.kind=="trade" and start<=x.event_time<end],key=lambda x:(x.event_time,x.received_at,x.sequence))
    quotes=sorted([x for x in identities.values() if x.kind=="quote"],key=lambda x:(x.event_time,x.received_at,x.sequence))
    times=[q.event_time for q in quotes]
    result=[]; current=start
    while current+timedelta(minutes=5)<=end:
        stop=current+timedelta(minutes=5)
        batch=[x for x in trades if current<=x.event_time<stop]
        amount=sum(x.price*x.size for x in batch)
        classified=buy=sell=0.0; unknown_reasons={}; sensitivity=[]
        for trade in batch:
            direction,reason=classify(trade,quotes,times,spans,1.0,tick_size)
            value=trade.price*trade.size
            if direction: classified+=value
            if direction>0: buy+=value
            if direction<0: sell+=value
            if not direction: unknown_reasons[reason]=unknown_reasons.get(reason,0)+1
        pi=(buy-sell)/amount if amount else 0.0
        for age in RULES["quote_age_sensitivity"]:
            for loose in (False,True):
                signed=sum(classify(t,quotes,times,spans,age,tick_size,loose=loose)[0]*t.price*t.size for t in batch)
                sensitivity.append(signed/amount if amount else 0.0)
        stable=not any(sign(value)*sign(pi)<0 for value in sensitivity)
        connected=union_seconds(spans,current,stop)/300
        ratio=classified/amount if amount else 0.0
        valid=(not conflicted and connected>=RULES["flow_connection_coverage"] and
            ratio>=RULES["classified_coverage"] and len(batch)>=RULES["min_flow_trades_per_slot"] and stable)
        start_quotes=[q for q in quotes if q.event_time<current and q.received_at<=current and q.bid<q.ask and q.unit=="shares" and not q.conditions]
        end_quotes=[q for q in quotes if q.event_time<stop and q.received_at<=stop and q.bid<q.ask and q.unit=="shares" and not q.conditions]
        first=start_quotes[-1] if start_quotes and (current-start_quotes[-1].event_time).total_seconds()<=2 else None
        last=end_quotes[-1] if end_quotes and (stop-end_quotes[-1].event_time).total_seconds()<=2 else None
        spreads=[(q.ask-q.bid)/((q.ask+q.bid)/2) for q in quotes if current<=q.event_time<stop and q.bid<q.ask and q.unit=="shares" and not q.conditions]
        result.append({"start":current.isoformat(),"end":stop.isoformat(),"amount":amount,
            "buy":buy,"sell":sell,"unknown":amount-classified,"pi":pi,"trade_count":len(batch),
            "classified_coverage":ratio,"connection_coverage":connected,"stable":stable,"valid":valid,
            "mid_start":(first.bid+first.ask)/2 if first else None,
            "mid_end":(last.bid+last.ask)/2 if last else None,
            "spread":median(spreads) if spreads else None,"unknown_reasons":unknown_reasons,
            "feed_type":"live_tick_by_tick","unit":"shares","sequence_conflict":conflicted})
        current=stop
    return result
