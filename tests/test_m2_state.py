import copy
from datetime import timedelta

from accuflow.state.episodes import transition, cross_day
from accuflow.features.bars import instant
from accuflow.detectors.unified import evaluate
from m2_fixtures import make_snapshot, add_prior_days


def next_result(result, snapshot, *, minutes=60, score=None, valid=True):
    result=copy.deepcopy(result); snapshot=copy.deepcopy(snapshot)
    snapshot['as_of']=(instant(snapshot['as_of'])+timedelta(minutes=minutes)).isoformat()
    snapshot['captured_at']=(instant(snapshot['as_of'])+timedelta(minutes=2)).isoformat()
    result['as_of']=snapshot['as_of']; result['quality']['valid']=valid
    if score is not None: result['score']=score
    result['quality']['independent_hours']+=minutes//60
    return result,snapshot


def test_repeated_checkpoint_and_missing_data_do_not_decay_or_duplicate_alert():
    snapshot=make_snapshot(); result=evaluate(snapshot); state=result['state_after']
    assert transition(state,result,snapshot)==(state,[])
    invalid,later=next_result(result,snapshot,score=0,valid=False)
    preserved,events=transition(state,invalid,later)
    assert preserved['episode_id']==state['episode_id']
    assert preserved['low_hours']==0 and preserved['status']==state['status']
    assert [e['event_type'] for e in events]==['data_quality']
    valid,later=next_result(result,snapshot,score=50)
    first,_=transition(state,valid,later)
    repeated,samehour=next_result(valid,later,minutes=5,score=50)
    same,_=transition(first,repeated,samehour)
    assert same['low_hours']==1
    second,secondhour=next_result(valid,later,score=50)
    last,events=transition(same,second,secondhour)
    assert last['status']=='waning'
    assert not any(e['notifiable'] for e in events)


def test_coverage_change_cannot_trigger_false_decay_and_invalidation_bypasses_cooldown():
    snapshot=add_prior_days(make_snapshot()); result=evaluate(snapshot);state=result['state_after']
    lower,later=next_result(result,snapshot,score=10)
    lower['evidence_coverage']=[]
    after,events=transition(state,lower,later)
    assert after['status']=='strengthened' and not events
    broken,later=next_result(result,snapshot,minutes=5,score=60)
    broken['invalidation']=True
    after,events=transition(state,broken,later)
    assert after['status']=='invalidated'
    assert [e['event_type'] for e in events]==['invalidated'] and events[0]['notifiable']
    again,later=next_result(broken,later,minutes=5)
    assert not transition(after,again,later)[1]


def test_same_day_only_one_support_and_stale_event_never_notifies():
    snapshot=add_prior_days(make_snapshot()); result=evaluate(snapshot)
    assert result['families']['cross_day']['support_days']==4
    current=result['daily_record']
    snapshot['prior_days'] += [current]*10
    replay=evaluate(snapshot)
    assert replay['families']['cross_day']['support_days']==4
    snapshot['captured_at']=(instant(snapshot['as_of'])+timedelta(days=1)).isoformat()
    assert all(not e['notifiable'] for e in evaluate(snapshot)['events'])


def test_enhancement_cooldown_and_closed_days_end_episode():
    snapshot=make_snapshot(); result=evaluate(snapshot); state=result['state_after']
    enhanced,later=next_result(result,snapshot,score=95)
    enhanced['evidence_ids']+=['new:first']
    after,events=transition(state,enhanced,later)
    assert not events  # Less than two hours after the first event.
    enhanced,later=next_result(result,snapshot,minutes=120,score=95)
    enhanced['evidence_ids']+=['new:second']
    after,events=transition(state,enhanced,later)
    assert [e['event_type'] for e in events]==['enhanced']
    assert after['enhancement_count']==1
    low,close=next_result(result,snapshot,minutes=210,score=30)
    low['closed']=True
    after,events=transition(state,low,close)
    assert after['episode_id'] and len(after['low_close_dates'])==1
    # A second check on the same close date is still only one day.
    same,same_close=next_result(low,close,minutes=5,score=30)
    same['closed']=True
    after,_=transition(after,same,same_close)
    assert len(after['low_close_dates'])==1
    tomorrow,daytwo=next_result(low,close,minutes=24*60,score=30)
    tomorrow['session_date']='2026-09-15';tomorrow['closed']=True
    daytwo['sessions'].append({'date':'2026-09-15'})
    after,events=transition(after,tomorrow,daytwo)
    assert after['episode_id'] is None and after['status']=='normal'
    assert [e['event_type'] for e in events]==['episode_closed']
