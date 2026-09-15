"""Deterministic multipart templates. No remote assets, scripts or tracking pixels."""
import hashlib
import json
from datetime import datetime
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime
from html import escape

VERSION="mail-v1"
LABELS={"new_anomaly":"新异动","strengthened":"持续迹象增强","enhanced":"新增增强证据",
        "waning":"迹象减弱","invalidated":"结构失效"}


def report_content(report, inputs, events):
    results=[x.get('detection',{}).get('result') for x in inputs]
    failed=any('采集失败' in text for text in report.counter_evidence)
    insufficient=any(not r or not r['quality']['valid'] for r in results)
    if failed: outcome='运行失败'; conclusion='今日部分采集或检测失败，无法完整评估。'
    elif insufficient: outcome='数据不足'; conclusion='今日无法完整评估，缺失数据不代表没有异动。'
    elif events: outcome='正常异动'; conclusion='检测到满足规则的状态变化，详见各股票证据。'
    else: outcome='无新异动'; conclusion='今日未发现满足规则的新异动，既有状态见下表。'
    rows=[]
    for item,r in zip(inputs,results):
        if not r: continue
        rows.append(f"{item['symbol']}：{r['score'] if r['score'] is not None else '不评分'} · {r['status_label']} · 支持 {r['families']['cross_day']['support_days']}/5 日")
    event_lines=[f"{e['symbol']}：{LABELS.get(e['event_type'],e['event_type'])}" for e in sorted(events,key=lambda x:x['event_id'])]
    subject=f"[AccuFlow][{report.report_type}][{outcome}] {report.report_time.date()}"
    sections=[('结论',[conclusion,report.judgment]),('股票状态',rows),('状态变化',event_lines),
              ('支持证据',report.evidence),('反证与限制',report.counter_evidence),('数据质量',[report.data_quality])]
    return subject,sections


def render(identity, subject, sections, as_of, sender, recipients):
    message_id=f"<{identity}.{VERSION}@accuflow.local>"
    footer=f"数据截至 {as_of} · 模板 {VERSION}。规则分不是概率，不构成投资建议。"
    body='\n\n'.join(title+'\n'+'\n'.join(lines or ['无']) for title,lines in sections)+'\n\n'+footer
    html='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body style="font-family:Arial,sans-serif;color:#202020;max-width:760px;margin:auto;padding:24px">'
    html+='<h1 style="font-size:22px;color:#b51218">'+escape(subject)+'</h1>'
    for title,lines in sections:
        html+='<h2 style="font-size:16px">'+escape(title)+'</h2><ul>'
        html+=''.join('<li style="margin:8px 0;overflow-wrap:anywhere">'+escape(str(line))+'</li>' for line in (lines or ['无']))+'</ul>'
    html+='<hr><p>'+escape(footer)+'</p></body></html>'
    message=EmailMessage(policy=SMTP)
    message['Subject']=subject;message['From']=sender or 'preview@accuflow.invalid'
    message['To']=', '.join(recipients) or 'preview@accuflow.invalid'
    message['Date']=format_datetime(datetime.fromisoformat(as_of))
    message['Message-ID']=message_id
    message.set_content(body,cte='quoted-printable')
    message.add_alternative(html,subtype='html',cte='quoted-printable')
    message.set_boundary('accuflow-'+identity)
    return {'subject':subject,'body':body,'html':html,'message_id':message_id,'mime':message.as_bytes()}


def transport_hash(settings):
    return hashlib.sha256(json.dumps([settings.smtp_host,settings.smtp_port,settings.smtp_security,
        settings.smtp_username],separators=(',',':')).encode()).hexdigest()
