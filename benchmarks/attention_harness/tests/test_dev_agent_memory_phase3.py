import json,time
from pathlib import Path
import pytest
from benchmarks.attention_harness.tests.agent_validation_fakes import fixture,synthetic_creation
from benchmarks.attention_harness import dev_experiment
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.memory_agent_validation import ARM_BUDGET
from benchmarks.attention_harness.memory_validation_worker import tick,set_enabled,atomic
from benchmarks.attention_harness.tests.autovalidate_fakes import outcome
from types import SimpleNamespace


def run_arm(f,arm='control'):
    note=dict(f.note,created_at=f.service.get_memory(f.note['memory_id']).created_at)
    batch=f.runner.begin(note,'agent-operation-001')
    key=batch['pairs'][0][arm+'_cell'];current=dict(batch,arm_id=key,arm=arm)
    f.runner.start(current,key);until=time.monotonic()+180
    while f.runner.poll(current)=='running':
        assert time.monotonic()<until
        time.sleep(.01)
    store=DevV2Store(batch['cells'][key]['ledger'])
    return batch,current,store,f.runner.finish(current)


def test_agent_validation_provision_and_arm(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch)
    try:
        with synthetic_creation(f.native):
            note=dict(f.note,created_at=f.service.get_memory(f.note['memory_id']).created_at)
            batch=f.runner.begin(note,'agent-operation-001')
            assert len(batch['cells'])==10 and batch['dev_mode']=='agent'
            key=batch['pairs'][0]['treatment_cell']
            store=DevV2Store(batch['cells'][key]['ledger'])
            manifest=dev_experiment.verify_manifest(dev_experiment.snapshot(store,batch['experiment_id']))
            assert manifest['agent_budget']==ARM_BUDGET
            from benchmarks.attention_harness.dev_agent_config import refuse_memory
            assert refuse_memory(manifest) is None
            current=dict(batch,arm_id=key,arm='treatment')
            f.runner.start(current,key)
            until=time.monotonic()+60
            while f.runner.poll(current)=='running':
                assert time.monotonic()<until
                time.sleep(.01)
            cell=store.cell(key)
            assert cell['state']!='invalid_generation',cell
            result=f.runner.finish(current)
            assert result['status']=='passed',result
            from attention_memory_service.core.store import StateConflictError
            with pytest.raises(StateConflictError,match='cannot record results'):f.service.dev_v2._result_evidence({},key)
    finally:f.runner.pool.shutdown()


def test_agent_arm_wall_is_1920_seconds(tmp_path):
    from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
    now=[100.]
    ledger=AgentLedger(tmp_path/'ledger.sqlite','cell',help_limit=0,validation=True,clock=lambda:now[0])
    ledger.reserve('setup','setup',{});ledger.claim('setup');ledger.settle('setup',{})
    assert ledger.snapshot()['deadline']==2020
    now[0]=2021.
    with pytest.raises(ValueError,match='wall_stop'):ledger.reserve('late','request',dict(formal_versions=1))


def test_mixed_batch_refused_and_replacements_cannot_expand_agent_budget(tmp_path,monkeypatch):
    from benchmarks.attention_harness.core.dev_store import DevConflict
    from benchmarks.attention_harness.memory_validation_replacements import authorize_replacement
    f=fixture(tmp_path,monkeypatch)
    try:
        with synthetic_creation(f.native):
            batch=f.provision(f.note,'agent-operation-001')
            pair=batch['pairs'][0];store=DevV2Store(batch['cells'][pair['control_cell']]['ledger'])
            current=dict(batch,pair_index=0,pair_version=2,plan={'pairs':batch['pairs']},
                discarded_pairs=[dict(version=1,reason='invalid',arm_id=pair['control_cell'],arm='control')])
            with pytest.raises(DevConflict,match='second formal version'):authorize_replacement(store,current)
            store.update_cell(pair['control_cell'],dev_mode='one_call')
            note=dict(f.note,created_at=f.service.get_memory(f.note['memory_id']).created_at)
            with pytest.raises(DevConflict,match='mixed agent validation batch'):f.runner.begin(note,'agent-operation-001')
    finally:f.runner.pool.shutdown()


def test_agent_budgets_stop_extra_probes_trials_help_and_formal(tmp_path,monkeypatch):
    script="""if turn==1:
 for i in range(60):assert json.loads(tool('observe').stdout)['status']=='completed'
 assert json.loads(tool('observe').stdout)['error']=='budget_exhausted'
 for i in range(4):assert json.loads(tool('run','--no-eval').stdout)['status']=='completed'
 assert json.loads(tool('run','--no-eval').stdout)['error']=='budget_exhausted'
 answer=json.loads(tool('ask_help','--question','Need help').stdout)
 assert answer.get('error') or answer.get('result',{}).get('delivered') is not True
 tool('submit')
"""
    f=fixture(tmp_path,monkeypatch,script=script)
    try:
        with synthetic_creation(f.native):
            batch,current,store,result=run_arm(f)
            assert result['status']=='passed',result
            from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
            ledger=AgentLedger(store.path,current['arm_id'],help_limit=0,validation=True)
            assert ledger.snapshot()['used']==dict(formal_versions=1,moving_executions=5,read_only_probes=60,eval_runs=1,help=0,trial_runs=4)
            with pytest.raises(ValueError,match='budget_exhausted'):
                ledger.reserve('forbidden-second-formal','request',dict(formal_versions=1,moving_executions=1),execution=True)
            with pytest.raises(ValueError,match='budget_exhausted'):ledger.reserve('forbidden-help','help',dict(help=1))
            assert len(f.executions)==5
    finally:f.runner.pool.shutdown()


def test_validation_exposure_tampering_refused(tmp_path,monkeypatch):
    from attention_memory_service.core.store import StateConflictError
    f=fixture(tmp_path,monkeypatch)
    try:
        with synthetic_creation(f.native):
            batch,current,store,result=run_arm(f,'treatment')
            assert result['status']=='passed'
            record=store.cell(current['arm_id'])['versions'][0]
            path=Path(record['dev_memory_exposure']['uri']);path.chmod(0o600)
            value=json.loads(path.read_bytes());value['entries'][0]['guidance_sha256']='0'*64
            path.write_text(json.dumps(value))
            with pytest.raises(StateConflictError):
                f.service.import_dev_v2_trial(import_id='tampered-trial',catalog_id='validator-'+current['arm_id'],descriptor='trial.json')
    finally:f.runner.pool.shutdown()


@pytest.mark.parametrize('script,reason',[
    ("Path('work/main.py').write_text('import os\\nos.system(\\\"forbidden\\\")\\n'); tool('submit')",'budget_exhausted'),
    ("raise RuntimeError('scripted Dev crash')",'fatal_error')])
def test_invalid_submit_or_model_crash_is_inconclusive(tmp_path,monkeypatch,script,reason):
    f=fixture(tmp_path,monkeypatch,script=script)
    try:
        with synthetic_creation(f.native):
            batch,current,store,result=run_arm(f)
            cell=store.cell(current['arm_id'])
            assert cell['state']=='invalid_generation' and cell['reason']=='agent_arm_invalid:'+reason
            assert not f.executions and result['status']=='failed'
            assert f.service.record_dev_v2_failed_check(**result['failed_check'])['decision']=='inconclusive'
            assert not f.service.list_pairs(batch['memory_id'])
    finally:f.runner.pool.shutdown()


@pytest.mark.parametrize('change',[dict(max_versions=2),dict(help_budget=1),dict(advisor_proxy={'enabled':True}),
    dict(memory_authority={'anything':True}),dict(positive_control=True),dict(fork={'anything':True}),
    dict(attempt_wall_seconds=1919),dict(startup_cleanup_seconds=120),dict(wall_limit=1),dict(seeds=[102])])
def test_non_arm_shapes_refused(tmp_path,monkeypatch,change):
    from benchmarks.attention_harness.memory_validation_provision import validate_creation
    f=fixture(tmp_path,monkeypatch)
    try:
        with synthetic_creation(f.native):
            batch=f.provision(f.note,'agent-operation-001')
            store=DevV2Store(next(iter(batch['cells'].values()))['ledger'])
            manifest=dev_experiment.verify_manifest(dev_experiment.snapshot(store,batch['experiment_id']))
            with pytest.raises((ValueError,RuntimeError)):
                validate_creation(dict(manifest['selection'],**change),manifest['validation_origin'])
            for cross in (dict(seed=102,contract=f.native.contract,runs=10),dict(seed=101,contract=f.native.contract,runs=6,family_check_id='family-test')):
                with pytest.raises((ValueError,RuntimeError)):
                    validate_creation(manifest['selection'],dict(manifest['validation_origin'],cross_seed=cross))
    finally:f.runner.pool.shutdown()


@pytest.mark.parametrize('result,models,policy_version,policy',[
    pytest.param('passed',dict(dev_model='gpt-6.1-sol',dev_reasoning_effort='medium',eval_model='gpt-6-sol'),'agent-policy-v1','reactive_help',id='passed-sol'),
    pytest.param('passed',dict(dev_model='gpt-6-astra',dev_reasoning_effort='low',eval_model='gpt-6-astra'),None,'reactive_help',id='passed-astra'),
    pytest.param('passed',None,'agent-policy-v2','trace_aware_hint_only',id='passed-v2-hidden-memory'),
    pytest.param('rejected',None,None,'reactive_help',id='rejected'),pytest.param('inconclusive',None,None,'reactive_help',id='inconclusive')])
def test_offline_ten_arm_worker_smoke(tmp_path,monkeypatch,result,models,policy_version,policy):
    import subprocess
    real_runner=result=='passed' and policy_version=='agent-policy-v1'
    from benchmarks.attention_harness.tests.agent_validation_runner_fakes import install,SCRIPT
    f=fixture(tmp_path,monkeypatch,outcome=result,prepared=False,models=models,policy_version=policy_version,policy=policy,
        script=SCRIPT.replace("tool('submit')","tool('run','--no-eval'); tool('submit')") if real_runner else None)
    if real_runner:
        synthetic=f.runner.runner_factory
        install(f,monkeypatch)
        real=f.runner.runner_factory
        # Keep the original synthetic failure signature in controls. The real
        # agent sandbox reports fatal_sdk_error for fatal native RPC errors;
        # that is deliberately different from the old one-call-style fixture.
        # All five treatment formal arms exercise the real runner and worker.
        f.runner.runner_factory=lambda **kw:(real if kw.get('memory_validation',{}).get('guidance') else synthetic)(**kw)
    outcome(f.service,f.note['memory_id'],0,ctx=dict(f.context,namespace='offline-agent-origin'))
    assert [n['memory_id'] for n in f.service.validation_queue()]==[f.note['memory_id']]
    root=f.provision.state_root
    deps=SimpleNamespace(root=root,runner=f.runner,client=f.service,clock=time.time,activity=lambda current:None,
        save=lambda state:atomic(root/'state.json',state),quota=lambda:dict(used_percent=1,observed_at=time.time()),
        refresh_quota=lambda:None)
    set_enabled(root,True);state={};starts=[];start=f.runner.start
    def track(current,key):
        if current.get('kind')!='origin':starts.append(key)
        return start(current,key)
    monkeypatch.setattr(f.runner,'start',track)
    try:
        with synthetic_creation(f.native):
            until=time.monotonic()+300
            while not state.get('results'):
                tick(state,deps)
                assert time.monotonic()<until,state
                time.sleep(.01)
        summary=state['results'][0]
        assert summary['status']=={'passed':'trusted','rejected':'rejected','inconclusive':'inconclusive'}[result],state
        batch=json.loads(next(root.glob('*/batch.json')).read_bytes())
        assert batch['dev_mode']=='agent' and len(batch['cells'])==10
        from benchmarks.attention_harness.dev_v2_loop import checked
        store=DevV2Store(next(iter(batch['cells'].values()))['ledger'])
        manifest=json.loads(checked(dev_experiment.snapshot(store,batch['experiment_id'])['manifest']).read_bytes())
        original=json.loads(Path(manifest['validation_origin']['manifest']['uri']).read_bytes())
        version=original.get('agent_policy_version','agent-policy-v1')
        assert all(row.get('agent_policy_version','agent-policy-v1')==version
            for row in (manifest,manifest['selection'],*manifest['cells'],*manifest['configs'].values()))
        if version=='agent-policy-v2':
            from benchmarks.attention_harness.dev_agent_policies import memory_exposed
            assert not memory_exposed(policy)
        for field in ('dev_model','dev_reasoning_effort','eval_model'):
            assert manifest[field]==manifest['selection'][field]==original[field]
        assert batch['dev_model']==dict(provider='codex',model=original['dev_model'])
        assert batch['dev_reasoning_effort']==original['dev_reasoning_effort'] and batch['eval_model']==original['eval_model']
        assert f.model_choices==[dict(model=original['dev_model'],reasoning_effort=original['dev_reasoning_effort'])]*len(starts)
        assert f.eval_choices==[original['eval_model']]*len(starts)
        decision=f.service.decision(batch['memory_id'])
        assert decision['decision']=={'passed':'promoted','rejected':'rejected','inconclusive':'inconclusive'}[result]
        if result!='inconclusive':
            assert len(starts)==10 and len(f.service.list_pairs(batch['memory_id']))==5
            assert len(f.executions)==20 and len(set(f.executions))==20
            if real_runner:
                from openarm365_dev_v2.runner import OpenArm365DevRunnerV2
                assert len(f.real_requests)==5 and all(type(r) is OpenArm365DevRunnerV2 for r in f.real_requests)
            assert f.service.get_memory(f.note['memory_id']).status.value=='disabled'
            if result=='passed':
                retrieved=f.service.v2.retrieve(dict(f.context,namespace='offline-agent-origin'),now=time.time())
                assert batch['memory_id'] in [m.memory_id for m in retrieved]
                from attention_memory_service.core.store import StateConflictError
                ctx=dict(f.context,namespace='offline-agent-origin');memory=f.service.get_memory(batch['memory_id'])
                args=dict(memory_id=memory.memory_id,development_id='sealed-only:v1',context=ctx,source_policy_sha256='c'*64,now=time.time())
                with pytest.raises(StateConflictError,match='explicit exact trusted Memory version'):f.service.authorize_dev_use(**args)
                grant=f.service.authorize_dev_use(**args,memory_version=memory.version)
                with pytest.raises(StateConflictError,match='offline sealed bundle import'):
                    f.service.record_dev_result(grant_id=grant['grant_id'],run_id='not-a-real-run',result_policy_sha256='d'*64,artifact_uri='unused',artifact_sha256='e'*64)
                original=(f.root/'v1/public-feedback.json').read_text()
                for index in range(5):
                    outcome(f.service,memory.memory_id,index,**({'text':original} if index>=2 else {}),ctx=ctx)
                assert f.service.get_memory(memory.memory_id).status.value=='provisional'
                with pytest.raises(StateConflictError,match='revoked|changed version'):f.service.get_dev_use(grant['grant_id'])
        else:
            assert len(starts)==1 and not f.service.list_pairs(batch['memory_id'])
            assert f.service.get_memory(f.note['memory_id']).status.value=='provisional'
        disk=[]
        for pair in batch['pairs']:
            for arm in ('control','treatment'):
                key=pair[arm+'_cell'];store=DevV2Store(batch['cells'][key]['ledger']);cell=store.cell(key)
                assert cell['excluded_from_results'] is True and cell['experiment_eligible'] is False
                directory=Path(cell['artifact_dir'])
                if (directory/'v1/memory-input.json').exists():
                    packet=json.loads((directory/'v1/memory-input.json').read_bytes())
                    assert [e['memory_id'] for e in packet['entries']]==([batch['memory_id']] if arm=='treatment' else [])
                    assert cell['agent_resources']['used']['help']==0
                disk.append(dict(cell=key,arm=arm,bytes=int(subprocess.check_output(['du','-sb',str(directory)],text=True).split()[0])))
        (tmp_path/'arm-disk.json').write_text(json.dumps(disk,indent=2))
        print('OFFLINE_ARM_DISK',result,json.dumps(disk))
    finally:f.runner.pool.shutdown()


@pytest.mark.parametrize('field,value',[('dev_model','gpt-6-sol'),('dev_reasoning_effort','low'),('eval_model','gpt-6-astra')])
def test_arm_and_batch_models_must_match_origin(tmp_path,monkeypatch,field,value):
    from benchmarks.attention_harness.memory_agent_validation import validate_options,validate_manifest
    from benchmarks.attention_harness.core.dev_store import DevConflict
    from copy import deepcopy
    f=fixture(tmp_path,monkeypatch)
    try:
        with synthetic_creation(f.native):
            batch=f.provision(f.note,'agent-operation-001')
            store=DevV2Store(next(iter(batch['cells'].values()))['ledger'])
            manifest=dev_experiment.verify_manifest(dev_experiment.snapshot(store,batch['experiment_id']))
            changed=deepcopy(manifest);changed['selection'][field]=value
            with pytest.raises(ValueError,match='exact agent validation arm'):
                validate_options(changed['selection'],changed['validation_origin'])
            changed=deepcopy(manifest);changed[field]=value
            with pytest.raises(ValueError,match='models differ from origin'):validate_manifest(changed)
            tampered=dict(batch)
            if field=='dev_model':tampered[field]=dict(provider='codex',model=value)
            else:tampered[field]=value
            monkeypatch.setattr(f.runner,'provision',lambda *a:tampered)
            note=dict(f.note,created_at=f.service.get_memory(f.note['memory_id']).created_at)
            with pytest.raises(DevConflict,match='batch models differ from origin'):f.runner.begin(note,'agent-operation-001')
    finally:f.runner.pool.shutdown()


@pytest.mark.parametrize('field,value',[(k,v) for k in ('dev_model','eval_model','dev_reasoning_effort') for v in ('unsupported',None,[])])
def test_origin_models_must_be_explicit_and_allowed(tmp_path,field,value):
    from benchmarks.attention_harness.dev_v2_loop import immutable
    from benchmarks.attention_harness.memory_agent_validation import origin_models
    manifest=dict(dev_mode='agent',dev_model='gpt-6.1-sol',dev_reasoning_effort='high',eval_model='gpt-6-astra')
    manifest[field]=value
    with pytest.raises(ValueError,match='unsupported agent validation origin models'):
        origin_models(dict(manifest=immutable(tmp_path/'manifest.json',manifest)))


def test_origin_model_manifest_digest_is_rechecked(tmp_path):
    from benchmarks.attention_harness.dev_v2_loop import immutable
    from benchmarks.attention_harness.memory_agent_validation import origin_models
    from benchmarks.attention_harness.core.dev_store import DevConflict
    ref=immutable(tmp_path/'manifest.json',dict(dev_mode='agent',dev_model='gpt-6.1-sol',dev_reasoning_effort='high',eval_model='gpt-6-astra'))
    Path(ref['uri']).chmod(0o600);Path(ref['uri']).write_text('{}')
    with pytest.raises(DevConflict,match='digest mismatch'):origin_models(dict(manifest=ref))


@pytest.mark.parametrize('origin_version',[None,'agent-policy-v2'])
@pytest.mark.parametrize('fault',['selection','manifest','config','cell','create'])
def test_arm_policy_epoch_must_match_sealed_origin(tmp_path,monkeypatch,fault,origin_version):
    from copy import deepcopy
    from benchmarks.attention_harness.memory_agent_validation import validate_options,validate_manifest
    f=fixture(tmp_path,monkeypatch,policy_version=origin_version)
    wrong='agent-policy-v1' if origin_version=='agent-policy-v2' else 'agent-policy-v2'
    try:
        with synthetic_creation(f.native):
            batch=f.provision(f.note,'agent-policy-epoch-001')
            store=DevV2Store(next(iter(batch['cells'].values()))['ledger'])
            manifest=dev_experiment.verify_manifest(dev_experiment.snapshot(store,batch['experiment_id']))
            changed=deepcopy(manifest)
            if fault=='create':
                selection={k:v for k,v in manifest['selection'].items() if k in dev_experiment.ALLOWED_SELECTION_FIELDS}
                with pytest.raises(ValueError,match='policy version differs from origin'):
                    dev_experiment.create(store,f.native.root,dict(selection,request_key='wrong-policy-epoch',agent_policy_version=wrong),validation_origin=manifest['validation_origin'])
                return
            row={'selection':changed['selection'],'manifest':changed,
                'config':next(iter(changed['configs'].values())),'cell':changed['cells'][0]}[fault]
            row['agent_policy_version']=wrong
            with pytest.raises(ValueError,match='policy version differs from origin'):
                validate_manifest(changed)
            if fault=='selection':
                with pytest.raises(ValueError,match='policy version differs from origin'):
                    validate_options(changed['selection'],changed['validation_origin'])
    finally:f.runner.pool.shutdown()


@pytest.mark.parametrize('version',['unsupported',None,[]])
def test_origin_policy_epoch_must_be_supported(tmp_path,version):
    from benchmarks.attention_harness.dev_v2_loop import immutable
    from benchmarks.attention_harness.memory_agent_validation import origin_policy_version
    ref=immutable(tmp_path/'origin.json',dict(dev_mode='agent',agent_policy_version=version))
    with pytest.raises(ValueError,match='origin policy version'):
        origin_policy_version(dict(manifest=ref))
