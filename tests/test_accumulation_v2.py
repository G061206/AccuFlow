"""Causality and signal mechanics, not evidence of investment performance."""
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from math import sin
import json

import pytest

from accuflow.scoring.accumulation_v2 import manifest, score_interval, verify_manifest
from accuflow.features.accumulation_v2 import build_series, compact_day, extract
from accuflow.detectors.accumulation_v2 import evaluate_v2
from accuflow.services.backtest_v2 import plan, replay_v2, run_study_v2, run_backtest_v2
from accuflow.services.calendar import calendar_for
from accuflow.config import Settings


def dates(count=200):
    cal=calendar_for(2026)
    labels=cal.sessions_in_range('2025-01-01','2026-09-15')[-count:]
    return [{"date":d.date().isoformat(),"open":cal.session_open(d).to_pydatetime(),"close":cal.session_close(d).to_pydatetime()} for d in labels]


def synthetic_data(labels):
    cal=calendar_for(2026)
    first=cal.sessions_in_range(labels[0]['date'],labels[-1]['date'])[0]
    earlier=cal.sessions_in_range(cal.session_offset(first,-120),cal.session_offset(first,-1))
    data={s:{"daily":[],"minute":[]} for s in ('TEST','SPY')}
    for symbol,base in (('TEST',100.),('SPY',400.)):
        for i,d in enumerate(earlier):
            close=base*(1+.0004*sin(i*.41))
            data[symbol]['daily'].append(dict(timestamp=d.date().isoformat(),open=base,high=max(base,close)*1.001,low=min(base,close)*.999,close=close,volume=400000))
            base=close
        for index,label in enumerate(labels):
            opening=base
            bars=[]
            count=int((label['close']-label['open']).total_seconds()/60)
            for n in range(count):
                drift=(.000008 if index>=len(labels)-35 else .0000005*sin(index*.37)) if symbol=='TEST' else .0000003*sin(index*.93)
                close=base*(1+drift+.000002*sin(n/7+index*.11))
                high=max(base,close)+.00001
                low=min(base,close)-.00001
                row=dict(timestamp=(label['open']+timedelta(minutes=n)).isoformat(),open=base,high=high,low=low,close=close,
                         volume=1000+(n%11)*10,average=(base+close)/2,source='IBKR',use_rth=1)
                bars.append(row)
                base=close
            data[symbol]['minute'].extend(bars)
            data[symbol]['daily'].append(dict(timestamp=label['date'],open=opening,high=max(b['high'] for b in bars),low=min(b['low'] for b in bars),close=base,volume=sum(b['volume'] for b in bars)))
    return data


@pytest.fixture(scope='module')
def material():
    config=manifest()
    labels=dates()
    captured=labels[-1]['close']+timedelta(hours=1)
    data=synthetic_data(labels)
    days=[compact_day(d) for d in build_series(data,'TEST',labels,captured,config)]
    return config,labels,captured,data,days


def snapshot(config,labels,captured,days):
    return {"manifest_hash":config['manifest_hash'],"feature_schema":"v2-daily-1","symbol":"TEST","instrument_key":"symbol:TEST",
            "as_of":days[-1]['as_of'],"captured_at":captured.isoformat(),"data_vintage":"historical_reconstruction",
            "sessions":[{k:v.isoformat() if hasattr(v,'isoformat') else v for k,v in label.items()} for label in labels],
            "days":days,"state_before":None,"flow_windows":[],
            "context":{"known_at":captured.isoformat(),"units_verified":True,"adjustment_verified":True}}


def test_manifest_isolated_and_immutable():
    config=manifest()
    verify_manifest(config)
    other=deepcopy(config)
    other['weights']['direction']=99
    with pytest.raises(ValueError,match='manifest'):
        verify_manifest(other)
    assert config['manifest_hash']!=manifest(70)['manifest_hash']
    with pytest.raises(ValueError): manifest(42)


def test_intervals_include_unknown_penalties_and_never_rescale():
    config=manifest()
    families={k:{'available':True,'value':1.} for k in config['weights']}
    assert score_interval(families,{'failed_response':0.,'concentration':0.},config)==(100.,100.)
    families['absorption']={'available':False,'value':None}
    assert score_interval(families,{'failed_response':None,'concentration':0.},config)==(65.,100.)


def test_continuous_normal_volume_can_establish_candidate(material):
    config,labels,captured,data,days=material
    state=None
    events=[]
    values=[]
    for i in range(len(days)-40,len(days)):
        snap=snapshot(config,labels[:i+1],captured,days[:i+1])
        snap['state_before']=state
        result=evaluate_v2(snap,config)
        state=result['state_after']
        values.append(result)
        events.extend(result['events'])
    assert any(r['quality']['valid'] for r in values)
    assert any(e['event_type']=='candidate' for e in events)
    assert all(not e['notifiable'] for e in events)
    assert max(r['score_lower'] or 0 for r in values)>=65
    assert values[-1]['flow_verdict']['status']=='unavailable'
    # No changed volume regime was required to detect the synthetic directional persistence.
    full_days=[d for d in days if len([s for s in labels if s['date']==d['date'] and (s['close']-s['open']).total_seconds()==23400])]
    assert len({d['volume'] for d in full_days})==1


def test_future_raw_rows_do_not_change_past_features(material):
    config,labels,captured,data,days=material
    cut=len(days)-15
    rebuilt=[compact_day(d) for d in build_series(data,'TEST',labels[:cut],captured,config)]
    assert rebuilt==days[:cut]
    for row in rebuilt:
        if row['fit']:
            assert row['fit']['fitted_through']<row['date']
        for event in row['absorption_events']:
            assert event['confirmed_at']<=row['as_of']


def test_no_holdings_no_future_events_no_same_day_accumulation(material):
    config,labels,captured,data,days=material
    snap=snapshot(config,labels,captured,days)
    original=evaluate_v2(snap,config)
    other=deepcopy(snap)
    other['holdings']={'future_13f':'irrelevant'}
    other['context'].update(event_known_at=(captured+timedelta(days=5)).isoformat(),event_review_through=(captured+timedelta(days=5)).isoformat(),major_event=True)
    changed=evaluate_v2(other,config)
    for key in ('score_lower','score_upper','families','status','event_context'):
        assert original[key]==changed[key]
    other['state_before']=changed['state_after']
    twice=evaluate_v2(other,config)
    assert twice['state_after']==changed['state_after']
    assert twice['events']==[]
    assert not changed['event_context']['checked']
    # Canonical JSON key order cannot change numeric evidence or comparison state.
    serialized=json.loads(json.dumps(config,sort_keys=True))
    assert evaluate_v2(snap,serialized)==original


def test_missing_day_never_implies_waning(material):
    config,labels,captured,data,days=material
    snap=snapshot(config,labels[:-1],captured,days[:-1])
    prior=evaluate_v2(snap,config)['state_after']
    prior.update(status='candidate',episode_id='test-episode',initial_sent=True)
    other=snapshot(config,labels,captured,deepcopy(days))
    other['state_before']=prior
    other['days'][-1]['quality'].update(valid=False,reasons=['stock_minute_coverage'])
    result=evaluate_v2(other,config)
    assert result['score_lower'] is None
    assert result['state_after']['status']=='candidate'
    assert result['state_after']['low_closes']==prior['low_closes']
    assert result['events']==[]


def test_window_baseline_excludes_current_window(material):
    config,labels,captured,data,days=material
    families,_,_=extract(days,config)
    for family in families.values():
        for width,row in family['windows'].items():
            assert row['baseline_end_before']==days[-int(width)]['date']
    early=snapshot(config,labels[:65],captured,days[:65])
    result=evaluate_v2(early,config)
    assert not result['quality']['valid']
    assert 'feature_warmup_or_coverage' in result['quality']['reasons']


def test_raw_archive_roundtrip_and_tamper_detection(material,tmp_path):
    config,labels,captured,data,days=material
    review={'units_verified':True,'adjustment_verified':True,'verification_notes':'synthetic mechanics only'}
    summary=run_study_v2(data,'TEST',labels,labels[-2:],captured,review,tmp_path,config,progress=None)
    assert summary['target_sessions']==2
    assert summary['holdings_validation']=='not_requested'
    answer=replay_v2(tmp_path,progress=None)
    assert answer['matches'] and answer['rebuilt_from_raw'] and answer['snapshots']==len(labels)
    assert not list(tmp_path.glob('*.db'))
    with (tmp_path/'features.jsonl.gz').open('ab') as handle: handle.write(b'corrupt')
    with pytest.raises(ValueError,match='checksum'):
        replay_v2(tmp_path,progress=None)


async def test_absent_data_blocks_and_existing_output_is_preserved(tmp_path):
    kwargs=dict(symbol='GOOG',start=date(2025,9,15),end=date(2026,9,15),cache=tmp_path/'absent.db',output=tmp_path/'output')
    answer=await run_backtest_v2(Settings(_env_file=None),**kwargs)
    assert answer['status']=='blocked'
    assert not (kwargs['output']/'snapshots.jsonl.gz').exists()
    with pytest.raises(FileExistsError):
        await run_backtest_v2(Settings(_env_file=None),**kwargs)
    labels,targets=plan(kwargs['start'],kwargs['end'],datetime(2026,9,16,tzinfo=UTC),manifest())
    assert len(labels)-len(targets)>65


def test_flow_support_is_separate_and_missing_days_are_not_spliced(material):
    from accuflow.detectors.accumulation_v2 import flow_verdict
    config,labels,captured,data,days=material
    snap=snapshot(config,labels,captured,days)
    def windows(selected):
        result=[]
        for index,label in enumerate(selected):
            pi=.8 if label==labels[-1] else .1+.05*sin(index)
            n=int((label['close']-label['open']).total_seconds()/300)
            for k in range(n):
                start=label['open']+timedelta(minutes=5*k)
                result.append({'start':start.isoformat(),'end':(start+timedelta(minutes=5)).isoformat(),
                    'available_at':captured.isoformat(),'feed_type':'live_tick_by_tick','unit':'shares',
                    'valid':True,'stable':True,'sequence_conflict':False,'trade_count':20,
                    'connection_coverage':1.,'classified_coverage':1.,'amount':100.,
                    'buy':50*(1+pi),'sell':50*(1-pi),'pi':pi})
        return result
    snap['flow_windows']=windows(labels[-40:])
    supported=evaluate_v2(snap,config)
    assert supported['flow_verdict']['status']=='supports'
    plain=snapshot(config,labels,captured,days)
    assert evaluate_v2(plain,config)['score_lower']==supported['score_lower']
    assert evaluate_v2(plain,config)['score_upper']==supported['score_upper']
    snap['flow_windows']=windows(labels[-40:-10]+labels[-1:])
    assert flow_verdict(snap)['status']=='inconclusive'
    for row in snap['flow_windows']:
        row['available_at']=(captured+timedelta(days=1)).isoformat()
    assert flow_verdict(snap)['status']=='unavailable'


def test_invalidation_requires_two_valid_closes_and_coverage_changes_do_not_decay():
    from accuflow.state.accumulation_v2 import initial,transition
    config=manifest()
    labels=dates(25)
    days=[{'date':d['date'],'as_of':d['close'].isoformat(),'close':90.,'invalidation_floor':95.,
           'raw':{'retention':[-.001]},'supported':False} for d in labels]
    previous=initial(config)
    previous.update(status='candidate',episode_id='episode',initial_sent=True,floor=95.,
                    as_of=days[-3]['as_of'],last_date=days[-3]['date'],coverage=['full'])
    def result(day,score=60,signature=None):
        return {'as_of':day['as_of'],'available_at':day['as_of'],'instrument_key':'symbol:TEST',
                'quality':{'valid':True,'comparison_signature':signature or ['full']},'score_lower':score,
                'reverse_percentile':.95,'families':{k:{'available':True,'value':.7} for k in config['weights']},
                'event_context':{'checked':False,'major_event':False},'flow_verdict':{'status':'unavailable'}}
    first,events=transition(previous,result(days[-2]),days[:-1],config)
    assert first['invalid_closes']==1 and not events
    second,events=transition(first,result(days[-1]),days,config)
    assert second['status']=='invalidated' and second['episode_id'] is None
    assert [e['event_type'] for e in events]==['invalidated']
    previous['low_closes']=2
    changed,events=transition(previous,result(days[-2],20,['reduced']),days[:-1],config)
    assert changed['low_closes']==0 and changed['status']=='candidate' and not events
