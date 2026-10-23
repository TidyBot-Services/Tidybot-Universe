"""Ledger adapter for automatically budgeted validation checks.

The injected provision(note, operation_id) resolves the sealed source and creates
10 cells in the existing authorized ledger. It returns the fields consumed by
begin below, plus source_catalogs/failed_execution/source_import. It must be
idempotent and must never copy or modify settled historical evidence.
"""
import json,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_ledger import DevV2Store,experiment_goal
from .dev_v2_loop import checked,immutable,job_for
from .dev_loop import process_identity,owner_alive
from .memory_validation_worker import freeze_plan,contract_catalog
from .memory_independent_validator import validate_arm,replay_fresh
from .memory_source_catalogs import manifest_sources,covers,registration_order


class LedgerRunner:
    def __init__(self,client,provision,*,experiments_root,validator_root,state_root,replay=replay_fresh,provider=None,runner_factory=None,gpu_meter=None):
        self.client,self.provision=client,provision
        self.experiments_root=Path(experiments_root).resolve();self.validator_root=Path(validator_root).resolve();self.state_root=Path(state_root).resolve()
        if self.experiments_root.is_relative_to(self.validator_root) or self.validator_root.is_relative_to(self.experiments_root):raise ValueError('independent validator root required')
        self.replay,self.provider,self.runner_factory,self.gpu_meter=replay,provider,runner_factory,gpu_meter
        self.pool=ThreadPoolExecutor(max_workers=1);self.futures={}

    def begin(self,note,operation):
        receipt=self.state_root/operation/'batch.json'
        if receipt.exists():return json.loads(receipt.read_bytes())
        batch=self.provision(note,operation)
        provenance=self.client.provenance(note['memory_id'])
        origin_sha=provenance.get('source_policy_sha256') or provenance.get('origin',{}).get('digests',{}).get('origin_source')
        if batch['origin']['sha256']!=origin_sha or batch['failure_signature']!=provenance['failure_signature']:raise DevConflict('batch differs from the note frozen origin/signature')
        if batch['dev_model']!={'provider':'codex','model':'gpt-6-astra'}:raise DevConflict('locked Codex Dev model required')
        checked(batch['origin']);config=json.loads(checked(batch['config']).read_bytes())
        from openarm365_dev_v2.scene_contract import registered_contract_ref
        if config.get('scene_contract')!=registered_contract_ref():raise DevConflict('registered scene contract required')
        catalogs=dict(batch['source_catalogs'])
        for payload in (batch['failed_execution'],batch['source_import']):
            catalog=batch['source_catalogs'][payload['catalog_id']]
            descriptor=json.loads(checked(dict(uri=str(Path(catalog['root'])/payload['descriptor']),sha256=catalog['files'][payload['descriptor']])).read_bytes())
            catalogs.update(manifest_sources(descriptor['manifest'],batch['context'],catalog['root'],contract=config['scene_contract']))
        for key,location in batch['cells'].items():
            store=DevV2Store(location['ledger']);c=store.cell(key)
            # All ten arms may use a different checkout from the origin. Include
            # their original source roots before choosing the contract role.
            with store.tx() as db:e=store.get(db,'experiments',c['experiment_id'])
            catalogs.update(manifest_sources(e['manifest'],batch['context'],e['artifact_dir'],contract=config['scene_contract']))
        covered=any(c['role']=='source' and c['context']==batch['context'] and covers(c,config['scene_contract']) for c in catalogs.values())
        if covered:catalogs={cid:c for cid,c in catalogs.items() if not (c['role']=='contract' and covers(c,config['scene_contract']))}
        else:self.client.register_dev_v2_catalog(catalog_id='contract-'+batch['config']['sha256'],catalog=contract_catalog(batch['context']))
        for cid,catalog in registration_order(catalogs).items():self.client.register_dev_v2_catalog(catalog_id=cid,catalog=catalog)
        failed=self.client.import_dev_v2_execution(**batch['failed_execution'])
        origin_summary=note['source_summary']
        if (failed['cell']!=origin_summary['cell'] or failed['version']!=origin_summary['version']
                or failed['config']['sha256']!=batch['config']['sha256']
                or json.loads(checked(failed['manifest']).read_bytes())['experiment_id']!=origin_summary['experiment_id']):raise DevConflict('source execution differs from the note origin')
        imported=self.client.import_dev_v2_source(**batch['source_import'])
        if imported['source_policy_sha256']!=origin_sha or imported['guidance']!=self.client.get_memory(note['memory_id']).guidance:raise DevConflict('imported source differs from the note origin/guidance')
        self.client.create_dev_v2_candidate(memory_id=batch['memory_id'],source_import_id=batch['source_import']['import_id'],created_at=note['created_at'],from_provisional=note['memory_id'])
        if len(batch['pairs'])!=5:raise DevConflict('five frozen pairs required')
        ids=[p[k] for p in batch['pairs'] for k in ('control_cell','treatment_cell')]
        if len(set(ids))!=10 or set(ids)!=set(batch['cells']):raise DevConflict('ten distinct validation cells required')
        for key,location in batch['cells'].items():
            store=DevV2Store(location['ledger']);c=store.cell(key)
            if c.get('run_notes') or c.get('memory_family') or c.get('memory_authority') or (c.get('planner_features') or {}).get('memory'):raise DevConflict('validation cells must have clean Memory')
            with store.tx() as db:e=store.get(db,'experiments',c['experiment_id'])
            from .dev_experiment import verify_manifest
            manifest=verify_manifest(e)
            frozen=manifest['configs'][f"{c['task_id']}:{c['seed']}"]
            if frozen!=config or e['selection'].get('model_source')!='codex' or e['selection'].get('budget_tier')!='dev':raise DevConflict('validation cell frozen configuration differs')
            if not Path(e['artifact_dir']).resolve().is_relative_to(self.experiments_root):raise DevConflict('validation executor outside experiments root')
            store.goal_for_cell(key)  # Existing authorization only; never ensure_goal.
        plan=freeze_plan(batch['memory_id'],batch['origin'],{**config,'sha256':batch['config']['sha256']},batch['context'],batch['failure_signature'],batch['pairs'],policy_id=batch['policy_id'],budget=batch['budget'],dev_model=batch['dev_model'])
        self.client.record_plan(batch['memory_id'],plan);batch['plan']=plan
        receipt.parent.mkdir(parents=True,exist_ok=True);immutable(receipt,batch)
        return batch

    def _cell(self,current):
        key=current['arm_id'];store=DevV2Store(current['cells'][key]['ledger']);return store,key,store.cell(key)

    def start(self,current,arm_id):
        future=self.futures.get(arm_id)
        if future is not None and not future.done():return
        store,key,c=self._cell(current);job=job_for(c,1,'execute')
        if job and job['state']=='settled':return
        if any(j['state'] in {'claimed','needs_review'} for j in c['jobs']):raise DevConflict('unresolved validation job; no redispatch')
        if c.get('owner') and owner_alive(c['owner']):raise DevConflict('validation cell already has a live owner')
        from .dev_experiment import acquire
        owner=process_identity();e=acquire(store,c['experiment_id'],owner)
        if e['state']=='needs_review':raise DevConflict('validation recovery needs review')
        store.update_cell(key,state='running',owner=owner)
        self.futures[arm_id]=self.pool.submit(self._run,dict(current),owner)

    def _run(self,current,owner):
        from . import dev_experiment as experiments
        from .memory_validation_generation import generate
        from .dev_memory_validation import prepare_validation
        from .dev_v2_loop import execute
        store,key,c=self._cell(current)
        try:
            config=json.loads(checked(current['config']).read_bytes());directory=Path(c['artifact_dir'])/'v1'
            with store.tx() as db:paused=bool(store.get(db,'experiments',c['experiment_id']).get('pause_requested_at'))
            if paused:store.update_cell(key,state='planned',reason='operator_paused');return
            store.start_cell_clock(key)
            record=next((v for v in c['versions'] if v['version']==1),None)
            if record is None:
                note=self.client.get_memory(current['memory_id'])
                if getattr(note.status,'value',note.status)!='candidate' or self.client.get_plan(current['memory_id'])!=current['plan']:raise DevConflict('validation candidate or plan changed before generation')
                memory=dict(memory_id=note.memory_id,version=note.version,guidance=note.guidance) if current['arm']=='treatment' else None
                record=generate(store,key,config,current['origin'],json.loads(checked(current['origin_feedback']).read_bytes()),owner,memory=memory,client=self.provider)
            with store.tx() as db:paused=bool(store.get(db,'experiments',c['experiment_id']).get('pause_requested_at'))
            if paused:
                store.update_cell(key,state='planned',reason='operator_paused');return
            envelope_ref=directory/'memory-validation-envelope.json'
            if envelope_ref.exists():envelope=json.loads(envelope_ref.read_bytes())
            else:
                envelope=prepare_validation(store,key,1,config,current['context'],self.client,current['memory_id'],'none' if current['arm']=='control' else 'candidate_validation',directory,origin_source_sha256=current['origin']['sha256'])
                immutable(envelope_ref,envelope)
            # Recheck the service's immutable grant and candidate immediately before dispatch.
            grant=json.loads(checked(envelope['grant']).read_bytes());note=self.client.get_memory(current['memory_id'])
            if getattr(note.status,'value',note.status)!='candidate' or note.version!=grant['binding']['memory_version']:raise DevConflict('validation candidate changed')
            execute(store,key,1,record,config,owner,directory,self.runner_factory,self.provider,memory_validation=envelope)
            store.update_cell(key,state='completed')
        except BaseException:
            store.update_cell(key,state='needs_review');raise
        finally:
            store.update_cell(key,owner=None)
            with store.tx() as db:
                row=store.get(db,'experiments',c['experiment_id']);row['owner']=None
                state=store.get(db,'cells',key)['state'];row['state']='needs_review' if state=='needs_review' else 'planned'
                if row.get('pause_requested_at') and state=='planned':
                    now=time.time();row.update(state='paused',paused_at=now)
                    goal=store.get(db,'goals',experiment_goal(row));goal['paused_at']=now;store.put(db,'goals',experiment_goal(row),goal)
                    store.event(db,'experiment_paused',dict(experiment_id=row['experiment_id'],paused_at=now))
                elif state=='completed':row.pop('pause_requested_at',None)
                store.put(db,'experiments',c['experiment_id'],row)

    def poll(self,current):
        store,key,c=self._cell(current);future=self.futures.get(key)
        if future is not None and future.done():future.result()
        job=job_for(c,1,'execute')
        if job and job['state']=='settled':return 'completed'
        with store.tx() as db:e=store.get(db,'experiments',c['experiment_id'])
        if e['state']=='paused':return 'paused'
        if c['state']=='needs_review':raise DevConflict('validation cell needs review')
        if future is None and not (c.get('owner') and owner_alive(c['owner'])):return 'pending'
        return 'running'

    def pause(self,current):
        from .dev_experiment import request_pause
        store,key,c=self._cell(current)
        if job_for(c,1,'execute') and job_for(c,1,'execute')['state']=='settled':return
        request_pause(store,c['experiment_id'])

    def resume(self,current):
        from .dev_experiment import resume_paused
        store,key,c=self._cell(current);resume_paused(store,c['experiment_id']);self.start(current,key)

    def finish(self,current):
        from .dev_v2_memory_import import prepare
        store,key,c=self._cell(current);record=next(v for v in c['versions'] if v['version']==1)
        with store.tx() as db:e=store.get(db,'experiments',c['experiment_id'])
        config=json.loads(checked(current['config']).read_bytes())
        sources=manifest_sources(e['manifest'],current['context'],e['artifact_dir'],contract=config.get('scene_contract'))
        for cid,sealed in registration_order(sources).items():self.client.register_dev_v2_catalog(catalog_id=cid,catalog=sealed)
        output=Path(e['artifact_dir'])/'validation-import'/key
        ref=output/'catalog.json'
        if not ref.exists():prepare(store,key,1,current['context']['namespace'],output,self.experiments_root,source_catalogs=sources)
        catalog=json.loads(ref.read_bytes());descriptor=json.loads((output/'descriptor.json').read_bytes())
        # Service import includes exact original refs without relocating them.
        # Independent replay keeps its stricter, arm-experiment-only root/seal.
        local_root=Path(e['artifact_dir']).resolve()
        local_catalog={**catalog,'root':str(local_root),'files':{str((Path(catalog['root'])/name).relative_to(local_root)):digest for name,digest in catalog['files'].items() if (Path(catalog['root'])/name).is_relative_to(local_root)}}
        receipt=self.state_root/'verdicts'/f'{key}.json';receipt.parent.mkdir(parents=True,exist_ok=True)
        if receipt.exists():result=json.loads(receipt.read_bytes())
        else:
            if self.gpu_meter is None:raise DevConflict('independent GPU time measurement unavailable')
            envelope=json.loads((Path(c['artifact_dir'])/'v1/memory-validation-envelope.json').read_bytes());grant=json.loads(checked(envelope['grant']).read_bytes())
            result=validate_arm(experiment_dir=e['artifact_dir'],experiments_root=self.experiments_root,cell=key,version=1,execution_import_id=key,
                bundle_ref=job_for(c,1,'execute')['receipt']['bundle'],config_ref=descriptor['config'],source_ref=record['source'],grant={**grant,'permission_scope':descriptor['permission_scope']},permission_scope=descriptor['permission_scope'],executor_catalog=local_catalog,
                contract=json.loads(checked(descriptor['config']).read_bytes())['scene_contract'],validator_root=self.validator_root,replay=self.replay,failure_signature=current['failure_signature'],budget=current['budget'],developer_model=current['dev_model']['model'],gpu_seconds=self.gpu_meter(store,key),ledger_ref=descriptor['ledger'])
            immutable(receipt,result)
        if result['status']=='refused':raise DevConflict('independent validation refused: '+str(result.get('reason')))
        self.client.register_dev_v2_catalog(catalog_id=key,catalog=catalog)
        self.client.register_dev_v2_catalog(catalog_id='validator-'+key,catalog=result['catalog'])
        self.client.import_dev_v2_execution(import_id=key,catalog_id=key,descriptor=str((output/'descriptor.json').relative_to(catalog['root'])))
        if result['status']=='failed':
            trial=json.loads((Path(result['catalog']['root'])/'trial.json').read_bytes())
            return dict(status='failed',descriptor=result['descriptor'],checks=result['checks'],failed_check=dict(memory_id=current['memory_id'],validation_grant_id=trial['validation_grant_id'],execution_import_id=key,catalog_id='validator-'+key,descriptor=str(Path(result['descriptor']['uri']).relative_to(result['catalog']['root']))))
        self.client.import_dev_v2_trial(import_id='trial-'+key,catalog_id='validator-'+key,descriptor='trial.json')
        return dict(trial_import_id='trial-'+key,status=result['status'],descriptor=result['descriptor'])


def from_config(root):
    """Factory for --adapter ...memory_validation_runtime:from_config.

    Runtime JSON may override the default sealed-origin provisioning factory.
    The default creates exactly ten arms within the worker's automatic budget.
    """
    import importlib,os
    from types import SimpleNamespace
    from .memory_service_client import MemoryServiceClient
    from .memory_validation_worker import run_activity,weekly_quota
    config=json.loads((Path(root)/'runtime.json').read_bytes())
    client=MemoryServiceClient.offline(config['base_url'],key_file=config['key_file'])
    config['state_root']=str(Path(root).resolve())
    module,name=config.get('provision_factory','benchmarks.attention_harness.memory_validation_provision:provision_factory').split(':')
    provision=getattr(importlib.import_module(module),name)(config)
    from .memory_validation_provision import Provisioner
    if isinstance(provision,Provisioner):provision.client=client
    runner=LedgerRunner(client,provision,experiments_root=config['experiments_root'],validator_root=config['validator_root'],state_root=root,
                        gpu_meter=getattr(provision,'gpu_seconds',None))
    def activity(current):
        own=current.get('experiment_id') if current else None
        if current and current.get('arm_id'):
            store,key,c=runner._cell(current);own=c['experiment_id']
        roots=[Path(p) for p in config['activity_ledger_roots']]
        explicit=[Path(p) for p in config['activity_ledgers']]
        if not roots and not explicit:raise ValueError('run activity ledger inventory is not configured')
        if any(not p.is_dir() for p in roots):raise ValueError('run activity root unavailable')
        ledgers=set(explicit)
        for folder in roots:ledgers.update(folder.rglob('ledger.sqlite3'));ledgers.update(folder.rglob('ledger.sqlite'))
        return run_activity(sorted(ledgers),own_experiment=own,own_pids=(os.getpid(),))
    return SimpleNamespace(client=client,runner=runner,activity=activity,quota=lambda:weekly_quota(config.get('quota_home')))


# Public default factory; from_config remains compatible with existing units.
build_runtime=from_config
