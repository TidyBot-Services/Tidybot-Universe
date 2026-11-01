"""Real runner guard, phase-3 dispatch/export, and in-process service verification."""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
import pytest

from benchmarks.attention_harness.tests.agent_validation_fakes import fixture, synthetic_creation
from benchmarks.attention_harness.tests.agent_validation_runner_fakes import install, SCRIPT
from benchmarks.attention_harness.tests.test_dev_agent_memory_phase3 import run_arm
from benchmarks.attention_harness.dev_v2_loop import checked, immutable
from benchmarks.attention_harness.core.dev_store import DevConflict
from openarm365_dev_v2.runner import OpenArm365DevRunnerV2


@pytest.mark.parametrize('mode',['onecall','onecall_validation','formal','agent'])
def test_runner_bytes_match_main(tmp_path,monkeypatch,mode):
    """Compare every emitted byte at identical paths against main 75300b8."""
    import shutil
    from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
    from benchmarks.attention_harness.tests.test_agent_validation_onecall_goldens import baseline,snapshot
    from openarm365_dev_v2 import runner as current
    old=baseline(current,'75300b8')
    f=install(SimpleNamespace(runner=SimpleNamespace(),executions=[]),monkeypatch)
    config=dict(task_id='pick',task_prompt='Offline',seed=101,controller='BASIC',safety_limits={},
        sdk_call_limit=320,attempt_wall_seconds=1920,runner_boundary='dev_loop',budget_tier='dev')
    if mode=='formal':config.pop('budget_tier')
    monkeypatch.setattr(current,'validate_config',lambda c:c)
    monkeypatch.setattr(current,'execute_formal_policy',lambda **kw:SimpleNamespace(status='completed',error=None,call_count=0))
    for name in ('validate_config','NativeOwner','probe_sandbox','finalize_recording','execute_formal_policy'):
        monkeypatch.setattr(old,name,getattr(current,name))
    monkeypatch.setattr(current.time,'monotonic',lambda:100.)
    monkeypatch.setattr(current.uuid,'uuid4',lambda:SimpleNamespace(hex='a'*32))
    path=tmp_path/'policy.py';path.write_text('from robot_sdk import sensors\nsensors.get_observation()\n')
    cref=immutable(tmp_path/'config.json',config)
    request=FormalRunRequest(suite='robocasa365',task_id='pick',seed=101,policy_code_path=path,
        policy_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),config_path=Path(cref['uri']),
        config_sha256=cref['sha256'],run_id='run:golden',attempt_id='attempt:golden:0',
        artifact_root=tmp_path/'episodes',overall_deadline_seconds=30,attempt_deadline_seconds=30,sdk_call_limit=320)
    envelope=None
    if mode=='onecall_validation':
        binding=dict(policy_sha256=request.policy_sha256,config_sha256=request.config_sha256,
            cell='golden',version=1,seed=101,memory_id='candidate',exposure='candidate_validation',context={},memory_version=1)
        grant=immutable(tmp_path/'grant.json',dict(binding=binding,grant_id='golden'))
        value=dict(memory_ids=['candidate'],exposure='candidate_validation',context={},
            policy_sha256=request.policy_sha256,config_sha256=request.config_sha256,validation_grant_id='golden')
        envelope=dict(input=immutable(tmp_path/'input.json',value),grant=grant,guidance='Golden note')
    outputs=[]
    for module in (old,current):
        runner=module.OpenArm365DevRunnerV2(service_source_root=None,sim_python=None,sim_source_root=None,
            task_source_root=None,asset_manifest=None,agent_mode=mode=='agent',memory_validation=envelope)
        if mode=='formal':runner.runner_boundary='formal'
        result=runner.execute(request)
        assert result['status']=='completed',result
        outputs.append((result,snapshot(request.artifact_root)))
        shutil.rmtree(request.artifact_root)
    assert outputs[0]==outputs[1]


def test_real_config_and_phase3_delivery_without_registry_stubs(tmp_path,monkeypatch):
    """Only native processes/recording and namespace transport are substituted.

    Uses the actual local SDK/model/asset lock and real config validation. The
    small grant client is fixture setup; service authentication is independently
    exercised by test_real_runner_consumption_export_and_service.
    """
    import time
    from openarm365_dev_v2.dev_config import make_lock,_make_dev_config,validate_dev_config
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_agent_workspace import Workspace
    from benchmarks.attention_harness.dev_agent_adapters import Executor
    from benchmarks.attention_harness.memory_agent_validation import Delivery,ARM_BUDGET,seal_worker_arms
    lock=make_lock(tmp_path/'sdk-lock.json')
    config=dict(_make_dev_config('pick_place_counter_to_sink',101,lock,budget_tier='dev'),dev_mode='agent')
    assert validate_dev_config(config)==config
    config_ref=immutable(tmp_path/'config.json',config)
    code='from robot_sdk import sensors\nprint(context["memory_validation"]["guidance"])\nsensors.get_observation()\n# fixed\n'
    source=immutable(tmp_path/'origin.py',code)
    models=dict(dev_model='gpt-6.1-sol',dev_reasoning_effort='high',eval_model='gpt-6.1-sol')
    original=immutable(tmp_path/'origin.json',dict(dev_mode='agent',cells=[dict(key='origin',policy_id='reactive_help')],**models))
    origin=dict(manifest=original,config=config_ref,source=source,cell='origin',version=1)
    selection=dict(mode='diagnostic',help_budget=0,run_notes=False,memory_family=False,target_depth=1,
        target_valid_executions=1,max_versions=1,reproducible_reset=True,attempt_wall_seconds=1920,
        agent_validation=origin,**models)
    cells=[dict(key=f'{arm}{i}',task_id=config['task_id'],seed=101,policy_id='reactive_help',
        validation_pair=i,validation_arm=arm,excluded_from_results=True) for i in range(5) for arm in ('control','treatment')]
    store=DevV2Store(tmp_path/'ledger.sqlite');key='treatment0';exp_id='offline-runner-test'
    m=dict(schema='attentionbench.dev-experiment.v3',experiment_id=exp_id,ledger_path=str(store.path),
        formal_eligible=False,validation_experiment=True,dev_mode='agent',validation_origin=origin,
        agent_validation=origin,selection=selection,agent_budget=ARM_BUDGET,cells=cells,
        configs={f"{config['task_id']}:101":config},schedule=[dict(cell=c['key'],through_version=1) for c in cells],**models)
    ref=immutable(tmp_path/'experiment.json',m)
    with store.tx() as db:
        store.put(db,'experiments',exp_id,dict(manifest=ref))
        for cell in cells:
            store.put(db,'cells',cell['key'],dict(cell,experiment_id=exp_id,
                artifact_dir=str(tmp_path/'cell') if cell['key']==key else str(tmp_path/'cells'/cell['key']),
                dev_mode='agent',validation_experiment=True))
    batch=dict(experiment_id=exp_id,memory_id='offline-candidate',config=config_ref,
        dev_model=dict(provider='codex',model=models['dev_model']),
        dev_reasoning_effort=models['dev_reasoning_effort'],eval_model=models['eval_model'],
        cells={c['key']:dict(ledger=str(store.path)) for c in cells},
        pairs=[dict(control_cell=f'control{i}',treatment_cell=f'treatment{i}') for i in range(5)])
    arms=seal_worker_arms(batch,'offline-note',tmp_path/'worker/arms')
    ws=Workspace(tmp_path/'cell/agent');immutable(ws.private/'sources/submit.py',code)
    note=SimpleNamespace(memory_id='offline-candidate',version=1,guidance='Offline note',status='candidate')
    def authorize(**kw):
        return dict(schema_version='attentionbench.dev-v2-validation-grant.v1',grant_id='offline-grant',issued_at=time.time(),
            binding=dict(kw,memory_version=1,dev_model=dict(provider='codex',model=models['dev_model']),
                guidance_sha256=hashlib.sha256(note.guidance.encode()).hexdigest(),plan_sha256='a'*64))
    client=SimpleNamespace(get_memory=lambda mid:note,get_plan=lambda mid:{},decision=lambda mid:None,
        authorize_dev_v2_validation=authorize)
    memory=SimpleNamespace(key=key,store=store,config=config,workspace=ws)
    delivery=Delivery(memory,dict(memory_id=note.memory_id,plan={},origin=source,arm='treatment',context={}),client,arm_record=arms[key])
    from benchmarks.attention_harness.memory_agent_validation import seal_controller
    memory.validation=delivery
    seal_controller(memory,None,SimpleNamespace(helper=None))
    memory.packet,_=delivery.load(1,code,ws.private/'memory/v1')
    from benchmarks.attention_harness.dev_agent_eval import feedback
    from benchmarks.attention_harness.dev_v2_loop import prompt_memory_view
    ws.publish('memory/v1.json',feedback(prompt_memory_view(dict(memory=memory.packet['entries']))))
    envelope=delivery.prepare(dict(version_id='v0001',request_id='submit'))
    f=install(SimpleNamespace(runner=SimpleNamespace(),executions=[]),monkeypatch,synthetic_registry=False)
    executor=Executor(config,sim_python=None,runner_factory=f.runner.runner_factory,
        validation_arm=arms[key],agent_configuration=dict(models,agent_policy_version='agent-policy-v1'))
    executor.bind_execution(key,1,'e1');executor.memory_validation=envelope
    # No GPU sampler is needed to test native runner dispatch in this offline test.
    from benchmarks.attention_harness import gpu_meter
    monkeypatch.setattr(gpu_meter,'GpuMeter',lambda:SimpleNamespace(start=lambda:None,stop=lambda:{}))
    (tmp_path/'execution').mkdir()
    result=executor.execute(code.encode(),tmp_path/'execution',deadline=time.monotonic()+30)
    assert result['status']=='completed',result
    trace=json.loads(checked(executor.result['artifacts']['trace']).read_bytes())
    assert trace['memory_validation']['consumed_before_policy'] is True
    assert trace['memory_validation']['memory_ids']==[note.memory_id]
    assert 'Offline note' in result['stdout']


def test_origin_drift_record_does_not_relax_running_arm_lock(tmp_path,monkeypatch):
    from openarm365_dev_v2.dev_config import make_lock,_make_dev_config
    from benchmarks.attention_harness import memory_agent_validation
    from attention_memory_service.source_policy import drift_record
    import openarm365_dev_v2.runner as module
    lock=make_lock(tmp_path/'current-lock.json')
    config=dict(_make_dev_config('pick_place_counter_to_sink',101,lock,budget_tier='dev'),dev_mode='agent')
    current=json.loads(Path(lock['path']).read_bytes());origin=deepcopy(current)
    verifier=str(Path(memory_agent_validation.__file__).resolve())
    assert verifier in current['files']
    origin['files'][verifier]='0'*64
    ref=immutable(tmp_path/'prior-lock.json',origin)
    drift=drift_record(config,origin,current,ref['sha256'],lock['sha256'])
    assert drift['files']==[dict(path=verifier,origin_sha256='0'*64,current_sha256=current['files'][verifier])]
    # Treating that prior digest as THIS arm's own lock must still fail in the
    # real runner's config loader, before owner construction or envelope logic.
    config['dev_lock']=dict(path=ref['uri'],sha256=ref['sha256'])
    config_ref=immutable(tmp_path/'bad-config.json',config)
    def forbidden(**kw):pytest.fail('own-lock mismatch reached native owner')
    monkeypatch.setattr(module,'NativeOwner',forbidden)
    runner=OpenArm365DevRunnerV2(service_source_root=None,sim_python=None,sim_source_root=None,
        task_source_root=None,asset_manifest=None,agent_mode=True)
    with pytest.raises(ValueError,match='resource source drift: .*memory_agent_validation.py'):
        runner.execute(SimpleNamespace(config_path=Path(config_ref['uri']),entry_lock=None))


@pytest.fixture
def executed(tmp_path,monkeypatch):
    f=install(fixture(tmp_path,monkeypatch,script=SCRIPT),monkeypatch)
    try:
        with synthetic_creation(f.native):
            batch,current,store,result=run_arm(f,'treatment')
            assert result['status']=='passed',result
            cell=store.cell(current['arm_id']);record=cell['versions'][0]
            job=next(j for j in cell['jobs'] if j['kind']=='execute')
            bundle=json.loads(checked(job['receipt']['bundle']).read_bytes())
            episode=Path(bundle['artifact_dir'])
            request=SimpleNamespace(suite='robocasa365',task_id=cell['task_id'],seed=cell['seed'],
                policy_sha256=bundle['policy_sha256'],config_sha256=bundle['config_sha256'],
                policy_code_path=episode/'policy.py',config_path=episode/'config.json',
                run_id=bundle['run_id'],attempt_id=bundle['attempt_id'],entry_lock=None,
                worker_arm=batch['agent_arm_records'][cell['key']],
                agent_configuration=dict(dev_model=f.model_choices[-1]['model'],
                    dev_reasoning_effort=f.model_choices[-1]['reasoning_effort'],eval_model=f.eval_choices[-1],
                    agent_policy_version='agent-policy-v1'))
            envelope=json.loads((Path(cell['artifact_dir'])/'v1/memory-validation-envelope.json').read_bytes())
            f.executed=SimpleNamespace(batch=batch,current=current,store=store,result=result,
                cell=cell,record=record,bundle=bundle,episode=episode,request=request,envelope=envelope)
            yield f
    finally:f.runner.pool.shutdown()


def test_real_runner_consumption_export_and_service(executed):
    f=executed;e=f.executed
    assert len(f.real_requests)==1 and type(f.real_requests[0]) is OpenArm365DevRunnerV2
    trace=json.loads(checked(e.bundle['artifacts']['trace']).read_bytes())
    from benchmarks.attention_harness.dev_memory_validation import policy_input
    public,receipt=policy_input(e.envelope,e.request)
    assert trace['memory_validation']==receipt
    assert trace['status']=='completed' and len(trace['sdk_events'])==2
    assert f.policy_contexts[0]['memory_validation']==public
    assert public['guidance'] in (e.episode/'policy-stdout.txt').read_text()
    # finish() already imported execution+trial via the real service, which
    # independently checks agent_validation.shape/usage/generation and receipt.
    assert e.result['status']=='passed' and e.cell['agent_export']=='v1'


def test_real_runner_control_arm(tmp_path,monkeypatch):
    f=install(fixture(tmp_path,monkeypatch,script=SCRIPT),monkeypatch)
    try:
        with synthetic_creation(f.native):
            batch,current,store,result=run_arm(f,'control')
            assert result['status']=='passed',result
            record=store.cell(current['arm_id'])['versions'][0]
            assert record['agent']['native_success'] is False
            context=f.policy_contexts[-1]['memory_validation']
            assert context==dict(memory_ids=[],exposure='none',guidance=None)
            assert len(f.real_requests)==1
    finally:f.runner.pool.shutdown()


def test_real_runner_v2_validation_override(tmp_path,monkeypatch):
    f=install(fixture(tmp_path,monkeypatch,script=SCRIPT,
        policy_version='agent-policy-v2',policy='trace_aware_hint_only'),monkeypatch)
    try:
        with synthetic_creation(f.native):
            batch,current,store,result=run_arm(f,'treatment')
            assert result['status']=='passed',result
            assert len(f.real_requests)==1
            assert f.policy_contexts[-1]['memory_validation']['exposure']=='candidate_validation'
            assert store.cell(current['arm_id'])['memory_visibility']=='validation_override'
    finally:f.runner.pool.shutdown()


@pytest.mark.parametrize('fault',[
    'empty','flag','onecall','schema','foreign_cell','foreign_version','foreign_policy','foreign_config',
    'source_bytes','config_bytes','input_digest','grant_digest','guidance','forged_grant',
    'missing_dispatch','duplicate_dispatch','missing_seal','foreign_experiment',
    'budget','models','epoch','advisor','excluded','validation_flag','wrong_arm',
    'nondev','formal_boundary'])
def test_real_runner_refuses_before_native_start(executed,monkeypatch,tmp_path,fault):
    f=executed;e=f.executed;envelope=deepcopy(e.envelope);request=deepcopy(e.request)
    config=json.loads(request.config_path.read_bytes())
    if fault=='empty':envelope={}
    elif fault=='flag':envelope=True
    elif fault=='onecall':envelope={k:envelope[k] for k in ('input','grant','guidance')}
    elif fault=='schema':envelope['schema_version']='foreign'
    elif fault=='foreign_cell':request.run_id='run:foreign'
    elif fault=='foreign_version':request.attempt_id=request.attempt_id[:-1]+'1'
    elif fault=='foreign_policy':request.policy_sha256='0'*64
    elif fault=='foreign_config':request.config_sha256='0'*64
    elif fault in ('source_bytes','config_bytes'):
        field='policy_code_path' if fault=='source_bytes' else 'config_path'
        path=tmp_path/(fault+'.txt');path.write_bytes(getattr(request,field).read_bytes()+b'\n')
        setattr(request,field,path)
    elif fault in ('input_digest','grant_digest'):envelope[fault.split('_')[0]]['sha256']='0'*64
    elif fault=='guidance':envelope['guidance']+=' forged'
    elif fault=='forged_grant':
        grant=json.loads(checked(envelope['grant']).read_bytes());grant['binding']['plan_sha256']='0'*64
        envelope['grant']=immutable(tmp_path/'forged-grant.json',grant)
    elif fault in ('missing_dispatch','duplicate_dispatch','missing_seal'):
        with e.store.tx() as db:
            kind='agent_validation_runner_dispatch' if fault=='missing_seal' else 'memory_validation_dispatch'
            if fault=='duplicate_dispatch':
                row=db.execute('SELECT payload FROM v2_events WHERE kind=?',(kind,)).fetchone()
                e.store.event(db,kind,json.loads(row[0]))
            else:db.execute('DELETE FROM v2_events WHERE kind=?',(kind,))
    elif fault=='nondev':config['budget_tier']='legacy'
    elif fault=='formal_boundary':pass
    else:
        m=json.loads(checked(envelope['experiment']).read_bytes())
        if fault=='budget':m['agent_budget']['help']=1
        elif fault=='models':m['dev_model']='gpt-6-sol'
        elif fault=='epoch':m['agent_policy_version']='agent-policy-v2'
        elif fault=='advisor':m['advisor_proxy']={'enabled':True}
        elif fault=='excluded':m['cells'][0]['excluded_from_results']=False
        elif fault=='validation_flag':m['validation_experiment']=False
        elif fault=='wrong_arm':
            cell=next(c for c in m['cells'] if request.run_id=='run:'+c['key']);cell['validation_arm']='control'
        envelope['experiment']=immutable(tmp_path/'foreign-experiment.json',m)
    # The guard runs before episode creation, probes, or owner construction.
    import openarm365_dev_v2.runner as module
    def forbidden(**kw):pytest.fail('invalid envelope reached native owner')
    monkeypatch.setattr(module,'NativeOwner',forbidden)
    monkeypatch.setattr(module,'validate_config',lambda c:config)
    runner=OpenArm365DevRunnerV2(service_source_root=None,sim_python=None,sim_source_root=None,
        task_source_root=None,asset_manifest=None,agent_mode=True,memory_validation=envelope)
    if fault=='formal_boundary':runner.runner_boundary='formal'
    with pytest.raises((ValueError,DevConflict,KeyError,TypeError)):
        runner.execute(request)


def refuse_named(f,monkeypatch,item,*,envelope=None,request=None):
    import openarm365_dev_v2.runner as module
    e=f.executed
    request=request or deepcopy(e.request)
    envelope=deepcopy(e.envelope) if envelope is None else envelope
    config=json.loads(request.config_path.read_bytes())
    def forbidden(**kw):pytest.fail('mismatch reached native owner')
    monkeypatch.setattr(module,'NativeOwner',forbidden)
    monkeypatch.setattr(module,'validate_config',lambda c:config)
    runner=OpenArm365DevRunnerV2(service_source_root=None,sim_python=None,sim_source_root=None,
        task_source_root=None,asset_manifest=None,agent_mode=True,memory_validation=envelope)
    with pytest.raises(DevConflict,match='^'+re.escape('agent validation envelope mismatch: '+item)+'$'):
        runner.execute(request)


@pytest.mark.parametrize('field',['batch_id','arm_id','pair_index','arm_role','note_id','memory_id'])
def test_worker_arm_binding_named_refusal(executed,monkeypatch,field):
    envelope=deepcopy(executed.executed.envelope)
    envelope[field]=(envelope[field]+1)%5 if field=='pair_index' else ('control' if field=='arm_role' else 'foreign')
    refuse_named(executed,monkeypatch,field,envelope=envelope)


@pytest.mark.parametrize('field',['dev_model','dev_reasoning_effort','eval_model','agent_policy_version'])
def test_running_agent_configuration_named_refusal(executed,monkeypatch,field):
    request=deepcopy(executed.executed.request)
    request.agent_configuration[field]='foreign'
    refuse_named(executed,monkeypatch,field,request=request)


def test_other_worker_arm_record_named_refusal(executed,monkeypatch):
    f=executed;e=f.executed;request=deepcopy(e.request)
    request.worker_arm=e.batch['agent_arm_records'][e.batch['pairs'][0]['control_cell']]
    refuse_named(f,monkeypatch,'arm_id',request=request)


def test_missing_agent_configuration_named_refusal(executed,monkeypatch):
    request=deepcopy(executed.executed.request);request.agent_configuration=None
    refuse_named(executed,monkeypatch,'agent_configuration',request=request)


@pytest.mark.parametrize('envelope',[{},False,[]])
def test_adapter_forwards_present_malformed_envelope(executed,monkeypatch,tmp_path,envelope):
    from benchmarks.attention_harness.dev_agent_adapters import Executor
    import openarm365_dev_v2.runner as module
    import time
    f=executed;e=f.executed
    def forbidden(**kw):pytest.fail('malformed envelope reached owner')
    monkeypatch.setattr(module,'NativeOwner',forbidden)
    config=json.loads(e.request.config_path.read_bytes())
    executor=Executor(config,sim_python=None,runner_factory=f.runner.runner_factory)
    executor.memory_validation=envelope;executor.bind_execution(e.cell['key'],1,'bad')
    directory=tmp_path/'bad-execution';directory.mkdir()
    with pytest.raises(DevConflict,match='^agent validation envelope mismatch: schema$'):
        executor.execute(e.request.policy_code_path.read_bytes(),directory,deadline=time.monotonic()+30)


def test_runner_uses_actual_session_configuration(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_agent_loop
    f=install(fixture(tmp_path,monkeypatch,script=SCRIPT),monkeypatch)
    original=dev_agent_loop.CodexAgentSession
    def session(*a,**kw):
        value=original(*a,**kw);value.model='foreign';return value
    monkeypatch.setattr(dev_agent_loop,'CodexAgentSession',session)
    try:
        with synthetic_creation(f.native):
            batch,current,store,result=run_arm(f,'treatment')
            assert result['status']=='failed' and not f.executions
            assert len(f.real_requests)==1 and type(f.real_requests[0]) is OpenArm365DevRunnerV2
            root=Path(store.cell(current['arm_id'])['artifact_dir'])
            responses=[json.loads(p.read_bytes()) for p in (root/'agent/ws/rpc/responses').glob('*.json')]
            assert any(r.get('result',{}).get('execution_error',{}).get('message')==
                'agent validation envelope mismatch: dev_model' for r in responses)
    finally:f.runner.pool.shutdown()


@pytest.mark.parametrize('fault,item',[
    ('empty','memory_packet.entries'),('extra','memory_packet.entries'),
    ('memory_id','memory_packet.memory_id'),('content','memory_packet.content'),
    ('version','memory_packet.version'),('excluded','memory_packet'),
    ('delivered_extra','memory_packet.delivered'),('delivered_missing','memory_packet.delivered'),
    ('canonical_extra','memory_packet.canonical'),('exposure','memory_exposure')])
def test_delivered_memory_packet_named_refusal(executed,monkeypatch,tmp_path,fault,item):
    f=executed;e=f.executed;base=Path(e.cell['artifact_dir'])/'agent'
    if fault in ('delivered_extra','delivered_missing','canonical_extra'):
        path=(base/'private/public' if fault=='canonical_extra' else base/'ws')/'memory/v1.json'
        if fault=='delivered_missing':path.unlink()
        else:
            value=json.loads(path.read_bytes());value['memory'].append(dict(content='Foreign note'))
            path.write_text(json.dumps(value))
    else:
        with e.store.tx() as db:
            row=db.execute("SELECT seq,payload FROM v2_events WHERE kind='agent_validation_memory'").fetchone()
            delivery=json.loads(row[1])
            if fault=='exposure':
                value=json.loads(checked(delivery['dev_memory_exposure']).read_bytes());value['entries']=[]
                delivery['dev_memory_exposure']=immutable(tmp_path/'changed-exposure.json',value)
            else:
                packet=json.loads(checked(delivery['memory_input']).read_bytes())
                if fault=='empty':packet['entries']=[]
                elif fault=='extra':packet['entries'].append(deepcopy(packet['entries'][0]))
                elif fault=='excluded':packet['excluded_from_results']=False
                else:packet['entries'][0][fault]=2 if fault=='version' else 'foreign'
                # Refresh the digest and delivery reference deliberately: test
                # semantic packet checks, not just stale-reference detection.
                delivery['memory_input']=immutable(tmp_path/'changed-packet.json',packet)
            db.execute('UPDATE v2_events SET payload=? WHERE seq=?',(json.dumps(delivery),row[0]))
    refuse_named(f,monkeypatch,item)


@pytest.mark.parametrize('where',['private','delivered'])
def test_control_packet_has_no_note_named_refusal(tmp_path,monkeypatch,where):
    f=install(fixture(tmp_path,monkeypatch,script=SCRIPT),monkeypatch)
    try:
        with synthetic_creation(f.native):
            batch,current,store,result=run_arm(f,'control');assert result['status']=='passed'
            cell=store.cell(current['arm_id']);root=Path(cell['artifact_dir'])
            bundle=json.loads((root/'v1/bundle.json').read_bytes());episode=Path(bundle['artifact_dir'])
            request=SimpleNamespace(suite='robocasa365',task_id=cell['task_id'],seed=cell['seed'],
                policy_sha256=bundle['policy_sha256'],config_sha256=bundle['config_sha256'],
                policy_code_path=episode/'policy.py',config_path=episode/'config.json',
                run_id=bundle['run_id'],attempt_id=bundle['attempt_id'],entry_lock=None,
                worker_arm=batch['agent_arm_records'][cell['key']],
                agent_configuration=json.loads(checked(batch['agent_arm_records'][cell['key']]).read_bytes())['agent_configuration'])
            f.executed=SimpleNamespace(request=request,envelope=json.loads((root/'v1/memory-validation-envelope.json').read_bytes()))
            if where=='delivered':
                (root/'agent/ws/memory/v1.json').write_text(json.dumps(dict(memory=[dict(content='Unexpected note')])))
            else:
                with store.tx() as db:
                    row=db.execute("SELECT seq,payload FROM v2_events WHERE kind='agent_validation_memory'").fetchone()
                    delivery=json.loads(row[1]);packet=json.loads(checked(delivery['memory_input']).read_bytes())
                    packet['entries']=[dict(memory_id=batch['memory_id'],content='Unexpected note')]
                    delivery['memory_input']=immutable(tmp_path/'control-packet.json',packet)
                    db.execute('UPDATE v2_events SET payload=? WHERE seq=?',(json.dumps(delivery),row[0]))
            refuse_named(f,monkeypatch,'memory_packet.delivered' if where=='delivered' else 'memory_packet.entries')
    finally:f.runner.pool.shutdown()
