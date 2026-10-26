"""Bounded C17 supplemental authorization, without rewriting frozen manifests.

A plan cell is a logical arm slot. Replacement versions have independent model,
execution, grant and import identities; both slots always advance together.
"""
import json
from pathlib import Path
from .core.dev_store import DevConflict
from .dev_ledger import experiment_goal,encoded
from .dev_v2_loop import checked,immutable

MAX_REPLACEMENTS = 2


def attempt_version(current):
    return current.get('pair_version',1)


def attempt_id(current):
    key=current['arm_id'];version=attempt_version(current)
    return key if version==1 else f'{key}-v{version}'


def authorize_replacement(store,current):
    """Append one idempotent two-arm allowance; retain every spent/reserved unit."""
    discarded=current['discarded_pairs'][-1]
    pair=current['plan']['pairs'][current['pair_index']]
    version=attempt_version(current)
    if version!=discarded['version']+1 or version not in (2,3):raise DevConflict('invalid replacement version')
    keys=[pair[a+'_cell'] for a in ('control','treatment')]
    with store.tx() as db:
        cells=[store.get(db,'cells',key) for key in keys]
        e=store.get(db,'experiments',cells[0]['experiment_id'])
        m=json.loads(checked(e['manifest']).read_bytes())
        if (not m.get('validation_experiment') or 'cross_seed' in m['validation_origin']
                or any(c['experiment_id']!=e['experiment_id'] for c in cells)
                or current['origin']!=m['validation_origin']['source']):raise DevConflict('replacement requires original single-scene validation experiment')
        expected={r['key'] for r in m['cells'] if r['validation_pair']==current['pair_index']}
        if set(keys)!=expected:raise DevConflict('replacement differs from frozen pair')
        if discarded['arm_id']!=pair[discarded['arm']+'_cell']:raise DevConflict('discarded arm differs from pair')
        row=db.execute('SELECT payload FROM v2_versions WHERE cell=? AND version=?',(discarded['arm_id'],discarded['version'])).fetchone()
        invalid=json.loads(row[0]) if row else {}
        if invalid.get('status')!='invalid_generation' or invalid.get('valid') is not False or invalid.get('validation_error')!=discarded['reason']:raise DevConflict('sealed invalid generation required for replacement')
        invalid_cell=cells[0] if keys[0]==discarded['arm_id'] else cells[1]
        if json.loads((Path(invalid_cell['artifact_dir'])/f"v{discarded['version']}"/'version.json').read_bytes())!=invalid:raise DevConflict('invalid generation artifact differs from ledger')
        events=e.setdefault('validation_replacements',[])
        entry=dict(pair_id=pair['pair_id'],version=version,discarded=discarded,manifest=e['manifest'])
        prior=next((r for r in events if r['pair_id']==pair['pair_id'] and r['version']==version),None)
        if prior:
            if prior!=entry:raise DevConflict('replacement authorization conflict')
            return
        if len(events)>=MAX_REPLACEMENTS:raise DevConflict('replacement limit reached')
        if e.get('cancelled') or e.get('owner'):raise DevConflict('replacement experiment stopped or owned')
        if db.execute("SELECT 1 FROM v2_jobs WHERE state IN ('reserved','claimed','needs_review') LIMIT 1").fetchone():raise DevConflict('unresolved validation work; no replacement budget')
        base=m['cell_budget'];gid=experiment_goal(e);goal=store.get(db,'goals',gid)
        if goal.get('real_finished_at'):raise DevConflict('validation goal already closed')
        if goal['limits']!={k:(10+2*len(events))*v for k,v in base.items()}:raise DevConflict('replacement goal allocation drift')
        allowance=2*(base['model_seconds']+base['execution_seconds'])
        if goal['wall_limit']!=m['wall_limit']+len(events)*allowance:raise DevConflict('replacement wall allocation drift')
        for c in cells:
            used_versions=sum(r['pair_id']==pair['pair_id'] for r in events)
            if c['budget']!={k:(1+used_versions)*v for k,v in base.items()}:raise DevConflict('replacement cell allocation drift')
            last=db.execute('SELECT COALESCE(MAX(version),0) FROM v2_versions WHERE cell=?',(c['key'],)).fetchone()[0]
            if last==version-2:
                # Control can be invalid before treatment is generated. Preserve
                # a non-executed tombstone so the ledger's versions stay consecutive.
                if db.execute('SELECT 1 FROM v2_jobs WHERE cell=? AND version=?',(c['key'],version-1)).fetchone():raise DevConflict('unrecorded counterpart work')
                skipped=dict(version=version-1,valid=False,status='discarded_pair',validation_error='paired_arm_invalid_generation',
                    source=current['origin'],manifest=e['manifest'],config_sha256=current['config']['sha256'],formal_eligible=False)
                immutable(Path(c['artifact_dir'])/f'v{version-1}'/'version.json',skipped)
                db.execute('INSERT INTO v2_versions VALUES (?,?,?)',(c['key'],version-1,encoded(skipped)))
            elif last!=version-1:raise DevConflict('replacement version cursor differs')
            c['budget']={k:v+base[k] for k,v in c['budget'].items()}
            store.put(db,'cells',c['key'],c)
        goal['limits']={k:v+2*base[k] for k,v in goal['limits'].items()}
        goal['wall_limit']+=allowance
        events.append(entry)
        store.put(db,'goals',gid,goal);store.put(db,'experiments',e['experiment_id'],e)
        store.event(db,'validation_replacement_authorized',dict(experiment_id=e['experiment_id'],**entry))


def legacy_invalid_available(current):
    """Read-only eligibility for --retry-current, including while switch is Off."""
    import sqlite3
    from pathlib import Path
    from .memory_validation_generation import parse_generation
    if not current or not current.get('active') or current.get('kind') in {'origin','cross_seed'}:return False
    key=current.get('arm_id');location=current.get('cells',{}).get(key)
    if not location:return False
    with sqlite3.connect(Path(location['ledger']).resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT payload FROM v2_cells WHERE id=?',(key,)).fetchone()
        if not row or json.loads(row[0]).get('state')!='needs_review':return False
        if db.execute("SELECT 1 FROM v2_jobs WHERE state IN ('reserved','claimed','needs_review') LIMIT 1").fetchone():return False
        rows=db.execute('SELECT kind,state,payload FROM v2_jobs WHERE cell=? AND version=?',(key,attempt_version(current))).fetchall()
    if any(kind=='execute' for kind,_,_ in rows):return False
    model=next((json.loads(p) for kind,state,p in rows if kind=='model' and state=='settled'),None)
    return bool(model and parse_generation(json.loads(checked(model['receipt']['response']).read_bytes())['content'])[2])
