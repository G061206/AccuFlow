from datetime import timedelta
from math import log, sqrt, isfinite
from statistics import median

from accuflow.features.bars import instant, minute_map, coverage, aggregate, effective_count, frozen_support, support_events
from accuflow.features.relative import relative_strength
from accuflow.scoring.rules import RULES, evidence, percentile
from accuflow.services.quality import valid_bar


def unavailable(reason):
    return {"available":False,"value":0.0,"reason":reason}


def usable_flow(row):
    try:
        values=[row[k] for k in ("amount","buy","sell","classified_coverage","connection_coverage","trade_count")]
        return (all(isfinite(float(v)) and float(v)>=0 for v in values) and row.get("valid") and
            row.get("stable") and not row.get("sequence_conflict",False) and row.get("unit")=="shares" and
            row.get("feed_type")=="live_tick_by_tick" and row["classified_coverage"]>=0.7 and
            row["connection_coverage"]>=0.95 and row["trade_count"]>=5 and row["amount"]>0 and
            row["buy"]+row["sell"]<=row["amount"]+1e-6 and abs(row["pi"]-(row["buy"]-row["sell"])/row["amount"])<1e-8)
    except (KeyError,TypeError,ValueError): return False


def hour_flow(rows):
    amount=sum(r["amount"] for r in rows)
    good=[r for r in rows if usable_flow(r)]
    coverage=sum(r["connection_coverage"]*300 for r in rows)/3600
    classified=sum((r["buy"]+r["sell"]) for r in rows)/amount if amount else 0
    signed=sum(r["buy"]-r["sell"] for r in good)
    contributions=[max(0,r["buy"]-r["sell"]) for r in good]
    longest=run=0
    for row in sorted(rows,key=lambda r:r['start']):
        run=run+1 if usable_flow(row) and row['pi']>0 else 0
        longest=max(longest,run)
    return {"longest_positive_slots":longest,"valid":coverage>=0.95 and classified>=0.7 and len(good)>=8,
        "pi":signed/amount if amount else 0,"positive":sum(r["pi"]>0 for r in good),
        "effective":effective_count(contributions),"rows":good,"amount":amount}


def flow_evidence(snapshot, days, current, slots, support, relative):
    # Invalid/duplicate finalized slots never inflate the number of independent windows.
    unique={}; conflicts=set()
    for row in snapshot.get("flow_windows",[]):
        try:
            start,end=instant(row['start']),instant(row['end'])
            numbers=[float(row[k]) for k in ('amount','buy','sell','connection_coverage','classified_coverage','trade_count','pi')]
            if not all(isfinite(n) for n in numbers) or min(numbers[:-1])<0: continue
            if max(row['connection_coverage'],row['classified_coverage'])>1: continue
            if start in unique and unique[start]!=row: conflicts.add(start)
            unique[start]=row
        except (KeyError,TypeError,ValueError): continue
    rows=[row for start,row in unique.items() if start not in conflicts]
    current_start,current_end=current["open"],current["end"]
    by_day={day["date"]:[r for r in rows if day["open"]<=instant(r["start"]) and instant(r["end"])<=day["end"]
             and instant(r["end"])-instant(r["start"])==timedelta(minutes=5)] for day in days+[current]}
    active=unavailable("缺少合格实时双流及至少20日同口径基线")
    passive=unavailable("缺少合格卖压、同步参照及至少30个历史匹配样本")
    current_rows=by_day[current["date"]]
    historical=[r for day in days for r in by_day[day["date"]] if usable_flow(r)]
    confirmed=[]; pending=[]
    for hour in range(int((current_end-current_start).total_seconds()//3600)):
        start=current_start+timedelta(hours=hour); end=start+timedelta(hours=1)
        group=[r for r in current_rows if start<=instant(r["start"])<end]
        summary=hour_flow(group)
        history=[]
        for day in days:
            hstart=day["open"]+timedelta(hours=hour); hend=hstart+timedelta(hours=1)
            if hend>day["end"]: continue
            item=hour_flow([r for r in by_day[day["date"]] if hstart<=instant(r["start"])<hend])
            if item["valid"]: history.append(item)
        if not summary["valid"] or len(history)<20: continue
        rank=percentile(summary["pi"],[h["pi"] for h in history])
        biggest=max(summary["rows"],key=lambda r:r["buy"]-r["sell"])
        remaining=summary["amount"]-biggest["amount"]
        trimmed=(summary["pi"]*summary["amount"]-(biggest["buy"]-biggest["sell"]))/remaining if remaining>0 else 0
        trimmed_rank=percentile(trimmed,[h["pi"] for h in history])
        candidate=summary["positive"]>=7 and rank>=0.8 and trimmed_rank>=0.8
        response=[b for b in slots if end<=instant(b["start"]) and instant(b["end"])<=end+timedelta(minutes=15)]
        before=[b for b in slots if instant(b["end"])==end]
        complete=len(response)==3 and all(b["quality"]["valid"] for b in response) and bool(before)
        held=complete and response[-1]["close"]>=before[-1]["close"] and slots[-1]["close"]>=before[-1]["close"]
        item={"available":True,"value":(evidence(rank)+evidence(percentile(summary["effective"],[h["effective"] for h in history])))/2 if candidate else 0.0,
              "candidate":candidate,"response":bool(held),"pending":candidate and not complete,
              "opposite":candidate and complete and not held,"pi":summary["pi"],"percentile":rank,
              "trimmed_percentile":trimmed_rank,"longest_positive_slots":summary["longest_positive_slots"],"positive_slots":summary["positive"],"effective_slots":summary["effective"],"samples":len(history),"confirmed_at":(end+timedelta(minutes=15)).isoformat(),
              "evidence_id":f"active:{start.isoformat()}"}
        if not active["available"] or item["value"]>active["value"]: active=item
        if candidate and held: confirmed.append(item["evidence_id"])
        elif candidate and not complete: pending.append(item["evidence_id"])
    if not relative.get("available") or not support:
        return active,passive,confirmed,pending
    scale=relative["history_scale"]*sqrt(5/390)
    def residual(row,day):
        if not row.get("mid_start") or not row.get("mid_end") or not row.get("spread"): return None
        start,end=instant(row["start"]),instant(row["end"])
        market=day.get("market_map",{})
        first,last=market.get(start),market.get(end-timedelta(minutes=1))
        if not first or not last: return None
        expected=relative["coefficients"][0]*(5/390)+relative["coefficients"][1]*log(float(last["close"])/float(first["open"]))
        if len(relative["coefficients"])==3:
            sector=day.get("sector_map",{}); first,last=sector.get(start),sector.get(end-timedelta(minutes=1))
            if not first or not last: return None
            expected+=relative["coefficients"][2]*log(float(last["close"])/float(first["open"]))
        return log(row["mid_end"]/row["mid_start"])-expected
    for row in current_rows:
        if not usable_flow(row) or row["pi"]>=-0.2: continue
        offset=int((instant(row["start"])-current_start).total_seconds()//60)
        same_slot=[]; matches=[]
        for day in days:
            for previous in by_day[day["date"]]:
                position=int((instant(previous["start"])-day["open"]).total_seconds()//60)
                if not usable_flow(previous): continue
                if position==offset: same_slot.append(previous)
                if abs(position-offset)<=30 and previous["pi"]< -0.2:
                    value=residual(previous,day)
                    if value is not None: matches.append((previous,max(0,-value)/max(scale,1e-6)))
        if len(same_slot)<20 or len(matches)<30: continue
        baseline=median(x["amount"] for x in same_slot)
        if baseline<=0: continue
        u=row["sell"]/baseline
        pressure_rank=percentile(row["sell"],[x["sell"] for x in same_slot])
        value=residual(row,current)
        if value is None: continue
        y=max(0,-value)/max(scale,1e-6)
        matches=[pair for pair in matches if abs(pair[0]["sell"]/baseline-u)<=max(0.5,0.5*u)
                 and 0.5<=pair[0]["spread"]/row["spread"]<=2.0]
        if len(matches)<30: continue
        nearest=sorted(matches,key=lambda pair:abs(pair[0]["sell"]/baseline-u)+abs(pair[0]["spread"]-row["spread"])/max(row["spread"],1e-6))[:30]
        expected=median(y0 for _,y0 in nearest)
        rank=1-percentile(y,[y0 for _,y0 in nearest])
        end=instant(row["end"])
        future=[r for r in current_rows if instant(r["end"])==end+timedelta(minutes=15) and usable_flow(r)]
        complete=bool(future and future[-1].get("mid_end"))
        recovery=complete and future[-1]["mid_end"]>=row["mid_start"]-0.05*support["atr"]
        candidate=pressure_rank>=0.8 and expected>0 and y<expected*0.5
        item={"available":True,"value":(evidence(pressure_rank)+evidence(rank))/2 if candidate else 0.0,
              "candidate":candidate,"response":bool(recovery),"pending":candidate and not complete,
              "opposite":candidate and complete and not recovery,"pressure_percentile":pressure_rank,
              "expected_impact":expected,"actual_impact":y,"matched_samples":30,
              "evidence_id":f"passive:{row['start']}","confirmed_at":(end+timedelta(minutes=15)).isoformat()}
        if not passive["available"] or item["value"]>passive["value"]: passive=item
        if candidate and recovery: confirmed.append(item["evidence_id"])
        elif candidate and not complete: pending.append(item["evidence_id"])
    return active,passive,confirmed,pending


def extract(snapshot):
    as_of=instant(snapshot["as_of"]); captured=instant(snapshot["captured_at"])
    session_rows=snapshot["sessions"]
    current_label=session_rows[-1]
    start=instant(current_label["open"]); end=min(as_of,instant(current_label["close"]))
    end=end.replace(second=0,microsecond=0)
    current_date=current_label["date"]
    total_minutes=int((end-start).total_seconds()//60)
    context=snapshot.get("context") or {}
    context_known=context.get("known_at") and instant(context["known_at"])<=captured and context.get("effective_session","")<=current_date
    mapping,invalid=minute_map(snapshot["minute"],instant(session_rows[0]["open"]),end,captured)
    market_map,_=minute_map(snapshot.get("market_minute",[]),instant(session_rows[0]["open"]),end,captured)
    sector_map,_=minute_map(snapshot.get("sector_minute",[]),instant(session_rows[0]["open"]),end,captured)
    current={"date":current_date,"open":start,"end":end,"market_map":market_map,"sector_map":sector_map}
    quality=coverage(mapping,start,end)
    quality["invalid_rows"]=invalid
    reasons=[]
    if not quality["valid"]: reasons.append("当前分钟覆盖低于95%或连续缺口超过3分钟")
    if total_minutes<60: reasons.append("当前交易日尚无完整小时窗口")
    if not context_known or not context.get("units_verified"): reasons.append("成交量单位尚未核验")
    if not context_known or not context.get("adjustment_verified"): reasons.append("公司行动与复权口径尚未核验")
    if snapshot.get("collection_error"): reasons.append(f"采集失败：{snapshot['collection_error']}")
    daily=[b for b in snapshot["daily"] if b["timestamp"][:10]<current_date and valid_bar(b) and (not b.get("received_at") or instant(b["received_at"])<=captured)]
    daily_dates={b["timestamp"][:10] for b in daily}
    if len(daily_dates)<60: reasons.append("完整日线基线不足60日")
    days=[]
    for label in session_rows[:-1]:
        opened,closed=instant(label["open"]),instant(label["close"])
        stop=opened+timedelta(minutes=total_minutes)
        if stop>closed: continue
        q=coverage(mapping,opened,stop)
        if q["valid"]:
            days.append({"date":label["date"],"open":opened,"end":stop,"quality":q,
                "slots":aggregate(mapping,opened,stop,5),"market_map":market_map,"sector_map":sector_map})
    days=days[-60:]
    if len(days)<20: reasons.append(f"相同时段合格基线仅 {len(days)}/20 日")
    slots=aggregate(mapping,start,end,5); hours=aggregate(mapping,start,end,60)
    good=[b for b in slots if b["quality"]["valid"]]
    independent=sum(b["quality"]["valid"] for b in hours)
    if len(good)<8: reasons.append("有效5分钟片段不足8个")
    # Foreign feeds cannot enter the historical TRADES baseline.
    if any(b.get("source","IBKR")!="IBKR" or not b.get("use_rth",1) for b in snapshot["minute"]):
        reasons.append("行情来源或会话口径不一致")
    daily.sort(key=lambda b:b["timestamp"])
    support=frozen_support(daily,current_date)
    recoveries,broken=support_events(slots,support)
    volume=sum(b["volume"] for b in good)
    history_volume=[sum(b["volume"] for b in d["slots"] if b["quality"]["valid"]) for d in days]
    volume_rank=percentile(volume,history_volume)
    rvol=volume/median(history_volume) if history_volume and median(history_volume)>0 else None
    trim=volume-max((b["volume"] for b in good),default=0)
    trimmed_history=[v-max((b["volume"] for b in d["slots"] if b["quality"]["valid"]),default=0) for d,v in zip(days,history_volume)]
    trimmed_rank=percentile(trim,trimmed_history)
    interior=[b for b in good if b["slot"]>=15 and instant(b["end"])<=instant(current_label["close"])-timedelta(minutes=15)]
    interior_history=[sum(b["volume"] for b in d["slots"] if b["quality"]["valid"] and b["slot"] in {x["slot"] for x in interior}) for d in days]
    interior_rank=percentile(sum(b["volume"] for b in interior),interior_history)
    edges_only=volume_rank is not None and volume_rank>=0.8 and (interior_rank is None or interior_rank<0.8)
    largest=volume_rank is not None and volume_rank>=0.8 and (trimmed_rank is None or trimmed_rank<0.8)
    proxy_candidate=(len(recoveries)>=2 and volume_rank is not None and volume_rank>=0.8 and not edges_only and not largest)
    retained=bool(support and slots and slots[-1]["close"]>=support["upper"])
    proxy={"available":len(days)>=20 and bool(support),"candidate":proxy_candidate,"response":proxy_candidate and retained,
        "value":(evidence(volume_rank)+float(len(recoveries)>=2)+float(retained))/3 if proxy_candidate else 0,
        "rvol":rvol,"percentile":volume_rank,"recoveries":recoveries,"approximation":"分钟量价承接代理"}
    def cumulative_return(rows,minute_rows,label,stop):
        preceding=sorted([b for b in rows if b["timestamp"][:10]<label and valid_bar(b) and (not b.get("received_at") or instant(b["received_at"])<=captured)],key=lambda b:b["timestamp"])
        last=minute_rows.get(stop-timedelta(minutes=1))
        return log(float(last["close"])/float(preceding[-1]["close"])) if preceding and last else None
    current_returns={name:cumulative_return(snapshot.get(dkey,[]),m,current_date,end) for name,dkey,m in
        (("stock","daily",mapping),("market","market_daily",market_map),("sector","sector_daily",sector_map))}
    if not coverage(market_map,start,end)["valid"]: current_returns["market"]=None
    if not coverage(sector_map,start,end)["valid"]: current_returns["sector"]=None
    window_history=[]
    for day in days:
        if not coverage(market_map,day["open"],day["end"])["valid"]: continue
        values={name:cumulative_return(snapshot.get(dkey,[]),m,day["date"],day["end"]) for name,dkey,m in
            (("stock","daily",mapping),("market","market_daily",market_map),("sector","sector_daily",sector_map))}
        if values["stock"] is not None and values["market"] is not None: window_history.append(values)
    def known_daily(key):
        return [b for b in snapshot.get(key,[]) if valid_bar(b) and (not b.get('received_at') or instant(b['received_at'])<=captured)]
    relative=relative_strength(daily,known_daily("market_daily"),known_daily("sector_daily"),current_returns,current_date,window_history,total_minutes/390,[s['date'] for s in session_rows])
    active,passive,flow_ids,pending=flow_evidence(snapshot,days,current,slots,support,relative)
    modes={"active_buying":active,"passive_absorption":passive,"price_volume":proxy}
    dominant=max(modes,key=lambda name:modes[name]["value"] if modes[name]["available"] else -1)
    previous_dominant=(snapshot.get("state_before") or {}).get("last_valid_dominant")
    if modes[dominant]["value"]<=0 and previous_dominant in modes and modes[previous_dominant]["available"]:
        dominant=previous_dominant
    core=modes[dominant]
    core_value=core["value"] if core["available"] else 0
    response=bool(core.get("response"))
    neff=effective_count([b["volume"] for b in good])
    neff_history=[effective_count([b["volume"] for b in d["slots"] if b["quality"]["valid"]]) for d in days]
    wap_known=bool(good) and all(b["wap"] is not None for b in good)
    vwap=sum(b["wap"]*b["volume"] for b in good)/volume if wap_known and volume else None
    path=[]; cumulative_volume=cumulative_notional=0.0
    if wap_known:
        for b in good:
            cumulative_volume+=b['volume']; cumulative_notional+=b['wap']*b['volume']
            if cumulative_volume:
                path.append({'at':b['end'],'vwap':cumulative_notional/cumulative_volume,'close':b['close']})
    above=sum(b['close']>=b['vwap'] for b in path)/len(path) if path else None
    # The two fixed subweights remain in the denominator when WAP is missing.
    distribution=(evidence(percentile(neff,neff_history))+(above or 0))/2
    down_rank=percentile(sum(b["volume"] for b in slots[-2:]),
        [sum(b["volume"] for b in day["slots"][-2:]) for day in days])
    invalidation_reference=(snapshot.get("state_before") or {}).get("invalidation_reference") or support
    _,broken=support_events(slots,invalidation_reference)
    invalidation=broken and down_rank is not None and down_rank>=0.8
    penalties=[]
    if core.get("opposite"): penalties.append("opposite_response")
    if largest: penalties.append("largest_slot")
    if edges_only: penalties.append("edges_only")
    quality.update({"valid":not reasons,"reasons":reasons,"baseline_days":len(days),"daily_days":len(daily_dates),
        "independent_hours":independent,"flow_available":active["available"] or passive["available"],
        "event_checked":bool(context_known and context.get("event_review_through") and
            instant(context["event_review_through"])>=end and context.get("effective_session")==current_date),
        "major_event":bool(context_known and context.get("major_event")),"event_notes":context.get("event_notes","") if context_known else "",
        "unit_verified":bool(context_known and context.get("units_verified"))})
    ids=[f"support:{x['at']}" for x in recoveries]+flow_ids
    return {"session_date":current_date,"session_open":start.isoformat(),"session_close":current_label["close"],
        "closed":end>=instant(current_label["close"]),"quality":quality,"support":support,
        "slots":slots,"hours":hours,"modes":modes,"dominant_evidence":dominant,
        "core_behavior":{"available":core["available"],"value":core_value,"established":bool(core.get("candidate"))},
        "price_response":{"available":bool(good),"value":float(response),"confirmed":response},
        "relative_strength":relative,"distribution":{"available":len(days)>=20,"value":distribution,
            "vwap":vwap,"vwap_path":path,"time_above_vwap":above,"wap_available":wap_known,"effective_slots":neff},
        "evidence_ids":sorted(set(ids)),"pending":pending,"penalties":penalties,"invalidation":invalidation,"invalidation_reference":invalidation_reference}
