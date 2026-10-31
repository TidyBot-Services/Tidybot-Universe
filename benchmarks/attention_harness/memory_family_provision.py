"""Target-task templates and probe-derived Dev arms, using ordinary allocation."""
import json
from pathlib import Path
from attention_memory_service.dev_v2 import digest
from attention_memory_service.family import TARGET_DIFFERENCES
from .core.dev_store import DevConflict
from .dev_v2_loop import checked,immutable
from .memory_validation_provision import Provisioner,read


class FamilyProvisioner(Provisioner):
    def target_origin(self,note,seed,contract):
        from .dev_tasks import get_task
        from .dev_ledger import DevV2Store
        from .dev_experiment import create
        from .dev_planner_memory import context
        from openarm365_dev.policy_validation import validate_generated_policy
        from openarm365_dev_v2.source_policy import DEV_SOURCE_BYTES
        spec=self.client.family_spec
        target=next(t for t in spec['targets'] if t['seed']==seed)
        task=get_task(spec['target_task_id'])
        baseline=target['baseline_source'];source_ref=dict(uri=baseline['path'],sha256=baseline['sha256'])
        source=checked(source_ref).read_bytes().decode('utf-8');validate_generated_policy(source,config=dict(budget_tier='dev',runner_boundary='dev_loop'))
        if len(source.encode())>DEV_SOURCE_BYTES:raise DevConflict('source_size_limit_exceeded')
        candidate=self.client.read('record');ctx=candidate['context']
        # Create an ordinary, never-started target template. It supplies a genuine
        # Dev manifest/config/identity; no execution or validator evidence is invented.
        key='family-template-'+digest(json.dumps([spec,seed],sort_keys=True).encode())[:32]
        eid='devexp-'+digest(key.encode())[:24]
        selection=dict(request_key=key,tasks=[task.task_id],seeds=[seed],policies=['full_trace_aware_attention_planner'],mode='diagnostic',budget_tier='dev',model_source='codex',help_budget=0,run_notes=False,memory_family=False,target_depth=1,target_valid_executions=1,max_versions=1,reproducible_reset=True,scene_contract=contract,planner_source=dict(kind='dev-local',source_root=str(Path(__file__).resolve().parents[2])))
        store=DevV2Store(self.root/eid/'ledger.sqlite3')
        experiment=create(store,self.root,selection)
        from .memory_validation_ownership import record_experiment
        record_experiment(self.state_root,key,eid)
        cell=experiment['cells'][0]
        config=read(experiment['manifest'])['configs'][f'{task.task_id}:{seed}']
        from .memory_fixture_guard import fixture_reason
        if fixture_reason(config):raise DevConflict('test_fixture_receipt_excluded_from_memory')
        # Every origin reference becomes transitive executor evidence when the
        # probe (and later its arms) is imported. Keep it with its owning cell,
        # including baselines supplied from outside any registered catalog.
        directory=Path(experiment['artifact_dir'])/'cells'/cell['key']
        config_ref=immutable(directory/'config.json',config)
        source_path=directory/'baseline-policy.py'
        if not source_path.exists():immutable(source_path,source)
        source_ref=dict(uri=str(source_path),sha256=baseline['sha256'])
        checked(source_ref)
        from .memory_cross_seed import register_contract
        registration_context=dict(ctx,task_id=task.task_id,task_prompt=task.task_prompt,
                                  task_variant_id=task.official_env+':'+ctx['task_variant_id'].split(':',1)[1])
        ref,catalog=register_contract(self.client,self.config.get('contract_registration_roots',[]),contract['uri'],seed,registration_context)
        if ref!=contract:raise DevConflict('Target contract changed before template admission')
        target_ctx=context(config,ctx['namespace'],contract_catalog=catalog)
        if any(ctx.get(k)!=target_ctx.get(k) for k in set(ctx)|set(target_ctx) if k not in TARGET_DIFFERENCES):raise DevConflict('Family target template differs from source scope/profile/SDK; fresh compatible source required')
        feedback=immutable(directory/'no-feedback.json',{})
        return dict(sealed=dict(manifest=experiment['manifest'],config=config_ref,source=source_ref,cell=cell['key'],version=1),
                    context=target_ctx,config=config,feedback=feedback,signature=self.client.provenance(note['memory_id'])['failure_signature'],policy_id=cell['policy_id'])

    def resolve_target(self,note,target,*,pairs=None):
        if pairs is None:return self.target_origin(note,target['seed'],target['contract'])
        probe=self.client.read('probe-'+str(target['seed']))
        e=probe['execution'];config=read(e['config'])
        from .memory_fixture_guard import fixture_reason
        if fixture_reason(config):raise DevConflict('test_fixture_receipt_excluded_from_memory')
        return dict(sealed=dict(manifest=e['manifest'],config=e['config'],source=e['source'],cell=e['cell'],version=e['version']),
                    context=e['context'],config=config,feedback=e['public_feedback'],signature=self.client.provenance(note['memory_id'])['failure_signature'],policy_id='full_trace_aware_attention_planner')
