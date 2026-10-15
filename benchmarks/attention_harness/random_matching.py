"""Pre-register random escalation counts from a completed, same-cell planner run."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from .core.policies import BudgetMatchedRandomEscalationPolicy
from .seed_guard import validate_seed


PLANNER = 'full_trace_aware_attention_planner'


def encoded(value) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)+'\n').encode()


def derive_random_config(source_run: Path, *, suite: str, task_id: str, seed: int,
                         random_seed: int | None = None) -> dict:
    """Read a terminal source and its persisted requests without opening a writer."""
    validate_seed(seed, allow_heldout=False)
    raw=source_run.read_bytes()
    source=json.loads(raw)
    identity={'suite':suite,'task_id':task_id,'seed':seed,'policy_id':PLANNER}
    if any(source.get(key)!=value for key,value in identity.items()):
        raise ValueError('source planner suite/task/seed mismatch')
    if Path(source.get('artifact_dir','')).resolve()!=source_run.parent.resolve():
        raise ValueError('source artifact directory mismatch')
    run_id='run:'+source_run.parent.name
    store_path=Path(source['store']).resolve()
    with sqlite3.connect(store_path.as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        row=db.execute('SELECT payload FROM runs WHERE id=?',(run_id,)).fetchone()
        run=json.loads(row[0]) if row else {}
        if any(run.get(key)!=value for key,value in identity.items()) or run.get('status') not in {'completed','failed','cancelled'}:
            raise ValueError('source planner run is not terminal or identity differs')
        rows=db.execute("SELECT payload FROM requests WHERE json_extract(payload,'$.run_id')=? ORDER BY id",(run_id,)).fetchall()
        requests=[json.loads(row[0]) for row in rows]
        if any(request['state'] in {'pending','fallback'} for request in requests):
            raise ValueError('source planner still has outstanding requests')
        ids=[request['request_id'] for request in source['requests']]
        if len(ids)!=len(set(ids)) or set(ids)!={request['request_id'] for request in requests}:
            raise ValueError('summary/persisted request identities differ')
        plan_row=db.execute('SELECT payload FROM events WHERE event_key=?',(f'task-plan:{run_id}',)).fetchone()
        if plan_row is None:
            raise ValueError('source planner attempt plan missing')
        plan=json.loads(plan_row[0])
    count=sum(request['request_type']!='interrupt' for request in requests)
    config={'target_request_count':count,'total_failure_slots':plan['max_attempts']-1,
            'seed':seed if random_seed is None else random_seed}
    BudgetMatchedRandomEscalationPolicy(**config)
    return {'schema_version':'attentionbench.random-count-match.v1',
            'source_run':str(source_run.resolve()),'source_run_sha256':hashlib.sha256(raw).hexdigest(),
            'source_store':str(store_path),'source_identity':{**identity,'run_id':run_id,'status':run['status']},
            'counted_request_ids':[request['request_id'] for request in requests if request['request_type']!='interrupt'],
            'excluded_interrupt_ids':[request['request_id'] for request in requests if request['request_type']=='interrupt'],
            'source_requests_sha256':hashlib.sha256(encoded(requests)).hexdigest(),
            'policy_config':config,'policy_config_sha256':hashlib.sha256(encoded(config)).hexdigest(),
            'policy_id':'budget_matched_random_escalation',
            'matching_scope':'requested count; realized count may be lower on early success or budget stop'}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run',type=Path,required=True)
    parser.add_argument('--suite',required=True)
    parser.add_argument('--task',required=True)
    parser.add_argument('--seed',type=int,required=True)
    parser.add_argument('--random-seed',type=int)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args(argv)
    result=derive_random_config(args.source_run,suite=args.suite,task_id=args.task,seed=args.seed,random_seed=args.random_seed)
    args.output_dir.mkdir(parents=True,exist_ok=False)
    config=args.output_dir/'policy_config.json'
    config.write_bytes(encoded(result['policy_config']))
    (args.output_dir/'match.json').write_bytes(encoded(result))
    (args.output_dir/'policy_config.sha256').write_text(result['policy_config_sha256']+'  policy_config.json\n')
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
