import json
from pathlib import Path
import pytest
from benchmarks.attention_harness.tests.invalid_arm_fakes import scenario,complete,SOURCE
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.memory_validation_worker import tick,request_current_retry,set_enabled
from benchmarks.attention_harness.memory_validation_generation import parse_generation
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.dev_ledger import DevV2Store

pytestmark=pytest.mark.usefixtures('r3_deployment')


@pytest.mark.parametrize('stage',['same_scene','probe','treatment'])
def test_non_pair_finish_without_arm_id_preserves_imports(tmp_path,monkeypatch,stage):
    from types import SimpleNamespace
    from benchmarks.attention_harness import memory_validation_runtime as runtime
    from benchmarks.attention_harness.dev_v2_loop import immutable
    from benchmarks.attention_harness.memory_origin_evidence import OriginalStore
    from benchmarks.attention_harness.tests.autovalidate_fakes import AutomaticOriginFakeRunner
    from benchmarks.attention_harness.tests.motion_c12_fakes import motion_fakes
    from benchmarks.attention_harness.tests.test_memory_origin_c9 import synthetic_export
    with motion_fakes():
        fake=AutomaticOriginFakeRunner(tmp_path/'fake',tmp_path/'worker')
        store=OriginalStore(fake.failed['descriptor']['ledger']['uri'])
        original_get=store.get
        def get(db,kind,key):
            value=original_get(db,kind,key)
            return {**value,'artifact_dir':str(fake.f.root)} if kind=='experiments' else value
        monkeypatch.setattr(store,'get',get)
        cell=store.cell('failed');descriptor=fake.failed['descriptor']
        if stage=='probe':
            Path(cell['artifact_dir'],'v1/memory-validation-input.json').write_bytes(Path(descriptor['validation_input']['uri']).read_bytes())
        else:
            grant=immutable(fake.f.root/'grant.json',{'binding':{'cell':'failed','version':1}})
            immutable(Path(cell['artifact_dir'])/'v1/memory-validation-envelope.json',{'grant':grant})
        runner=runtime.LedgerRunner.__new__(runtime.LedgerRunner)
        runner._cell=lambda current:(store,'failed',cell)
        runner.experiments_root=fake.f.root;runner.validator_root=fake.f.validator;runner.state_root=tmp_path/'worker'
        catalogs=[];executions=[];trials=[];probes=[];validations=[]
        def probe_write(route,**kwargs):
            assert route=='probes'
            probes.append(kwargs)
            return {'verdict':'transfer'}
        runner.client=SimpleNamespace(register_dev_v2_catalog=lambda **kw:catalogs.append(kw),
            import_dev_v2_execution=lambda **kw:executions.append(kw),
            import_dev_v2_trial=lambda **kw:trials.append(kw),cross_seed_write=probe_write)
        runner.gpu_meter=lambda store,key:0
        runner.replay=lambda *a:pytest.fail('No simulator replay in this wiring test')
        # Even a stray replacement cursor must not version these legacy imports.
        current=dict(config=descriptor['config'],context=descriptor['context'],failure_signature={},budget={},
                     dev_model={'model':'fake'},memory_id=fake.mid,pair_version=2)
        if stage!='same_scene':
            current.update(kind='cross_seed',stage=stage,target_seed=114,origin=fake.failed['record']['source'],
                target=dict(contract=fake.f.contract,catalog=dict(role='contract',root=str(Path(fake.f.contract['uri']).parent),files={'scene.json':fake.f.contract['sha256']})))
        verdict=dict(status='passed',catalog=dict(root=str(fake.f.validator),files={}),
                     descriptor=dict(uri=str(fake.f.validator/'trial.json'),sha256='fake'))
        def validate(**kwargs):
            validations.append(kwargs)
            assert kwargs['cell']=='failed' and kwargs['version']==1 and kwargs['execution_import_id']=='failed'
            assert kwargs['import_purpose']=='memory_validation'
            assert kwargs['permission_scope']['actor']=='dev_tier_sim_motion'
            assert ('probe_input' in kwargs)==(stage=='probe')
            assert (kwargs['grant'] is None)==(stage=='probe')
            return verdict
        monkeypatch.setattr(runtime,'validate_arm',validate)
        assert 'arm_id' not in current
        with synthetic_export(fake):
            result=runner.finish(current)
            assert runner.finish(current)==result  # Reuse the original durable verdict.
        assert len(validations)==1
        assert json.loads((runner.state_root/'verdicts/failed.json').read_bytes())==verdict
        assert (fake.f.root/'validation-import/failed/descriptor.json').is_file()
        assert all(row['import_id']==row['catalog_id']=='failed' for row in executions) and len(executions)==2
        assert [row['catalog_id'] for row in catalogs].count('validator-failed')==2
        if stage=='probe':
            assert result==dict(status='probe',probe={'verdict':'transfer'}) and not trials
            assert len(probes)==2 and all(row['execution_import_id']=='failed' for row in probes)
        else:
            assert result==dict(status='passed',trial_import_id='trial-failed',descriptor=verdict['descriptor']) and not probes
            assert trials==[dict(import_id='trial-failed',catalog_id='validator-failed',descriptor='trial.json')]*2


@pytest.mark.parametrize('stage',['origin','probe'])
@pytest.mark.parametrize('empty_discards',[False,True])
def test_non_pair_monitor_projection_unchanged(tmp_path,stage,empty_discards):
    current=dict(title='Note',kind='origin' if stage=='origin' else 'cross_seed')
    expected=dict(title='Note',paused=False,stage=stage)
    if stage=='probe':
        current.update(stage='probe',pair_index=0,arm='probe',target_seed=114)
        expected.update(pair=1,arm='probe',seed=114,pairs_needed=0)
    result=dict(title='Note',status='inconclusive',decision={'reason':'Check failed'})
    if empty_discards:current['discarded_pairs']=[];result['discarded_pairs']=[]
    (tmp_path/'state.json').write_text(json.dumps(dict(current=current,results=[result])))
    view=automatic_checks(tmp_path)
    assert not view['errors']
    assert view['current']==expected
    assert view['results']==[dict(title='Note',status='inconclusive',note_remains=None,
                                 reason='Check failed',pairs=None,control=None,treatment=None)]


@pytest.mark.parametrize('arm',['control','treatment'])
def test_invalid_pair_replaced_real_service_five_valid_pairs(tmp_path,arm):
    with scenario(tmp_path,((arm,1),)) as case:
        result=complete(case)
        assert result['status']=='trusted'
        assert len(result['discarded_pairs'])==1
        discard=result['discarded_pairs'][0]
        assert discard['arm']==arm and discard['reason']=='source_size_limit_exceeded'
        assert case.state['runs_today']==(11 if arm=='control' else 12)
        pairs=case.fake.service.list_pairs(result['memory_id'])
        assert len(pairs)==5
        assert any(p['control_attempt_id'].endswith(':1') and p['treatment_attempt_id'].endswith(':1') for p in pairs)
        replaced={key for key,version,_,_ in case.generated if version==2}
        assert all(p[a+'_attempt_id'] not in {f'attempt:{key}:0' for key in replaced} for p in pairs for a in ('control','treatment'))
        assert (discard['arm_id'],1) not in case.executed
        assert result['decision']['report']['control_signature_count']==5
        assert result['decision']['report']['treatment_signature_count']==0
        view=automatic_checks(case.deps.root)
        assert view['results'][0]['discarded_pairs'][0]['reason']=='source_size_limit_exceeded'
        ledger=next(case.fake.f.root.glob('devexp-*/ledger.sqlite3'));store=DevV2Store(ledger)
        goal=store.goal_for_cell(discard['arm_id'])
        assert goal['limits']['dev_calls']==goal['limits']['executions']==12
        assert goal['used']['dev_calls']==len(case.generated)
        assert goal['used']['executions']==len(case.executed)
        # Fresh prompts retain the original source and the original per-arm allowance.
        requests=[json.loads(p.read_bytes()) for p in ledger.parent.glob('*/v*/model-input.json')]
        packets=[json.loads(r['messages'][1]['content']) for r in requests]
        assert len({p['previous_complete_source'] for p in packets})==1
        assert len({json.dumps(p['remaining_budget'],sort_keys=True) for p in packets})==1


def test_third_invalid_pair_inconclusive_without_service_evaluate(tmp_path):
    with scenario(tmp_path,(('treatment',1),('treatment',2),('treatment',3))) as case:
        result=complete(case)
        assert result['status']=='inconclusive' and result['note_remains']=='provisional'
        assert len(result['discarded_pairs'])==3 and case.state['runs_today']==6
        assert case.fake.service.list_pairs(result['memory_id'])==[]
        assert case.fake.service.decision(result['memory_id']) is None
        assert case.fake.service.get_memory(case.fake.mid).status.value=='provisional'
        assert len(automatic_checks(case.deps.root)['results'][0]['discarded_pairs'])==3


def test_infrastructure_error_still_pauses(tmp_path):
    with scenario(tmp_path,infra=True) as case:
        tick(case.state,case.deps);tick(case.state,case.deps)
        assert 'paused: provider transport unavailable' in case.state['status']
        assert not case.state.get('results') and not case.state['current'].get('discarded_pairs')
        assert not case.executed


@pytest.mark.parametrize('text,reason',[
    ('bad json','unparsable_output'),
    (json.dumps(dict(source=42,reason='why')),'unparsable_output'),
    (json.dumps(dict(source='import os\nos.system("x")',reason='why')),'source_validation_failed'),
    (json.dumps(dict(source=SOURCE+'#'+'x'*18000,reason='why')),'source_size_limit_exceeded'),
    ('```json\n'+json.dumps(dict(source=SOURCE,reason='why'))+'\n```',None),
])
def test_generation_refusals(text,reason):
    assert parse_generation(text)[2]==reason


def test_two_replacements_are_batch_wide_and_restart_idempotent(tmp_path):
    from benchmarks.attention_harness.memory_validation_replacements import authorize_replacement
    with scenario(tmp_path,((0,'treatment',1),(2,'control',1))) as case:
        for _ in range(10):
            tick(case.state,case.deps)
            current=case.state['current']
            if current.get('discarded_pairs'):break
        assert len(current['discarded_pairs'])==1
        assert 'running replacement 1 of 2' in case.state['status']
        assert automatic_checks(case.deps.root)['current']['discarded_pairs'][0]['arm']=='treatment'
        # Crash/restart after supplemental authorization, before dispatch.
        store=DevV2Store(current['cells'][current['arm_id']]['ledger'])
        authorize_replacement(store,current)
        before=store.goal_for_cell(current['arm_id'])
        authorize_replacement(store,current)
        assert store.goal_for_cell(current['arm_id'])['limits']==before['limits']
        case.runner.futures.clear()
        case.state=json.loads((case.deps.root/'state.json').read_bytes())
        result=complete(case)
        assert result['status']=='trusted' and len(result['discarded_pairs'])==2
        assert [r['pair'] for r in result['discarded_pairs']]==[1,3]
        assert store.goal_for_cell(current['arm_id'])['limits']['executions']==14
        assert case.state['runs_today']==13


def test_replacement_respects_off_and_daily_limit(tmp_path):
    from benchmarks.attention_harness.memory_validation_worker import set_daily_run_limit
    with scenario(tmp_path) as case:
        for _ in range(5):
            tick(case.state,case.deps)
            if case.state['current'].get('discarded_pairs'):break
        set_daily_run_limit(case.deps.root,2)
        tick(case.state,case.deps)
        assert 'done for today' in case.state['status'] and len(case.generated)==2
        set_enabled(case.deps.root,False);tick(case.state,case.deps)
        assert case.state['status']=='Off' and len(case.generated)==2
        set_daily_run_limit(case.deps.root,None);set_enabled(case.deps.root,True)
        assert complete(case)['status']=='trusted'


def test_legacy_settled_invalid_recovery_retry_while_off(tmp_path):
    from concurrent.futures import Future
    from benchmarks.attention_harness.core.dev_store import DevConflict
    from benchmarks.attention_harness.dev_ledger import experiment_goal,encoded
    with scenario(tmp_path) as case:
        tick(case.state,case.deps);tick(case.state,case.deps)
        current=case.state['current'];key=current['arm_id']
        assert current['arm']=='treatment' and len(case.generated)==2
        store=DevV2Store(current['cells'][key]['ledger']);cell=store.cell(key)
        # Reconstruct exactly the old failure boundary: settled model, no version
        # record/execution, experiment and cell parked as needs_review.
        (Path(cell['artifact_dir'])/'v1/version.json').unlink()
        with store.tx() as db:
            db.execute('DELETE FROM v2_versions WHERE cell=? AND version=1',(key,))
            raw=store.get(db,'cells',key);raw['state']='needs_review';store.put(db,'cells',key,raw)
            e=store.get(db,'experiments',cell['experiment_id']);e['state']='needs_review';store.put(db,'experiments',e['experiment_id'],e)
            g=store.get(db,'goals',experiment_goal(e));g['real_started_at']-=30000;store.put(db,'goals',experiment_goal(e),g)
            j=next(j for j in cell['jobs'] if j['kind']=='model');j.pop('state',None);j['settled_at']-=30000
            db.execute('UPDATE v2_jobs SET payload=? WHERE id=?',(encoded(j),j['job_id']))
        future=Future();future.set_exception(DevConflict('source_size_limit_exceeded'));case.runner.futures[key]=future
        set_enabled(case.deps.root,False);tick(case.state,case.deps)
        assert request_current_retry(case.deps.root).startswith('Current invalid-generation recovery requested')
        tick(case.state,case.deps);assert len(case.generated)==2 and case.state['status']=='Off'
        set_enabled(case.deps.root,True);tick(case.state,case.deps)
        assert len(case.state['current']['discarded_pairs'])==1 and len(case.generated)==2
        assert not case.runner.recover_invalid(case.state['current'])
        assert complete(case)['status']=='trusted'
        assert len(case.generated)==12
        with store.tx() as db:
            assert db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='invalid_generation_recovered'").fetchone()[0]==1
            assert db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='validation_replacement_authorized'").fetchone()[0]==1
