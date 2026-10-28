import json
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness.tests.family_fakes import run,Provider
from benchmarks.attention_harness.memory_family import generalize,tick_channel
from benchmarks.attention_harness.memory_validation_worker import atomic,settings
from benchmarks.attention_harness.memory_layers import label,order
from tests.test_family import setup


def test_fixture_pipeline(tmp_path):run(tmp_path)
def test_real_pipeline(tmp_path,monkeypatch):run(tmp_path,real=True,monkeypatch=monkeypatch)

def test_generalize_resume_no_second_provider_call(tmp_path):
    f,s,*_=setup(tmp_path);p=Provider()
    first=generalize(s,'fixture:fixture-one',provider=p,output_dir=tmp_path/'rewrite')
    assert generalize(s,'fixture:fixture-one',provider=p,output_dir=tmp_path/'rewrite')==first
    assert len(p.calls)==1


@pytest.mark.parametrize('bad',['[]',json.dumps(dict(title='Sink',guidance='Move into the sink.'))])
def test_rewrite_retries_one_invalid_response(tmp_path,bad):
    _,s,*_=setup(tmp_path)
    class RetryProvider(Provider):
        def chat(self,**kw):
            if not self.calls:
                self.calls.append(kw)
                return SimpleNamespace(artifact=lambda:dict(content=bad))
            return super().chat(**kw)
    p=RetryProvider()
    assert generalize(s,'fixture:fixture-one',provider=p,output_dir=tmp_path/'rewrite')['rewrite_kind']=='model_rewrite'
    assert len(p.calls)==2


def test_target_template_uses_frozen_manifest_and_probe_feedback(tmp_path,monkeypatch):
    from benchmarks.attention_harness.memory_family_provision import FamilyProvisioner
    from benchmarks.attention_harness.dev_v2_loop import immutable
    from benchmarks.attention_harness import dev_experiment,dev_planner_memory,memory_cross_seed
    from benchmarks.attention_harness.core.dev_store import DevConflict
    task='pick_place_counter_to_cabinet';seed=114
    # Above the legacy source limit, within the approved Dev source cap.
    baseline=tmp_path/'policy.py';baseline.write_text('from robot_sdk import sensors\nobservation = sensors.get_observation()\n#'+'x'*21000+'\n')
    from attention_memory_service.dev_v2 import digest
    source=dict(path=str(baseline),sha256=digest(baseline.read_bytes()))
    spec=dict(target_task_id=task,targets=[dict(seed=seed,baseline_source=source)])
    source_ctx=dict(namespace='test',task_id='pick_place_counter_to_sink',task_variant_id='PickPlaceCounterToSink:layout0:style0:fixed',seed=102,execution_profile='same',sdk_interface_sha256='a'*64)
    config=dict(task_id=task,seed=seed)
    manifest=immutable(tmp_path/'manifest.json',dict(configs={f'{task}:{seed}':config}))
    selections=[]
    def create(store,root,selection):
        selections.append(selection)
        return dict(manifest=manifest,artifact_dir=str(root/'template'),cells=[dict(key='cell',policy_id='full_trace_aware_attention_planner')])
    monkeypatch.setattr(dev_experiment,'create',create)
    contract=dict(uri='/contract',sha256='b'*64)
    catalog={'role':'contract'}
    monkeypatch.setattr(memory_cross_seed,'register_contract',lambda *args:(contract,catalog))
    def context(config,namespace,*,contract_catalog):
        assert contract_catalog==catalog
        return dict(source_ctx,task_id=task,seed=seed)
    monkeypatch.setattr(dev_planner_memory,'context',context)
    probe=dict(manifest=manifest,config=immutable(tmp_path/'probe-config.json',config),source=dict(uri=str(baseline),sha256=source['sha256']),cell='probe-cell',version=1,context=dict(source_ctx,task_id=task,seed=seed),public_feedback=immutable(tmp_path/'probe-feedback.json',dict(failed=True)))
    client=SimpleNamespace(family_spec=spec,read=lambda action:dict(context=source_ctx) if action=='record' else dict(execution=probe),provenance=lambda mid:dict(failure_signature={'frozen':True}))
    p=FamilyProvisioner(dict(experiments_root=str(tmp_path/'experiments'),state_root=str(tmp_path/'state')),client)
    origin=p.target_origin(dict(memory_id='candidate'),seed,contract)
    assert json.loads(__import__('pathlib').Path(origin['sealed']['config']['uri']).read_text())==config
    assert selections[0]['model_source']=='codex' and selections[0]['run_notes'] is False
    resolved=p.resolve_target(dict(memory_id='candidate'),dict(seed=seed),pairs=3)
    assert resolved['feedback']==probe['public_feedback'] and resolved['sealed']['cell']=='probe-cell'
    monkeypatch.setattr(dev_planner_memory,'context',lambda config,namespace,**kw:dict(source_ctx,task_id=task,seed=seed,sdk_interface_sha256='different'))
    with pytest.raises(DevConflict,match='differs from source'):p.target_origin(dict(memory_id='candidate'),seed,contract)


def test_family_switch_default_off(tmp_path):
    assert not settings(tmp_path).get('family_checks',{}).get('enabled',False)
    atomic(tmp_path/'autovalidate.json',dict(enabled=True))
    assert not tick_channel({},SimpleNamespace(root=tmp_path),lambda *a:pytest.fail('off channel ran'))
    atomic(tmp_path/'autovalidate.json',dict(enabled=True,family_checks=dict(enabled='yes')))
    with pytest.raises(ValueError):settings(tmp_path)


@pytest.mark.parametrize('status',['revoked','disabled'])
def test_closed_family_between_batches_has_no_current_arm(tmp_path,status):
    spec=dict(candidate_id='candidate',family_check_id='check')
    atomic(tmp_path/'family-checks/check.json',spec)
    atomic(tmp_path/'autovalidate.json',dict(enabled=True,family_checks=dict(enabled=True)))
    state=dict(family_active='candidate',family_checks=dict(candidate=dict(worker=dict(current=None))))
    client=SimpleNamespace(read=lambda action:spec if action=='check' else dict(status=status))
    runner=SimpleNamespace(pause=lambda *a:pytest.fail('no active arm to pause'))
    deps=SimpleNamespace(root=tmp_path,family_adapters={'candidate':(client,runner)},save=lambda value:None)
    assert tick_channel(state,deps,lambda *a:pytest.fail('closed family dispatched'))
    assert state['family_checks']['candidate']['done'] is True
    assert state['family_checks']['candidate']['status']==dict(status=status)
    assert 'family_active' not in state


def test_family_reference_label_and_cap():
    trusted=dict(layer='family',trust_scope='family_scoped',created_at=1)
    reference=dict(layer='family',created_at=2)
    assert order([reference,trusted],family=True)==[trusted]
    assert label(trusted)=='Trusted for the family (model rewrite): '
