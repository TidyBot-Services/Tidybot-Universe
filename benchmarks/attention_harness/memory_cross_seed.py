"""Per-seed contract admission and sealed-origin cross-seed provisioning.

Only the offline importer registers catalogs. Nothing changes the global scene
contract, and missing target contracts never fall back to it.
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_v2_loop import checked, immutable
from .memory_validation_provision import read, target_config, validate_creation


def register_contract(client, roots, path, seed, context):
    from openarm365_dev_v2.scene_contract import load_contract, seed_entry, SCHEMA_V2
    if not path:raise DevConflict(f'Seed {seed}: no scene contract registered')
    p=Path(path)
    if not p.is_absolute() or p!=p.resolve() or not p.is_file():raise DevConflict(f'Seed {seed}: no scene contract registered')
    if not any(Path(r).is_absolute() and Path(r)==Path(r).resolve() and p.is_relative_to(Path(r)) for r in roots):
        raise DevConflict(f'Seed {seed}: contract is outside configured registration roots')
    ref=dict(uri=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
    value=load_contract(ref)
    if value['schema']!=SCHEMA_V2:raise DevConflict(f'Seed {seed}: a seed-keyed scene contract is required')
    try:seed_entry(value,context['task_id'],seed)
    except (ValueError,KeyError):raise DevConflict(f'Seed {seed}: contract does not contain this seed') from None
    ctx=dict(context,seed=seed,scene_contract_ref=ref)
    catalog=dict(role='contract',root=str(p.parent),context=ctx,files={p.name:ref['sha256']})
    cid='cross-contract-'+hashlib.sha256(json.dumps(catalog,sort_keys=True).encode()).hexdigest()
    try:client.register_dev_v2_catalog(catalog_id=cid,catalog=catalog)
    except Exception:raise DevConflict(f'Seed {seed}: no scene contract registered (registration refused)') from None
    return ref,catalog


def resolve_origin(provision,note):
    """Use the linked M-8 origin; verify it against the trusted note's source."""
    if getattr(provision.client,'spec',None) is not None:return provision.resolve(note)
    p=provision.client.provenance(note['memory_id'])
    linked=p.get('from_provisional')
    seen={note['memory_id']}
    while linked:
        if linked in seen:raise DevConflict('Cyclic origin provenance')
        seen.add(linked);prior=provision.client.provenance(linked)
        if prior.get('origin'):break
        linked=prior.get('from_provisional')
    if linked:
        origin=prior.get('origin',{})
        summary=dict(experiment_id=origin.get('experiment_id'),cell=origin.get('cell'),version=origin.get('origin_version'))
        result=provision.resolve(dict(memory_id=linked,source_summary=summary))
    else:
        # Manually validated Dev v2 candidates may predate provisional linking.
        catalogs=provision.catalogs();matches=[];sources=[]
        for cid,catalog in catalogs.items():
            if catalog['role']!='executor':continue
            for name,digest in catalog['files'].items():
                if not name.endswith('.json'):continue
                d=read(dict(uri=str(Path(catalog['root'])/name),sha256=digest))
                if not isinstance(d,dict) or d.get('schema_version')!='attentionbench.dev-v2-import.v1':continue
                if 'failed_execution_import_id' in d:sources.append((cid,name,d));continue
                if 'execute_job_id' not in d:continue
                v=read(d['version_record'])
                if v['source']['sha256']==p['source_policy_sha256'] and 'run:'+d['cell']==p['source_run_id'] and f"attempt:{d['cell']}:{d['version']-1}"==p['source_attempt_id']:
                    matches.append((cid,name,d,v))
        if len(matches)!=1:raise DevConflict('Frozen origin execution is missing or ambiguous')
        cid,name,d,v=matches[0];m=read(d['manifest']);config=read(d['config'])
        sources=[s for s in sources if s[2]['manifest']==d['manifest'] and s[2]['cell']==d['cell'] and s[2]['config']==d['config']]
        if len(sources)!=1:raise DevConflict('Frozen origin source import is missing or ambiguous')
        sc,sn,sd=sources[0]
        # Idempotently re-import under the original immutable source ID. This
        # rejects a different frozen config even when its policy hash matches.
        for registered_id,catalog in catalogs.items():provision.client.register_dev_v2_catalog(catalog_id=registered_id,catalog=catalog)
        provision.client.import_dev_v2_execution(import_id=sd['failed_execution_import_id'],catalog_id=cid,descriptor=name)
        imported=provision.client.import_dev_v2_source(import_id=p['source_import_id'],catalog_id=sc,descriptor=sn)
        if imported['source_policy_sha256']!=p['source_policy_sha256'] or imported['guidance']!=provision.client.get_memory(note['memory_id']).guidance:raise DevConflict('Frozen origin source import differs from trusted note')
        with __import__('sqlite3').connect(checked(d['ledger']).as_uri()+'?mode=ro',uri=True) as db:
            jobs=[json.loads(r[0]) for r in db.execute('SELECT payload FROM v2_jobs WHERE id=?',(d['execute_job_id'],))]
        if len(jobs)!=1:raise DevConflict('Frozen origin execution is missing')
        cell=next(c for c in m['cells'] if c['key']==d['cell'])
        result=dict(sealed=dict(manifest=d['manifest'],config=d['config'],source=v['source'],cell=d['cell'],version=d['version']),
                    config=config,context=d['context'],signature=p['failure_signature'],feedback=jobs[0]['receipt']['public_feedback'],policy_id=cell['policy_id'],catalogs=catalogs)
    if result['sealed']['source']['sha256']!=p['source_policy_sha256'] or result['signature']!=p['failure_signature']:
        raise DevConflict('Cross-seed origin differs from trusted source')
    return result


def admit(provision,note,seed,path):
    if not path or not Path(path).is_file():raise DevConflict(f'Seed {seed}: no scene contract registered')
    resolved=resolve_origin(provision,note)
    roots=provision.config.get('contract_registration_roots',[])
    ref,catalog=register_contract(provision.client,roots,path,seed,resolved['context'])
    return dict(seed=seed,contract=ref,catalog=catalog)


def provision_cross(provision,note,operation,target,*,pairs=None):
    runs=1 if pairs is None else 2*pairs
    if runs not in (1,6,10):raise DevConflict('One probe, three or five pairs required')
    resolved=resolve_origin(provision,note)
    # Recheck file and importer admission on every retry before any allocation.
    ref,catalog=register_contract(provision.client,provision.config.get('contract_registration_roots',[]),target['contract']['uri'],target['seed'],resolved['context'])
    if ref!=target['contract']:raise DevConflict('Target contract changed after admission')
    origin=dict(resolved['sealed'],cross_seed=dict(seed=target['seed'],contract=ref,runs=runs))
    config=target_config(origin)
    selection=dict(request_key='cross-'+operation,tasks=[config['task_id']],seeds=[config['seed']],policies=[resolved['policy_id']],mode='diagnostic',budget_tier='dev',model_source='codex',help_budget=0,run_notes=False,memory_family=False,target_depth=1,target_valid_executions=1,max_versions=1,reproducible_reset=True,scene_contract=ref,attempt_wall_seconds=config['attempt_wall_seconds'])
    from .dev_configuration import normalize_configuration,derive_cell_budget,worst_slot
    normalized=normalize_configuration(selection);budget=derive_cell_budget(normalized)
    selection['wall_limit']=max(worst_slot(normalized),runs*(budget['model_seconds']+budget['execution_seconds']))
    validate_creation(selection,origin)
    exp_id='devexp-'+hashlib.sha256(selection['request_key'].encode()).hexdigest()[:24]
    ledger=provision.root/exp_id/'ledger.sqlite3'
    if not ledger.exists():
        state_path=provision.state_root/'state.json';state=json.loads(state_path.read_bytes()) if state_path.exists() else {}
        today=datetime.fromtimestamp(provision.clock()).astimezone().date().isoformat()
        used=state.get('runs_today',0) if state.get('day')==today else 0
        from .memory_validation_worker import daily_run_stop
        if type(used) is not int or used<0 or daily_run_stop({'runs_today':used},provision.state_root,needed=runs):raise DevConflict(f'Fewer than {runs} automatic checks remain today')
    from .dev_experiment import create
    from .dev_ledger import DevV2Store
    from .memory_validation_ownership import record_experiment
    store=DevV2Store(ledger);experiment=create(store,provision.root,selection,validation_origin=origin)
    record_experiment(provision.state_root,operation,exp_id)
    cells=experiment['cells'];config_ref=dict(uri=str(Path(cells[0]['artifact_dir'])/'config.json'))
    config_ref['sha256']=hashlib.sha256(Path(config_ref['uri']).read_bytes()).hexdigest()
    context=dict(resolved['context'],seed=target['seed'],scene_contract_ref=ref)
    rows=[] if pairs is None else [dict(pair_id=str(i),**{a+'_cell':next(c['key'] for c in cells if c['validation_pair']==i and c['validation_arm']==a) for a in ('control','treatment')}) for i in range(pairs)]
    return dict(kind='cross_seed',stage='probe' if pairs is None else 'pairs',target_seed=target['seed'],target=target,note=note,operation=operation,
                experiment_id=exp_id,memory_id=note['memory_id'],memory_version=note['version'],origin=origin['source'],origin_feedback=resolved['feedback'],
                config=config_ref,context=context,failure_signature=resolved['signature'],dev_model=dict(provider='codex',model='gpt-6-astra'),
                budget=dict(assistance_credits=provision.client.get_plan(note['memory_id'])['assistance_credits']),pairs=rows,
                probe_cell=cells[0]['key'] if pairs is None else None,cells={c['key']:dict(ledger=str(ledger)) for c in cells})
