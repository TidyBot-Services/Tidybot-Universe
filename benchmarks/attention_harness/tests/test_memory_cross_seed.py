import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness.memory_validation_worker import settings,atomic,tick
from benchmarks.attention_harness.memory_cross_seed import register_contract,provision_cross
from benchmarks.attention_harness.tests.cross_seed_fakes import CrossFakeRunner,lifecycle
from benchmarks.attention_harness.tests.autovalidate_fakes import synthetic_creation


def test_fake_e2e(tmp_path):
    fake,state=lifecycle(tmp_path)
    assert [len(b['cells']) for b in fake.cross_batches]==[1,10,1,6]
    assert [r['role'] for r in fake.service.cross_seed.status(memory_id=fake.candidate_id)['seeds']]==['neutral','transfer']
    for b in fake.cross_batches:
        for key in b['cells']:
            r=fake.f.records[key]
            if b['stage']=='probe':assert 'model_input' not in r['record']
            else:
                packet=json.loads(json.loads(Path(r['record']['model_input']['uri']).read_bytes())['messages'][1]['content'])
                expected=[{'content':fake.service.get_memory(b['memory_id']).guidance}] if r['trial']['exposure']=='candidate_validation' else []
                assert packet['attention_control']=={'memory':expected}
                assert not {'run_notes','family','help'}&packet.keys()


@pytest.mark.parametrize('cross',[None,{},dict(enabled=False),dict(enabled=True,candidate_seeds=[],contracts={})])
def test_config_defaults(tmp_path,cross):
    assert settings(tmp_path)=={'enabled':False}
    value=dict(enabled=True)
    if cross is not None:value['cross_seed']=cross
    atomic(tmp_path/'autovalidate.json',value)
    assert settings(tmp_path).get('cross_seed',{}).get('enabled',False) is (cross is not None and cross.get('enabled',False))


@pytest.mark.parametrize('fault',['missing','outside','wrong_seed','refused'])
def test_no_contract_fallback(tmp_path,fault):
    p=tmp_path/'contract.json'
    p.write_text(json.dumps(dict(schema='attentionbench.public-scene-contract.v2',tolerance=1e-6,tasks={'pick':{'seeds':{'114' if fault!='wrong_seed' else '115':{'normalized_sha256':'a'*64}}}})))
    calls=[]
    def register(**kw):
        calls.append(kw)
        if fault=='refused':raise PermissionError()
    client=SimpleNamespace(register_dev_v2_catalog=register)
    with pytest.raises(Exception,match={'missing':'no scene contract registered','outside':'outside','wrong_seed':'does not contain','refused':'registration refused'}[fault]):
        register_contract(client,[] if fault=='outside' else [str(tmp_path)],None if fault=='missing' else str(p),114,{'task_id':'pick'})
    assert len(calls)==int(fault=='refused')


def test_provision_seed_aliases_and_clean_budget(tmp_path):
    fake=CrossFakeRunner(tmp_path/'fake',tmp_path/'state');note=fake.promote_origin()
    target=fake.admit_seed(note,114,fake.paths['114'])
    atomic(tmp_path/'state/state.json',{})
    with synthetic_creation(fake.f):
        batch=provision_cross(fake.provision,note,'probe-operation',target)
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_experiment import snapshot,verify_manifest
    store=DevV2Store(next(iter(batch['cells'].values()))['ledger']);e=snapshot(store,batch['experiment_id'])
    with synthetic_creation(fake.f):m=verify_manifest(e)
    assert len(e['cells'])==1 and m['goal_limits']['executions']==1
    config=json.loads(Path(batch['config']['uri']).read_bytes())
    assert {k:v for k,v in config.items() if k not in {'seed','scene_contract'}}=={k:v for k,v in fake.failed['config'].items() if k not in {'seed','scene_contract'}}
    assert config['seed']==114 and config['scene_contract']==target['contract']
    assert e['cells'][0]['run_notes'] is e['cells'][0]['memory_family'] is False


def worker_deps(root,role='transfer'):
    from benchmarks.attention_harness.tests.test_memory_validation_worker import deps,Runner
    d=deps(root);note=dict(memory_id='cross',version=1,title='Cross note',source_seed=102,seeds=[],uses=1)
    d.client.cross_seed_queue=lambda:[note]
    d.client.cross_seed_status=lambda **kw:dict(memory_id='cross',seeds=[],task_trust={})
    d.client.cross_seed_write=lambda *a,**kw:None
    d.client.validation_queue=lambda:[]
    r=d.runner;r.cross=[]
    r.admit_seed=lambda n,s,p:dict(seed=s,contract={'uri':p}) if p else (_ for _ in ()).throw(ValueError(f'Seed {s}: no scene contract registered'))
    def begin(n,op,target,*,pairs=None):
        r.cross.append(pairs)
        return dict(kind='cross_seed',memory_id=n['memory_id'],note=n,target=target,target_seed=target['seed'],operation=op,stage='probe' if pairs is None else 'pairs',probe_cell='probe',plan={'pairs':[dict(control_cell=f'cc{i}',treatment_cell=f'tt{i}') for i in range(pairs or 0)]})
    r.begin_cross=begin
    finish=r.finish
    r.finish=lambda c:dict(probe=dict(role=role)) if c['stage']=='probe' else finish(c)
    atomic(root/'autovalidate.json',dict(enabled=True,cross_seed=dict(enabled=True,candidate_seeds=[114],contracts={'114':'/fake/114.json'})))
    return d


@pytest.mark.parametrize('role,pairs',[('transfer',5),('neutral',3),('could_not_finish',None)])
def test_worker_probe_then_service_role(tmp_path,role,pairs):
    d=worker_deps(tmp_path,role);state={};tick(state,d)
    assert state['runs_today']==1 and d.runner.cross==[None]
    d.runner.phase='completed';tick(state,d)
    assert state['current'] is None
    if pairs is None:assert 'cross_waiting' not in state;return
    tick(state,d)
    assert d.runner.cross==[None,pairs] and state['runs_today']==2


@pytest.mark.parametrize('role,used,allowed',[('transfer',10,True),('transfer',11,False),('neutral',14,True),('neutral',15,False)])
def test_whole_pair_budget_or_nothing(tmp_path,role,used,allowed):
    d=worker_deps(tmp_path,role);state={};tick(state,d);d.runner.phase='completed';tick(state,d)
    state['runs_today']=used;tick(state,d)
    assert len(d.runner.cross)==(2 if allowed else 1)
    assert state['runs_today']==used+int(allowed)


def test_same_scene_first_and_cross_missing_skipped(tmp_path):
    d=worker_deps(tmp_path);queue=[dict(memory_id='same',version=1,title='Same',uses=2)]
    d.client.validation_queue=lambda:queue;state={};tick(state,d)
    assert d.runner.begun==['same'] and not d.runner.cross
    state['current']=None;queue.clear()
    value=settings(tmp_path);value['cross_seed']['contracts']={};atomic(tmp_path/'autovalidate.json',value)
    tick(state,d)
    assert state['skipped_seeds'][0]['reason']=='Seed 114: no scene contract registered' and not d.runner.cross


@pytest.mark.parametrize('reason',['activity','weekly','daily','unknown','disabled'])
def test_cross_shared_pause_resume_and_quota(tmp_path,reason):
    d=worker_deps(tmp_path);state={};tick(state,d)
    if reason=='activity':d.activity=lambda c:'The frozen main matrix is planned or running'
    if reason=='weekly':d.quota=lambda:70
    if reason=='daily':d.quota=lambda:16
    if reason=='unknown':d.quota=lambda:None
    if reason=='disabled':
        value=settings(tmp_path);value['cross_seed']['enabled']=False;atomic(tmp_path/'autovalidate.json',value)
    tick(state,d);assert d.runner.pauses==1 and state['runs_today']==1
    d.activity=lambda c:None;d.quota=lambda:10
    value=settings(tmp_path);value['cross_seed']['enabled']=True;atomic(tmp_path/'autovalidate.json',value)
    tick(state,d);assert d.runner.resumes==1 and state['runs_today']==1


def test_cross_disabled_does_not_query_service(tmp_path):
    d=worker_deps(tmp_path);value=settings(tmp_path);value['cross_seed']['enabled']=False;atomic(tmp_path/'autovalidate.json',value)
    d.client.cross_seed_queue=lambda:pytest.fail('cross-seed queue called while disabled')
    tick({},d);assert not d.runner.cross


def test_probe_independent_validator_refuses_notes_and_models(tmp_path):
    from benchmarks.attention_harness.tests.test_memory_independent_validator import arm
    from benchmarks.attention_harness.memory_independent_validator import validate_arm
    f,r,args=arm(tmp_path);args.update(grant=None,probe_input=r['descriptor']['validation_input'],origin_source_sha256=r['record']['source']['sha256'])
    result=validate_arm(**args);assert result['status']=='passed' and 'trial.json' not in result['catalog']['files']
    clean=json.loads(Path(args['probe_input']['uri']).read_bytes());clean['memory_ids']=['forbidden']
    args['probe_input']=f.write(Path(args['probe_input']['uri']),clean);args['executor_catalog']=f.seal()['arm']
    assert validate_arm(**args)['status']=='refused'


def test_task_trusted_order_and_frozen_allowlist():
    from benchmarks.attention_harness.memory_layers import order,label
    from benchmarks.attention_harness.formal_memory_contract import FrozenMemoryGateway
    trusted=dict(memory_id='new-task',version=1,layer='task',match_level='same_task',trusted_here=True)
    reference=dict(memory_id='reference',version=1,layer='task',trusted_here=False)
    run=dict(layer='run',help_version=2)
    assert order([reference,trusted],[run])==[trusted,run,reference]
    assert label(trusted)=='Trusted for this task: ' and label(reference).startswith('Reference')
    signed=dict(trusted,memory_id='signed')
    service=SimpleNamespace(retrieve_entries=lambda *a,**kw:[trusted,signed,reference])
    frozen=FrozenMemoryGateway(service,dict(visibility='trusted',context={},initial_state=dict(versions=[dict(memory_id='signed',version=1)])))
    assert frozen.retrieve_entries({},now=1)==[signed]


def test_dev_memory_accepts_service_task_trust_and_keeps_references(monkeypatch):
    from benchmarks.attention_harness import dev_planner_memory as memory
    ctx={k:'same' for k in memory.TASK_SCOPE+memory.SCENE_SCOPE}
    rows=[dict(memory_id='task',version=1,guidance='Use the exact name',status='trusted',applicability=ctx,match_level='same_task',trusted_here=True),
          dict(memory_id='ref',version=1,guidance='A reference',status='trusted',applicability=ctx,match_level='same_task',trusted_here=False)]
    gateway=SimpleNamespace(store_id=lambda:'authority',retrieve_entries=lambda *a,**kw:rows)
    monkeypatch.setattr(memory,'context',lambda *a:ctx)
    monkeypatch.setattr(memory,'memory_scope',lambda *a:{'level':'scene'})
    reader=memory.DevMemory(gateway,dict(namespace='same',store_id='authority'),{},layered=True)
    assert [n.memory_id for n in reader.eligible()]==['task']
    assert [n.memory_id for n in reader.selected()]==['task','ref']
    assert [n.trusted_here for n in reader.selected()]==[True,False]
    # Missing attestation retains the old reference default. A family claim or
    # provisional note cannot elevate itself with a true boolean.
    rows[0].pop('trusted_here');assert reader.eligible()==[]
    rows[0].update(trusted_here=True,status='provisional');assert [n.memory_id for n in reader.selected()]==['ref']


def test_runtime_probe_executes_source_without_model_and_exports_target_contract(tmp_path,monkeypatch):
    from benchmarks.attention_harness.memory_validation_runtime import LedgerRunner
    from benchmarks.attention_harness import dev_v2_loop,dev_planner_memory
    from benchmarks.attention_harness.dev_v2_memory_import import prepare
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_loop import process_identity
    from benchmarks.attention_harness.memory_source_catalogs import manifest_sources
    fake=CrossFakeRunner(tmp_path/'fake',tmp_path/'state');note=fake.promote_origin();atomic(tmp_path/'state/state.json',{})
    target=fake.admit_seed(note,114,fake.paths['114'])
    runner=LedgerRunner(fake.proxy,fake.provision,experiments_root=fake.f.root,validator_root=fake.f.validator,state_root=tmp_path/'state',provider=lambda *a:pytest.fail('probe called model'))
    with synthetic_creation(fake.f):batch=runner.begin_cross(note,'runtime-probe',target)
    key=batch['probe_cell'];current=dict(batch,arm_id=key,arm='probe');store=DevV2Store(batch['cells'][key]['ledger'])
    owner=process_identity();store.update_cell(key,state='running',owner=owner)
    def execute(s,k,v,record,config,who,directory,*args,**kw):
        assert not kw.get('memory_validation')
        assert Path(record['source']['uri']).read_bytes()==Path(batch['origin']['uri']).read_bytes()
        clean=json.loads((directory/'memory-validation-input.json').read_bytes());assert clean['memory_ids']==[]
        assert clean['exposure']=='none' and 'validation_grant_id' not in clean
        job=s.reserve(k,v,'execute',{'executions':1});s.claim(job['job_id'],who)
        bundle=dev_v2_loop.immutable(directory/'bundle.json',dict(artifact_dir=str(directory)))
        dev_v2_loop.immutable(directory/'scene-condition.json',dict(evidence_kind='controlled_test'))
        s.settle(job['job_id'],{'executions':1},dict(bundle=bundle))
    monkeypatch.setattr(dev_v2_loop,'execute',execute)
    runner._run(current,owner);runner.pool.shutdown()
    cell=store.cell(key);assert [j['kind'] for j in cell['jobs']]==['execute']
    with store.tx() as db:e=store.get(db,'experiments',cell['experiment_id'])
    monkeypatch.setattr(dev_planner_memory,'context',lambda *a,**kw:batch['context'])
    monkeypatch.setattr(dev_planner_memory,'sdk_identity',lambda config:fake.f.sdk)
    sources=manifest_sources(e['manifest'],batch['context'],fake.f.root,contract=target['contract'])
    output=Path(e['artifact_dir'])/'export'
    with synthetic_creation(fake.f):ref=prepare(store,key,1,batch['context']['namespace'],output,fake.f.root,source_catalogs=sources,contract_catalog=target['catalog'])
    descriptor=json.loads((output/'descriptor.json').read_bytes())
    assert descriptor['context']['seed']==114 and descriptor['context']['scene_contract_ref']==target['contract']
    assert json.loads(Path(ref['uri']).read_bytes())['role']=='executor'


def test_real_probe_could_not_finish_and_failed_check_exit(tmp_path):
    fake=CrossFakeRunner(tmp_path/'fake',tmp_path/'state',roles={107:'could_not_finish',104:'neutral'})
    note=fake.promote_origin();fake.enable_cross();state={};atomic(tmp_path/'state/state.json',state);deps=fake.deps(tmp_path/'state')
    for _ in range(15):
        tick(state,deps);fake.phase='completed'
        current=state.get('current')
        if current and current.get('stage')=='pairs':fake.failed_check=(current['plan']['pairs'][0]['treatment_cell'],'safety')
        rows=fake.service.cross_seed.status(memory_id=note['memory_id'])['seeds']
        if any(r['verdict']=='neutral_harm' for r in rows):break
    assert {r['target_seed']:r['verdict'] for r in rows}=={107:'could_not_finish',104:'neutral_harm'},state['status']
    assert state['runs_today']==4 and state['current'] is None
    assert fake.service.decision(note['memory_id'])['trust_scope']=='scene_scoped'
