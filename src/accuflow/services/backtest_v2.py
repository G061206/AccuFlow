"""Isolated v2 research: no 13F, database writes, business state or delivery."""
import asyncio
import csv
import gzip
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, median

from accuflow.detectors.accumulation_v2 import evaluate_v2
from accuflow.features.accumulation_v2 import build_series, compact_day
from accuflow.features.bars import instant
from accuflow.scoring.accumulation_v2 import canonical, manifest, verify_manifest
from accuflow.services.backtest import make_plan, load_bars, download_history, review_context, forward_performance
from accuflow.services.calendar import calendar_for, sessions


def digest_file(path):
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan(start,end,captured,config):
    _,targets=make_plan(start,end,captured)
    # Raw-minute dependencies: slot baseline + two daily normalizations + two
    # largest windows (nonoverlapping reference) + target rolling reference count.
    warm=(config["min_minute_history"]+2*config["min_day_history"]+
          2*max(map(int,config["windows"]))+config["feature_history"])
    cal=calendar_for(start.year)
    first=cal.sessions_in_range(start,end)[0]
    older=cal.sessions_in_range(cal.session_offset(first,-warm),cal.session_offset(first,-1))
    labels=[{"date":d.date().isoformat(),"open":cal.session_open(d).to_pydatetime(),
             "close":cal.session_close(d).to_pydatetime()} for d in older]+targets
    return labels,targets


def serial_labels(labels):
    return [{k:v.isoformat() if hasattr(v,"isoformat") else v for k,v in s.items()} for s in labels]


def performance(rows):
    result={}
    for h in ("5","10","20"):
        outcomes=[r["forward"][h] for r in rows if r["forward"][h]["available"]]
        values=[r["return_pct"] for r in outcomes]
        excess=[r["excess_return_pp"] for r in outcomes if r["excess_return_pp"] is not None]
        result[h]={"samples":len(values),"mean_return_pct":mean(values) if values else None,
                   "median_return_pct":median(values) if values else None,
                   "positive_fraction":mean(v>0 for v in values) if values else None,
                   "mean_excess_pp":mean(excess) if excess else None}
    return result


def report(output,summary,rows):
    (output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    if not rows:
        (output/"report-zh.md").write_text("# V2 研究未运行\n\n"+summary.get("reason","没有输入数据")+"\n",encoding="utf-8")
        return
    fields=["date","valid","score_lower","score_upper","status","first_signal","support_days_5","support_days_10","reasons"]
    with (output/"daily.csv").open("w",encoding="utf-8-sig",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row|{"reasons":";".join(row["reasons"])})
    (output/"daily.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    lines=["# V2 连续买方需求研究结果","",f"模型：{summary['model_version']}；阈值 {summary['threshold']}（固定实验值，未校准）。",
           f"区间：{summary['start']} 至 {summary['end']}。", "",
           f"目标 {summary['target_sessions']} 日；可评分 {summary['valid_sessions']} 日；首次候选 {summary['signal_episodes']} 段。",
           f"最高分下界：{summary['max_score_lower']}；首次可评分日期：{summary['first_valid_date']}。", "",
           "本轮未使用 13F，没有机构识别准确率结论。行情为事后下载的历史版本；结果用于验证实现与观察证据分布。",
           "缺失字段产生分数范围；该范围不是概率或统计置信区间。未收盘与历史不足不当作正常无信号。", "",
           "## 候选后的价格表现", "", "价格从下一交易日开盘起算，不含分红、费用、滑点，不是持仓策略收益。", "",
           "|交易日|样本|平均价格收益 %|平均相对 SPY 差（百分点）|", "|---|---:|---:|---:|"]
    for h,v in summary["signal_performance"].items():
        number=lambda x: "无样本" if x is None else f"{x:.3f}"
        lines.append(f"|{h}|{v['samples']}|{number(v['mean_return_pct'])}|{number(v['mean_excess_pp'])}|")
    lines += ["", "## 质量原因", "", "```json",json.dumps(summary["quality_reasons"],ensure_ascii=False,indent=2),"```", "",
              "## 首次候选", "", "|日期|分数下界|近5日支持数|近10日支持数|", "|---|---:|---:|---:|"]
    for r in rows:
        if r["first_signal"]:
            lines.append(f"|{r['date']}|{r['score_lower']}|{r['support_days_5']}|{r['support_days_10']}|")
    lines += ["", "原始输入、每日特征与检查点分别归档；运行 replay-v2 可从原始行情重建特征，再逐个重放状态。", ""]
    (output/"report-zh.md").write_text("\n".join(lines),encoding="utf-8")


def run_study_v2(data,symbol,labels,targets,captured,review,output,config,progress=print):
    verify_manifest(config)
    labels=serial_labels(labels)
    target_dates={s["date"] for s in targets}
    package={"data":data,"symbol":symbol,"sessions":labels,"captured_at":captured.isoformat(),"review":review,
             "config":config,"target_dates":sorted(target_dates)}
    with gzip.open(output/"inputs.json.gz","wt",encoding="utf-8",compresslevel=1) as handle:
        json.dump(package,handle,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)
    all_days=build_series(data,symbol,labels,captured,config,progress)
    days=[compact_day(d) for d in all_days]
    del all_days
    feature_ids=[]
    with gzip.open(output/"features.jsonl.gz","wt",encoding="utf-8",compresslevel=1) as handle:
        for day in days:
            ident=hashlib.sha256(canonical(day).encode()).hexdigest()
            feature_ids.append(ident)
            handle.write(canonical({"id":ident,"day":day})+"\n")
    state=None
    rows=[]
    snapshots=0
    latest=sessions(captured,1)[-1]["date"]
    with gzip.open(output/"snapshots.jsonl.gz","wt",encoding="utf-8",compresslevel=1) as handle:
        for i,day in enumerate(days):
            context=review_context(review,{"date":day["date"],"close":instant(day["as_of"])},captured)
            event=review.get("events",{}).get(day["date"],{})
            if event.get("known_at"):
                context["event_known_at"]=event["known_at"]
            snap={"feature_schema":"v2-daily-1","manifest_hash":config["manifest_hash"],"symbol":symbol,
                  "instrument_key":f"symbol:{symbol}","as_of":day["as_of"],"captured_at":captured.isoformat(),
                  "data_vintage":"historical_reconstruction","days":days[:i+1],"sessions":labels[:i+1],
                  "context":context,"state_before":state,"flow_windows":[]}
            result=evaluate_v2(snap,config)
            stored={k:v for k,v in snap.items() if k!="days"}
            stored["feature_ids"]=feature_ids[:i+1]
            handle.write(canonical({"snapshot":stored,"result":result})+"\n")
            state=result["state_after"]
            snapshots+=1
            if progress and (i+1)%25==0:
                progress(f"V2 evaluated {day['date']} ({i+1}/{len(days)})")
            if day["date"] not in target_dates:
                continue
            rows.append({"date":day["date"],"valid":result["quality"]["valid"],"reasons":result["quality"]["reasons"],
                         "score_lower":result["score_lower"],"score_upper":result["score_upper"],"status":result["status"],
                         "first_signal":any(e["event_type"]=="candidate" for e in result["events"]),
                         "families":{k:v["value"] for k,v in result["families"].items()},"penalties":result["penalties"],
                         "base_coverage":result["quality"]["base_coverage"],"episode_id":state["episode_id"],
                         "flow_verdict":result["flow_verdict"],"event_context":result["event_context"],
                         "support_days_5":result["support_days_5"],"support_days_10":result["support_days_10"],
                         "forward":forward_performance(day["date"],data[symbol]["daily"],data["SPY"]["daily"],latest,horizons=(5,10,20))})
    valid=[r for r in rows if r["valid"]]
    signals=[r for r in rows if r["first_signal"]]
    summary={"status":"completed" if len(valid)==len(rows) else "needs_review","model_version":config["version"],
             "manifest_hash":config["manifest_hash"],"threshold":config["threshold"],"calibration":config["calibration"],
             "symbol":symbol,"start":targets[0]["date"],"end":targets[-1]["date"],"target_sessions":len(targets),
             "valid_sessions":len(valid),"first_valid_date":valid[0]["date"] if valid else None,"signal_episodes":len(signals),
             "max_score_lower":max((r["score_lower"] for r in valid),default=None),"snapshots":snapshots,
             "quality_reasons":dict(Counter(reason for r in rows for reason in r["reasons"])),
             "signal_performance":performance(signals),"holdings_validation":"not_requested", "data_vintage":"historical_reconstruction",
             "archive_sha256":{name:digest_file(output/name) for name in ("inputs.json.gz","features.jsonl.gz","snapshots.jsonl.gz")}}
    report(output,summary,rows)
    (output/"manifest.json").write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


async def run_backtest_v2(settings, *, symbol,start,end,cache,output,review=None,download=False,threshold=65):
    config=manifest(threshold)
    captured=datetime.now(UTC)
    labels,targets=plan(start,end,captured,config)
    if symbol=="SPY": raise ValueError("target must differ from SPY")
    output.mkdir(parents=True,exist_ok=False)
    if download:
        await download_history(settings,cache,(symbol,"SPY"),labels,outcome_sessions=20,
                               progress=lambda message:print(message,flush=True))
    data=load_bars(cache,(symbol,"SPY"))
    if any(not data[s][key] for s in (symbol,"SPY") for key in ("daily","minute")):
        summary={"status":"blocked","reason":"目标及 SPY 缺少真实日线或分钟行情；未生成替代数据。"}
        report(output,summary,[])
        return summary
    return await asyncio.to_thread(run_study_v2,data,symbol,labels,targets,datetime.now(UTC),review or {},output,config,
                                   lambda message:print(message,flush=True))


def replay_v2(output,rebuild=True,progress=print):
    output=Path(output)
    summary=json.loads((output/"summary.json").read_text(encoding="utf-8"))
    for name,sha in summary["archive_sha256"].items():
        if name not in ("inputs.json.gz","features.jsonl.gz","snapshots.jsonl.gz") or digest_file(output/name)!=sha:
            raise ValueError("v2 archive checksum mismatch")
    with gzip.open(output/"inputs.json.gz","rt",encoding="utf-8") as handle:
        package=json.load(handle)
    config=package["config"]
    verify_manifest(config)
    records={}
    with gzip.open(output/"features.jsonl.gz","rt",encoding="utf-8") as handle:
        for line in handle:
            row=json.loads(line)
            if hashlib.sha256(canonical(row["day"]).encode()).hexdigest()!=row["id"]:
                raise ValueError("v2 feature checksum mismatch")
            records[row["id"]]=row["day"]
    if rebuild:
        rebuilt=build_series(package["data"],package["symbol"],package["sessions"],instant(package["captured_at"]),config,progress)
        expected=[hashlib.sha256(canonical(compact_day(d)).encode()).hexdigest() for d in rebuilt]
        if expected!=list(records): raise ValueError("raw-to-feature rebuild mismatch")
    count=0
    state=None
    with gzip.open(output/"snapshots.jsonl.gz","rt",encoding="utf-8") as handle:
        for line in handle:
            row=json.loads(line)
            snap=row["snapshot"]
            snap["days"]=[records[i] for i in snap.pop("feature_ids")]
            if snap["state_before"]!=state: raise ValueError("v2 episode continuity mismatch")
            result=evaluate_v2(snap,config)
            if result!=row["result"]: raise ValueError("v2 replay mismatch")
            state=result["state_after"]
            count+=1
            if progress and count%50==0: progress(f"V2 replayed {count} snapshots")
    answer={"matches":True,"snapshots":count,"rebuilt_from_raw":rebuild,"manifest_hash":config["manifest_hash"]}
    (output/"replay-verification.json").write_text(json.dumps(answer,indent=2),encoding="utf-8")
    return answer
