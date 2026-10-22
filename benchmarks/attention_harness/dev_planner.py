"""Opt-in Dev planner control plane. Same ledger, public inputs, no authority minting.

Human capability is held by the API process, never stored in a manifest or sent
into a policy/model. Offline preparation never executes generated source.
"""
import ast
import hashlib
import json
import math
import time
from pathlib import Path

from .core.dev_store import DevConflict, DevBudgetExhausted
from .dev_help import public_screen
from .dev_ledger import encoded
from .dev_loop import sha, immutable, process_identity, owner_alive

POLICY = 'full_trace_aware_attention_planner'
TERMINAL = {'rejected', 'timed_out', 'cancelled', 'invalidated', 'consumed'}
class PlannerActionBlocked(DevConflict):
    pass


DEFAULT = dict(schema='attentionbench.dev-planner.v1', human_wait_seconds=60,
               background_seconds=5, approval_rule='generated_motion_policy', memory=None)


def configuration(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError('bounded planner configuration required')
    p = {**DEFAULT, **value}
    if p['schema'] != DEFAULT['schema'] or p['approval_rule'] not in {'generated_motion_policy','none'}:
        raise ValueError('unsupported planner permission rule')
    for key, maximum in [('human_wait_seconds', 600), ('background_seconds', 5)]:
        minimum=0 if p['approval_rule']=='none' else 1
        if type(p[key]) is not int or not minimum <= p[key] <= maximum:
            raise ValueError('bounded positive planner duration required: ' + key)
    if p['approval_rule']=='none' and (p['human_wait_seconds'] or p['background_seconds']):
        raise ValueError('planner without approval cannot prepare or wait for approval')
    if p['memory'] is not None:
        memory_settings(p['memory'])
    return p


def memory_settings(m):
    """Validate a Memory authority binding (planner feature or shared authority)."""
    if not isinstance(m, dict) or set(m) not in ({'base_url', 'store_id', 'namespace'}, {'base_url', 'store_id', 'namespace', 'url_sha256'}):
        raise ValueError('Memory endpoint, authority and isolated namespace required')
    if not all(isinstance(v, str) and v.strip() for v in m.values()):
        raise ValueError('nonempty Memory configuration required')
    from urllib.parse import urlsplit
    url = urlsplit(m['base_url'])
    if (url.username or url.password or url.query or url.fragment or
            url.scheme not in {'http', 'https'} or not url.hostname or
            url.scheme == 'http' and url.hostname not in {'127.0.0.1', 'localhost'}):
        raise ValueError('Memory endpoint must be credential-free HTTPS or loopback')
    if 'url_sha256' in m and m['url_sha256'] != hashlib.sha256(m['base_url'].encode()).hexdigest():
        raise ValueError('Memory endpoint digest mismatch')
    return m


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def binding(cell, version, source, config):
    # Approval is for starting this exact bounded policy, including every
    # branch/argument encoded in its SHA. Editing source never needs approval.
    tree = ast.parse(source)
    from openarm365_dev.policy_validation import OPERATIONS
    # A callable may be assigned to a variable/container before invocation.
    # Inventory imported motion capabilities and ALL method references, rather
    # than assuming calls use the original group name. Source SHA binds every
    # branch/argument; this conservative inventory cannot authorize other code.
    imported={alias.name for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)
              and n.module=='robot_sdk' for alias in n.names}
    referenced={n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute)}
    operations=sorted(f'{group}.{method}' for group in imported-{'sensors'}
                      for method in OPERATIONS.get(group,()) if method in referenced)
    if not operations:
        return None
    return dict(run_id='run:' + cell, cell=cell, code_version=version,
                code_sha256=hashlib.sha256(source.encode()).hexdigest(), config_sha256=digest(config),
                action='execute_bounded_motion_policy', action_version=1,
                execution_target='robocasa365_sim', risk='bounded simulated robot motion under existing SDK safety limits',
                scope=dict(operations=operations, robot=config['robot'], task_id=config['task_id'],
                           seed=config['seed'], sdk_calls=config['sdk_call_limit'],
                           control_steps=config['horizon'], wall_seconds=config['attempt_wall_seconds']))


def advisor_scope(store,key,version,messages):
    c=store.cell(key);packet=json.loads(messages[-1]['content'])
    config_path=Path(c['artifact_dir'])/'config.json'
    return dict(cell=key,version=version,input_sha256=digest(messages),
                source_sha256=hashlib.sha256(packet['previous_complete_source'].encode()).hexdigest(),
                policy_id=c['policy_id'],config_sha256=sha(config_path))


class PlannerControl:
    def __init__(self, store):
        self.store = store
        with store.tx() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS v2_planner_requests '
                            '(id TEXT PRIMARY KEY,cell TEXT,payload TEXT);'
                            'CREATE TABLE IF NOT EXISTS v2_planner_work '
                            '(id TEXT PRIMARY KEY,cell TEXT,payload TEXT);')

    @staticmethod
    def save(db, row):
        db.execute('INSERT INTO v2_planner_requests VALUES (?,?,?) ON CONFLICT(id) '
                   'DO UPDATE SET payload=excluded.payload', (row['request_id'], row['cell'], encoded(row)))

    def request(self, cell, version, kind, scope, timeout):
        public_screen(scope)
        if kind not in {'approval', 'advisor'} or not 0 < timeout <= 600:
            raise ValueError('bounded request type and timeout required')
        rid = 'planner-' + digest(dict(cell=cell, version=version, kind=kind, scope=scope))
        with self.store.tx() as db:
            prior = db.execute('SELECT payload FROM v2_planner_requests WHERE id=?', (rid,)).fetchone()
            if prior:
                return self.refresh(db, json.loads(prior[0]))
            c = self.store.get(db, 'cells', cell)
            exp = self.store.get(db, 'experiments', c['experiment_id'])
            goal = self.store._goal(db, self.store.cell_goal(db, c))
            if exp.get('cancelled') or goal['wall_remaining'] <= 0:
                raise DevBudgetExhausted('request cannot start after stop/deadline')
            now = time.time()
            # Starting a wait starts the same goal clock, once. Restarts cannot
            # create a fresh window before any provider/native job has claimed.
            if goal['real_started_at'] is None:
                raw = self.store.get(db, 'goals', self.store.cell_goal(db, c))
                raw['real_started_at'] = now
                self.store.put(db, 'goals', self.store.cell_goal(db, c), raw)
            deadline = min(now + timeout, (goal['real_started_at'] or now) + goal['wall_limit'],
                           goal.get('authorized_until', float('inf')))
            row = dict(request_id=rid, cell=cell, version=version, request_type=kind,
                       binding=scope, state='waiting', created_at=now, deadline_epoch=deadline,
                       response=None, adopted=None, consumed_by=None)
            self.save(db, row)
            self.store.event(db, 'planner_request_created', row)
            return row

    def refresh(self, db, row):
        if row['state'] not in {'waiting', 'approved'}:
            return row
        c = self.store.get(db, 'cells', row['cell'])
        e = self.store.get(db, 'experiments', c['experiment_id'])
        reason = None
        if e.get('cancelled'):
            reason = 'cancelled'
        elif time.time() >= row['deadline_epoch']:
            reason = 'timed_out'
        elif row['request_type'] == 'approval':
            v = db.execute('SELECT payload FROM v2_versions WHERE cell=? AND version=?',
                           (row['cell'], row['version'])).fetchone()
            latest = db.execute('SELECT MAX(version) FROM v2_versions WHERE cell=?', (row['cell'],)).fetchone()[0]
            if not v or latest != row['version'] or json.loads(v[0])['source']['sha256'] != row['binding']['code_sha256']:
                reason = 'invalidated'
        elif row['request_type']=='advisor':
            scope=row['binding']
            versions=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_versions WHERE cell=? ORDER BY version',(row['cell'],))]
            config_path=Path(c['artifact_dir'])/'config.json'
            if (len(versions)!=row['version']-1 or c['policy_id']!=scope['policy_id'] or c.get('pending_policy')
                or not config_path.is_file() or sha(config_path)!=scope['config_sha256']
                or versions and (versions[-1]['source']['sha256']!=scope['source_sha256'] or sha(versions[-1]['source']['uri'])!=scope['source_sha256'])):
                reason='invalidated'
        if reason:
            row.update(state=reason, ended_at=time.time())
            self.save(db, row)
            self.store.event(db, 'planner_request_' + reason, row)
        return row

    def get(self, rid):
        with self.store.tx() as db:
            found = db.execute('SELECT payload FROM v2_planner_requests WHERE id=?', (rid,)).fetchone()
            if not found:
                raise DevConflict('unknown planner request')
            return self.refresh(db, json.loads(found[0]))

    def reply(self, rid, *, scope, approved, actor, response_id):
        if type(approved) is not bool or not all(isinstance(x, str) and x.strip() for x in [actor, response_id]):
            raise ValueError('explicit authenticated decision/identity required')
        with self.store.tx() as db:
            found = db.execute('SELECT payload FROM v2_planner_requests WHERE id=?', (rid,)).fetchone()
            if not found:
                raise DevConflict('unknown planner request')
            row = self.refresh(db, json.loads(found[0]))
            if row['request_type'] != 'approval' or encoded(scope) != encoded(row['binding']):
                raise DevConflict('approval request/version/permission binding mismatch')
            response = dict(approved=approved, actor=actor, response_id=response_id)
            if row.get('response'):
                if row['response'] != response:
                    raise DevConflict('conflicting or duplicate decision')
                return row  # Idempotent receipt; never grants an expired action.
            if row['state'] != 'waiting':
                raise DevConflict('late decision rejected: ' + row['state'])
            row.update(state='approved' if approved else 'rejected', response=response, responded_at=time.time())
            self.save(db, row)
            self.store.event(db, 'planner_human_decision', row)
            return row

    def finish_advisor(self, rid, *, response, current_scope):
        with self.store.tx() as db:
            row = self.refresh(db, json.loads(db.execute('SELECT payload FROM v2_planner_requests WHERE id=?', (rid,)).fetchone()[0]))
            if row['state']=='answered' and row['response']==response and encoded(row['binding'])==encoded(current_scope):return row
            valid = row['state'] == 'waiting' and encoded(row['binding']) == encoded(current_scope)
            row.update(state='answered' if valid else row['state'], response=response, accepted=valid, adopted=None if valid else False, responded_at=time.time())
            if encoded(row['binding']) != encoded(current_scope):
                row.update(state='invalidated', adopted=False)
            self.save(db, row)
            self.store.event(db, 'planner_advisor_response', row)
            return row

    def associate_generation(self, cell, version, model_input, source, hint_rejected=False):
        for ref in (model_input,source):
            if sha(ref['uri'])!=ref['sha256']:raise DevConflict('generation association evidence drift')
        packet=json.loads(Path(model_input['uri']).read_bytes())
        content=json.loads(packet['messages'][-1]['content'])['attention_control'].get('help',{}).get('content')
        with self.store.tx() as db:
            rows=db.execute('SELECT payload FROM v2_planner_requests WHERE cell=?',(cell,)).fetchall()
            for payload, in rows:
                row=json.loads(payload)
                if row['version']!=version or row['request_type']!='advisor' or row['state']!='answered':continue
                response=row.get('response')
                if not response or sha(response['uri'])!=response['sha256']:raise DevConflict('Advisor association response changed')
                expected=json.loads(Path(response['uri']).read_bytes())['content']
                adopted=bool(row.get('accepted') and not hint_rejected and content==expected)
                if row.get('adoption'):
                    if row['adoption']!={'model_input':model_input,'result_source':source}:raise DevConflict('guidance association already bound')
                    continue
                row.update(adopted=adopted,adoption={'model_input':model_input,'result_source':source},
                           adoption_claim='included_in_generation_input_only_no_causal_benefit_claim')
                self.save(db,row);self.store.event(db,'planner_guidance_associated',row)

    def snapshot(self, cell):
        with self.store.tx() as db:
            rows = [self.refresh(db, json.loads(p)) for p, in db.execute(
                'SELECT payload FROM v2_planner_requests WHERE cell=? ORDER BY rowid', (cell,)).fetchall()]
            work = [json.loads(p) for p, in db.execute('SELECT payload FROM v2_planner_work WHERE cell=? ORDER BY rowid', (cell,))]
            for w in work:
                if w['state']=='running' and not owner_alive(w.get('owner')):
                    w.update(state='interrupted',ended_at=min(time.time(),w['deadline_epoch']),actual_seconds=max(0,min(time.time(),w['deadline_epoch'])-w['started_at']))
                    db.execute('UPDATE v2_planner_work SET payload=? WHERE id=?',(encoded(w),w['work_id']))
                    self.store.event(db,'planner_background_recovered',w)
            events = [dict(at=at, kind=kind, detail=json.loads(p)) for at, kind, p in db.execute(
                "SELECT at,kind,payload FROM v2_events WHERE kind LIKE 'planner_memory_%' ORDER BY seq")
                      if json.loads(p).get('cell') == cell]
        return dict(requests=rows, work=work, memory=events)


def prepare_independent(store, key, version, kind, source, feedback, directory, request_id, seconds):
    """One finite preparation job. No provider, SDK, eval or response access."""
    from openarm365_dev.policy_validation import validate_generated_policy
    public_screen(source)
    public_screen(feedback)
    control = PlannerControl(store)
    wid = f'{request_id}:public-preparation'
    with store.tx() as db:
        old = db.execute('SELECT payload FROM v2_planner_work WHERE id=?', (wid,)).fetchone()
        if old:
            row = json.loads(old[0])
            # Once claimed this CPU work is never silently executed again.
            if row.get('result') and sha(row['result']['uri']) != row['result']['sha256']:
                raise DevConflict('background evidence digest changed')
            return row
        now = time.time()
        row = dict(work_id=wid, cell=key, version=version, request_id=request_id,
                   kind=kind, state='running', owner=process_identity(), started_at=now, deadline_epoch=now + seconds,
                   inputs=dict(source_sha256=hashlib.sha256(source.encode()).hexdigest(), public_feedback_sha256=digest(feedback)),
                   dependency='frozen public evidence only; pending response excluded', provider_calls=0, sdk_calls=0)
        db.execute('INSERT INTO v2_planner_work VALUES (?,?,?)', (wid, key, encoded(row)))
        store.event(db, 'planner_background_started', row)
    started = time.monotonic()
    try:
        events = feedback.get('action_trace', [])
        summary = dict(event_count=len(events), failed_operations=[
            {k: e[k] for k in ('source', 'operation', 'status', 'error') if k in e}
            for e in events if e.get('status') == 'failed' or e.get('error')])
        try:
            validate_generated_policy(source)
            diagnostic = 'Public SDK syntax and operation validation passed'
        except Exception:
            diagnostic = 'Source requires public SDK validation repair'
        # Standalone preparation is the checked prior code and a concrete public
        # action inventory. It is explicitly unaccepted and unexecuted.
        prepared = dict(schema='attentionbench.dev-independent-preparation.v1', request_id=request_id,
                        inputs=row['inputs'], public_trace_analysis=summary, source_check=diagnostic,
                        prepared_complete_source=source, acceptance='pending review; unexecuted',
                        response_dependency=False)
        public_screen(prepared)
        if time.monotonic() - started > seconds:
            raise TimeoutError('background preparation deadline')
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        ref = immutable(directory / (kind + '-independent-preparation.json'), prepared)
        row.update(state='completed', result=ref)
    except BaseException as exc:
        row.update(state='failed', error=type(exc).__name__)
        raise
    finally:
        row.update(ended_at=time.time(), actual_seconds=time.monotonic() - started)
        with store.tx() as db:
            db.execute('UPDATE v2_planner_work SET payload=? WHERE id=?', (encoded(row), wid))
            store.event(db, 'planner_background_finished', row)
    return row


def wait_for_approval(store, key, version, source, config, owner, directory, features, feedback, *, real_action=False):
    scope = binding(key, version, source, config)
    if scope is None:
        return None
    control = PlannerControl(store)
    seconds = features['human_wait_seconds']
    # Reserve before exposing a request. Approval itself is not a repair hint;
    # its measured waiting time is charged to the shared wait budget.
    job = store.reserve(key, version, 'human', {'help_wait_seconds': seconds})
    try:
        row = control.request(key, version, 'approval', scope, seconds)
        if not job.get('claimed_at') and not job.get('actual'):
            store.claim(job['job_id'], owner)
    except (DevConflict,DevBudgetExhausted):
        latest=next(j for j in store.cell(key)['jobs'] if j['job_id']==job['job_id'])
        if latest['state']=='reserved':store.release_unclaimed(job['job_id'])
        if 'row' in locals():
            row=control.get(row['request_id'])
            if row['state'] in {'cancelled','timed_out','invalidated'}:raise PlannerActionBlocked('motion approval '+row['state'])
        raise
    store.update_cell(key, planner_wait=row['request_id'])
    try:
        prepare_independent(store, key, version, 'human', source, feedback, directory,
                            row['request_id'], features['background_seconds'])
        row=control.get(row['request_id'])
        if real_action and row['state']=='waiting':
            eid=store.cell(key)['experiment_id']
            immutable(Path(directory)/'human-review-needed.json',dict(
                schema='attentionbench.human-review-needed.v1',verification_class='real_action_pending_external_human',
                ui_url=__import__('os').getenv('ATTENTION_PLANNER_UI_URL'),request_id=row['request_id'],run_id=scope['run_id'],
                code_sha256=scope['code_sha256'],readable_source=source,source={'uri':str(Path(directory)/'policy.py'),'sha256':scope['code_sha256']},action=scope['action'],binding=scope,deadline_epoch=row['deadline_epoch'],
                independent_work=control.snapshot(key)['work'],
                decision_api=f"{__import__('os').getenv('ATTENTION_PLANNER_APPROVAL_API_PREFIX','/api/dev')}/experiments/{eid}/approvals/{row['request_id']}/decision",
                required_headers=['X-Attention-Token','X-Attention-Human-Token'],
                decision_body={'binding':scope,'approved':'external human boolean decision required','response_id':'unique human reply ID'}))
        while True:
            row = control.get(row['request_id'])
            if row['state'] != 'waiting':
                break
            time.sleep(min(.1, max(0, row['deadline_epoch'] - time.time())))
        if row['state'] != 'approved':
            raise PlannerActionBlocked('motion approval ' + row['state'])
        return row['request_id']
    finally:
        latest = next(j for j in store.cell(key)['jobs'] if j['job_id'] == job['job_id'])
        if latest['state'] != 'settled':
            elapsed = max(0, min(seconds, (row.get('responded_at') or row.get('ended_at') or time.time()) - row['created_at']))
            receipt_path=Path(directory)/'human-decision-receipt.json'
            if receipt_path.exists():
                prior=json.loads(receipt_path.read_bytes())
                if prior!=row:
                    # A crash may leave an approved receipt before settlement.
                    # Expiry/cancellation still wins; keep that original byte
                    # evidence and append the now-terminal request separately.
                    if (prior.get('state')!='approved' or row['state'] not in {'timed_out','cancelled','invalidated'}
                        or any(encoded(prior.get(k))!=encoded(row.get(k)) for k in ('request_id','binding','created_at','deadline_epoch'))):
                        raise DevConflict('human decision receipt drift')
                    receipt_path=Path(directory)/('terminal-'+row['state'])/'human-decision-receipt.json'
            request_ref=immutable(receipt_path,row)
            store.settle(job['job_id'], {'help_wait_seconds': elapsed}, {'planner_request_id': row['request_id'], 'state': row['state'],'request':request_ref})
        store.update_cell(key, planner_wait=None)
