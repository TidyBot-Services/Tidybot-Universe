"""Restricted agent arms of the offline single-scene validation plan."""
import hashlib,json
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_v2_loop import checked,immutable

ARM_BUDGET=dict(formal_versions=1,moving_executions=5,read_only_probes=60,eval_runs=4,help=0,
    trial_runs=4,sdk_calls_per_execution=320,control_steps_per_execution=9600,
    execution_wall_seconds=1920,active_wall_seconds=1920,model_turns=None,tokens=None)


MODEL_FIELDS=('dev_model','dev_reasoning_effort','eval_model')


def origin_models(origin):
    """Read explicit model choices only from the digest-checked origin manifest."""
    from .dev_agent_config import MODELS
    manifest=json.loads(checked(origin['manifest']).read_bytes())
    models={k:manifest.get(k) for k in MODEL_FIELDS}
    if (manifest.get('dev_mode')!='agent' or any(type(v) is not str for v in models.values())
            or models['dev_model'] not in MODELS or models['eval_model'] not in MODELS
            or models['dev_reasoning_effort'] not in ('low','medium','high')):
        raise ValueError('unsupported agent validation origin models')
    return models


def origin_policy_version(origin):
    """Legacy origins have the v1 epoch; new arms never use creation defaults."""
    if not isinstance(origin,dict) or not isinstance(origin.get('manifest'),dict):
        raise ValueError('agent_memory_or_control_not_supported')
    manifest=json.loads(checked(origin['manifest']).read_bytes())
    version=manifest.get('agent_policy_version','agent-policy-v1')
    if manifest.get('dev_mode')!='agent' or version not in ('agent-policy-v1','agent-policy-v2'):
        raise ValueError('unsupported agent validation origin policy version')
    return version


def origin_visibility(origin):
    from .dev_agent_policies import memory_exposed
    manifest=json.loads(checked(origin['manifest']).read_bytes())
    cell=next(c for c in manifest['cells'] if c['key']==origin['cell'])
    if origin_policy_version(origin)=='agent-policy-v2' and not memory_exposed(cell['policy_id']):
        return 'validation_override'
    return None


def validate_options(selection,origin):
    if not isinstance(origin,dict) or set(origin)!={'manifest','config','source','cell','version'}:
        raise ValueError('agent_memory_or_control_not_supported')
    manifest=json.loads(checked(origin['manifest']).read_bytes());config=json.loads(checked(origin['config']).read_bytes())
    if manifest.get('dev_mode')!='agent' or config.get('dev_mode')!='agent':raise ValueError('mixed agent validation is unsupported')
    if selection.get('agent_policy_version','agent-policy-v1')!=origin_policy_version(origin):
        raise ValueError('agent validation policy version differs from origin')
    expected=dict(mode='diagnostic',help_budget=0,run_notes=False,memory_family=False,target_depth=1,
        target_valid_executions=1,max_versions=1,reproducible_reset=True,attempt_wall_seconds=1920,
        **origin_models(origin))
    if any(selection.get(k)!=v or type(selection.get(k)) is not type(v) for k,v in expected.items()) or config.get('attempt_wall_seconds')!=1920:
        raise ValueError('exact agent validation arm required')
    if any(selection.get(k) for k in ('memory_authority','planner_features','human_proxy','positive_control','fork')) or selection.get('advisor_proxy',{}).get('enabled') or selection.get('eval_agent',{}).get('enabled'):
        raise ValueError('agent validation cannot acquire extra assistance')
    if selection.get('agent_validation',origin)!=origin:raise ValueError('agent validation origin differs')
    defaults=dict(coverage='budget_limited',startup_cleanup_seconds=60,model_reserve_seconds=360,max_input_bytes_per_request=150000)
    if any(selection.get(k,v)!=v for k,v in defaults.items()):raise ValueError('exact agent validation resource allocation required')


def validate_manifest(manifest):
    origin=manifest['validation_origin'];validate_options(manifest['selection'],origin)
    version=origin_policy_version(origin)
    rows=[manifest,*manifest['configs'].values(),*manifest['cells']]
    visibility=origin_visibility(origin)
    if any((row.get('memory_visibility')!=visibility or ('memory_visibility' in row)!=(visibility is not None)) for row in (manifest,*manifest['cells'])) or any('memory_visibility' in row for row in manifest['configs'].values()):
        raise ValueError('agent validation memory visibility differs from origin')
    if any(row.get('agent_policy_version','agent-policy-v1')!=version for row in rows):
        raise ValueError('agent validation policy version differs from origin')
    if any(manifest.get(k)!=v for k,v in origin_models(origin).items()):raise ValueError('agent validation models differ from origin')
    if (manifest.get('agent_validation')!=origin or manifest.get('agent_budget')!=ARM_BUDGET
            or any(type(manifest['agent_budget'].get(k)) is not type(v) for k,v in ARM_BUDGET.items())
            or manifest.get('schedule')!=[dict(cell=c['key'],through_version=1) for c in manifest['cells']]
            or len(manifest['cells'])!=10 or any(c.get('excluded_from_results') is not True for c in manifest['cells'])):raise ValueError('agent validation frozen budget/schedule differs')


def shared(ref):
    from .dev_agent_workspace import read_beneath
    path=checked(ref)
    data=read_beneath(path.parent,path.name,32*1024*1024,shared=True)
    if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise DevConflict('agent validation origin digest differs')
    return data


def seed(workspace,current):
    from .dev_agent_workspace import atomic_write
    source=shared(current['origin']);feedback=json.loads(shared(current['origin_feedback']))
    private=workspace.private/'validation';private.mkdir(parents=True,exist_ok=True)
    immutable(private/'parent-source.py',source.decode())
    immutable(private/'origin-feedback.json',feedback)
    if not (workspace.canonical/'origin/public-feedback.json').exists():
        atomic_write(workspace.ws,'work/main.py',source)
        workspace.publish('origin/policy.py',source)
        workspace.publish('origin/public-feedback.json',feedback)


class Delivery:
    def __init__(self,memory,current,client):
        self.memory,self.current,self.client=memory,current,client
        self.note=client.get_memory(current['memory_id']);self.plan=current['plan']
        self.version=self.note.version;self.guidance=self.note.guidance

    def recheck(self,packet=None):
        note=self.client.get_memory(self.current['memory_id'])
        if (getattr(note.status,'value',note.status)!='candidate' or note.version!=self.version
                or note.guidance!=self.guidance or self.client.get_plan(note.memory_id)!=self.plan
                or self.client.decision(note.memory_id) is not None):raise PermissionError('validation candidate changed')
        if packet is not None and packet!=self.packet:raise PermissionError('validation packet changed')
        if hasattr(self,'input'):
            if json.loads(checked(self.input).read_bytes())!=self.packet:raise PermissionError('validation input changed')
            checked(self.exposure)

    def load(self,number,previous,directory):
        self.recheck()
        if number!=1 or hashlib.sha256(previous.encode()).hexdigest()!=self.current['origin']['sha256']:
            raise PermissionError('agent validation has one origin generation')
        entries=[]
        if self.current['arm']=='treatment':
            entries=[dict(memory_id=self.note.memory_id,version=self.version,content=self.guidance,
                development_id=f'{self.memory.key}:v1',source_policy_sha256=self.current['origin']['sha256'],
                unvalidated=True,excluded_from_results=True,trusted_here=False)]
        self.packet=dict(schema_version='attentionbench.memory-input.v1',entries=entries,excluded_from_results=True)
        self.input=immutable(directory/'memory-input.json',self.packet)
        self.exposure=immutable(directory/'dev-memory-exposure.json',dict(schema_version='attentionbench.dev-memory-exposure.v1',
            cell=self.memory.key,version=1,model_input=self.input,entries=[dict(memory_id=e['memory_id'],
                memory_version=e['version'],guidance_sha256=hashlib.sha256(e['content'].encode()).hexdigest()) for e in entries]))
        with self.memory.store.tx() as db:
            payload=dict(cell=self.memory.key,version=1,memory_input=self.input,dev_memory_exposure=self.exposure)
            rows=[json.loads(p) for p, in db.execute("SELECT payload FROM v2_events WHERE kind='agent_validation_memory'")]
            prior=[p for p in rows if p['cell']==self.memory.key]
            if prior and prior!=[payload]:raise DevConflict('agent validation delivery differs')
            if not prior:self.memory.store.event(db,'agent_validation_memory',payload)
        return self.packet,self

    def prepare(self,response):
        self.recheck(self.memory.packet)
        if response['version_id']!='v0001':raise PermissionError('agent validation formal version exceeded')
        key=self.memory.key;store=self.memory.store;config=self.memory.config
        source=dict(uri=str(self.memory.workspace.private/'sources'/(response['request_id']+'.py')))
        source['sha256']=hashlib.sha256(Path(source['uri']).read_bytes()).hexdigest()
        config_ref=immutable(Path(store.cell(key)['artifact_dir'])/'config.json',config)
        grant=self.client.authorize_dev_v2_validation(memory_id=self.note.memory_id,cell=key,version=1,seed=config['seed'],
            context=self.current['context'],policy_sha256=source['sha256'],config_sha256=config_ref['sha256'],
            exposure='candidate_validation' if self.current['arm']=='treatment' else 'none',origin_source_sha256=self.current['origin']['sha256'])
        directory=Path(store.cell(key)['artifact_dir'])/'v1'
        value=dict(memory_ids=[self.note.memory_id] if self.packet['entries'] else [],exposure=grant['binding']['exposure'],
            context=self.current['context'],policy_sha256=source['sha256'],config_sha256=config_ref['sha256'],validation_grant_id=grant['grant_id'])
        envelope=dict(input=immutable(directory/'memory-validation-input.json',value),
            grant=immutable(directory/'memory-validation-grant.json',grant),guidance=self.guidance if self.packet['entries'] else None)
        immutable(directory/'memory-validation-envelope.json',envelope)
        with store.tx() as db:
            store.event(db,'memory_validation_dispatch',dict(cell=key,version=1,validation_input=envelope['input'],
                validation_grant_id=grant['grant_id'],source=source,config=config_ref))
        self.envelope=envelope
        return envelope


def invalid_arm(runner,current,store,key,cell):
    import sqlite3
    from .dev_experiment import verify_manifest
    with store.tx() as db:experiment=store.get(db,'experiments',cell['experiment_id'])
    manifest=verify_manifest(experiment)
    out=Path(experiment['artifact_dir'])/'invalid-import'/key;out.mkdir(parents=True,exist_ok=True)
    result_path=out/'result.json'
    if result_path.exists():return json.loads(result_path.read_bytes())
    marker=out/'catalog.json'
    if marker.exists():catalog=json.loads(marker.read_bytes())
    else:
        backup=out/'ledger.sqlite'
        with sqlite3.connect(store.path.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(backup) as dest:source.backup(dest)
        ledger=dict(uri=str(backup),sha256=hashlib.sha256(backup.read_bytes()).hexdigest())
        config=immutable(out/'config.json',manifest['configs'][f"{cell['task_id']}:{cell['seed']}"])
        immutable(out/'descriptor.json',dict(cell=key,ledger=ledger,manifest=experiment['manifest'],config=config,reason=cell['reason']))
        root=Path(experiment['artifact_dir'])
        refs=[ledger,config,experiment['manifest'],dict(uri=str(out/'descriptor.json'),sha256=hashlib.sha256((out/'descriptor.json').read_bytes()).hexdigest())]
        catalog=dict(root=str(root),role='executor',context=current['context'],ledger_origin=str(store.path),
            files={str(Path(ref['uri']).relative_to(root)):ref['sha256'] for ref in refs})
        immutable(marker,catalog)
    eid='invalid-'+key;runner.client.register_dev_v2_catalog(catalog_id=eid,catalog=catalog)
    raw=json.loads((out/'descriptor.json').read_bytes())
    envelope=Path(cell['artifact_dir'])/'v1/memory-validation-envelope.json'
    if envelope.exists():grant=json.loads(checked(json.loads(envelope.read_bytes())['grant']).read_bytes())
    else:
        grant=runner.client.authorize_dev_v2_validation(memory_id=current['memory_id'],cell=key,version=1,
            seed=manifest['configs'][f"{cell['task_id']}:{cell['seed']}"]['seed'],context=current['context'],
            policy_sha256=current['origin']['sha256'],config_sha256=current['config']['sha256'],
            exposure='candidate_validation' if current['arm']=='treatment' else 'none',origin_source_sha256=current['origin']['sha256'])
    independent=runner.validator_root/('invalid-'+key);independent.mkdir(parents=True,exist_ok=True)
    descriptor=immutable(independent/'invalid.json',dict(schema_version='attentionbench.agent-validation-invalid.v1',
        execution_import_id=key,executor_catalog_id=eid,executor_descriptor=str((out/'descriptor.json').relative_to(catalog['root'])),
        ledger=raw['ledger'],manifest=raw['manifest'],config=raw['config'],cell=key,reason=raw['reason'],validation_grant_id=grant['grant_id']))
    validator=dict(root=str(independent),role='validator',context=current['context'],files={'invalid.json':descriptor['sha256']})
    runner.client.register_dev_v2_catalog(catalog_id='validator-'+eid,catalog=validator)
    result=dict(status='failed',descriptor=descriptor,checks=dict(native_evaluation=False),failed_check=dict(memory_id=current['memory_id'],
        validation_grant_id=grant['grant_id'],execution_import_id=key,catalog_id='validator-'+eid,descriptor='invalid.json'))
    immutable(result_path,result)
    return result
