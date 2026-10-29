"""Read-only exported agent origins; service repeats the shared checks."""
import json
from pathlib import Path

from attention_memory_service.agent_origin import verify_source
from attention_memory_service.core.store import StateConflictError
from .core.dev_store import DevConflict
from .dev_v2_loop import checked
from .dev_loop import sha


def references(cell, number):
    root = Path(cell['artifact_dir'])
    paths = dict(agent_help_evidence=root/f'v{number}/agent-help-evidence.json', help=root/f'v{number}/help.json')
    for label, n in (('origin', number - 1), ('help', number)):
        paths[label + '_source'] = root/f'v{n}/policy.py'
        paths[label + '_public_feedback'] = root/f'v{n}/public-feedback.json'
    return {name: dict(uri=str(path), sha256=sha(path)) for name, path in paths.items()}


def verify(store, cell, version, manifest, config):
    refs = references(cell, version['version'])
    def read(ref, raw=False):
        data = checked(ref).read_bytes()
        return data if raw else json.loads(data)
    try:
        with store.tx() as db:
            result = verify_source(refs, read, db, cell, version, manifest, config)
    except StateConflictError as exc:
        raise DevConflict(str(exc)) from exc
    return dict(result, agent_origin=refs)


def register(provision, found, catalogs, execution, source):
    from .memory_source_catalogs import covers, registration_order
    from .memory_validation_worker import contract_catalog
    key=found['key'];client=provision.client;ctx=execution['context']
    contract=found['config']['scene_contract']
    if not any(c['role'] in ('source','contract') and covers(c,contract) for c in catalogs.values()):
        catalogs[key+'-contract']=contract_catalog(ctx)
    for cid,catalog in registration_order(catalogs).items():client.register_dev_v2_catalog(catalog_id=cid,catalog=catalog)
    def name(cid):
        catalog=catalogs[cid]
        matches=[]
        for path,digest in catalog['files'].items():
            if path.endswith('/descriptor.json') and json.loads((Path(catalog['root'])/path).read_bytes())==(execution if cid==key+'-executor' else source):matches.append(path)
        if len(matches)!=1:raise DevConflict('agent origin descriptor missing or ambiguous')
        return matches[0]
    client.import_dev_v2_execution(import_id=key,catalog_id=key+'-executor',descriptor=name(key+'-executor'))
    imported=client.import_dev_v2_source(import_id=key+'-source',catalog_id=key+'-source',descriptor=name(key+'-source'))
    note=client.get_memory(found['note']['memory_id'])
    if imported['guidance']!=note.guidance or imported['source_policy_sha256']!=found['version']['source']['sha256']:
        raise DevConflict('agent source differs from provisional origin')
    client.create_dev_v2_candidate(memory_id=key+'-candidate',source_import_id=key+'-source',
        created_at=note.created_at,from_provisional=found['note']['memory_id'])
    return dict(execution_import_id=key,source_import_id=key+'-source',memory_id=key+'-candidate')
