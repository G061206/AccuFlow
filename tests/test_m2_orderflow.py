from datetime import UTC, datetime, timedelta

from accuflow.features.orderflow import flow_windows

START=datetime(2026,9,14,13,30,tzinfo=UTC)
END=START+timedelta(minutes=5)
SPAN=[dict(start=START,end=END,connection_session='one',trades_connected=True,quotes_connected=True)]


def observations():
    rows=[]
    for i in range(5):
        at=START+timedelta(seconds=10+i*10)
        common=dict(connection_session='one',unit='shares',timestamp_precision_seconds=.001)
        rows.append(dict(kind='quote',event_time=at,received_at=at,sequence=i,bid=99,ask=101,**common))
        rows.append(dict(kind='trade',event_time=at+timedelta(milliseconds=100),received_at=at+timedelta(milliseconds=200),sequence=i,price=101,size=1,**common))
    return rows


def run(rows, spans=SPAN): return flow_windows(rows,spans,START,END)[0]


def test_direction_requires_prior_quote_and_prior_arrival():
    rows=observations(); assert run(rows)['pi']==1 and run(rows)['valid']
    for q in rows[::2]: q['received_at']+=timedelta(seconds=1)
    bad=run(rows)
    assert bad['pi']==0 and bad['unknown']==bad['amount'] and not bad['valid']
    rows=observations()
    for q in rows[::2]: q['event_time']+=timedelta(seconds=1)
    assert run(rows)['pi']==0


def test_midpoint_units_disconnect_and_same_second_precision_are_unknown():
    rows=observations()
    for t in rows[1::2]: t['price']=100
    assert run(rows)['classified_coverage']==0
    rows=observations()
    for t in rows[1::2]: t['unit']='unknown'
    assert not run(rows)['valid']
    assert not run(observations(),[])['valid']
    rows=observations()
    for row in rows: row['timestamp_precision_seconds']=1
    assert run(rows)['pi']==0 and not run(rows)['valid']


def test_only_callback_identity_deduplicates_and_conflicts_disable():
    rows=observations(); original=run(rows)
    assert run(rows+rows)==original
    trade=dict(rows[-1]); trade['sequence']=100
    assert run(rows+[trade])['trade_count']==6
    trade=dict(rows[-1]); trade['price']=99
    assert run(rows+[trade])['sequence_conflict'] and not run(rows+[trade])['valid']


def test_unknown_amount_stays_in_pressure_denominator():
    rows=observations(); rows[1]['price']=100
    result=run(rows)
    assert 0<result['pi']<1
    assert result['pi']==result['buy']/result['amount']


def test_locked_stale_quotes_and_direction_sensitivity_fail_closed():
    rows=observations()
    for quote in rows[::2]: quote['bid']=quote['ask']
    assert not run(rows)['valid']
    rows=observations()
    for quote in rows[::2]: quote['event_time']-=timedelta(seconds=3)
    assert not run(rows)['valid']
    rows=observations()
    extra=[]
    for q,t in zip(rows[::2],rows[1::2]):
        # Strict classification uses the older ask quote. Loose matching sees the
        # same-time quote, which reverses the inferred direction.
        extra.append(dict(q,event_time=t['event_time'],received_at=t['received_at'],sequence=q['sequence']+100,bid=102,ask=104))
    result=run(rows+extra)
    assert result['pi']==1 and not result['stable'] and not result['valid']
