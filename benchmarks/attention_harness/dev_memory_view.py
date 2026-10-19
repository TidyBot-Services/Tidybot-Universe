"""Read-only Dev v2 Memory evidence projection for daily and main bridge views."""
import json
import sqlite3
from pathlib import Path

from .core.dev_store import DevConflict
from .dev_loop import sha


def _evidence(ref):
    if not isinstance(ref, dict) or not ref.get('uri') or not ref.get('sha256'):
        raise DevConflict('Memory display evidence reference missing')
    if sha(ref['uri']) != ref['sha256']:
        raise DevConflict('Memory display evidence digest mismatch')
    return json.loads(Path(ref['uri']).read_bytes())


def projection(store, cell):
    """Never asks the Service for private candidate details or changes the ledger."""
    with sqlite3.connect('file:'+str(store.path.resolve())+'?mode=ro',uri=True) as db:
        events=[dict(seq=seq,kind=kind,detail=json.loads(payload))
                for seq,kind,payload in db.execute('SELECT seq,kind,payload FROM v2_events ORDER BY seq')
                if kind in {'planner_memory_offline_queued','planner_memory_authority_receipt',
                            'planner_memory_retrieved','planner_memory_generation','memory_validation_dispatch'}
                and json.loads(payload).get('cell')==cell['key']]
    queues=[];retrievals=[];generations=[];validations=[];receipts=[]
    for event in events:
        kind=event['kind'];value=event['detail']
        if kind=='planner_memory_offline_queued':
            queued=_evidence(value['queue'])
            if queued.get('cell')!=cell['key'] or queued.get('version')!=value['version'] or queued.get('kind')!=value['kind']:
                raise DevConflict('Memory queue display binding mismatch')
            queues.append(dict(version=value['version'],kind=value['kind'],status=queued['status'],
                               source='Offline import proposal' if value['kind']=='source' else 'Offline result queue',
                               authority_candidate=queued['authority_candidate'],sha256=value['queue']['sha256']))
        elif kind=='planner_memory_retrieved':
            packet=_evidence(value['packet'])
            entries=[]
            for entry in packet.get('entries',[]):
                context=entry.get('context',{})
                trace=entry.get('source_trace_id','')
                seeded=entry.get('seeded_for_tests') if type(entry.get('seeded_for_tests')) is bool else trace.startswith(('seeded-for-tests:','test-seed:'))
                excluded=entry.get('excluded_from_results') if type(entry.get('excluded_from_results')) is bool else seeded
                entries.append(dict(memory_id=entry['memory_id'],version=entry['version'],grant_id=entry['grant_id'],
                                    trusted=True,status=entry.get('status','trusted'),source='Seeded for tests' if seeded else 'Dev v2 candidate' if trace.startswith('dev-v2-source:') else 'Source unverified',
                                    seeded_for_tests=seeded,excluded_from_results=excluded,
                                    explicit_flags=type(entry.get('seeded_for_tests')) is bool or type(entry.get('excluded_from_results')) is bool,
                                    authority=entry.get('authority'),
                                    applicability={k:context.get(k) for k in ('seed','scene_id','object_set_id','camera_config_id','task_variant_id','camera_names','task_prompt','scene_contract_ref') if k in context}))
            retrievals.append(dict(version=value['version'],authority=value['authority'],namespace=value['namespace'],
                                   packet_sha256=value['packet']['sha256'],entries=entries))
        elif kind=='planner_memory_generation':
            generations.append(dict(version=value['version'],memory_packet_sha256=value['memory_packet']['sha256'],
                                    model_input_sha256=value['model_input']['sha256'],result_source_sha256=value['result_source']['sha256'],
                                    seeded_for_tests=value.get('seeded_for_tests') is True,
                                    excluded_from_results=value.get('excluded_from_results') is True))
        elif kind=='memory_validation_dispatch':
            frozen=_evidence(value['validation_input'])
            validations.append(dict(version=value['version'],memory_ids=frozen.get('memory_ids',[]),
                                    exposure=frozen.get('exposure'),grant_id=value['validation_grant_id'],
                                    status='dispatched'))
        else:
            receipt=_evidence(value['receipt'])
            response=receipt.get('response',{})
            if not isinstance(response,dict):response={}
            record=response.get('memory') if isinstance(response.get('memory'),dict) else response
            seeded=(response.get('seeded_for_tests') is True or response.get('source')=='test_seed'
                    or record.get('seeded_for_tests') is True or record.get('source')=='test_seed')
            receipts.append(dict(version=value['version'],operation=value['operation'],
                                 status='receipt_recorded',authority_status=record.get('status'),
                                 source='Seeded for tests' if seeded else 'Dev v2 candidate' if value['operation']=='candidate' else 'Offline result',
                                 memory_id=record.get('memory_id'),memory_version=record.get('version'),
                                 seeded_for_tests=seeded))
    for retrieval in retrievals:
        for entry in retrieval['entries']:
            provenance=next((r for r in reversed(receipts) if r['memory_id']==entry['memory_id']
                             and r['memory_version'] in (None,entry['version'])
                             and (r['seeded_for_tests'] or r['operation']=='candidate')),None)
            if provenance and not entry['explicit_flags']:
                entry['source']=provenance['source']
                entry['seeded_for_tests']=provenance['seeded_for_tests']
                entry['excluded_from_results']=provenance['seeded_for_tests']
    for queue in queues:
        if any(r['version']==queue['version'] and r['operation']==queue['kind'] for r in receipts):
            queue['status']='offline_receipt_recorded'
    return dict(queues=queues,validations=validations,retrievals=retrievals,
                generations=generations,receipts=receipts,
                empty=not any((queues,validations,retrievals,generations,receipts)))
