"""Read-only, event-ordered request timeline for human and proxy validation."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from .core.store import AttentionStore


STATES=('pending','answered','timeout','fallback','cancelled','ignored','deferred')


def request_timeline(store: AttentionStore | Path, run_id: str) -> dict:
    path=store.path if isinstance(store,AttentionStore) else Path(store)
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
            raise ValueError('timeline run not found')
        requests=[json.loads(row[0]) for row in db.execute(
            "SELECT payload FROM requests WHERE json_extract(payload,'$.run_id')=? ORDER BY id",(run_id,))]
        rows={}
        for request in requests:
            response_row=db.execute('SELECT payload FROM responses WHERE id=?',(request.get('response_id'),)).fetchone()
            response=json.loads(response_row[0]) if response_row else None
            rows[request['request_id']]={'request_id':request['request_id'],
                'request_type':request['request_type'],'state':request['state'],
                'opened_at':request['created_at'],'deadline_at':request.get('deadline_at'),
                'times':{state:[] for state in STATES},'transitions':[],
                'reply':response,'response_latency_seconds':(response['created_at']-request['created_at'] if response else None),
                'disposition':None,'used_response':None,'execution_links':[],'candidate_memories':[]}
        if rows:
            events=db.execute('SELECT sequence,entity_id,event_type,payload FROM events WHERE entity_id IN ('+
                ','.join('?' for _ in rows)+') ORDER BY sequence',list(rows)).fetchall()
        else:events=[]
        for sequence,request_id,kind,raw in events:
            row=rows[request_id];payload=json.loads(raw)
            state={'request.created':'pending','request.timeout_observed':'timeout',
                   'request.ignored':'ignored','response.ignored':'ignored',
                   'request.deferred':'deferred'}.get(kind)
            if kind in {'request.answered','request.timeout','request.fallback','request.cancelled'}:
                state=kind.split('.')[1]
            if state:
                at=payload.get('transitioned_at')
                if kind=='request.created':
                    at=(payload.get('request') or payload).get('created_at')
                elif kind=='request.answered':
                    at=(payload.get('response') or row['reply'] or {}).get('created_at',at)
                elif kind=='request.timeout_observed':at=payload.get('timed_out_at')
                elif kind in {'request.ignored','response.ignored'}:at=payload.get('ignored_at')
                elif kind=='request.deferred':at=payload.get('deferred_at')
                row['transitions'].append({'state':state,'at':at,'sequence':sequence,'event_type':kind,
                    'time_source':'persisted_event' if at is not None else 'unrecorded'})
                if at is not None:row['times'][state].append(at)
            elif kind=='request.recovered':
                # Recovery commits cancellation and this measured time atomically.
                for transition in reversed(row['transitions']):
                    if transition['state']=='cancelled':
                        transition.update(at=payload['cancelled_at'],time_source='recovery_event')
                        row['times']['cancelled']=[payload['cancelled_at']]
                        break
            elif kind=='response.used':
                row['disposition']='accepted'
                row['used_response']=row['reply'] if row['reply'] and row['reply']['response_id']==payload['response_id'] else None
                row['use']=payload
            elif kind=='response.execution_linked':row['execution_links'].append(payload)
            elif kind=='memory.candidate_ingested':row['candidate_memories'].append(payload)
            if kind=='response.ignored':row['disposition']='ignored'
    return {'schema_version':'attentionbench.request-timeline.v1','run_id':run_id,
            'clock':'persisted Unix seconds; missing historical clocks remain null',
            'requests':list(rows.values())}


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store-path',type=Path,required=True)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    value=request_timeline(args.store_path,args.run_id)
    text=json.dumps(value,sort_keys=True,ensure_ascii=False,indent=2)+'\n'
    if args.output:
        with args.output.open('x') as output:output.write(text)
    else:print(text,end='')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
