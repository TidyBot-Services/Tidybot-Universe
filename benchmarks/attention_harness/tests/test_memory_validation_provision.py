import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
pytestmark = pytest.mark.usefixtures("r3_deployment")
from benchmarks.attention_harness.tests.autovalidate_fakes import ProvisionedFakeRunner,synthetic_creation
from benchmarks.attention_harness.memory_validation_provision import Provisioner
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_v2_loop import immutable
from benchmarks.attention_harness.core.dev_store import DevConflict


def provision(tmp_path):
    fake=ProvisionedFakeRunner(tmp_path/'fake',tmp_path/'worker')
    note=fake.service.validation_queue()[0]
    return fake,note


def test_default_provision_creates_exact_budget_once_and_excludes_ordinary_runs(tmp_path):
    from benchmarks.attention_harness import dev_experiment as experiments,dev_report
    from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
    from benchmarks.attention_harness.dev_planner_memory import cell_memory
    fake,note=provision(tmp_path)
    original={p:p.read_bytes() for p in (fake.f.root/'failed').rglob('*') if p.is_file()}
    with synthetic_creation(fake.f):
        batch=fake.provision(note,'operation-001');again=fake.provision(note,'operation-001')
        assert batch==again
        store=DevV2Store(next(iter(batch['cells'].values()))['ledger'])
        e=experiments.snapshot(store,batch['experiment_id']);m=experiments.verify_manifest(e)
        assert len(experiments.list_experiments(store))==1 and len(e['cells'])==10
        assert m['goal_limits']['executions']==m['goal_limits']['dev_calls']==10
        assert all(m['goal_limits'][k]==0 for k in ('advisor_calls','eval_calls','help_count','help_credits'))
        assert m['goal_limits']=={k:10*v for k,v in m['cell_budget'].items()}
        assert m['model_source']=='codex' and m['configs']=={'pick:101':fake.failed['config']}
        for cell in e['cells']:
            assert cell['validation_experiment'] and not cell['experiment_eligible']
            assert cell['run_notes'] is False and cell['memory_family'] is False and not cell_memory(cell)
            assert Path(cell['artifact_dir'],'config.json').read_bytes()==Path(batch['config']['uri']).read_bytes()
        assert DailyDevV2.sessions(None,e)==[]
        with pytest.raises(ValueError,match='excluded'):dev_report.report(store,e['experiment_id'])
    assert all(path.read_bytes()==value for path,value in original.items())


@pytest.mark.parametrize('fault',['missing','source','feedback','signature','ledger','snapshot'])
def test_missing_or_tampered_origin_never_creates_budget(tmp_path,fault):
    fake,note=provision(tmp_path)
    if fault=='missing':(fake.f.root/'failed/source.json').unlink()
    elif fault in ('source','feedback'):
        p=Path(fake.failed['record']['source']['uri']) if fault=='source' else fake.f.root/'failed/feedback.json'
        p.write_text('tampered')
    elif fault=='signature':
        original=fake.service.provenance
        fake.provision.client=SimpleNamespace(provenance=lambda mid:{**original(mid),'failure_signature':{'public_error_class':'Other','operation':'left_arm.move_delta'}})
    elif fault=='ledger':note['source_summary']['cell']='missing-cell'
    else:
        ref=fake.provision.config['evidence_catalogs']['failed'];Path(ref['uri']).write_text('{}')
    with pytest.raises((DevConflict,ValueError,FileNotFoundError)):
        with synthetic_creation(fake.f):fake.provision(note,'operation-001')
    assert not list(fake.f.root.glob('devexp-*'))


@pytest.mark.parametrize('used',[10,11,19,20])
def test_remaining_daily_cap(tmp_path,used):
    fake,note=provision(tmp_path);root=fake.provision.state_root;root.mkdir()
    (root/'state.json').write_text(json.dumps(dict(day=datetime.now().astimezone().date().isoformat(),runs_today=used)))
    with synthetic_creation(fake.f):
        if used==10:assert len(fake.provision(note,'operation-001')['cells'])==10
        else:
            with pytest.raises(DevConflict,match='Fewer than 10'):fake.provision(note,'operation-001')
            assert not list(fake.f.root.glob('devexp-*'))


def test_idle_probe_owns_validation_but_blocks_user_run(tmp_path):
    from benchmarks.attention_harness.memory_validation_worker import run_activity
    fake,note=provision(tmp_path)
    with synthetic_creation(fake.f):batch=fake.provision(note,'operation-001')
    ledger=next(iter(batch['cells'].values()))['ledger'];store=DevV2Store(ledger)
    with store.tx() as db:
        e=store.get(db,'experiments',batch['experiment_id']);e['state']='running';store.put(db,'experiments',e['experiment_id'],e)
    assert run_activity([ledger],own_experiment=e['experiment_id']) is None
    assert run_activity([ledger]).startswith('Another run is active: '+e['experiment_id'])
    with store.tx() as db:store.put(db,'experiments','user',dict(experiment_id='user',selection=dict(budget_tier='dev'),state='running'))
    assert run_activity([ledger],own_experiment=e['experiment_id'])=='Another run is active: user'


@pytest.mark.parametrize('fault',[None,'missing','unmeasured','tampered','binding'])
def test_gpu_seconds_only_from_sealed_measurement(tmp_path,fault):
    bundle=immutable(tmp_path/'bundle.json',dict(attempt_id='attempt:cell:0'))
    measurement=dict(source='harness_gpu_meter',cell='cell',version=1,bundle_sha256=bundle['sha256'],attempt_id='attempt:cell:0',gpu_seconds=1.25,samples=3)
    if fault=='unmeasured':measurement.update(gpu_seconds=None,samples=0)
    if fault=='binding':measurement['cell']='other'
    ref=immutable(tmp_path/'gpu.json',measurement)
    if fault=='tampered':Path(ref['uri']).chmod(0o644);Path(ref['uri']).write_text('{}')
    receipt=dict(bundle=bundle,**({} if fault=='missing' else dict(gpu_measurement=ref)))
    store=SimpleNamespace(cell=lambda key:dict(jobs=[dict(version=1,kind='execute',state='settled',receipt=receipt)]))
    if fault:
        with pytest.raises((DevConflict,ValueError)):Provisioner.gpu_seconds(store,'cell')
    else:assert Provisioner.gpu_seconds(store,'cell')==1.25


def test_adapter_inprocess_service_and_clean_generated_prompts(tmp_path):
    fake,note=provision(tmp_path);batch=fake.begin(note,'operation-001')
    pair=batch['pairs'][0]
    for arm in ('control','treatment'):
        current=dict(batch,arm_id=pair[arm+'_cell'],arm=arm)
        fake.finish(current)
        record=fake.f.records[current['arm_id']]['record']
        request=json.loads(Path(record['model_input']['uri']).read_bytes())
        packet=json.loads(request['messages'][1]['content'])
        assert request['model_source']=='codex' and request['model']=='gpt-6-astra'
        expected=[{'content':fake.service.get_memory(batch['memory_id']).guidance}] if arm=='treatment' else []
        assert packet['attention_control']=={'memory':expected}
        assert not {'run_notes','help','family','memory_authority'}&packet.keys()
        assert packet['previous_complete_source']==Path(batch['origin']['uri']).read_text()


def test_validation_creation_preserves_real_registered_config_and_checks(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_experiment as experiments
    from openarm365_dev_v2 import dev_config
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    monkeypatch.setattr(dev_config,'model_sources',lambda *a,**kw:{})
    origin_store=DevV2Store(tmp_path/'origin.sqlite3')
    selection=dict(request_key='original-001',tasks=['open_cabinet'],seeds=[102],policies=['autonomous'],mode='diagnostic',budget_tier='dev',target_depth=1,target_valid_executions=1,max_versions=1,help_budget=0,reproducible_reset=True,scene_contract=registered_contract_ref())
    original=experiments.create(origin_store,tmp_path/'original',selection)
    m=experiments.verify_manifest(original);config=m['configs']['open_cabinet:102']
    frozen=dict(manifest=original['manifest'],config=immutable(tmp_path/'config.json',config),source=immutable(tmp_path/'origin.py',m['initial_sources']['open_cabinet']['source']),cell=original['cells'][0]['key'],version=1)
    validation=dict(selection,request_key='validation-001',model_source='codex',run_notes=False,memory_family=False,attempt_wall_seconds=config['attempt_wall_seconds'])
    store=DevV2Store(tmp_path/'validation.sqlite3')
    result=experiments.create(store,tmp_path/'validation',validation,validation_origin=frozen)
    manifest=experiments.verify_manifest(result)
    assert manifest['configs']=={'open_cabinet:102':config} and len(result['cells'])==10
    from benchmarks.attention_harness.dev_ledger import frozen_simulation_target
    assert frozen_simulation_target(result['cells'][0],result,config)
    for field,value in [('memory_family',True),('run_notes',True),('help_budget',1),('max_versions',2)]:
        with pytest.raises(DevConflict):experiments.create(store,tmp_path/'validation',dict(validation,**{field:value}),validation_origin=frozen)
    # A fake settled execution exercises the real exporter with the preserved
    # original config/lock references. No native execution or provider is called.
    from benchmarks.attention_harness.dev_v2_memory_import import prepare
    from benchmarks.attention_harness.memory_source_catalogs import manifest_sources
    from benchmarks.attention_harness.dev_planner_memory import context
    from benchmarks.attention_harness.dev_loop import process_identity
    cell=result['cells'][0];key=cell['key'];directory=Path(cell['artifact_dir'])/'v1';directory.mkdir()
    record=dict(version=1,valid=True,formal_eligible=False,source=immutable(directory/'policy.py','from robot_sdk import sensors\nsensors.get_observation()\n'),config_sha256=frozen['config']['sha256'],manifest=result['manifest'])
    immutable(directory/'version.json',record);store.version(key,record);store.update_cell(key,state='running',owner=process_identity())
    job=store.reserve(key,1,'execute',{'executions':1});store.claim(job['job_id'],process_identity())
    bundle=immutable(directory/'bundle.json',dict(artifact_dir=str(directory)))
    immutable(directory/'scene-condition.json',dict(evidence_kind='controlled_test'))
    store.settle(job['job_id'],{'executions':1},dict(bundle=bundle))
    sources=manifest_sources(result['manifest'],context(config,'controlled_test'),tmp_path,contract=config['scene_contract'])
    output=directory/'export';ref=prepare(store,key,1,'controlled_test',output,tmp_path,source_catalogs=sources)
    catalog=json.loads(Path(ref['uri']).read_bytes())
    assert str(Path(frozen['config']['uri']).relative_to(tmp_path)) in catalog['files']
    assert str(Path(config['dev_lock']['path']).relative_to(tmp_path)) in catalog['files']
    assert Path(catalog['root'])==tmp_path
