"""Ledger adapter for automatically budgeted validation checks.

The injected provision(note, operation_id) resolves the sealed source and creates
10 logical arm cells in the existing authorized ledger. C17 replacements use
fresh versions in both paired cells and a bounded supplemental authorization. It returns the fields consumed by
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
from .memory_validation_replacements import attempt_version,attempt_id,authorize_replacement


class LedgerRunner:
    def __init__(self,client,provision,*,experiments_root,validator_root,state_root,replay=replay_fresh,provider=None,runner_factory=None,gpu_meter=None):
        self.client,self.provision=client,provision
        self.experiments_root=Path(experiments_root).resolve();self.validator_root=Path(validator_root).resolve();self.state_root=Path(state_root).resolve()
        if self.experiments_root.is_relative_to(self.validator_root) or self.validator_root.is_relative_to(self.experiments_root):raise ValueError('independent validator root required')
        self.replay,self.provider,self.runner_factory,self.gpu_meter=replay,provider,runner_factory,gpu_meter
        self.pool=ThreadPoolExecutor(max_workers=1);self.futures={}
        from .memory_origin_evidence import OriginPreparation
        self.origins=OriginPreparation(self)

    def origin_needed(self,note):
        if getattr(self.client,'family_channel',False):return None
        from .memory_validation_provision import Provisioner
        return self.origins.needed(note) if isinstance(self.provision,Provisioner) else None

    def admit_seed(self,note,seed,path):
        from .memory_cross_seed import admit
        return admit(self.provision,note,seed,path)

    def begin_cross(self,note,operation,target,*,pairs=None):
        from .memory_cross_seed import provision_cross
        receipt=self.state_root/operation/'batch.json'
        if receipt.exists():return json.loads(receipt.read_bytes())
        batch=provision_cross(self.provision,note,operation,target,pairs=pairs)
        config=json.loads(checked(batch['config']).read_bytes())
        for key,location in batch['cells'].items():
            store=DevV2Store(location['ledger']);cell=store.cell(key)
            if cell.get('run_notes') or cell.get('memory_family') or cell.get('memory_authority') or cell.get('planner_features'):raise DevConflict('cross-seed cells must have clean Memory')
            with store.tx() as db:e=store.get(db,'experiments',cell['experiment_id'])
            from .dev_experiment import verify_manifest
            manifest=verify_manifest(e)
            if manifest['configs'][f"{cell['task_id']}:{cell['seed']}"]!=config:raise DevConflict('target frozen configuration differs')
            store.goal_for_cell(key)
            for cid,catalog in registration_order(manifest_sources(e['manifest'],batch['context'],e['artifact_dir'],contract=config['scene_contract'])).items():
                self.client.register_dev_v2_catalog(catalog_id=cid,catalog=catalog)
        if pairs is not None:
            plan=self.client.cross_seed_write('plans',memory_id=note['memory_id'],target_seed=target['seed'],pairs=batch['pairs'],dev_model=batch['dev_model'])
            if plan['validation_config_sha256']!=batch['config']['sha256'] or plan['origin_source_sha256']!=batch['origin']['sha256'] or plan['context']!=batch['context']:raise DevConflict('service cross-seed plan differs from frozen batch')
            batch['plan']=plan
        receipt.parent.mkdir(parents=True,exist_ok=True);immutable(receipt,batch)
        return batch

    def begin(self,note,operation):
        receipt=self.state_root/operation/'batch.json'
        if receipt.exists():return json.loads(receipt.read_bytes())
        batch=self.provision(note,operation)
        provenance=self.client.provenance(note['memory_id'])
        origin_sha=provenance.get('source_policy_sha256') or provenance.get('origin',{}).get('digests',{}).get('origin_source')
        if batch['origin']['sha256']!=origin_sha or batch['failure_signature']!=provenance['failure_signature']:raise DevConflict('batch differs from the note frozen origin/signature')
        if batch.get('dev_mode')!='agent' and batch['dev_model']!={'provider':'codex','model':'gpt-6-astra'}:raise DevConflict('locked Codex Dev model required')
        checked(batch['origin']);config=json.loads(checked(batch['config']).read_bytes())
        from openarm365_dev_v2.scene_contract import registered_contract_ref
        if config.get('scene_contract')!=registered_contract_ref():raise DevConflict('registered scene contract required')
        catalogs=dict(batch['source_catalogs'])
        for payload in filter(None,(batch['failed_execution'],batch['source_import'])):
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
                or failed['config']['sha256']!=batch.get('origin_config',batch['config'])['sha256']
                or json.loads(checked(failed['manifest']).read_bytes())['experiment_id']!=origin_summary['experiment_id']):raise DevConflict('source execution differs from the note origin')
        if batch.get('positive_control'):
            self.client.create_positive_control(**batch['positive_control'])
        else:
            imported=self.client.import_dev_v2_source(**batch['source_import'])
            if imported['source_policy_sha256']!=origin_sha or imported['guidance']!=self.client.get_memory(note['memory_id']).guidance:raise DevConflict('imported source differs from the note origin/guidance')
            self.client.create_dev_v2_candidate(memory_id=batch['memory_id'],source_import_id=batch['source_import']['import_id'],created_at=note['created_at'],from_provisional=note['memory_id'])
        if len(batch['pairs'])!=5:raise DevConflict('five frozen pairs required')
        ids=[p[k] for p in batch['pairs'] for k in ('control_cell','treatment_cell')]
        if len(set(ids))!=10 or set(ids)!=set(batch['cells']):raise DevConflict('ten distinct validation cells required')
        modes=set()
        for key,location in batch['cells'].items():
            store=DevV2Store(location['ledger']);c=store.cell(key)
            modes.add(c.get('dev_mode','one_call'))
            if c.get('run_notes') or c.get('memory_family') or c.get('memory_authority') or (c.get('planner_features') or {}).get('memory'):raise DevConflict('validation cells must have clean Memory')
            with store.tx() as db:e=store.get(db,'experiments',c['experiment_id'])
            from .dev_experiment import verify_manifest
            manifest=verify_manifest(e)
            if batch.get('dev_mode')=='agent':
                from .memory_agent_validation import origin_models
                models=origin_models(manifest['validation_origin'])
                if (batch['dev_model']!=dict(provider='codex',model=models['dev_model'])
                        or any(batch.get(k)!=models[k] for k in ('dev_reasoning_effort','eval_model'))):
                    raise DevConflict('agent validation batch models differ from origin')
            frozen=manifest['configs'][f"{c['task_id']}:{c['seed']}"]
            if frozen!=config or e['selection'].get('model_source')!='codex' or e['selection'].get('budget_tier')!='dev':raise DevConflict('validation cell frozen configuration differs')
            if not Path(e['artifact_dir']).resolve().is_relative_to(self.experiments_root):raise DevConflict('validation executor outside experiments root')
            store.goal_for_cell(key)  # Existing authorization only; never ensure_goal.
        if len(modes)!=1 or (modes=={'agent'})!=(batch.get('dev_mode')=='agent'):raise DevConflict('mixed agent validation batch')
        plan=freeze_plan(batch['memory_id'],batch['origin'],{**config,'sha256':batch['config']['sha256']},batch['context'],batch['failure_signature'],batch['pairs'],policy_id=batch['policy_id'],budget=batch['budget'],dev_model=batch['dev_model'])
        self.client.record_plan(batch['memory_id'],plan);batch['plan']=plan
        receipt.parent.mkdir(parents=True,exist_ok=True);immutable(receipt,batch)
        return batch

    def validation_ready(self,note,current,*,cross=False):
        if getattr(note,'record_kind',None)=='family_candidate':
            return cross and note.status in {'candidate','family_trusted'}
        if getattr(note,'record_kind',None)=='positive_control':
            decision=self.client.decision(current['memory_id'])
            return bool(decision and decision['decision']=='would_pass') if cross else decision is None
        return getattr(note.status,'value',note.status)==('trusted' if cross else 'candidate')

    def _cell(self,current):
        key=current['arm_id'];store=DevV2Store(current['cells'][key]['ledger']);return store,key,store.cell(key)

    def start(self,current,arm_id):
        if current.get('kind')=='origin':return self.origins.start(current)
        future=self.futures.get(attempt_id(current))
        if future is not None and not future.done():return
        store,key,c=self._cell(current);version=attempt_version(current)
        if version>1:authorize_replacement(store,current)
        job=job_for(c,version,'execute')
        if job and job['state']=='settled':return
        if any(j['state'] in {'claimed','needs_review'} for j in c['jobs']):raise DevConflict('unresolved validation job; no redispatch')
        if c.get('owner') and owner_alive(c['owner']):raise DevConflict('validation cell already has a live owner')
        from .dev_experiment import acquire
        owner=process_identity();e=acquire(store,c['experiment_id'],owner)
        if e['state']=='needs_review':raise DevConflict('validation recovery needs review')
        store.update_cell(key,state='running',owner=owner)
        self.futures[attempt_id(current)]=self.pool.submit(self._run,dict(current),owner)

    def _run(self,current,owner):
        from . import dev_experiment as experiments
        from .memory_validation_generation import generate
        from .dev_memory_validation import prepare_validation
        from .dev_v2_loop import execute
        store,key,c=self._cell(current);version=attempt_version(current)
        try:
            config=json.loads(checked(current['config']).read_bytes());directory=Path(c['artifact_dir'])/f'v{version}'
            with store.tx() as db:paused=bool(store.get(db,'experiments',c['experiment_id']).get('pause_requested_at'))
            if paused:store.update_cell(key,state='planned',reason='operator_paused');return
            store.start_cell_clock(key)
            if c.get('dev_mode')=='agent':
                if current.get('kind')=='cross_seed' or version!=1:raise DevConflict('agent validation is single-scene and single-version')
                with store.tx() as db:experiment=store.get(db,'experiments',c['experiment_id'])
                manifest=experiments.verify_manifest(experiment)
                from .dev_agent_loop import run_agent_cell
                result=run_agent_cell(store,experiment,manifest,key,owner=owner,client=self.provider,runner_factory=self.runner_factory,
                    validation=dict(current=current,client=self.client))
                cell=store.cell(key)
                execution=job_for(cell,1,'execute')
                if result.get('reason')!='operator_paused' and (not execution or execution['state']!='settled'):
                    store.update_cell(key,state='invalid_generation',reason='agent_arm_invalid:'+str(result.get('reason')))
                return
            record=next((v for v in c['versions'] if v['version']==version),None)
            cross=current.get('kind')=='cross_seed';probe=cross and current['stage']=='probe'
            if cross:checked(current['target']['contract'])
            note=self.client.get_memory(current['memory_id']) if cross else None
            if cross and (not self.validation_ready(note,current,cross=True) or note.version!=current['memory_version']):raise DevConflict('cross-seed note changed')
            if record is None:
                if probe:
                    directory.mkdir(exist_ok=True)
                    if c.get('run_notes') or c.get('memory_family') or c.get('memory_authority') or c.get('planner_features') or c['jobs']:raise DevConflict('probe must execute origin directly with clean Memory')
                    record=dict(version=version,source=immutable(directory/'policy.py',checked(current['origin']).read_text()),config_sha256=current['config']['sha256'],valid=True,formal_eligible=False)
                    with store.tx() as db:record['manifest']=store.get(db,'experiments',c['experiment_id'])['manifest']
                    immutable(directory/'version.json',record);store.version(key,record)
                else:
                    note=self.client.get_memory(current['memory_id'])
                    if not cross and (not self.validation_ready(note,current) or self.client.get_plan(current['memory_id'])!=current['plan']):raise DevConflict('validation candidate or plan changed before generation')
                    memory=dict(memory_id=note.memory_id,version=note.version,guidance=note.guidance) if current['arm']=='treatment' else None
                    if memory is not None and getattr(note,'treatment_label',None):memory['label']=note.treatment_label
                    record=generate(store,key,config,current['origin'],json.loads(checked(current['origin_feedback']).read_bytes()),owner,memory=memory,client=self.provider,version=version)
            if record.get('status')=='invalid_generation':
                store.update_cell(key,state='invalid_generation',reason=record['validation_error']);return
            with store.tx() as db:paused=bool(store.get(db,'experiments',c['experiment_id']).get('pause_requested_at'))
            if paused:
                store.update_cell(key,state='planned',reason='operator_paused');return
            envelope_ref=directory/'memory-validation-envelope.json'
            if probe:
                immutable(directory/'memory-validation-input.json',dict(memory_ids=[],exposure='none',context=current['context'],policy_sha256=record['source']['sha256'],config_sha256=current['config']['sha256']))
                execute(store,key,version,record,config,owner,directory,self.runner_factory,self.provider,gpu_measurement=True)
                store.update_cell(key,state='completed');return
            if envelope_ref.exists():envelope=json.loads(envelope_ref.read_bytes())
            else:
                envelope=prepare_validation(store,key,version,config,current['context'],self.client,current['memory_id'],'none' if current['arm']=='control' else 'candidate_validation',directory,origin_source_sha256=current['origin']['sha256'],**({'target_seed':current['target_seed']} if cross else {}))
                immutable(envelope_ref,envelope)
            # Recheck the service's immutable grant and candidate immediately before dispatch.
            grant=json.loads(checked(envelope['grant']).read_bytes());note=self.client.get_memory(current['memory_id'])
            if not self.validation_ready(note,current,cross=cross) or note.version!=grant['binding']['memory_version']:raise DevConflict('validation candidate changed')
            execute(store,key,version,record,config,owner,directory,self.runner_factory,self.provider,memory_validation=envelope)
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
                elif state in {'completed','invalid_generation'}:row.pop('pause_requested_at',None)
                store.put(db,'experiments',c['experiment_id'],row)

    def recover_invalid(self,current):
        from .memory_validation_generation import generate,parse_generation
        store,key,c=self._cell(current);version=attempt_version(current)
        if c.get('state')!='needs_review':return False
        with store.tx() as db:
            if db.execute("SELECT 1 FROM v2_jobs WHERE state IN ('reserved','claimed','needs_review') LIMIT 1").fetchone():return False
        if any(j['state'] in {'reserved','claimed','needs_review'} for j in c['jobs']):return False
        if c.get('owner') and owner_alive(c['owner']):return False
        model=job_for(c,version,'model')
        if not model or model['state']!='settled' or job_for(c,version,'execute'):return False
        output=json.loads(checked(model['receipt']['response']).read_bytes())
        error=parse_generation(output['content'])[2]
        if error is None:return False
        record=next((v for v in c['versions'] if v['version']==version),None)
        if record and (record.get('status')!='invalid_generation' or record.get('validation_error')!=error):raise DevConflict('generation record differs from settled invalid output')
        if record is None:
            note=self.client.get_memory(current['memory_id'])
            memory=dict(memory_id=note.memory_id,version=note.version,guidance=note.guidance) if current['arm']=='treatment' else None
            generate(store,key,json.loads(checked(current['config']).read_bytes()),current['origin'],
                     json.loads(checked(current['origin_feedback']).read_bytes()),None,memory=memory,version=version)
        with store.tx() as db:
            e=store.get(db,'experiments',c['experiment_id'])
            if e.get('owner') and owner_alive(e['owner']):raise DevConflict('validation experiment still owned')
            # The old worker parked after this settled generation. Account its
            # downtime once, just as an ordinary paused interval, without refunds.
            g=store.get(db,'goals',experiment_goal(e))
            paused_since=g.get('paused_at') or model['settled_at']
            g['paused_seconds']=g.get('paused_seconds',0)+max(0,time.time()-paused_since)
            g['paused_at']=None
            store.put(db,'goals',experiment_goal(e),g)
            e.update(state='planned',owner=None);e.pop('pause_requested_at',None)
            store.put(db,'experiments',e['experiment_id'],e)
            raw=store.get(db,'cells',key);raw.update(state='invalid_generation',owner=None)
            store.put(db,'cells',key,raw)
            store.event(db,'invalid_generation_recovered',dict(cell=key,version=version))
        return True

    def poll(self,current):
        if current.get('kind')=='origin':return self.origins.poll(current)
        store,key,c=self._cell(current);version=attempt_version(current);future=self.futures.get(attempt_id(current))
        if future is not None:
            if not future.done():return 'running'
            future.result()
        if self.recover_invalid(current):return 'completed'
        c=store.cell(key)
        if c.get('dev_mode')=='agent' and c.get('state')=='invalid_generation':return 'completed'
        if any(v['version']==version and v.get('status')=='invalid_generation' for v in c['versions']):return 'completed'
        job=job_for(c,version,'execute')
        if job and job['state']=='settled':return 'completed'
        with store.tx() as db:e=store.get(db,'experiments',c['experiment_id'])
        if e['state']=='paused':return 'paused'
        if c['state']=='needs_review':raise DevConflict('validation cell needs review')
        if future is None and not (c.get('owner') and owner_alive(c['owner'])):return 'pending'
        return 'running'

    def pause(self,current):
        if current.get('kind')=='origin':return self.origins.pause(current)
        from .dev_experiment import request_pause
        store,key,c=self._cell(current)
        if c.get('state') in {'invalid_generation','needs_review','completed'}:return
        if job_for(c,attempt_version(current),'execute') and job_for(c,attempt_version(current),'execute')['state']=='settled':return
        request_pause(store,c['experiment_id'])

    def resume(self,current):
        if current.get('kind')=='origin':return self.origins.resume(current)
        from .dev_experiment import resume_paused
        store,key,c=self._cell(current);resume_paused(store,c['experiment_id']);self.start(current,key)

    def finish(self,current):
        if current.get('kind')=='origin':return self.origins.finish(current)
        from .dev_v2_memory_import import prepare_recoverable
        store,key,c=self._cell(current)
        # Other finish paths retain the resolved cell key and original v1 imports.
        pair_arm=(current.get('kind') not in {'begin','origin','cross_seed'}
                  and current.get('arm') in {'control','treatment'} and current.get('arm_id')==key)
        version=attempt_version(current) if pair_arm else 1
        identity=attempt_id(current) if pair_arm else key
        if c.get('dev_mode')=='agent' and c.get('state')=='invalid_generation':
            from .memory_agent_validation import invalid_arm
            return invalid_arm(self,current,store,key,c)
        record=next(v for v in c['versions'] if v['version']==version)
        if pair_arm and record.get('status')=='invalid_generation':return dict(status='invalid_generation',reason=record['validation_error'],cell=key,version=version,model_output=record['model_output'])
        with store.tx() as db:e=store.get(db,'experiments',c['experiment_id'])
        config=json.loads(checked(current['config']).read_bytes())
        sources=manifest_sources(e['manifest'],current['context'],e['artifact_dir'],contract=config.get('scene_contract'))
        for cid,sealed in registration_order(sources).items():self.client.register_dev_v2_catalog(catalog_id=cid,catalog=sealed)
        output=Path(e['artifact_dir'])/'validation-import'/identity
        ref=output/'catalog.json'
        cross=current.get('kind')=='cross_seed';probe=cross and current['stage']=='probe'
        prepare_recoverable(store,key,version,current['context']['namespace'],output,self.experiments_root,source_catalogs=sources,motion_permission='dev_tier_sim_motion',import_purpose='memory_validation',**({'contract_catalog':current['target']['catalog']} if cross else {}))
        catalog=json.loads(ref.read_bytes());descriptor=json.loads((output/'descriptor.json').read_bytes())
        # Service import includes exact original refs without relocating them.
        # Independent replay keeps its stricter, arm-experiment-only root/seal.
        local_root=Path(e['artifact_dir']).resolve()
        local_catalog={**catalog,'root':str(local_root),'files':{str((Path(catalog['root'])/name).relative_to(local_root)):digest for name,digest in catalog['files'].items() if (Path(catalog['root'])/name).is_relative_to(local_root)}}
        receipt=self.state_root/'verdicts'/f'{identity}.json';receipt.parent.mkdir(parents=True,exist_ok=True)
        if receipt.exists():result=json.loads(receipt.read_bytes())
        else:
            if self.gpu_meter is None:raise DevConflict('independent GPU time measurement unavailable')
            grant=None
            if not probe:
                envelope=json.loads((Path(c['artifact_dir'])/f'v{version}/memory-validation-envelope.json').read_bytes());grant=json.loads(checked(envelope['grant']).read_bytes())
            result=validate_arm(experiment_dir=e['artifact_dir'],experiments_root=self.experiments_root,cell=key,version=version,execution_import_id=identity,
                bundle_ref=job_for(c,version,'execute')['receipt']['bundle'],config_ref=descriptor['config'],source_ref=record['source'],grant=None if probe else {**grant,'permission_scope':descriptor['permission_scope']},permission_scope=descriptor['permission_scope'],executor_catalog=local_catalog,
                contract=json.loads(checked(descriptor['config']).read_bytes())['scene_contract'],validator_root=self.validator_root,replay=self.replay,failure_signature=current['failure_signature'],budget=current['budget'],developer_model=current['dev_model']['model'],gpu_seconds=self.gpu_meter(store,key,version) if version>1 else self.gpu_meter(store,key),ledger_ref=descriptor['ledger'],import_purpose=descriptor.get('import_purpose'),source_drift=descriptor.get('source_drift'),current_lock=descriptor.get('current_lock'),
                **({'probe_input':descriptor['validation_input'],'origin_source_sha256':current['origin']['sha256']} if probe else {}))
            immutable(receipt,result)
        if result['status']=='refused':raise DevConflict('independent validation refused: '+str(result.get('reason')))
        self.client.register_dev_v2_catalog(catalog_id=identity,catalog=catalog)
        self.client.register_dev_v2_catalog(catalog_id='validator-'+identity,catalog=result['catalog'])
        self.client.import_dev_v2_execution(import_id=identity,catalog_id=identity,descriptor=str((output/'descriptor.json').relative_to(catalog['root'])))
        if probe:
            value=self.client.cross_seed_write('probes',memory_id=current['memory_id'],target_seed=current['target_seed'],execution_import_id=identity,catalog_id='validator-'+identity,descriptor=str(Path(result['descriptor']['uri']).relative_to(result['catalog']['root'])),scene_contract_ref=current['target']['contract'])
            return dict(status='probe',probe=value)
        if result['status']=='failed':
            trial=json.loads((Path(result['catalog']['root'])/'trial.json').read_bytes())
            return dict(status='failed',descriptor=result['descriptor'],checks=result['checks'],failed_check=dict(memory_id=current['memory_id'],validation_grant_id=trial['validation_grant_id'],execution_import_id=identity,catalog_id='validator-'+identity,descriptor=str(Path(result['descriptor']['uri']).relative_to(result['catalog']['root']))))
        self.client.import_dev_v2_trial(import_id='trial-'+identity,catalog_id='validator-'+identity,descriptor='trial.json')
        return dict(trial_import_id='trial-'+identity,status=result['status'],descriptor=result['descriptor'])


def from_config(root,*,quota_probe_command=None):
    """Factory for --adapter ...memory_validation_runtime:from_config.

    Runtime JSON may override the default sealed-origin provisioning factory.
    The default creates exactly ten arms within the worker's automatic budget.
    """
    import importlib,os
    from types import SimpleNamespace
    from .memory_service_client import MemoryServiceClient,validate_evaluate_timeout
    from .memory_validation_worker import run_activity,weekly_quota_snapshot,refresh_quota
    config=json.loads((Path(root)/'runtime.json').read_bytes())
    evaluate_timeout=validate_evaluate_timeout(config.get('evaluate_timeout_seconds',1800))
    client=MemoryServiceClient.offline(config['base_url'],key_file=config['key_file'],evaluate_timeout_seconds=evaluate_timeout)
    config['state_root']=str(Path(root).resolve())
    module,name=config.get('provision_factory','benchmarks.attention_harness.memory_validation_provision:provision_factory').split(':')
    provision=getattr(importlib.import_module(module),name)(config)
    from .memory_validation_provision import Provisioner
    if isinstance(provision,Provisioner):provision.client=client
    runner=LedgerRunner(client,provision,experiments_root=config['experiments_root'],validator_root=config['validator_root'],state_root=root,
                        gpu_meter=getattr(provision,'gpu_seconds',None))
    def activity(current):
        from .memory_validation_ownership import owned_experiments
        activity.ignored_stale_runs=[]
        own=current.get('experiment_id') if current else None
        if current and current.get('arm_id'):
            store,key,c=runner._cell(current);own=c['experiment_id']
        roots=[Path(p) for p in config['activity_ledger_roots']]
        explicit=[Path(p) for p in config['activity_ledgers']]
        if not roots and not explicit:raise ValueError('run activity ledger inventory is not configured')
        if any(not p.is_dir() for p in roots):raise ValueError('run activity root unavailable')
        ledgers=set(explicit)
        for folder in roots:ledgers.update(folder.rglob('ledger.sqlite3'));ledgers.update(folder.rglob('ledger.sqlite'))
        return run_activity(sorted(ledgers),own_experiment=own,owned_experiments=owned_experiments(root),own_pids=(os.getpid(),),ignored_stale_runs=activity.ignored_stale_runs)
    return SimpleNamespace(client=client,runner=runner,activity=activity,
        quota=lambda:weekly_quota_snapshot(config.get('quota_home')),
        refresh_quota=lambda:refresh_quota(home=config.get('quota_home'),command=quota_probe_command))


# Public default factory; from_config remains compatible with existing units.
build_runtime=from_config
