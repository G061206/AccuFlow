"""Causal bar-derived evidence. No institutional identity or holdings inputs."""
from collections import defaultdict
from datetime import timedelta
from math import log
from statistics import mean, median

from accuflow.features.bars import aggregate, coverage, effective_count, instant, minute_map
from accuflow.features.relative import robust_fit
from accuflow.services.quality import valid_bar
from accuflow.services.calendar import calendar_for
from accuflow.scoring.accumulation_v2 import clip, evidence, mapped, percentile


def daily_map(rows, captured):
    result = {}
    conflicts = set()
    for row in rows:
        day = row["timestamp"][:10]
        if not valid_bar(row) or (row.get("received_at") and instant(row["received_at"]) > captured):
            continue
        if day in result and result[day] != row:
            conflicts.add(day)
        result[day] = row
    return {d: r for d, r in result.items() if d not in conflicts}


def fit_reference(own, market, sector, day, config):
    earliest = min(own, default=day)
    if earliest >= day:
        return None
    labels = [x.date().isoformat() for x in calendar_for(int(day[:4])).sessions_in_range(earliest, day)]
    records = []
    for a, b in zip(labels, labels[1:]):
        if b >= day:
            break
        if all(a in rows and b in rows for rows in (own, market)):
            ret = lambda rows: log(float(rows[b]["close"]) / float(rows[a]["close"]))
            records.append((b, ret(own), ret(market), ret(sector) if a in sector and b in sector else None))
    records = records[-config["regression_history"]:]
    if len(records) < config["min_regression_history"]:
        return None
    use_sector = all(r[3] is not None for r in records)
    x = [[1., r[2]] + ([r[3]] if use_sector else []) for r in records]
    beta = robust_fit(x, [r[1] for r in records])
    return {"coefficients": beta, "sector": use_sector, "fitted_through": records[-1][0], "samples": len(records)} if beta else None


def residual(own_return, market_return, sector_return, fit, fraction):
    if fit is None or market_return is None or (fit["sector"] and sector_return is None):
        return None
    beta = fit["coefficients"]
    return own_return - beta[0] * fraction - beta[1] * market_return - (beta[2] * sector_return if fit["sector"] else 0.)


def direction(slots):
    if not slots or any(s["wap"] is None for s in slots):
        return [None] * 3
    total = sum(s["volume"] for s in slots)
    if total <= 0:
        return [None] * 3
    clv = sum(s["clv"] * s["volume"] for s in slots) / total
    weights = [clip(s["v"], 0, 3) for s in slots if s["wap_change"] is not None]
    weighted = [clip(s["v"], 0, 3) * clip(s["wap_change"] / s["scale"], -1, 1)
                for s in slots if s["wap_change"] is not None]
    participation = sum(weighted) / sum(weights) if sum(weights) else 0.
    positive = [max(s["clv"], 0) * s["volume"] for s in slots]
    distribution = effective_count(positive) / len(slots) * max(0., (clv + participation) / 2)
    return [clv, participation, distribution]


def build_day(label, own_map, market_map, sector_map, history, daily, market_daily, sector_daily, config):
    opened, closed = instant(label["open"]), instant(label["close"])
    day = label["date"]
    q = coverage(own_map, opened, closed)
    mq = coverage(market_map, opened, closed)
    sq = coverage(sector_map, opened, closed)
    slots = aggregate(own_map, opened, closed, 5)
    market = {s["slot"]: s for s in aggregate(market_map, opened, closed, 5) if s["quality"]["valid"]}
    sector = {s["slot"]: s for s in aggregate(sector_map, opened, closed, 5) if s["quality"]["valid"]}
    fit = fit_reference(daily, market_daily, sector_daily if sq["valid"] else {}, day, config)
    baseline = [d for d in history[-config["minute_history"]:] if d["quality"]["bars_valid"]]
    reasons = []
    if not q["valid"]: reasons.append("stock_minute_coverage")
    if not mq["valid"]: reasons.append("market_minute_coverage")
    if fit is None: reasons.append("reference_history")
    good = [s for s in slots if s["quality"]["valid"] and s["volume"] > 0]
    if not good or any(s["wap"] is None for s in good): reasons.append("wap_missing")
    prepared = []
    for i, slot in enumerate(good):
        reference = [s for d in baseline for s in d["slots"] if s["slot"] == slot["slot"]]
        volumes = [s["volume"] for s in reference]
        med_volume = median(volumes) if volumes else 0
        raw_return = log(slot["close"] / slot["open"])
        own_returns = [s["raw_return"] for s in reference]
        center = median(own_returns) if own_returns else 0.
        scale = max(config["tick_size"] / slot["close"], 1.4826 * median([abs(r - center) for r in own_returns])) if own_returns else config["tick_size"] / slot["close"]
        m, sec = market.get(slot["slot"]), sector.get(slot["slot"])
        rr = residual(raw_return, log(m["close"] / m["open"]) if m else None,
                      log(sec["close"] / sec["open"]) if sec else None, fit, 5 / 390)
        preceding = good[i-1] if i and good[i-1]["slot"] == slot["slot"] - 5 else None
        change = log(slot["wap"] / preceding["wap"]) if preceding and preceding["wap"] and slot["wap"] else None
        prepared.append(slot | {"raw_return": raw_return, "residual": rr, "scale": scale,
            "clv": (2 * slot["close"] - slot["high"] - slot["low"]) / (slot["high"] - slot["low"]) if slot["high"] > slot["low"] else 0.,
            "v": slot["volume"] / med_volume if med_volume else 0., "wap_change": change,
            "baseline_samples": len(volumes)})
    if not prepared or any(s["baseline_samples"] < config["min_minute_history"] for s in prepared):
        reasons.append("same_slot_history")
    events, confirmed, pending, unmatched, failures = [], [], [], 0, []
    previous_events = [e for d in baseline for e in d["sell_events"]]
    next_event_slot = -1
    for i, slot in enumerate(prepared):
        if slot["residual"] is None:
            continue
        if slot["residual"] < 0 and slot["v"] >= 1:
            events.append({"slot": slot["slot"], "v": slot["v"], "impact": -slot["residual"] / slot["scale"]})
        following = prepared[i+1:i+1+config["response_slots"]]
        complete = len(following) == config["response_slots"] and all(s["slot"] == slot["slot"] + 5 * (j+1) for j, s in enumerate(following))
        if not complete:
            if slot["residual"] < 0 and slot["v"] >= 1:
                pending.append(slot["end"])
            continue
        last = following[-1]
        m0, m1 = market.get(slot["slot"]), market.get(last["slot"])
        s0, s1 = sector.get(slot["slot"]), sector.get(last["slot"])
        after = residual(log(last["close"] / slot["open"]), log(m1["close"] / m0["open"]) if m0 and m1 else None,
                         log(s1["close"] / s0["open"]) if s0 and s1 else None, fit, 35 / 390)
        if after is None:
            continue
        if slot["clv"] > 0:
            failures.append(max(0., -after / slot["scale"]) * slot["clv"])
        if slot["residual"] >= 0 or slot["v"] < 1 or slot["slot"] < next_event_slot:
            continue
        next_event_slot = last["slot"] + 5
        matches = [e for e in previous_events if abs(e["slot"] // 60 - slot["slot"] // 60) <= 1
                   and abs(log(max(e["v"], 1e-12) / slot["v"])) <= config["match_log_volume_distance"]]
        matches.sort(key=lambda e: (abs(log(e["v"] / slot["v"])), abs(e["slot"] - slot["slot"])))
        if len(matches) < config["matched_events"]:
            unmatched += 1
            continue
        expected = median(e["impact"] for e in matches[:config["max_matches"]])
        impact = clip((expected + slot["residual"] / slot["scale"]) / expected) if expected > 0 else 0.
        recovery = clip((after - slot["residual"]) / max(-slot["residual"], slot["scale"]))
        confirmed.append({"at": slot["start"], "confirmed_at": last["end"], "matched": len(matches),
                          "impact": impact, "recovery": recovery, "value": (impact + recovery) / 2 if impact > 0 and recovery > 0 else 0.})
    b = mean(e["value"] for e in confirmed) if confirmed else 0.
    if unmatched > 0 and len(confirmed) / (len(confirmed) + unmatched) < .8:
        b = None
    def wap(ss):
        vol = sum(s["volume"] for s in ss)
        return sum(s["wap"] * s["volume"] for s in ss) / vol if vol and all(s["wap"] for s in ss) else None
    own_wap, market_wap, sector_wap = wap(prepared), wap(list(market.values())), wap(list(sector.values()))
    previous = history[-1] if history else None
    wap_residual = None
    if previous and previous["quality"]["bars_valid"] and own_wap and previous["wap"] and market_wap and previous["market_wap"]:
        sr = log(sector_wap / previous["sector_wap"]) if sector_wap and previous["sector_wap"] else None
        wap_residual = residual(log(own_wap / previous["wap"]), log(market_wap / previous["market_wap"]), sr, fit, 1.)
    past = [daily[d] for d in sorted(daily) if d < day][-21:]
    atr = median([max(float(b["high"])-float(b["low"]), abs(float(b["high"])-float(a["close"])), abs(float(b["low"])-float(a["close"])))
                  for a,b in zip(past[-15:-1],past[-14:])]) if len(past) >= 15 else None
    frozen = median(float(b["close"]) for b in past[-20:]) - atr if atr and len(past) >= 20 else None
    a = direction(prepared)
    largest = max(prepared, key=lambda s:s["volume"], default=None)
    trimmed = direction([s for s in prepared if s is not largest])
    interior = direction([s for s in prepared if 15 <= s["slot"] < int((closed-opened).total_seconds()/60)-15])
    raw = {"direction": a, "absorption": [b], "retention": [wap_residual],
           "direction_trim": trimmed, "direction_interior": interior,
           "failed_response": [mean(failures) if failures else 0.]}
    if reasons:
        raw = {k: [None] * len(v) for k, v in raw.items()}
    return {"date": day, "as_of": closed.isoformat(), "quality": {"bars_valid":q["valid"], "valid":not reasons,
            "reasons":reasons, "coverage":q, "reference": "market_and_sector" if fit and fit["sector"] else "market_only"},
            "raw":raw, "slots":prepared, "sell_events":events, "absorption_events":confirmed, "pending":pending,
            "unmatched_events":unmatched, "fit":fit, "wap":own_wap, "market_wap":market_wap,
            "sector_wap":sector_wap, "close":prepared[-1]["close"] if prepared else None,
            "invalidation_floor":frozen, "volume":sum(s["volume"] for s in prepared)}


def build_series(data, symbol, labels, captured, config, progress=None):
    """One pass over raw data, using only preceding observations for each feature."""
    if not labels:
        return []
    opened, ended = instant(labels[0]["open"]), instant(labels[-1]["close"])
    maps, daily = {}, {}
    for key, name in (("own",symbol),("market","SPY"),("sector", data.get("sector_symbol"))):
        rows = data.get(name, {}) if name else {}
        maps[key], _ = minute_map(rows.get("minute",[]), opened, min(ended,captured), captured)
        daily[key] = daily_map(rows.get("daily",[]), captured)
    history = []
    for label in labels:
        if instant(label["close"]) > captured:
            break
        row = build_day(label, maps["own"], maps["market"], maps["sector"], history,
                        daily["own"], daily["market"], daily["sector"], config)
        history.append(row)
        if progress and len(history) % 25 == 0:
            progress(f"V2 features through {label['date']} ({len(history)}/{len(labels)})")
    # Normalize each daily observation causally before constructing persistence.
    for i, row in enumerate(history):
        past = [d for d in history[max(0,i-config["feature_history"]):i] if d["quality"]["valid"]]
        local = {}
        for name in ("direction","absorption"):
            values = row["raw"][name]
            refs = [[d["raw"][name][j] for d in past if d["raw"][name][j] is not None] for j in range(len(values))]
            local[name] = mapped(values, refs, config["min_day_history"])
        u = max(v for v in local.values() if v is not None) if local["direction"] is not None else None
        u_history = [d["u"] for d in past if d.get("u") is not None]
        rank = percentile(u,u_history) if len(u_history) >= config["min_day_history"] else None
        row.update(local=local, u=u, supported=bool(u is not None and u>0 and rank is not None and rank>=config["support_percentile"]), support_available=rank is not None)
    return history


def compact_day(row):
    return {k:v for k,v in row.items() if k not in ("slots","sell_events")}


def window_raw(days, end, width, name):
    start = end-width+1
    if start < 0:
        return None
    rows = days[start:end+1]
    if not all(d["quality"]["valid"] for d in rows):
        return None
    if name == "persistence":
        if any(d.get("u") is None or not d["support_available"] for d in rows):
            return None
        u = [d["u"] for d in rows]
        return [mean(u), mean(float(d["supported"]) for d in rows), effective_count(u)/width]
    if name == "retention":
        values = [d["raw"][name][0] for d in rows]
        if any(v is None for v in values):
            return None
        total = peak = 0.
        for v in values:
            total += v
            peak = max(peak,total)
        scale = max(1e-6, median(abs(v) for v in values))
        return [mean(values), clip(total/peak) if peak>scale else 0.]
    values = [d["raw"][name] for d in rows]
    if any(v is None for row in values for v in row):
        return None
    return [mean(row[j] for row in values) for j in range(len(values[0]))]


def extract(days, config):
    end = len(days)-1
    families = {}
    for name in ("direction","absorption","retention","persistence","direction_trim","direction_interior","failed_response"):
        windows = {}
        total = 0.
        available = True
        for text in sorted(config["windows"], key=int):
            weight = config["windows"][text]
            width = int(text)
            current = window_raw(days,end,width,name)
            stop = end-width+1  # Historical window MUST end before current window starts.
            refs = [window_raw(days,j,width,name) for j in range(max(width-1,stop-config["feature_history"]),stop)]
            refs = [r for r in refs if r is not None]
            value = mapped(current, [list(x) for x in zip(*refs)], config["min_feature_history"]) if current is not None and refs else None
            if value is None:
                available = False
            else:
                total += weight*value
            windows[text] = {"value":value, "raw":current, "samples":len(refs),
                             "baseline_end_before": days[stop]["date"] if stop>=0 else None}
        families[name] = {"available":available, "value":total if available else None, "windows":windows}
    a, trim, interior = (families[k] for k in ("direction","direction_trim","direction_interior"))
    concentration = max(clip((a["value"]-x["value"])/max(a["value"],1e-9)) for x in (trim,interior)) if all(x["available"] for x in (a,trim,interior)) else None
    failure = families["failed_response"]
    penalties = {"failed_response":failure["value"] if failure["available"] else None,"concentration":concentration}
    return {k:families[k] for k in config["weights"]}, penalties, {k:families[k] for k in ("direction_trim","direction_interior")}
