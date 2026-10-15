"""Receipt checks and terminal-only reconciliation. Never dispatches work."""
import json
import math
import time
from pathlib import Path

from .core.dev_store import DevConflict
from .dev_ledger import encoded, cost, GOAL, experiment_goal
from .dev_loop import sha, owner_alive, immutable

EXECUTION_RESOURCES = ('executions', 'sdk_calls', 'control_steps', 'execution_seconds')


def read(ref):
    if not isinstance(ref, dict) or sha(ref['uri']) != ref['sha256']:
        raise DevConflict('recovery evidence digest mismatch')
    return json.loads(Path(ref['uri']).read_bytes())


def dead(identity, *, required=False):
    if identity is None and not required:
        return
    if (not isinstance(identity, dict) or set(identity) != {'pid', 'start_ticks', 'boot_id'}
        or type(identity['pid']) is not int or identity['pid'] <= 0
        or not all(isinstance(identity[k], str) and identity[k] for k in ('start_ticks', 'boot_id'))):
        raise DevConflict('unknown owner identity')
    if owner_alive(identity):
        raise DevConflict('live owner')


def verify_execution_history(store, db, gid, jobs):
    """Only raw, fully settled ordinary executions support used executions.

    Checks artifact contents as well as SHA, original claims/settlements, and
    cell/goal totals. A settled flag or aggregate counter alone is insufficient.
    """
    from .dev_planner import binding, digest
    totals = dict.fromkeys(EXECUTION_RESOURCES, 0)
    per_cell = {}
    for state, j in jobs:
        if j['kind'] != 'execute':
            continue
        if state == 'released' and not j.get('claimed_at') and not j.get('actual'):
            continue
        if state != 'settled':
            raise DevConflict('unresolved historical execution')
        cost(j['actual'])
        if (set(j['actual']) != set(EXECUTION_RESOURCES) or j['actual']['executions'] != 1
            or any(j['actual'][k] > j['reservation'][k] for k in EXECUTION_RESOURCES)
            or not j['reserved_at'] <= j['claimed_at'] <= j['settled_at']):
            raise DevConflict('execution accounting mismatch')
        columns = db.execute('SELECT cell,version,kind FROM v2_jobs WHERE id=?', (j['job_id'],)).fetchone()
        if columns != (j['cell'], j['version'], 'execute') or j.get('goal_id') != gid:
            raise DevConflict('execution ledger identity mismatch')
        for kind in ('claimed', 'settled'):
            events = [json.loads(p) for p, in db.execute('SELECT payload FROM v2_events WHERE kind=?', (kind,))
                      if json.loads(p).get('job_id') == j['job_id']]
            if len(events) != 1 or any(events[0].get(k) != j.get(k) for k in
                (('cell', 'version', 'reservation', 'claimed_at') if kind == 'claimed' else
                 ('cell', 'version', 'reservation', 'actual', 'receipt', 'settled_at'))):
                raise DevConflict('execution claim/settlement event mismatch')
        c = store.get(db, 'cells', j['cell'])
        e = store.get(db, 'experiments', c['experiment_id'])
        m = read(e['manifest'])
        if m.get('goal_id', GOAL) != gid or m['experiment_id'] != c['experiment_id']:
            raise DevConflict('historical manifest identity mismatch')
        cfg = m['configs'][f"{c['task_id']}:{c['seed']}"]
        cfgref = {'uri':str(Path(c['artifact_dir'])/'config.json')}
        # Immutable JSON formatting is independent of encoded(); compare contents.
        if json.loads(Path(cfgref['uri']).read_bytes()) != cfg:
            raise DevConflict('historical config drift')
        v = json.loads(db.execute('SELECT payload FROM v2_versions WHERE cell=? AND version=?', (j['cell'],j['version'])).fetchone()[0])
        source = Path(v['source']['uri']).read_text()
        if not v['valid'] or sha(v['source']['uri']) != v['source']['sha256']:
            raise DevConflict('historical source drift')
        receipt = j['receipt']; bundle = read(receipt['bundle'])
        expected = dict(run_id='run:'+j['cell'], attempt_id=f"attempt:{j['cell']}:{j['version']-1}",
                        task_id=c['task_id'], seed=c['seed'], policy_sha256=v['source']['sha256'],
                        config_sha256=sha(cfgref['uri']), suite='robocasa365')
        if (any(bundle.get(k) != v for k,v in expected.items()) or bundle['dev_version'] != j['version']
            or bundle['policy_id'] != c['policy_id'] or read(bundle['config_lock']) != cfg
            or bundle.get('outcome_unknown') is not False or bundle.get('infrastructure_failure') is not False
            or bundle['status'] not in {'succeeded','failed','success','completed','timeout','cancelled'}
            or receipt['execution_status'] != bundle['status']):
            raise DevConflict('historical execution bundle mismatch/unknown')
        artifacts = {k:read(ref) for k,ref in bundle['artifacts'].items()}
        sandbox = artifacts['sandbox_receipt']
        if (sandbox.get('outcome_unknown') or any(sandbox.get(k)!=expected[k] for k in ('run_id','attempt_id'))
            or sandbox['sdk_call_budget']['limit']!=cfg['sdk_call_limit']
            or any(bundle.get('sandbox',{}).get(k) is not True for k in
                ('process_isolated','sdk_rpc_only','deadline_enforced','action_cancellation_verified'))):
            raise DevConflict('historical sandbox outcome unknown/mismatch')
        expected_actual = dict(executions=1, sdk_calls=sandbox['sdk_call_budget']['dispatched'],
                               control_steps=sandbox['control_steps'], execution_seconds=sandbox['elapsed_seconds'])
        if j['actual'] != expected_actual:
            raise DevConflict('historical receipt/actual mismatch')
        for name in ('trace', 'native_result'):
            if receipt[name] != bundle['artifacts'][name]:
                raise DevConflict('historical artifact reference mismatch')
            fields = expected if name == 'trace' else {k:expected[k] for k in ('run_id','attempt_id')}
            if any(artifacts[name].get(k) != value for k,value in fields.items()):
                raise DevConflict('historical trace/native identity mismatch')
        read(receipt['public_feedback'])
        native = artifacts['native_result']
        if (native.get('evaluated') is not True or type(native.get('native_success')) is not bool
            or bundle.get('native_evaluator')!=native or bundle.get('native_success')!=native['native_success']):
            raise DevConflict('historical native outcome unknown')
        scope = binding(j['cell'], j['version'], source, cfg)
        if scope:
            rid = 'planner-'+digest(dict(cell=j['cell'],version=j['version'],kind='approval',scope=scope))
            found = db.execute('SELECT payload FROM v2_planner_requests WHERE id=?', (rid,)).fetchone()
            approval = json.loads(found[0]) if found else {}
            if (approval.get('state') != 'consumed' or approval.get('consumed_by') != j['job_id']
                or approval.get('binding') != scope or not (approval.get('response') or {}).get('approved')):
                raise DevConflict('historical approval consumption mismatch')
        cell_totals = per_cell.setdefault(j['cell'], dict.fromkeys(EXECUTION_RESOURCES, 0))
        for k in totals:
            totals[k] += j['actual'][k]; cell_totals[k] += j['actual'][k]
    g = store.get(db, 'goals', gid)
    if any(g['used'][k] != totals[k] for k in totals):
        raise DevConflict('goal execution recompute mismatch; raw settled jobs required')
    for key,p in db.execute('SELECT id,payload FROM v2_cells'):
        c = json.loads(p)
        if store.cell_goal(db,c) == gid and any(c['used'][k] != per_cell.get(key,{}).get(k,0) for k in totals):
            raise DevConflict('cell execution recompute mismatch')


def reconcile_waits(store, exp_id):
    """Terminalize verifiable dead-owner waits, even when dispatch is forbidden.

    No source lock is reopened. Original request creation and claim events are
    required, so changed source can invalidate permission without losing costs.
    Ownership check, terminal transition and settlement share one transaction.
    """
    from .dev_planner import PlannerControl, digest
    result = []
    with store.tx() as db:
        e = store.get(db, 'experiments', exp_id)
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_planner_requests'").fetchone():
            return result
        jobs = store.goal_jobs(db, experiment_goal(e))
        for state,j in jobs:
            c = store.get(db,'cells',j['cell'])
            if c['experiment_id'] != exp_id or j['kind'] != 'human' or state != 'claimed':
                continue
            try:
                # Live work owns its own finally/settlement. Do not race it.
                dead(e.get('owner')); dead(c.get('owner')); dead(j.get('owner'),required=True)
                for path in Path(e['artifact_dir']).glob('scheduler-process-*.json'):
                    dead(json.loads(path.read_bytes()),required=True)
            except (OSError,ValueError,KeyError,TypeError,DevConflict) as exc:
                if str(exc) != 'live owner':
                    j['reason']='human wait reconciliation: '+str(exc)
                    db.execute("UPDATE v2_jobs SET state='needs_review',payload=? WHERE id=?",(encoded(j),j['job_id']))
                    c.update(state='needs_review',reason=j['reason']);store.put(db,'cells',j['cell'],c)
                    store.event(db,'planner_orphan_wait_needs_review',dict(job_id=j['job_id'],reason=j['reason']))
                result.append(dict(job_id=j['job_id'],status='held',reason=str(exc)))
                continue
            try:
                columns=db.execute('SELECT cell,version,kind FROM v2_jobs WHERE id=?',(j['job_id'],)).fetchone()
                if (columns!=(j['cell'],j['version'],'human') or j.get('goal_id')!=experiment_goal(e)
                    or j['job_id']!=f"{j['cell']}:{j['version']}:human"):
                    raise DevConflict('original human job/goal identity mismatch')
                if j.get('actual') or j.get('receipt') or set(j['reservation']) != {'help_wait_seconds'}:
                    raise DevConflict('unknown human accounting/reservation')
                seconds = j['reservation']['help_wait_seconds']; cost(j['reservation'])
                if not 0 < seconds <= 600:
                    raise DevConflict('unknown original wait reservation')
                events = [json.loads(p) for p, in db.execute("SELECT payload FROM v2_events WHERE kind='planner_request_created'")
                          if json.loads(p).get('cell') == j['cell'] and json.loads(p).get('version') == j['version']
                          and json.loads(p).get('request_type') == 'approval']
                if len(events) != 1:
                    raise DevConflict('original approval creation missing/ambiguous')
                original = events[0]; rid = original['request_id']
                found = db.execute('SELECT cell,payload FROM v2_planner_requests WHERE id=?',(rid,)).fetchone()
                if not found or found[0] != j['cell']:
                    raise DevConflict('original approval missing/identity mismatch')
                r = json.loads(found[1]); scope = r['binding']
                if (any(r.get(k) != original.get(k) for k in ('request_id','cell','version','request_type','binding','created_at','deadline_epoch'))
                    or rid != 'planner-'+digest(dict(cell=j['cell'],version=j['version'],kind='approval',scope=scope))
                    or scope['cell'] != j['cell'] or scope['run_id'] != 'run:'+j['cell'] or scope['code_version'] != j['version']
                    or r.get('consumed_by') or r['state'] not in {'waiting','approved','rejected','timed_out','cancelled','invalidated'}):
                    raise DevConflict('original approval binding/state mismatch')
                claims = [json.loads(p) for p, in db.execute("SELECT payload FROM v2_events WHERE kind='claimed'") if json.loads(p).get('job_id') == j['job_id']]
                if len(claims) != 1 or any(claims[0].get(k) != j.get(k) for k in ('cell','version','kind','goal_id','reservation','reserved_at','claimed_at')):
                    raise DevConflict('original human claim missing/mismatch')
                dead(claims[0].get('owner'),required=True)
                previous = claims[0]['owner']
                for transfer in j.get('owner_transfers',[]):
                    if transfer['previous_owner'] != previous or transfer['request_id'] != rid:
                        raise DevConflict('human owner transfer mismatch')
                    previous = transfer['owner']
                if previous != j['owner']:
                    raise DevConflict('human owner identity mismatch')
                now = time.time()
                times = [r['created_at'],r['deadline_epoch'],j['claimed_at'],j['reserved_at']]
                if (any(type(t) not in (int,float) or not math.isfinite(t) for t in times)
                    or not 0 < r['deadline_epoch']-r['created_at'] <= seconds
                    or not j['reserved_at'] <= j['claimed_at'] or not r['created_at'] <= j['claimed_at'] <= r['deadline_epoch']
                    or now < j['claimed_at']):
                    raise DevConflict('original wait timing mismatch')
                for field in ('responded_at','ended_at'):
                    t = r.get(field)
                    if t is not None and (type(t) not in (int,float) or not math.isfinite(t) or not r['created_at'] <= t <= now):
                        raise DevConflict('original response/end timing mismatch')
                if r.get('responded_at') is not None:
                    if not r.get('response') or r['responded_at'] > r['deadline_epoch']:
                        raise DevConflict('original response timing/identity missing')
                    decisions = [json.loads(p) for p, in db.execute("SELECT payload FROM v2_events WHERE kind='planner_human_decision'") if json.loads(p).get('request_id') == rid]
                    if len(decisions) != 1 or any(decisions[0].get(k) != r.get(k) for k in ('response','responded_at','binding')):
                        raise DevConflict('original human decision mismatch')
                elif r.get('response') or r['state'] in {'approved','rejected'}:
                    raise DevConflict('original response timestamp missing')
                g = store._goal(db,experiment_goal(e))
                if g['real_started_at'] is None or not g['real_started_at'] <= r['created_at']:
                    raise DevConflict('original goal clock missing/mismatch')
                goal_wait=sum(job.get('actual',{}).get('help_wait_seconds',0) for state,job in jobs if state=='settled')
                cell_wait=sum(job.get('actual',{}).get('help_wait_seconds',0) for state,job in jobs if state=='settled' and job['cell']==j['cell'])
                if (not math.isclose(goal_wait,g['used']['help_wait_seconds'],rel_tol=0,abs_tol=1e-9)
                    or not math.isclose(cell_wait,c['used']['help_wait_seconds'],rel_tol=0,abs_tol=1e-9)):
                    raise DevConflict('original wait usage recompute mismatch')
                # Never clean a wait whose paired action is unresolved/consumed.
                if any(job['kind']=='execute' and job['cell']==j['cell'] and job['version']==j['version'] for _,job in jobs):
                    raise DevConflict('paired native action exists; independent review required')
                reason = None
                if e.get('cancelled'): reason = 'cancelled'
                elif now >= r['deadline_epoch']: reason = 'timed_out'
                elif g.get('real_finished_at') or g['wall_remaining'] <= 0 or any(g['remaining'][k] < v for k,v in {'executions':1,'sdk_calls':scope['scope']['sdk_calls'],'control_steps':scope['scope']['control_steps'],'execution_seconds':45}.items()): reason = 'authorization_exhausted'
                else:
                    # Source/lock validity gates dispatch only, never cleanup.
                    from .dev_experiment import verify_manifest
                    try:
                        verify_manifest(e)
                        v = db.execute('SELECT payload FROM v2_versions WHERE cell=? AND version=?',(j['cell'],j['version'])).fetchone()
                        latest = db.execute('SELECT MAX(version) FROM v2_versions WHERE cell=?',(j['cell'],)).fetchone()[0]
                        cfg = Path(c['artifact_dir'])/'config.json'
                        if (not v or latest != j['version'] or not json.loads(v[0])['valid']
                            or sha(json.loads(v[0])['source']['uri']) != scope['code_sha256']
                            or digest(json.loads(cfg.read_bytes())) != scope['config_sha256']): reason = 'invalidated'
                    except (OSError,ValueError,KeyError,TypeError,DevConflict): reason = 'invalidated'
                terminal = r['state'] in {'rejected','timed_out','cancelled','invalidated'}
                if not terminal and not reason:
                    continue
                if terminal and r.get('ended_at') is None and r.get('responded_at') is None:
                    raise DevConflict('original terminal wait end missing')
                receipt_path=Path(e['artifact_dir'])/'cells'/j['cell']/f"v{j['version']}"/'terminal-reconciliation'/'human-decision-receipt.json'
                if not terminal:
                    ended=min(now,e.get('cancelled_at',now)) if reason=='cancelled' else now
                    r.update(state='invalidated' if reason=='authorization_exhausted' else reason,ended_at=ended,terminal_reason=reason)
                if receipt_path.exists():
                    # Files can survive a crash before the SQLite commit. Reuse
                    # the first verifiable terminal instant, never overwrite it.
                    prior=json.loads(receipt_path.read_bytes())
                    fields=('request_id','cell','version','binding','created_at','deadline_epoch','response','responded_at','state')
                    if (any(prior.get(k)!=r.get(k) for k in fields)
                        or not r['created_at']<=prior['ended_at']<=now
                        or prior.get('terminal_reason')!=(r.get('terminal_reason') or r['state'])):
                        raise DevConflict('terminal reconciliation receipt drift')
                    r['ended_at']=prior['ended_at']
                # Deadline caps delayed orphan discovery; an earlier reply wins.
                end = min(r['deadline_epoch'],r.get('responded_at') or r.get('ended_at') or now)
                elapsed = min(seconds,end-r['created_at'])
                if elapsed < 0: raise DevConflict('negative original wait duration')
                receipt = dict(r,wait_ended_at=end,terminal_reason=r.get('terminal_reason') or r['state'])
                if receipt_path.exists():
                    if json.loads(receipt_path.read_bytes())!=receipt:
                        raise DevConflict('terminal reconciliation receipt content drift')
                    ref={'uri':str(receipt_path.resolve()),'sha256':sha(receipt_path)}
                else:
                    ref = immutable(receipt_path,receipt)
                PlannerControl.save(db,r)
                store.settle_in_transaction(db,j['job_id'],{'help_wait_seconds':elapsed},dict(planner_request_id=rid,state=r['state'],request=ref,terminal_reason=receipt['terminal_reason']))
                unresolved=any(job['job_id']!=j['job_id'] and state in {'claimed','needs_review'} for state,job in jobs)
                c = store.get(db,'cells',j['cell']); c.update(planner_wait=None,owner=None,state='needs_review' if unresolved else 'cancelled' if e.get('cancelled') else 'stopped',reason='motion approval '+receipt['terminal_reason']+('; unresolved work retained' if unresolved else ''))
                store.put(db,'cells',j['cell'],c)
                store.event(db,'planner_orphan_wait_settled',dict(job_id=j['job_id'],request_id=rid,request=ref,actual_seconds=elapsed,reason=receipt['terminal_reason']))
                e.update(owner=None,state='needs_review' if unresolved else 'cancelled' if e.get('cancelled') else 'stopped')
                store.put(db,'experiments',exp_id,e)
                result.append(dict(job_id=j['job_id'],status='settled',reason=receipt['terminal_reason']))
            except (OSError,ValueError,KeyError,TypeError,IndexError,DevConflict) as exc:
                j['reason']='human wait reconciliation: '+str(exc)
                db.execute("UPDATE v2_jobs SET state='needs_review',payload=? WHERE id=?",(encoded(j),j['job_id']))
                c.update(state='needs_review',reason=j['reason']);store.put(db,'cells',j['cell'],c)
                store.event(db,'planner_orphan_wait_needs_review',dict(job_id=j['job_id'],reason=j['reason']))
                result.append(dict(job_id=j['job_id'],status='needs_review',reason=j['reason']))
    return result
