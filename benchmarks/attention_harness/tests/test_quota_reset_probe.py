"""Reset recovery with fake clocks, rollouts and probes. Never invoke real Codex."""
import json
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_agent_quota as quota
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
from benchmarks.attention_harness.dev_agent_loop import drive
from benchmarks.attention_harness.dev_agent_broker import Broker, FakeEvaluator
from benchmarks.attention_harness.dev_agent_adapters import FakeProber
from benchmarks.attention_harness.tests.test_dev_agent_d2 import ws, fake_session, Executor

NOW=1794614400
USAGE=dict(input_tokens=3000,cached_input_tokens=2000,output_tokens=1)


@pytest.fixture(autouse=True)
def forbid_real_codex(monkeypatch):
    popen=subprocess.Popen
    def guarded(argv,*args,**kw):
        assert str(argv[0]).split('/')[-1]!='codex', 'real Codex is forbidden'
        return popen(argv,*args,**kw)
    monkeypatch.setattr(subprocess,'Popen',guarded)


def rig(tmp_path, *, used=2, failure=None, tokens=3001, readable=True):
    now=[NOW];calls=[];current=[None]
    ledger=AgentLedger(tmp_path/'ledger.sqlite3','cell',clock=lambda:now[0])
    def reader():
        if current[0] is None:raise OSError('synthetic reset: no reading')
        return current[0]
    def probe(private,on_event):
        calls.append(private)
        on_event(dict(type='thread.started',thread_id='fake-probe'))
        on_event(dict(type='turn.completed',usage=USAGE))
        if readable:current[0]=dict(used_percent=used,window_minutes=10080,resets_at=NOW+604800,observed_at=now[0])
        if failure:raise failure
    def make():
        return quota.QuotaMonitor(tmp_path,ledger,stop_percent=77,clock=lambda:now[0],reader=reader,
                                  probe=probe,rollout_reader=lambda tid:tokens)
    return SimpleNamespace(make=make,ledger=ledger,calls=calls,current=current,now=now)


def samples(root):return [json.loads(line) for line in (root/'quota.jsonl').read_text().splitlines()]


def test_reset_probe_is_once_accounted_and_survives_resume(tmp_path):
    r=rig(tmp_path);m=r.make()
    m.check('before_turn')
    row=samples(tmp_path)[-1]
    assert row['run_tokens']==3001 and row['run_points']==3001/quota.TOKENS_PER_POINT
    assert row['quota_probe']['status']=='completed' and row['stopped'] is False
    events=[v for v in samples(tmp_path) if v.get('event')=='quota_probe_after_reset']
    assert len(events)==1 and events[0]['reason']=='quota_probe_after_reset'
    job=r.ledger.jobs()[0]
    assert job['kind']=='quota_probe' and job['state']=='settled' and job['usage']==USAGE
    again=r.make();again.check('resumed')
    assert samples(tmp_path)[-1]['run_tokens']==3001 and len(r.calls)==1
    r.current[0]=None  # The one-probe allowance does not reset on controller restart.
    again.latest=None
    with pytest.raises(quota.QuotaUnavailable,match='^quota_unreadable_after_probe$'):again.check('missing_again')
    assert len(r.calls)==1


@pytest.mark.parametrize('failure',[None,OSError('fake process failure'),TimeoutError('fake timeout')])
def test_probe_without_reading_stops_with_specific_reason(tmp_path,failure):
    r=rig(tmp_path,readable=False,failure=failure);m=r.make()
    for monitor in (m,r.make()):
        with pytest.raises(quota.QuotaUnavailable,match='^quota_unreadable_after_probe$'):
            monitor.check('before_turn')
    assert len(r.calls)==1
    assert samples(tmp_path)[-1]['error']=='quota_unreadable_after_probe'
    with sqlite3.connect(r.ledger.path) as db:
        assert json.loads(db.execute('SELECT payload FROM v2_agent_quota ORDER BY seq DESC LIMIT 1').fetchone()[0])['error']=='quota_unreadable_after_probe'


@pytest.mark.parametrize('used',[77,88,100])
def test_probe_reading_at_or_above_stop_line_stops(tmp_path,used):
    r=rig(tmp_path,used=used)
    with pytest.raises(ValueError,match='^quota_stop$'):r.make().check('before_turn')
    assert samples(tmp_path)[-1]['stop_percent']==77 and samples(tmp_path)[-1]['stopped']
    assert len(r.calls)==1


def test_probe_tokens_crossing_five_points_stop_the_run(tmp_path):
    r=rig(tmp_path);m=r.make()
    m.spent(dict(total_tokens=5*quota.TOKENS_PER_POINT-3000))
    with pytest.raises(ValueError,match='^quota_stop$'):m.check('before_turn')
    row=samples(tmp_path)[-1]
    assert row['run_cap_points']==5 and row['run_cap_reached'] and row['run_tokens']==125000001


def test_already_at_cap_does_not_probe_without_reading(tmp_path):
    r=rig(tmp_path);m=r.make();m.spent(dict(total_tokens=5*quota.TOKENS_PER_POINT))
    with pytest.raises(ValueError,match='^quota_stop$'):m.check('before_turn')
    assert not r.calls and not (tmp_path/'quota-probe.json').exists()


def test_current_reading_never_probes(tmp_path):
    r=rig(tmp_path)
    r.current[0]=dict(used_percent=10,window_minutes=10080,resets_at=NOW+1000,observed_at=NOW)
    r.make().check('before_turn')
    assert not r.calls


def test_reported_usage_counts_before_rollout_is_available(tmp_path):
    r=rig(tmp_path,tokens=None)
    r.make().check('before_turn')
    assert samples(tmp_path)[-1]['run_tokens']==3001


def test_probe_without_any_usage_cannot_start_even_with_a_reading(tmp_path):
    r=rig(tmp_path);m=r.make()
    def unaccounted(private,on_event):
        r.current[0]=dict(used_percent=1,window_minutes=10080,resets_at=NOW+1000,observed_at=NOW)
    m.probe=unaccounted
    with pytest.raises(quota.QuotaUnavailable,match='quota_unreadable_after_probe'):m.check('before_turn')


@pytest.mark.parametrize('used,expected',[(1,'agent_idle'),(77,'quota_stop'),(None,'quota_unreadable_after_probe')])
def test_drive_only_starts_after_probe_passes(ws,used,expected):
    r=rig(ws.private,used=used,readable=used is not None)
    session=fake_session(ws,['pass','pass','pass'])
    result=drive(ws,session,r.ledger,executor=Executor(),prober=FakeProber(),quota=r.make(),agent_policy_version='agent-policy-v2',helper=SimpleNamespace(agent_policy_version='agent-policy-v2'))
    assert result['reason']==expected, result
    assert result['state']==('needs_review' if used is None else 'failed')
    assert (session.turn>0)==(used==1) and len(r.calls)==1


def test_broker_keeps_specific_probe_failure(ws):
    r=rig(ws.private,readable=False)
    broker=Broker(ws,executor=Executor(),prober=FakeProber(),evaluator=FakeEvaluator(),quota=r.make(),agent_mode=True)
    try:
        response=broker.accept(dict(schema='attentionbench.dev-agent-request.v1',request_id='read',op='observe',payload={}))
        assert response['error']==broker.stop_reason=='quota_unreadable_after_probe'
    finally:broker.close()


def test_real_probe_adapter_uses_one_short_isolated_turn(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_agent_session
    calls=[];events=[]
    class FakeSession:
        def __init__(self,ws,private,**options):
            assert ws.is_dir() and ws==private/'ws'
            assert options==dict(model='gpt-6.1-sol',reasoning_effort='low')
        def start(self,prompt):
            calls.append(prompt)
            yield dict(type='turn.completed',usage=USAGE)
        def cancel(self,reason):calls.append(reason)
    monkeypatch.setattr(dev_agent_session,'CodexAgentSession',FakeSession)
    quota.probe_weekly_quota(tmp_path,events.append)
    assert len(events)==1 and len(calls)==2 and 'only OK' in calls[0]
    assert calls[1]=='quota_probe_finished'


def test_latest_probe_failure_is_read_only_and_reset_scoped(tmp_path):
    root=tmp_path/'devexp-synthetic';root.mkdir()
    ledger=AgentLedger(root/'ledger.sqlite3','cell')
    ledger.quota(dict(at=NOW,error='quota_unreadable_after_probe'))
    assert quota.latest_probe_failure(tmp_path,since=NOW-1)=='quota_unreadable_after_probe'
    assert quota.latest_probe_failure(tmp_path,since=NOW+1) is None
    ledger.quota(dict(at=NOW+2,used_percent=1))
    assert quota.latest_probe_failure(tmp_path) is None


def test_known_usage_at_cap_blocks_probe_even_with_a_missing_other_session(tmp_path):
    r=rig(tmp_path,tokens=None);m=r.make()
    m.spent(dict(total_tokens=5*quota.TOKENS_PER_POINT))
    m.event(dict(type='thread.started',thread_id='missing'),session=True)
    assert m.own_tokens()[0] is None
    with pytest.raises(ValueError,match='^quota_stop$'):m.check('before_turn')
    assert not r.calls


def test_expired_weekly_rollout_refreshes_via_fake_probe(tmp_path):
    from datetime import datetime,timezone
    root=tmp_path/'home';path=root/'.codex/sessions/rollout-synthetic.jsonl'
    path.parent.mkdir(parents=True)
    def write(used,reset):
        path.write_text(json.dumps(dict(timestamp=datetime.fromtimestamp(NOW,timezone.utc).isoformat(),
            payload=dict(rate_limits=dict(secondary=dict(used_percent=used,window_minutes=10080,resets_at=reset)))))+'\n')
    write(76,NOW-1)
    r=rig(tmp_path);m=r.make();calls=[]
    m.reader=lambda:quota.read_weekly_quota(root,now=NOW)
    def probe(private,on_event):
        calls.append(1);on_event(dict(type='turn.completed',usage=USAGE));write(1,NOW+604800)
    m.probe=probe
    m.check('before_turn')
    assert calls==[1] and samples(tmp_path)[-1]['used_percent']==1
    assert samples(tmp_path)[-1]['run_tokens']==3001


def test_probe_crossing_cap_still_stops_with_another_session_missing(tmp_path):
    r=rig(tmp_path);m=r.make()
    m.spent(dict(total_tokens=5*quota.TOKENS_PER_POINT-3000))
    m.event(dict(type='thread.started',thread_id='missing'),session=True)
    m.rollout_reader=lambda tid:3001 if tid=='fake-probe' else None
    with pytest.raises(ValueError,match='^quota_stop$'):m.check('before_turn')
    row=samples(tmp_path)[-1]
    assert row['run_cap_reached'] and row['run_points']>5
    assert row['run_points_source']=='known_tokens:session_file_missing'
