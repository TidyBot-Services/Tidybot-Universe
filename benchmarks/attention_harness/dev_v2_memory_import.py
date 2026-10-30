"""Trusted offline Dev v2 evidence preparation/import; never imported by a model.

Preparation pins actual files and takes a consistent SQLite backup. It does not
invent an independent verdict, register catalogs, promote, or edit an authority.
The operator provisions the resulting catalog before authenticated HTTP import.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from .core.dev_store import DevConflict
from .dev_ledger import DevV2Store
from .dev_loop import sha
from .dev_v2_loop import immutable, checked


def queue(store, cell, version, directory, kind, refs):
    if kind=='result':
        packet_path=Path(directory)/'memory-input.json'
        if packet_path.is_file():
            packet=json.loads(packet_path.read_bytes())
            if packet.get('excluded_from_results') is True or any(
                    e.get('excluded_from_results') is True or e.get('seeded_for_tests') is True
                    for e in packet.get('entries',[])):
                raise DevConflict('test-seeded Memory grant excluded from result queue')
    names={'result':'memory-result.json','provisional_outcome':'memory-provisional-outcome.json'}
    ref=immutable(Path(directory)/names.get(kind,'memory-candidate-proposal.json'),dict(
        schema_version='attentionbench.dev-v2-offline-queue.v1',cell=cell,version=version,
        kind=kind,ledger_path=str(store.path.resolve()),evidence=refs,
        status='pending_offline_import',authority_candidate=False))
    with store.tx() as db:
        exists=db.execute("SELECT 1 FROM v2_events WHERE kind=? AND payload LIKE ?",
                         ('planner_memory_offline_queued','%'+ref['sha256']+'%')).fetchone()
        if not exists:store.event(db,'planner_memory_offline_queued',dict(cell=cell,version=version,kind=kind,queue=ref))
    return ref


def prepare(store, cell, version, namespace, output, catalog_root, *, source=None, source_catalogs=None, contract_catalog=None, failure_signature=None, motion_permission=None, import_purpose=None):
    from .dev_planner_memory import context, sdk_identity
    from .dev_planner import binding
    from .memory_motion_permission import ACTION, PURPOSE, validation_scope
    if motion_permission not in (None, ACTION) or import_purpose not in (None, PURPOSE, 'memory_origin'):
        raise DevConflict('unsupported import permission or purpose')
    if motion_permission and (source is not None or import_purpose not in (PURPOSE, 'memory_origin')):
        raise DevConflict('simulated Dev motion is only for Memory validation execution imports')
    def planner_snapshot():
        if hasattr(store,'planner_snapshot'):return store.planner_snapshot(cell)
        from .memory_origin_evidence import OriginalStore
        return OriginalStore(store.path).planner_snapshot(cell)
    c=store.cell(cell)
    agent_validation=c.get('dev_mode')=='agent' and c.get('validation_experiment') is True and import_purpose==PURPOSE and source is None
    if c.get('dev_mode')=='agent':
        from .dev_agent_export import exported
        if (not exported(c,version) or not agent_validation and ((source is not None or motion_permission or import_purpose) and import_purpose != 'memory_origin'
                or c.get('validation_experiment') or c.get('validation_origin')
                or (Path(c['artifact_dir'])/f'v{version}'/'memory-validation-input.json').exists()) or c.get('positive_control')):
            raise DevConflict('agent_memory_auto_import_and_validation_unsupported')
    if import_purpose == 'memory_origin' and c.get('dev_mode') != 'agent':raise DevConflict('agent origin purpose required')
    v=next(v for v in c['versions'] if v['version']==version)
    if source is None:
        packet_path=Path(c['artifact_dir'])/f'v{version}'/'memory-input.json'
        if packet_path.is_file():
            packet=json.loads(packet_path.read_bytes())
            if not agent_validation and (packet.get('excluded_from_results') is True or any(
                    e.get('excluded_from_results') is True or e.get('seeded_for_tests') is True
                    for e in packet.get('entries',[]))):
                raise DevConflict('test-seeded Memory grant excluded from result import')
    with store.tx() as db:e=store.get(db,'experiments',c['experiment_id'])
    config_ref={'uri':str(Path(c['artifact_dir'])/'config.json')}
    config_ref['sha256']=sha(config_ref['uri']);config=json.loads(checked(config_ref).read_bytes())
    if c.get('dev_mode')=='agent':
        manifest=json.loads(checked(e['manifest']).read_bytes())
        if agent_validation:
            from .dev_experiment import verify_manifest
            verify_manifest(e)
        if (not agent_validation and (manifest.get('validation_experiment')
                or any(obj.get(k) for obj in (manifest.get('selection',{}),config,manifest) for k in ('validation_experiment','positive_control','validation_origin','origin_dev_lock')))):
            raise DevConflict('agent_memory_auto_import_and_validation_unsupported')
    record_ref={'uri':str(Path(c['artifact_dir'])/f'v{version}'/'version.json')}
    record_ref['sha256']=sha(record_ref['uri'])
    if json.loads(checked(record_ref).read_bytes())!=v:raise DevConflict('original version artifact differs from ledger')
    source_evidence={}
    if source and c.get('dev_mode')=='agent':
        from .memory_agent_origin import verify
        source_evidence=verify(store,c,v,json.loads(checked(e['manifest']).read_bytes()),config)
    elif source and source.get('request_id') is None and json.loads(checked(v['help']).read_bytes()).get('provider')=='advisor':
        from .memory_advisor_evidence import verify_advisor_job
        source_evidence=verify_advisor_job(store,c,v,json.loads(checked(e['manifest']).read_bytes()),config)
    elif source:
        from .memory_advisor_evidence import verify_source_providers
        source_evidence=verify_source_providers(c,v,json.loads(checked(e['manifest']).read_bytes()),config)
    root=Path(catalog_root).resolve();out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False)
    backup=out/'ledger.sqlite'
    with sqlite3.connect('file:'+str(store.path.resolve())+'?mode=ro',uri=True) as original, sqlite3.connect(backup) as dest:original.backup(dest)
    descriptor=dict(schema_version='attentionbench.dev-v2-import.v1',cell=cell,version=version,
        context=context(config,namespace,**({'contract_catalog':contract_catalog} if contract_catalog is not None else {})),sdk_identity=sdk_identity(config),ledger={'uri':str(backup),'sha256':sha(backup)},
        manifest=e['manifest'],config=config_ref,version_record=record_ref)
    jobs={j['kind']:j for j in c['jobs'] if j['version']==version}
    if source:
        descriptor['feedback_projection']=source_evidence['feedback_projection']
        # These names refer to separately provisioned independent validator
        # output and an already imported failed execution; no boolean verdicts.
        if set(source)!={'failed_execution_import_id','validator_id','validation_descriptor','request_id'}:raise ValueError('exact independent source prerequisites required')
        if failure_signature is not None:descriptor['failure_signature']=failure_signature
        if c.get('dev_mode')=='agent':descriptor.update(source,agent_origin=source_evidence['agent_origin'],import_purpose='memory_origin')
        else:descriptor.update(source,advisor_job_id=jobs['advisor']['job_id'],model_job_id=jobs['model']['job_id'])
        # Visual and verified non-planner Advisor help have no request row.
        # Omit the field so readers do not query a lazily-created table with a null ID.
        if descriptor.get('request_id') is None:descriptor.pop('request_id',None)
    else:
        j=jobs['execute']
        if j['state']!='settled':raise DevConflict('execution must settle; preserve unknown holds')
        descriptor.update(execute_job_id=j['job_id'])
        if 'model' in jobs:descriptor['model_job_id']=jobs['model']['job_id']
        bundle=json.loads(checked(j['receipt']['bundle']).read_bytes());episode=Path(bundle['artifact_dir'])
        if import_purpose is not None:descriptor['import_purpose']=import_purpose
        scope=binding(cell,version,checked(v['source']).read_text(),config)
        if c.get('dev_mode')=='agent':
            descriptor['agent_execution']=dict(request_id=j['receipt']['agent_request_id'])
        if scope and c.get('dev_mode')=='agent':
            descriptor['permission_scope']=dict(scope, action='agent_dev_tier_motion')
        elif scope:
            approvals=planner_snapshot()['requests']
            matches=[a for a in approvals if a.get('state')=='consumed' and a.get('consumed_by')==j['job_id']]
            if not matches and motion_permission == ACTION:
                try:
                    descriptor['permission_scope']=validation_scope(cell,version,checked(v['source']).read_text(),config,bundle,import_purpose)
                except ValueError as exc:raise DevConflict(str(exc)) from exc
            else:
                if len(matches)!=1:raise DevConflict('The original execution needs exactly one consumed motion approval.')
                a=matches[0]
                descriptor.update(permission_scope=scope,approval_id=a['request_id'])
        else:
            descriptor['permission_scope']=dict(run_id='run:'+cell,cell=cell,code_version=version,
                code_sha256=v['source']['sha256'],config_sha256=__import__('benchmarks.attention_harness.dev_planner',fromlist=['digest']).digest(config),action='sensor_only',action_version=1,scope=dict(operations=['sensors'],task_id=config['task_id'],robot=config['robot'],seed=config['seed'],sdk_calls=config['sdk_call_limit'],control_steps=config['horizon'],wall_seconds=config['attempt_wall_seconds']))
        descriptor['public_observations']={}
        for label in ('initial','final'):
            p=episode/(label+'_public_observation.json')
            if p.is_file():descriptor['public_observations'][label]={'uri':str(p),'sha256':sha(p)}
        if config.get('scene_contract'):
            p=episode/'scene-condition.json';descriptor['scene_state']={'uri':str(p),'sha256':sha(p)}
        validation=Path(c['artifact_dir'])/f'v{version}'/'memory-validation-input.json'
        if validation.is_file():descriptor['validation_input']={'uri':str(validation),'sha256':sha(validation)}
    from .memory_source_drift import current_identity
    manifest=json.loads(checked(e['manifest']).read_bytes())
    if manifest.get('validation_experiment') or config.get('origin_dev_lock'):
        from openarm365_dev_v2.dev_config import verify_lock
        current_lock=manifest['identity_lock']
        epoch=json.loads(checked(dict(uri=current_lock['path'],sha256=current_lock['sha256'])).read_bytes())['source_root']
        verify_lock(current_lock,expected_source_root=epoch)
        drift=None
    else:current_lock,drift=current_identity(config,manifest['identity_lock'],out)
    if drift:
        descriptor.update(current_lock=current_lock,source_drift=drift)
    elif manifest.get('source_drift'):
        descriptor['source_drift']=manifest['source_drift']
    descriptor_ref=immutable(out/'descriptor.json',descriptor)
    files={};symlinks={};visited=set()
    def pin(path,expected=None):
        p=Path(path)
        if not p.is_absolute():
            if c.get('dev_mode')!='agent':raise ValueError('only original absolute evidence paths are pinned')
            from .dev_agent_workspace import read_beneath
            base=Path(c['artifact_dir'])/'agent/private/public'
            data=read_beneath(base,str(p),32*1024*1024,shared=True)
            if hashlib.sha256(data).hexdigest()!=expected:raise DevConflict('agent public evidence digest drift')
            p=base/p
        # This registered contract is sealed in a source or separate contract
        # catalog. Only exact locked source files may use source catalogs.
        if contract_catalog is not None:
            # The target manifest also pins its frozen origin manifest/config.
            # Keep that original registered contract independently; it must
            # never stand in for the target seed's explicit contract above.
            from openarm365_dev_v2.scene_contract import registered_contract_ref
            origin=json.loads(checked(e['manifest']).read_bytes()).get('validation_origin',{})
            if origin:
                origin_contract=json.loads(checked(origin['config']).read_bytes()).get('scene_contract')
                if origin_contract=={'uri':str(p),'sha256':expected} and origin_contract==registered_contract_ref():
                    checked(origin_contract);return
        if config.get('scene_contract')=={'uri':str(p),'sha256':expected}:
            from openarm365_dev_v2.scene_contract import registered_contract_ref
            if config['scene_contract']==registered_contract_ref():return
            if contract_catalog is not None:
                from .memory_source_catalogs import covers
                if contract_catalog.get('role')=='contract' and covers(contract_catalog,config['scene_contract']):
                    checked(config['scene_contract']);return
        if not p.is_relative_to(root):
            for cid,catalog in (source_catalogs or {}).items():
                base=Path(catalog['root'])
                if (catalog['role']=='source' and catalog['context']==descriptor['context'] and p.is_relative_to(base)
                        and catalog['files'].get(str(p.relative_to(base)))==expected):
                    name=str(p.relative_to(base))
                    if name in catalog.get('links',{}):
                        from .memory_source_catalogs import verify_source_link
                        verify_source_link(source_catalogs,cid,name,expected);return
                    if p.resolve()!=p:raise DevConflict('canonical original source file required: '+str(p))
                    if sha(p)!=expected:raise DevConflict('source evidence digest drift: '+str(p))
                    return
        if not p.is_relative_to(root) or not p.resolve().is_relative_to(root):raise ValueError('catalog root must contain only explicitly pinned evidence: '+str(p))
        actual=sha(p)
        if expected and actual!=expected:raise DevConflict('source evidence digest drift: '+str(p))
        files[str(p.relative_to(root))]=actual
        if any(q.is_symlink() for q in (*p.parents,p)):
            symlinks[str(p.relative_to(root))]=str(p.resolve().relative_to(root));pin(p.resolve(),actual)
        if p in visited:return
        visited.add(p)
        if p.suffix=='.json':walk(json.loads(p.read_bytes()))
    def walk(value):
        if isinstance(value,dict):
            if 'sha256' in value and ('uri' in value or 'path' in value):pin(value.get('uri',value.get('path')),value['sha256'])
            # Historical locks remain sealed; their code pins resolve via the sealed current lock.
            if value.get('schema')=='attentionbench.dev-identity-lock.v2':
                from openarm365_dev_v2.source_policy import dev_tier
                if dev_tier(config):
                    current=json.loads(checked(dict(uri=current_lock['path'],sha256=current_lock['sha256'])).read_bytes())
                    for name,digest in current['files'].items():pin(name,digest)
                    for key,item in value.items():
                        if key not in ('files','source_sets'):walk(item)
                    return
            # Identity locks pin transitive SDK, planner and model bytes.
            for k,vv in value.items():
                if isinstance(k,str) and k.startswith('/') and isinstance(vv,str) and len(vv)==64:pin(k,vv)
                else:walk(vv)
        elif isinstance(value,list):
            for vv in value:walk(vv)
    walk(descriptor_ref)
    # SQLite references settled provider/bundle/approval receipts not necessarily
    # reachable from version.json, and source imports need the prior failed job.
    for j in c['jobs']:
        if j['version']<=version:walk(j.get('receipt',{}))
    walk(planner_snapshot())
    return immutable(out/'catalog.json',dict(root=str(root),role='executor',ledger_origin=str(store.path.resolve()),
        context=descriptor['context'],files=files,symlinks=symlinks))


def prepare_recoverable(store, cell, version, namespace, output, catalog_root, **options):
    """Resume a worker export without relocating its absolute sealed references.

    catalog.json is the completion marker, written last by prepare. Serialize
    retries before removing an interrupted export; completed exports are never
    removed or rewritten, even if a later consumer rejects them.
    """
    import fcntl
    import shutil
    out=Path(output)
    out.parent.mkdir(parents=True,exist_ok=True)
    with (out.parent/('.'+out.name+'.prepare.lock')).open('a') as guard:
        fcntl.flock(guard,fcntl.LOCK_EX)
        if out.is_symlink():raise DevConflict('import output must not be a symlink')
        catalog=out/'catalog.json'
        if catalog.exists():return dict(uri=str(catalog.resolve()),sha256=sha(catalog))
        if out.exists():shutil.rmtree(out)
        return prepare(store,cell,version,namespace,out,catalog_root,**options)


OPERATIONS={'execution':'import_dev_v2_execution','source':'import_dev_v2_source',
    'candidate':'create_dev_v2_candidate','trial':'import_dev_v2_trial','pair':'record_dev_v2_pair','result':'record_dev_v2_result'}


def submit(base_url, operation, payload, output, *, ledger=None, cell=None, version=None):
    from .memory_service_client import MemoryServiceClient
    if ledger:
        check=DevV2Store(ledger);record=check.cell(cell)
        if not any(v['version']==version for v in record['versions']):raise DevConflict('offline receipt target version missing')
    online=os.environ.get('ATTENTION_MEMORY_API_KEY');importer=os.environ.get('ATTENTION_MEMORY_IMPORTER_KEY')
    if not online or not importer or online==importer:raise PermissionError('distinct offline importer capability required')
    client=MemoryServiceClient(base_url,api_key=online,importer_key=importer)
    response=getattr(client,OPERATIONS[operation])(**payload)
    # IDs/payloads are immutable in Service; retries cannot create new attempts.
    ref=immutable(output,dict(operation=operation,payload=payload,response=response))
    if ledger:
        store=DevV2Store(ledger)
        if not cell or type(version) is not int:raise ValueError('offline import receipt must bind cell/version')
        c=store.cell(cell)
        with store.tx() as db:
            value=dict(cell=cell,version=version,operation=operation,receipt=ref)
            prior=db.execute("SELECT 1 FROM v2_events WHERE kind='planner_memory_authority_receipt' AND payload=?",(__import__('benchmarks.attention_harness.dev_ledger',fromlist=['encoded']).encoded(value),)).fetchone()
            if not prior:store.event(db,'planner_memory_authority_receipt',value)
    return ref


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--store',required=True);a.add_argument('--cell',required=True);a.add_argument('--version',type=int,required=True);a.add_argument('--namespace',required=True);a.add_argument('--catalog-root',required=True);a.add_argument('--output',required=True);a.add_argument('--source-prerequisites')
    a=sub.add_parser('submit');a.add_argument('--base-url',required=True);a.add_argument('--operation',choices=OPERATIONS,required=True);a.add_argument('--payload',required=True);a.add_argument('--output',required=True);a.add_argument('--store',required=True);a.add_argument('--cell',required=True);a.add_argument('--version',type=int,required=True)
    a=p.parse_args()
    if a.command=='prepare':prepare(DevV2Store(a.store),a.cell,a.version,a.namespace,a.output,a.catalog_root,source=json.loads(Path(a.source_prerequisites).read_bytes()) if a.source_prerequisites else None)
    else:submit(a.base_url,a.operation,json.loads(Path(a.payload).read_bytes()),a.output,ledger=a.store,cell=a.cell,version=a.version)


if __name__=='__main__':main()
