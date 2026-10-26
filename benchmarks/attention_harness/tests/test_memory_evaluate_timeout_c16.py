"""C16: isolated HTTP transport and durable completed-batch recovery."""
import io
import json
from enum import Enum
from types import SimpleNamespace

import pytest

from attention_memory_service import memory_service_client as service_client
from benchmarks.attention_harness import memory_validation_runtime as runtime
from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.memory_service_client import MemoryServiceClient
from benchmarks.attention_harness.tests.test_memory_validation_worker import deps


@pytest.mark.parametrize('options,expected', [({},1800),({'evaluate_timeout_seconds':2400.5},2400.5)])
def test_only_evaluate_and_decision_use_long_timeout(monkeypatch,options,expected):
    client=MemoryServiceClient('http://localhost:18000',api_key='fake',timeout=17,**options)
    client._importer_file=True
    client.importer_key='fake-importer'
    calls=[]
    def urlopen(request,*,timeout):
        calls.append((request.method,request.full_url,timeout))
        assert client.timeout==17  # No shared mutation, even during evaluation.
        if request.full_url.endswith('/evaluate'):
            client.health()  # An overlapping ordinary call keeps its own timeout.
        return io.BytesIO(b'{"decision": null, "status": "trusted"}')
    monkeypatch.setattr(service_client,'urlopen',urlopen)
    monkeypatch.setattr(service_client,'_memory',lambda value:value)
    client.evaluate('note / one')
    client.decision('note / one')
    client.get_memory('note / one')
    client.record_plan('note / one',{})
    client.cross_seed_status(memory_id='note / one')
    client.cross_seed_write('pairs',memory_id='note / one')
    assert [c[2] for c in calls]==[expected,17,expected,17,17,17,17]
    assert calls[0][1].endswith('/memories/note%20%2F%20one/evaluate')
    def fail(*args,**kwargs):raise TimeoutError('timed out')
    monkeypatch.setattr(service_client,'urlopen',fail)
    with pytest.raises(TimeoutError):client.evaluate('note')
    assert client.timeout==17


@pytest.mark.parametrize('value',[0,-1,True,False,None,'1800',[],{},float('nan'),float('inf'),-float('inf')])
def test_runtime_rejects_invalid_timeout_before_credentials(tmp_path,monkeypatch,value):
    (tmp_path/'runtime.json').write_text(json.dumps(dict(evaluate_timeout_seconds=value)))
    monkeypatch.setattr(MemoryServiceClient,'offline',lambda *a,**kw:pytest.fail('credentials read'))
    with pytest.raises(ValueError,match='evaluate_timeout_seconds'):
        runtime.from_config(tmp_path)


@pytest.mark.parametrize('options,expected',[({},1800),({'evaluate_timeout_seconds':.5},.5),({'evaluate_timeout_seconds':3600},3600)])
def test_runtime_passes_timeout_to_shared_worker_and_cross_seed_client(tmp_path,monkeypatch,options,expected):
    from benchmarks.attention_harness.memory_validation_provision import Provisioner
    config=dict(base_url='http://localhost:18000',key_file='unused',experiments_root=str(tmp_path/'experiments'),
                validator_root=str(tmp_path/'validator'),activity_ledgers=[],activity_ledger_roots=[],**options)
    worker.atomic(tmp_path/'runtime.json',config)
    def offline(base_url,*,key_file,**kwargs):
        assert key_file=='unused'
        return MemoryServiceClient(base_url,api_key='fake',**kwargs)
    monkeypatch.setattr(MemoryServiceClient,'offline',offline)
    result=runtime.from_config(tmp_path)
    try:
        assert result.client.evaluate_timeout_seconds==expected
        assert result.client.timeout==30
        assert isinstance(result.runner.provision,Provisioner)
        assert result.client is result.runner.client is result.runner.provision.client
    finally:result.runner.pool.shutdown()


def completed_batch(root):
    d=deps(root)
    worker.set_enabled(root,True)
    current=dict(memory_id='candidate',title='Pickup',pair_index=5,arm='control',active=False,
                 plan={'pairs':[dict(control_cell=f'c{i}',treatment_cell=f't{i}') for i in range(5)]})
    state=dict(current=current,runs_today=10,day='2026-10-23')
    return d,state


@pytest.mark.parametrize('outcome',['trusted','rejected'])
@pytest.mark.parametrize('record_result',[False,True])
@pytest.mark.parametrize('initial_decision',[None,{'reason':'legacy decision details'}])
def test_legacy_decision_records_result_in_final_arm_cycle(tmp_path,outcome,record_result,initial_decision):
    d=deps(tmp_path)
    worker.set_daily_run_limit(tmp_path,None)
    worker.set_enabled(tmp_path,True)
    state={};worker.tick(state,d)
    state['runs_today']=1001
    d.runner.phase='completed'
    for _ in range(9):worker.tick(state,d)
    assert state['current']['pair_index']==4 and state['current']['arm']=='treatment'
    assert len(d.runner.started)==10 and not state.get('results')
    calls=[]
    details=dict(reason=outcome)
    def decision(mid):
        calls.append('decision')
        return details if 'evaluate' in calls else initial_decision
    def evaluate(mid):
        calls.append('evaluate')
        view=automatic_checks(tmp_path)
        assert view['status']=='Evaluating 5 pairs (this can take several minutes)'
        assert view['current']['stage']=='evaluate'
        if record_result:
            status=Enum('Status',{'trusted':'trusted','rejected':'rejected'})
            return SimpleNamespace(status=status(outcome))
        return dict(status=outcome)
    d.client.decision=decision;d.client.evaluate=evaluate
    worker.tick(state,d)
    assert calls==['decision','evaluate','decision']
    assert state['current'] is None
    assert state['results']==[dict(memory_id='head',title='Head',status=outcome,decision=details)]
    assert len(d.runner.started)==10 and state['runs_today']==1010
    saved=json.loads((tmp_path/'state.json').read_bytes())
    assert saved['current'] is None and saved['results']==state['results']


@pytest.mark.parametrize('committed',[False,True])
def test_timeout_keeps_current_and_explicit_retry_checks_decision_first(tmp_path,committed):
    d,state=completed_batch(tmp_path)
    calls=[];recorded=None
    def decision(mid):
        calls.append('decision')
        return recorded
    def evaluate(mid):
        nonlocal recorded
        calls.append('evaluate')
        view=automatic_checks(tmp_path)
        assert view['status']=='Evaluating 5 pairs (this can take several minutes)'
        assert view['current']['stage']=='evaluate' and not view['current']['paused']
        if committed or calls.count('evaluate')>1:recorded=dict(decision='promoted',report={})
        if calls.count('evaluate')==1:raise TimeoutError('timed out')
    d.client.decision=decision;d.client.evaluate=evaluate
    worker.tick(state,d)
    current=json.loads((tmp_path/'state.json').read_bytes())['current']
    assert current['pair_index']==5 and 'timed out' in current['evaluate_error']
    view=automatic_checks(tmp_path)
    assert view['current']['paused'] and any('timed out' in e for e in view['errors'])
    worker.tick(state,d)
    assert calls==['decision','evaluate']  # Polling does not repeat expensive work.
    state=json.loads((tmp_path/'state.json').read_bytes())
    assert worker.main(['--state-dir',str(tmp_path),'--retry-current'])==0
    worker.set_enabled(tmp_path,False)
    worker.tick(state,d)
    assert calls==['decision','evaluate'] and state['current']
    assert automatic_checks(tmp_path)['current']['paused']
    assert any('timed out' in e for e in automatic_checks(tmp_path)['errors'])
    worker.set_enabled(tmp_path,True)
    worker.tick(state,d)
    assert calls==(['decision','evaluate','decision'] if committed else ['decision','evaluate','decision','evaluate','decision'])
    assert state['current'] is None and state['results'][0]['status']=='trusted'
    assert not d.runner.started and not automatic_checks(tmp_path)['errors']


@pytest.mark.parametrize('outcome,expected',[('promoted','trusted'),('rejected','rejected')])
def test_existing_decision_finishes_without_evaluating(tmp_path,outcome,expected):
    d,state=completed_batch(tmp_path)
    d.client.decision=lambda mid:dict(decision=outcome,report={})
    d.client.evaluate=lambda mid:pytest.fail('already decided')
    worker.tick(state,d)
    assert state['current'] is None and state['results'][0]['status']==expected


@pytest.mark.parametrize('failed_read',[1,2])
def test_decision_timeout_is_recoverable_without_duplicate_evaluation(tmp_path,failed_read):
    d,state=completed_batch(tmp_path)
    reads=0;evaluations=[];recorded=None
    def decision(mid):
        nonlocal reads
        reads+=1
        if reads==failed_read:raise TimeoutError('decision timed out')
        return recorded
    def evaluate(mid):
        nonlocal recorded
        evaluations.append(mid);recorded=dict(decision='promoted',report={})
    d.client.decision=decision;d.client.evaluate=evaluate
    worker.tick(state,d)
    assert state['current'] and 'decision timed out' in state['status']
    worker.request_current_retry(tmp_path)
    worker.tick(state,d)
    assert state['current'] is None and evaluations==['candidate']


def test_stale_evaluation_retry_cannot_unlock_another_batch(tmp_path):
    d,state=completed_batch(tmp_path)
    d.client.decision=lambda mid:None
    def fail(mid):raise TimeoutError('timed out')
    d.client.evaluate=fail
    worker.tick(state,d)
    worker.request_current_retry(tmp_path)
    state['current']['memory_id']='different-candidate'
    d.client.decision=lambda mid:pytest.fail('stale retry accepted')
    worker.tick(state,d)
    assert 'timed out' in state['status']
