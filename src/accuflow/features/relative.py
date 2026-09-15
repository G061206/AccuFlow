"""Small ridge/Huber regression fitted only on prior completed sessions."""
from math import log
from accuflow.services.quality import valid_bar
from statistics import median

from accuflow.scoring.rules import RULES, percentile, evidence


def solve(matrix, target):
    rows=[list(a)+[b] for a,b in zip(matrix,target)]
    n=len(rows)
    for col in range(n):
        pivot=max(range(col,n),key=lambda i:abs(rows[i][col]))
        rows[col],rows[pivot]=rows[pivot],rows[col]
        divisor=rows[col][col]
        if abs(divisor)<1e-14: return None
        rows[col]=[x/divisor for x in rows[col]]
        for i in range(n):
            if i!=col:
                factor=rows[i][col]
                rows[i]=[a-factor*b for a,b in zip(rows[i],rows[col])]
    return [r[-1] for r in rows]


def robust_fit(x,y):
    weights=[1.0]*len(y); coefficients=None
    for _ in range(RULES["relative_huber_iterations"]):
        width=len(x[0])
        matrix=[[sum(w*row[i]*row[j] for w,row in zip(weights,x))+
                 (RULES["relative_ridge"] if i==j and i>0 else 0)
                 for j in range(width)] for i in range(width)]
        target=[sum(w*row[i]*value for w,row,value in zip(weights,x,y)) for i in range(width)]
        coefficients=solve(matrix,target)
        if coefficients is None: return None
        residuals=[value-sum(a*b for a,b in zip(row,coefficients)) for row,value in zip(x,y)]
        center=median(residuals); scale=max(1e-6,1.4826*median(abs(r-center) for r in residuals))
        weights=[min(1.0,1.345*scale/max(abs(r-center),1e-12)) for r in residuals]
    return coefficients


def daily_returns(rows, session_date, session_dates=None):
    closes={b["timestamp"][:10]:float(b["close"]) for b in rows
            if b["timestamp"][:10]<session_date and valid_bar(b)}
    days=sorted(closes) if session_dates is None else [d for d in session_dates if d<session_date]
    return {day:log(closes[day]/closes[previous]) for previous,day in zip(days,days[1:]) if previous in closes and day in closes},closes


def relative_strength(daily, market_daily, sector_daily, current_returns, session_date, window_history, fraction=1.0, session_dates=None):
    own,own_closes=daily_returns(daily,session_date,session_dates)
    market,_=daily_returns(market_daily,session_date,session_dates)
    sector,_=daily_returns(sector_daily,session_date,session_dates)
    dates=sorted(set(own)&set(market))[-60:]
    unavailable={"available":False,"value":0.0,"reason":"大盘基线或同步窗口不足","coefficients":None}
    if len(dates)<60 or current_returns.get("stock") is None or current_returns.get("market") is None:
        return unavailable
    sector_dates=sorted(set(dates)&set(sector))
    use_sector=len(sector_dates)==60 and current_returns.get("sector") is not None
    x=[[1.0,market[day]]+([sector[day]] if use_sector else []) for day in dates]
    y=[own[day] for day in dates]
    coefficients=robust_fit(x,y)
    if coefficients is None: return unavailable
    current=[fraction,current_returns["market"]]+([current_returns["sector"]] if use_sector else [])
    residual=current_returns["stock"]-sum(a*b for a,b in zip(current,coefficients))
    history=[row["stock"]-sum(a*b for a,b in zip([fraction,row["market"]]+([row["sector"]] if use_sector else []),coefficients))
             for row in window_history if row.get("market") is not None and (not use_sector or row.get("sector") is not None)]
    if len(history)<RULES["min_baseline_days"]: return unavailable
    rank=percentile(residual,history)
    return {"available":True,"value":evidence(rank),"residual":residual,"percentile":rank,
        "coefficients":coefficients,"fitted_through":dates[-1],"samples":len(dates),
        "reference":"market_and_sector" if use_sector else "market_only",
        "history_scale":max(1e-6,median(abs(v-median(history)) for v in history)*1.4826)}
