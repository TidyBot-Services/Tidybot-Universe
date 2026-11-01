"""Incident data stays read-only; all mutable evidence lives under pytest's temp root.

The guard test uses the incident's exact native config bytes. The start-path
test generates a worktree identity lock and keeps all other native settings.
It never substitutes identity, fairness, isolation or source checks.
"""
from copy import deepcopy
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time
from types import SimpleNamespace as NS

import pytest

from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_agent_workspace import Workspace, atomic_write, encode
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_v2_loop import checked, immutable
from benchmarks.attention_harness.memory_agent_validation import (
    Delivery, seal_controller, seal_worker_arms, verify_runner_envelope)

RUNS=Path('/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments')
WORKER=Path('/home/truares/桌面/AttentionBench-Workspace/development/memory-m3/validation-state')
INCIDENT='devexp-9dd83103ff1eacc15906bcb2'
OPERATION='1704b59f7d55f6ec67458360'


def backup(source,target):
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src,sqlite3.connect(target) as dest:
        src.backup(dest)


def copy_ref(ref,target):
    raw=checked(ref).read_bytes()
    target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    return dict(uri=str(target),sha256=hashlib.sha256(raw).hexdigest())


@pytest.fixture(scope='module')
def incident(tmp_path_factory):
    if not (RUNS/INCIDENT/'manifest.json').is_file():pytest.skip('local incident evidence unavailable')
    root=tmp_path_factory.mktemp('arm-incident')
    shutil.copyfile(RUNS/INCIDENT/'manifest.json',root/'experiment.json')
    m=json.loads((root/'experiment.json').read_bytes())
    origin=deepcopy(m['validation_origin'])
    for field,name in (('manifest','origin.json'),('config','origin-config.json'),('source','origin.py')):
        origin[field]=copy_ref(origin[field],root/name)
    batch=json.loads((WORKER/OPERATION/'batch.json').read_bytes())
    shutil.copyfile(WORKER/OPERATION/'batch.json',root/'batch.json')
    batch['origin_feedback']=copy_ref(batch['origin_feedback'],root/'origin-feedback.json')
    shutil.copyfile(WORKER/'state.json',root/'worker-state.json')
    # Copy the origin preparation receipt; do not reopen its live state for writes.
    receipts=list((WORKER/'origins').glob('*/receipt.json'))
    for i,path in enumerate(receipts):shutil.copyfile(path,root/f'origin-receipt-{i}.json')
    backup(RUNS/INCIDENT/'ledger.sqlite3',root/'arm.sqlite')
    backup(RUNS/'devexp-259ba58166741ec605e87a39/ledger.sqlite3',root/'origin.sqlite')
    backup(WORKER.parent/'store.sqlite3',root/'store.sqlite')
    from attention_memory_service.core.store import AttentionStore
    note=AttentionStore(root/'store.sqlite').get_memory(batch['memory_id'])
    assert note is not None and note.version==1
    assert checked(batch['config']).read_bytes()==(root/'origin-config.json').read_bytes()
    return NS(root=root,m=m,origin=origin,batch=batch,note=note)


def envelope_rig(f,root,arm='treatment',change=None):
    m=deepcopy(f.m);m['validation_origin']=deepcopy(f.origin)
    m['agent_validation']=m['selection']['agent_validation']=m['validation_origin']
    if change:change(m)
    backup(f.root/'arm.sqlite',root/'ledger.sqlite')
    store=DevV2Store(root/'ledger.sqlite');m['ledger_path']=str(store.path)
    ref=immutable(root/'experiment.json',m)
    key=next(c['key'] for c in m['cells'] if c['validation_pair']==0 and c['validation_arm']==arm)
    with store.tx() as db:
        exp=store.get(db,'experiments',m['experiment_id']);exp['manifest']=ref
        store.put(db,'experiments',m['experiment_id'],exp)
        db.execute("DELETE FROM v2_events WHERE kind IN ('agent_validation_memory','memory_validation_dispatch','agent_validation_runner_dispatch','agent_validation_controller')")
    for c in m['cells']:
        store.update_cell(c['key'],artifact_dir=str(root/'cells'/c['key']),agent_validation_controller=None)
    batch=deepcopy(f.batch);batch['cells']={c['key']:dict(ledger=str(store.path)) for c in m['cells']}
    batch['config']=copy_ref(f.origin['config'],root/'config.json')
    arms=seal_worker_arms(batch,'offline-incident-note',root/'arms')
    ws=Workspace(root/'cells'/key/'agent')
    source=checked(f.origin['source']).read_text()
    immutable(ws.private/'sources/submit.py',source)
    def authorize(**kw):
        return dict(schema_version='attentionbench.dev-v2-validation-grant.v1',grant_id='offline-incident-grant',issued_at=time.time(),
            binding=dict(kw,memory_version=f.note.version,dev_model=batch['dev_model'],
                guidance_sha256=hashlib.sha256(f.note.guidance.encode()).hexdigest(),plan_sha256='a'*64))
    client=NS(get_memory=lambda mid:f.note,get_plan=lambda mid:batch['plan'],decision=lambda mid:None,
        authorize_dev_v2_validation=authorize)
    memory=NS(store=store,key=key,workspace=ws,config=next(iter(m['configs'].values())))
    current=dict(batch,arm=arm,arm_id=key,origin=f.origin['source'])
    memory.validation=Delivery(memory,current,client,arm_record=arms[key])
    seal_controller(memory,None,NS(helper=None))
    memory.packet,_=memory.validation.load(1,source,ws.private/'memory/v1')
    from benchmarks.attention_harness.dev_agent_eval import feedback
    from benchmarks.attention_harness.dev_v2_loop import prompt_memory_view
    ws.publish('memory/v1.json',feedback(prompt_memory_view(dict(memory=memory.packet['entries']))))
    envelope=memory.validation.prepare(dict(version_id='v0001',request_id='submit'))
    config_path=Path(batch['config']['uri'])
    request=NS(suite='robocasa365',task_id=memory.config['task_id'],seed=memory.config['seed'],
        run_id='run:'+key,attempt_id=f'attempt:{key}:0',worker_arm=arms[key],
        policy_code_path=ws.private/'sources/submit.py',policy_sha256=hashlib.sha256(source.encode()).hexdigest(),
        config_path=config_path,config_sha256=batch['config']['sha256'],
        agent_configuration=dict({k:m[k] for k in ('dev_model','dev_reasoning_effort','eval_model')},
            agent_policy_version=m['agent_policy_version']))
    return NS(memory=memory,store=store,request=request,envelope=envelope,config=memory.config,m=m)


@pytest.mark.parametrize('arm',['control','treatment'])
def test_incident_native_bytes_pass_guard(incident,tmp_path,arm):
    f=envelope_rig(incident,tmp_path,arm)
    assert f.config['advisor_proxy']['enabled'] is True
    assert f.request.config_path.read_bytes()==checked(incident.origin['config']).read_bytes()
    verify_runner_envelope(f.envelope,f.request,f.config)
    entries=f.memory.packet['entries']
    assert [e['content'] for e in entries]==([incident.note.guidance] if arm=='treatment' else [])


@pytest.mark.parametrize('fault,item',[
    ('selection_advisor','advisor_proxy'),('budget_help','agent_budget'),
    ('selection_help','help_budget'),('helper','helper_constructed'),
    ('broker_helper','broker_helper_constructed'),('advisor_client','advisor_client_constructed'),
    ('missing_seal','controller_seal'),('native_bytes','config_sha256')])
def test_incident_named_refusals(incident,tmp_path,fault,item):
    def change(m):
        if fault=='selection_advisor':m['selection']['advisor_proxy']['enabled']=True
        if fault=='budget_help':m['agent_budget']['help']=1
        if fault=='selection_help':m['selection']['help_budget']=1
    f=envelope_rig(incident,tmp_path)
    if fault in ('selection_advisor','budget_help','selection_help'):
        # Replace all worker-owned refs consistently, to reach the intended
        # check rather than merely failing a changed experiment reference.
        m=deepcopy(f.m);change(m);ref=immutable(tmp_path/'changed-experiment.json',m)
        arm=json.loads(checked(f.request.worker_arm).read_bytes());arm['experiment']=ref
        f.request.worker_arm=immutable(tmp_path/'changed-arm.json',arm);f.envelope['experiment']=ref
    elif fault in ('helper','broker_helper','advisor_client','missing_seal'):
        cell=f.store.cell(f.memory.key)
        if fault=='missing_seal':f.store.update_cell(f.memory.key,agent_validation_controller=None)
        else:
            record=json.loads(checked(cell['agent_validation_controller']).read_bytes());record[item]=True
            ref=immutable(tmp_path/'changed-controller.json',record)
            f.store.update_cell(f.memory.key,agent_validation_controller=ref)
    else:
        path=tmp_path/'changed-config.json';path.write_bytes(f.request.config_path.read_bytes()+b'\n')
        f.request.config_path=path
    with pytest.raises(DevConflict,match='envelope mismatch: '+item+'$'):
        verify_runner_envelope(f.envelope,f.request,f.config)


@pytest.mark.parametrize('field',['helper','broker_helper','advisor_client'])
def test_controller_refuses_actual_constructed_objects(incident,tmp_path,field):
    f=envelope_rig(incident,tmp_path)
    with pytest.raises(DevConflict,match=field+'_constructed'):
        seal_controller(f.memory,object() if field=='helper' else None,
            NS(helper=object() if field=='broker_helper' else None),advisor_client_constructed=field=='advisor_client')


def memory_rig(root,*,validation=False):
    from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
    from benchmarks.attention_harness.dev_agent_memory import Memory
    config=dict(budget_tier='dev',runner_boundary='dev_loop',sdk_call_limit=320,horizon=9600,
        attempt_wall_seconds=1920,dev_mode='agent')
    ws=Workspace(root/'agent').prepare(dict(task_prompt='Offline error test'),config)
    atomic_write(ws.ws,'work/main.py',b'from robot_sdk import sensors\nsensors.get_observation()\n')
    store=DevV2Store(root/'ledger.sqlite')
    with store.tx() as db:
        store.put(db,'experiments','exp',dict(selection={}))
        store.put(db,'cells','cell',dict(key='cell',experiment_id='exp',dev_mode='agent',state='running',
            artifact_dir=str(root),policy_id='reactive_help'))
    memory=Memory(store,'cell',ws,config,'initial source')
    if validation:memory.validation=NS(arm_record=dict(uri='fixture://worker',sha256='a'*64))
    return NS(ws=ws,store=store,memory=memory,ledger=AgentLedger(store.path,'cell',help_limit=0,validation=validation))


@pytest.mark.parametrize('validation,resumed',[(False,False),(True,False),(False,True)])
def test_memory_start_error_visible_and_persisted(tmp_path,monkeypatch,validation,resumed):
    from benchmarks.attention_harness.dev_agent_loop import drive
    from benchmarks.attention_harness.dev_agent_adapters import FakeProber
    from benchmarks.attention_harness.dev_agent_view import projection
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import Executor
    f=memory_rig(tmp_path,validation=validation)
    if resumed:
        f.store.version('cell',dict(version=1,source=immutable(tmp_path/'previous.py','previous source'),formal_eligible=False))
    def fail(*a,**kw):raise ConnectionError('svc listing failed')
    monkeypatch.setattr(f.memory,'open',fail)
    class Session:
        thread_id=None;turn=0;usage={}
        quiesce=staticmethod(nullcontext)
        def start(self,prompt):
            self.turn+=1
            assert 'Memory error: ConnectionError: svc listing failed' in prompt
            self.thread_id='offline'
            return iter(())
        def resume(self,thread,prompt):self.turn+=1;return iter(())
        def cancel(self,reason):pass
    session=Session()
    from benchmarks.attention_harness.dev_agent_attention import Helper
    helper=None if validation else Helper(f.ws,f.ledger,policy='reactive_help',config=f.memory.config,
        memory=f.memory,agent_policy_version='agent-policy-v2')
    result=drive(f.ws,session,f.ledger,executor=Executor(),prober=FakeProber(),memory=f.memory,
        helper=helper,agent_policy_version='agent-policy-v2',validation=validation)
    assert result['reason']==('memory_start_failed' if validation else 'agent_idle'),result
    assert session.turn==(0 if validation else 3)
    assert result['state']==('invalid_generation' if validation else 'failed')
    assert result['memory_errors'][0]['error']=='ConnectionError: svc listing failed'
    assert result['memory_errors'][0]['version']==(2 if resumed else 1)
    assert f.store.cell('cell')['memory_errors']==result['memory_errors']
    with f.store.tx() as db:
        row=json.loads(db.execute("SELECT payload FROM v2_events WHERE kind='agent_memory_error'").fetchone()[0])
    assert row['status']=='error' and row['phase']=='start'
    assert json.loads((f.ws.canonical/'events/evt000001.json').read_bytes())['status']=='error'
    view=projection(f.store,f.store.cell('cell'))
    assert any(e['source']=='Memory' and 'svc listing failed' in str(e['error']) for e in view['agent']['failure_errors'])
    # /report, /export.json and /ledger.json for agent runs return this projection.
    assert 'svc listing failed' in json.dumps(view)
    if not validation:
        assert json.loads((f.ws.ws/f'memory/v{2 if resumed else 1}.json').read_bytes())==dict(memory=[])
        assert f.memory.previous==('previous source' if resumed else 'initial source')


def test_incident_start_path_preserves_preflight(incident,tmp_path,monkeypatch):
    """Reach the real start path, recording a sandbox refusal without bypassing it.

    The enclosing sandbox may prohibit loopback sockets. In that case this is
    explicitly xfailed, never claimed to have reached the simulator boundary.
    """
    from openarm365_dev_v2.dev_config import make_lock,verify_lock,validate_dev_config
    from curobo_v2.execution_settings import from_config
    from benchmarks.attention_harness import dev_experiment,dev_agent_loop
    from openarm365_dev_v2.owner import NativeOwnerV2
    original=json.loads(checked(incident.origin['manifest']).read_bytes())
    config=json.loads(checked(incident.origin['config']).read_bytes())
    # Real registered planner pin remains frozen; this lock hashes THIS worktree.
    lock=make_lock(tmp_path/'worktree-lock.json',execution_settings=from_config(config['execution_settings']))
    verify_lock(lock)
    config['dev_lock']=lock
    validate_dev_config(config)
    original['identity_lock']=lock;original['configs']={f"{config['task_id']}:{config['seed']}":config}
    origin=deepcopy(incident.origin)
    origin['config']=immutable(tmp_path/'current-origin-config.json',config)
    origin['manifest']=immutable(tmp_path/'current-origin.json',original)
    # Frozen selection includes derived fields that are not creation inputs.
    selection={k:deepcopy(v) for k,v in incident.m['selection'].items() if k in dev_experiment.ALLOWED_SELECTION_FIELDS}
    selection['request_key']='offline-arm-start-incident'
    from benchmarks.attention_harness.memory_validation_provision import validate_creation
    validate_creation(selection,origin)
    store=DevV2Store(tmp_path/'start.sqlite')
    # Naming seam only: honor the task's filename rule without changing checks.
    class EvidencePath(type(Path())):
        def __truediv__(self,name):
            return super().__truediv__('experiment.json' if name=='manifest.json' else name)
    monkeypatch.setattr(dev_experiment,'Path',EvidencePath)
    exp=dev_experiment.create(store,tmp_path/'runs',selection,validation_origin=origin)
    m=dev_experiment.verify_manifest(exp)
    assert dev_experiment.validate_fairness(m)
    assert next(iter(m['configs'].values()))==config
    key=m['cells'][0]['key'];arm=m['cells'][0]['validation_arm']
    batch=dict(incident.batch,experiment_id=m['experiment_id'],config=origin['config'],
        cells={c['key']:dict(ledger=str(store.path)) for c in m['cells']},
        pairs=[{a+'_cell':next(c['key'] for c in m['cells'] if c['validation_pair']==i and c['validation_arm']==a)
                for a in ('control','treatment')} for i in range(5)])
    arms=seal_worker_arms(batch,'offline-incident-note',tmp_path/'worker/arms')
    called=[]
    def boundary(self):called.append('simulator');raise AssertionError('offline simulator boundary reached')
    monkeypatch.setattr(NativeOwnerV2,'start',boundary)
    # Tripwire only: no real model or quota lookup is authorized in this task.
    class SessionReached(Exception):pass
    def forbidden(*a,**kw):raise SessionReached('preflight passed; start path reached the model session constructor')
    monkeypatch.setattr(dev_agent_loop,'CodexAgentSession',forbidden)
    try:result=dev_agent_loop.run_agent_cell(store,exp,m,key,validation=dict(
        current=dict(batch,arm=arm,arm_id=key,origin=origin['source'],origin_feedback=incident.batch['origin_feedback']),
        client=None,arm_record=arms[key]))
    except SessionReached:return
    ws=Workspace(Path(store.cell(key)['artifact_dir'])/'agent')
    assert (ws.ws/'origin/policy.py').read_bytes()==checked(origin['source']).read_bytes()
    assert not called
    if 'start path reached the model session constructor' in str(result.get('error')):return
    if result.get('error')=='[Errno 1] Operation not permitted':
        pytest.xfail('real start path refuses loopback socket in enclosing sandbox; isolation was not bypassed')
    pytest.fail('start path refused before simulator: '+json.dumps(result))


def test_incident_walkthrough_reaches_simulator_boundary(incident,tmp_path,monkeypatch,capfd):
    """Same real start path, with a scripted Dev that submits once and the REAL runner.

    Only two substitutions: the Dev model session (scripted, no model call) and the
    final native simulator start (raises a marker). Everything else -- creation,
    fairness, lock, workspace, Memory delivery, broker, runner guard, native config,
    agent SDK build -- runs for real. Requires loopback sockets (run outside sandbox).
    """
    from openarm365_dev_v2.dev_config import make_lock,verify_lock,validate_dev_config
    from curobo_v2.execution_settings import from_config
    from benchmarks.attention_harness import dev_experiment,dev_agent_loop,dev_agent_eval
    from benchmarks.attention_harness.dev_agent_broker import FakeEvaluator
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import fake_session
    from openarm365_dev_v2.owner import NativeOwnerV2
    original=json.loads(checked(incident.origin['manifest']).read_bytes())
    config=json.loads(checked(incident.origin['config']).read_bytes())
    lock=make_lock(tmp_path/'worktree-lock.json',execution_settings=from_config(config['execution_settings']))
    verify_lock(lock)
    config['dev_lock']=lock
    validate_dev_config(config)
    original['identity_lock']=lock;original['configs']={f"{config['task_id']}:{config['seed']}":config}
    origin=deepcopy(incident.origin)
    origin['config']=immutable(tmp_path/'current-origin-config.json',config)
    origin['manifest']=immutable(tmp_path/'current-origin.json',original)
    selection={k:deepcopy(v) for k,v in incident.m['selection'].items() if k in dev_experiment.ALLOWED_SELECTION_FIELDS}
    selection['request_key']='offline-arm-walkthrough'
    from benchmarks.attention_harness.memory_validation_provision import validate_creation
    validate_creation(selection,origin)
    store=DevV2Store(tmp_path/'walk.sqlite')
    class EvidencePath(type(Path())):
        def __truediv__(self,name):
            return super().__truediv__('experiment.json' if name=='manifest.json' else name)
    monkeypatch.setattr(dev_experiment,'Path',EvidencePath)
    exp=dev_experiment.create(store,tmp_path/'runs',selection,validation_origin=origin)
    m=dev_experiment.verify_manifest(exp)
    assert dev_experiment.validate_fairness(m)
    key=m['cells'][0]['key'];arm=m['cells'][0]['validation_arm']
    batch=dict(incident.batch,experiment_id=m['experiment_id'],config=origin['config'],
        cells={c['key']:dict(ledger=str(store.path)) for c in m['cells']},
        pairs=[{a+'_cell':next(c['key'] for c in m['cells'] if c['validation_pair']==i and c['validation_arm']==a)
                for a in ('control','treatment')} for i in range(5)])
    arms=seal_worker_arms(batch,'offline-incident-note',tmp_path/'worker/arms')
    reached=[]
    def boundary(self):reached.append('simulator');raise RuntimeError('OFFLINE_SIMULATOR_BOUNDARY_REACHED')
    monkeypatch.setattr(NativeOwnerV2,'start',boundary)
    script="Path('work/main.py').write_text(Path('work/main.py').read_text()+'\\n# walkthrough\\n'); tool('submit')"
    def session(ws,private,**kw):
        value=fake_session(NS(ws=ws,private=private),[script]);value.model=kw['model'];value.reasoning_effort=kw['reasoning_effort'];return value
    monkeypatch.setattr(dev_agent_loop,'CodexAgentSession',session)
    def evaluator(*a,**kw):
        value=FakeEvaluator();value.model=kw.get('model');return value
    monkeypatch.setattr(dev_agent_eval,'Evaluator',evaluator)
    monkeypatch.setattr(dev_agent_loop,'QuotaMonitor',lambda *a,**kw:NS(check=lambda *a:None,event=lambda *a:None))
    # Read-only svc view over a COPY of the live store; decision is reported as not yet
    # recorded (pre-verdict state) because the incident batch already closed inconclusive.
    from attention_memory_service.memory_service import MemoryService
    store_copy=tmp_path/'store-copy.sqlite3';backup(WORKER.parent/'store.sqlite3',store_copy)
    svc=MemoryService(store_copy,dev_v2_registration_roots=json.loads((WORKER.parent/'registration-roots.json').read_bytes()))
    with sqlite3.connect(store_copy) as db:  # private copy only: restore the pre-verdict state
        db.execute('DELETE FROM memory_v2_decisions WHERE memory_id=?',(incident.batch['memory_id'],))
    class Client:
        def get_memory(self,memory_id):return svc.store.get_memory(memory_id)
        def __getattr__(self,name):return getattr(svc,name)
    client=Client()
    result=dev_agent_loop.run_agent_cell(store,exp,m,key,validation=dict(
        current=dict(batch,arm=arm,arm_id=key,origin=origin['source'],origin_feedback=incident.batch['origin_feedback']),
        client=client,arm_record=arms[key]))
    ws=Workspace(Path(store.cell(key)['artifact_dir'])/'agent')
    errors=[json.loads(p.read_bytes()) for p in sorted((ws.private/'errors').glob('*.json'))] if (ws.private/'errors').exists() else []
    print('WALKTHROUGH result:',json.dumps(result)[:400]);print('WALKTHROUGH errors:',json.dumps(errors)[:800]);print('WALKTHROUGH reached:',reached)
    # The re-created experiment has fresh cell keys, so the real svc (store copy) refuses the
    # grant against the incident's frozen pairs. Reaching that call proves creation, fairness,
    # lock, workspace, isolation preflight, Memory delivery, broker and formal submit all pass
    # on real data; the runner guard on the incident's exact bytes is covered separately.
    if reached==['simulator'] or any('OFFLINE_SIMULATOR_BOUNDARY_REACHED' in json.dumps(e) for e in errors):return
    assert result.get('fatal_cause')=='broker_invariant', result
    captured=capfd.readouterr()
    assert 'outside frozen single-scene pairs' in captured.out+captured.err, result
