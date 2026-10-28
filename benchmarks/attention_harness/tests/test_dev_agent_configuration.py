"""Agent creation gates and old byte contracts, using frozen synthetic fixtures."""
import builtins
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness.tests.c21_fixtures import base,r4_deployment,offline_dev_identity
from benchmarks.attention_harness import dev_configuration,dev_experiment
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_agent_config import normalize
from openarm365_dev_v2.source_policy import source_limit

SELECTION=dict(request_key='agent-test-001',tasks=['open_cabinet'],seeds=[102],policies=['autonomous'],mode='interactive',dev_mode='agent')


def test_defaults_and_old_config_bytes():
    old=dev_configuration.normalize_configuration({})
    assert json.dumps(old,sort_keys=True)==json.dumps(dev_configuration.normalize_configuration({'dev_mode':'one_call'}),sort_keys=True)
    assert not any(k in old for k in ('dev_mode','dev_model','eval_model','quota_stop_percent'))
    agent=normalize(SELECTION)
    assert agent==dict(dev_mode='agent',dev_model='gpt-6-astra',eval_model='gpt-6-astra',dev_reasoning_effort='high',quota_stop_percent=90)
    assert source_limit(dict(budget_tier='dev',runner_boundary='dev_loop'))==32768
    assert source_limit({})==16384
    assert source_limit(dict(budget_tier='dev',runner_boundary='dev_loop',dev_mode='agent'))==131072
    with pytest.raises(ValueError):source_limit(dict(budget_tier='legacy',dev_mode='agent'))

@pytest.mark.parametrize('change',[dict(budget_tier='legacy'),dict(budget_tier='formal'),dict(configuration={}),
    dict(seeds=[101,102]),dict(tasks=[]),dict(positive_control=True),dict(memory_authority={'x':1}),
    dict(memory_family=True),dict(model_source='parcc'),dict(dev_model='unknown'),dict(quota_stop_percent=float('nan'))])
def test_creation_options_reject_unsupported(change):
    with pytest.raises(ValueError):normalize({**SELECTION,**change})


def test_create_freezes_agent_only(tmp_path,offline_dev_identity):
    store=DevV2Store(tmp_path/'db')
    selection=dict(SELECTION,dev_model='gpt-6-sol',eval_model='gpt-6-astra',quota_stop_percent=91)
    exp=dev_experiment.create(store,tmp_path/'runs',selection)
    manifest=dev_experiment.verify_manifest(exp)
    assert manifest['dev_mode']=='agent' and manifest['dev_model']=='gpt-6-sol'
    assert manifest['eval_model']=='gpt-6-astra' and manifest['quota_stop_percent']==91
    assert manifest['source_size_limit_bytes']==131072 and len(manifest['schedule'])==1
    assert next(iter(manifest['configs'].values()))['dev_mode']=='agent'
    assert dev_experiment.create(store,tmp_path/'runs',selection)['manifest']==exp['manifest']
    with pytest.raises(Exception,match='different experiment conditions'):
        dev_experiment.create(store,tmp_path/'runs',dict(selection,quota_stop_percent=92))


def test_old_manifest_absent_flag_golden(tmp_path,offline_dev_identity,monkeypatch):
    # Execute pre-D2 modules from git's committed source in an isolated module
    # namespace. Same directory/clock/identity inputs make exact bytes comparable.
    import subprocess,types,sys
    def original(name):
        path='benchmarks/attention_harness/'+name+'.py'
        source=subprocess.check_output(['git','show','HEAD:'+path],text=True)
        module=types.ModuleType('benchmarks.attention_harness._golden_'+name)
        module.__package__='benchmarks.attention_harness'
        exec(compile(source,path,'exec'),module.__dict__)
        return module
    conf=original('dev_configuration')
    assert json.dumps(conf.normalize_configuration({}),sort_keys=True)==json.dumps(dev_configuration.normalize_configuration({}),sort_keys=True)
    old=original('dev_experiment')
    selection={k:v for k,v in SELECTION.items() if k!='dev_mode'}
    selection.update(target_depth=1,target_valid_executions=1,max_versions=1)
    # Capture manifests before immutable materialization. Both use the same real
    # synthetic lock and public task fixtures. Separate DBs do not enter v2 bytes.
    store=DevV2Store(tmp_path/'db')
    exp=old.create(store,tmp_path/'runs',selection)
    before=Path(exp['manifest']['uri']).read_bytes()
    (tmp_path/'db').unlink()
    store=DevV2Store(tmp_path/'db')
    current=dev_experiment.create(store,tmp_path/'runs',selection)
    assert Path(current['manifest']['uri']).read_bytes()==before
    assert dev_experiment.create(store,tmp_path/'runs',dict(selection,dev_mode='one_call'))['manifest']==exp['manifest']


def test_old_run_cell_never_imports_agent(monkeypatch):
    from benchmarks.attention_harness import dev_v2_loop
    original=builtins.__import__
    def guarded(name,*a,**kw):
        if 'dev_agent' in name:raise AssertionError('agent imported on legacy path')
        return original(name,*a,**kw)
    monkeypatch.setattr(builtins,'__import__',guarded)
    class ReachedLegacy(Exception):pass
    class Store:
        def cell(self,key):raise ReachedLegacy()
    with pytest.raises(ReachedLegacy):dev_v2_loop.run_cell(Store(),{}, {},'c',1,None)


def test_agent_memory_admission_refused(tmp_path):
    from benchmarks.attention_harness.dev_agent_config import refuse_memory
    from benchmarks.attention_harness.memory_auto_import import provisional_payload,Refused,pending
    from benchmarks.attention_harness import dev_v2_memory_import
    with pytest.raises(ValueError,match='unsupported'):refuse_memory({'dev_mode':'agent'})
    with pytest.raises(Refused,match='unsupported'):
        provisional_payload({'schema_version':'attentionbench.dev-v2-provisional-proposal.v1','status':'pending_import','dev_mode':'agent'},'test')
    store=DevV2Store(tmp_path/'db')
    with store.tx() as db:
        store.put(db,'experiments','agent',{'selection':{'dev_mode':'agent'}})
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)',('agent-cell','agent',json.dumps({'dev_mode':'agent'})))
    assert list(pending(store.path))==[]  # An unrelated old cell would remain eligible.
    with store.tx() as db:
        store.event(db,'planner_memory_offline_queued',{'kind':'provisional','cell':'agent-cell','queue':{}})
    with pytest.raises(Refused,match='unsupported'):list(pending(store.path))
    with pytest.raises(Exception,match='unsupported'):
        dev_v2_memory_import.prepare(SimpleNamespace(cell=lambda _:dict(dev_mode='agent')),'c',1,'n',tmp_path/'out',tmp_path)
    with pytest.raises(ValueError,match='not_supported'):
        dev_experiment.create(store,tmp_path,SELECTION,validation_origin={'dev_mode':'agent'})


def test_preflight_failure_surfaces_cell_state(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_agent_loop,dev_agent_isolation
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG,SOURCE
    states=[]
    store=SimpleNamespace(path=tmp_path/'db',cell=lambda _:dict(task_id='fake',seed=1),update_cell=lambda key,**kw:states.append(kw))
    def fail(*a,**kw):raise dev_agent_isolation.IsolationUnavailable('synthetic_preflight_denied')
    monkeypatch.setattr(dev_agent_isolation,'verify_runtime',fail)
    class Listener:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def bind(self,*a):pass
        def listen(self):pass
        def getsockname(self):return ('127.0.0.1',12345)
    monkeypatch.setattr(dev_agent_loop.socket,'socket',Listener)
    result=dev_agent_loop.run_agent_cell(store,dict(artifact_dir=str(tmp_path)),dict(selection={'help_budget':1},configs={'fake:1':dict(CONFIG,task_prompt='fixture')},initial_sources={'fake':{'source':SOURCE.decode()}}),'c')
    assert result['reason']=='isolation_unavailable'
    assert states[-1]['state']=='needs_review' and states[-1]['error']=='synthetic_preflight_denied'


def test_scheduler_dispatches_agent_before_old_recovery(monkeypatch):
    from benchmarks.attention_harness import dev_agent_loop,dev_wait_recovery
    calls=[]
    monkeypatch.setattr(dev_experiment,'snapshot',lambda *a:{'selection':{'dev_mode':'agent'}})
    monkeypatch.setattr(dev_agent_loop,'run_agent_experiment',lambda *a,**kw:calls.append(kw) or {'agent':'done'})
    monkeypatch.setattr(dev_wait_recovery,'reconcile_waits',lambda *a:pytest.fail('old scheduler entered'))
    assert dev_experiment.run(None,'e',max_cells=1,outer_deadline_epoch=123)=={'agent':'done'}
    assert calls[0]['outer_deadline_epoch']==123


def test_cell_dispatch_uses_agent_loop(monkeypatch):
    from benchmarks.attention_harness import dev_v2_loop,dev_agent_loop
    monkeypatch.setattr(dev_agent_loop,'run_agent_cell',lambda *a,**kw:{'agent':'done'})
    assert dev_v2_loop.run_cell(None,{}, {'dev_mode':'agent'},'c',10,None)=={'agent':'done'}
