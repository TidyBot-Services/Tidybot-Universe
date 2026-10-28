"""Real family provisioning/export with offline identity and settled execution.

Only the native deployment/model inventory and the execution are synthetic.
Experiment creation, config validation, family paths, source catalogs and prepare
all run unmodified. No provider, simulator or live Memory service is started.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from benchmarks.attention_harness import dev_experiment
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_loop import process_identity
from benchmarks.attention_harness.dev_v2_loop import immutable, checked
from benchmarks.attention_harness.dev_planner_memory import context
from benchmarks.attention_harness.memory_cross_seed import admit, provision_cross
from benchmarks.attention_harness.memory_family_provision import FamilyProvisioner
from benchmarks.attention_harness.memory_source_catalogs import manifest_sources
from benchmarks.attention_harness.memory_source_drift import current_source_lock
from benchmarks.attention_harness.dev_v2_memory_import import prepare_recoverable


def layout(root, monkeypatch, *, legacy_template=False):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    # Reuse authenticated offline R4 fixtures; their files all live under root.
    from .test_dev_local_deployment import base
    from .c21_fixtures import r4_deployment
    from .c22_fixtures import source_only_lock
    from openarm365_dev_v2 import dev_config
    base_value=base.__wrapped__(root,monkeypatch)
    r4_deployment.__wrapped__(base_value,monkeypatch)
    source_only_lock.__wrapped__(monkeypatch)
    make_lock=dev_experiment.make_lock
    def stable_lock(path, **kwargs):
        ref=make_lock(path,**kwargs);value=json.loads(path.read_bytes())
        folder=dev_config.ROOT/'openarm365_dev_v2/policies'
        value.update(formal_eligible=False,source_sets={str(folder):[]})
        pin=value['planner_lock'];value['files'][pin['path']]=pin['sha256']
        path.write_text(json.dumps(current_source_lock(value),sort_keys=True))
        path.chmod(0o444)
        return dict(ref,sha256=dev_config.sha(path))
    monkeypatch.setattr(dev_experiment,'make_lock',stable_lock)

    experiments=root/'experiments';state=root/'worker'
    task='pick_place_counter_to_cabinet';seed=114
    contract=immutable(root/'contracts/target.json',dict(
        schema='attentionbench.public-scene-contract.v2',tolerance=1e-6,
        tasks={task:dict(seeds={str(seed):dict(normalized_sha256='0'*64)})}))
    contract_catalog=dict(role='contract',root=str(root/'contracts'),files={'target.json':contract['sha256']})
    source_store=DevV2Store(experiments/'source/ledger.sqlite3')
    source_experiment=dev_experiment.create(source_store,experiments,dict(
        request_key='family-source-context',tasks=[task],seeds=[seed],
        policies=['full_trace_aware_attention_planner'],mode='diagnostic',budget_tier='dev',
        model_source='codex',help_budget=0,run_notes=False,memory_family=False,
        target_depth=1,target_valid_executions=1,max_versions=1,reproducible_reset=True,
        scene_contract=contract,planner_source=dict(kind='dev-local',source_root=str(dev_config.ROOT))))
    config=json.loads(checked(source_experiment['manifest']).read_bytes())['configs'][f'{task}:{seed}']
    ctx=context(config,'family-import-test',contract_catalog=contract_catalog)
    # Family admits a source task differing only in its allowed target fields.
    ctx.update(task_id='pick_place_counter_to_sink',task_variant_id='PickPlaceCounterToSink:layout11:style34:commanded_motion')
    baseline=immutable(root/'external-baseline/policy.py','from robot_sdk import sensors\nsensors.get_observation()\n')
    spec=dict(family_check_id='family-import-test',target_task_id=task,
              targets=[dict(seed=seed,baseline_source=dict(path=baseline['uri'],sha256=baseline['sha256']))])
    records={'record':dict(context=ctx)};registered={}
    client=SimpleNamespace(family_channel=True,family_spec=spec,read=records.__getitem__,
        provenance=lambda mid:dict(failure_signature={}),get_plan=lambda mid:dict(assistance_credits=0),
        register_dev_v2_catalog=lambda **kw:registered.update({kw['catalog_id']:kw['catalog']}))
    provision=FamilyProvisioner(dict(experiments_root=str(experiments),state_root=str(state),
        contract_registration_roots=[str(root/'contracts')]),client)
    if legacy_template:
        # Reproduce the pre-T5 template layout without faking the provisioner or
        # exporter: only redirect its two immutable template writes to state.
        from benchmarks.attention_harness import memory_family_provision
        def old_immutable(path,value):
            if path.name in {'config.json','no-feedback.json'}:
                path=state/'family-templates'/path.parent.parent.name/path.name
            return immutable(path,value)
        monkeypatch.setattr(memory_family_provision,'immutable',old_immutable)
    note=dict(memory_id='family-candidate',version=1)
    target=admit(provision,note,seed,contract['uri'])
    template=provision.target_origin(note,seed,contract)
    assert provision.target_origin(note,seed,contract)==template
    probe=provision_cross(provision,note,'family-probe-import',target)
    probe_export=settle(probe,experiments)
    records['probe-'+str(seed)]=dict(execution=dict(
        manifest=probe_export.experiment['manifest'],config=probe['config'],
        source=probe_export.record['source'],cell=probe['probe_cell'],version=1,
        context=probe['context'],public_feedback=probe_export.feedback))
    arms=provision_cross(provision,note,'family-arms-import',target,pairs=3)
    arm_export=settle(arms,experiments)
    return SimpleNamespace(template=template,probe=probe,arms=arms,exports=[probe_export,arm_export],
                           experiments=experiments,state=state,baseline=baseline,registered=registered)


def settle(batch, experiments):
    key=batch['probe_cell'] or batch['pairs'][0]['control_cell']
    store=DevV2Store(batch['cells'][key]['ledger']);cell=store.cell(key)
    with store.tx() as db:experiment=store.get(db,'experiments',cell['experiment_id'])
    directory=Path(cell['artifact_dir'])/'v1';directory.mkdir()
    config_ref=immutable(Path(cell['artifact_dir'])/'config.json',json.loads(checked(batch['config']).read_bytes()))
    config=json.loads(checked(config_ref).read_bytes())
    record=dict(version=1,valid=True,formal_eligible=False,
        source=immutable(directory/'policy.py',checked(batch['origin']).read_text()),
        config_sha256=config_ref['sha256'],manifest=experiment['manifest'])
    immutable(directory/'version.json',record);store.version(key,record)
    immutable(directory/'memory-validation-input.json',dict(memory_ids=[],exposure='none',
        context=batch['context'],policy_sha256=record['source']['sha256'],config_sha256=config_ref['sha256']))
    feedback=immutable(directory/'public-feedback.json',dict(execution_status='completed'))
    bundle=immutable(directory/'bundle.json',dict(artifact_dir=str(directory),
        config_sha256=config_ref['sha256'],policy_sha256=record['source']['sha256']))
    immutable(directory/'scene-condition.json',dict(evidence_kind='controlled_test'))
    owner=process_identity();store.update_cell(key,state='running',owner=owner)
    job=store.reserve(key,1,'execute',{'executions':1});store.claim(job['job_id'],owner)
    store.settle(job['job_id'],{'executions':1},dict(bundle=bundle,public_feedback=feedback))
    sources=manifest_sources(experiment['manifest'],batch['context'],experiment['artifact_dir'],contract=config['scene_contract'])
    output=Path(experiment['artifact_dir'])/'validation-import'/key
    return SimpleNamespace(store=store,key=key,experiment=experiment,record=record,feedback=feedback,
        output=output,args=(store,key,1,batch['context']['namespace'],output,experiments),
        options=dict(source_catalogs=sources,contract_catalog=batch['target']['catalog'],
                     motion_permission='dev_tier_sim_motion',import_purpose='memory_validation'))


def run(root, monkeypatch):
    evidence=layout(root,monkeypatch)
    refs=[evidence.template['sealed'][k] for k in ('config','source','manifest')]+[evidence.template['feedback']]
    for export in evidence.exports:
        ref=prepare_recoverable(*export.args,**export.options)
        catalog=json.loads(checked(ref).read_bytes())
        for origin in refs[:3]:
            name=str(Path(origin['uri']).relative_to(evidence.experiments))
            assert catalog['files'][name]==origin['sha256']
        assert all(not Path(name).is_absolute() for name in catalog['files'])
        # Reuse completed snapshots verbatim, including their SQLite backup.
        before={p:p.read_bytes() for p in export.output.iterdir()}
        assert prepare_recoverable(*export.args,**export.options)==ref
        assert all(p.read_bytes()==raw for p,raw in before.items())
    for ref in refs:assert Path(ref['uri']).is_relative_to(evidence.experiments)
    assert checked(evidence.template['sealed']['source']).read_bytes()==checked(evidence.baseline).read_bytes()
    assert not (evidence.state/'family-templates').exists()
    return evidence
