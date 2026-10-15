"""Append-only operator identities and executor-confirmed Attention handovers.

The original run, action policy, model, DAG and budget rows are never rewritten.
SQLite BEGIN IMMEDIATE serializes competing clients and the executor. Only the
executor calls boundary(); HTTP request handlers cannot acknowledge a switch.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from .core.store import StateConflictError

SAFE_KINDS={'sdk_action_completed','attention_decision','human_wait','run_ended'}
SWITCHABLE={'autonomous','reactive_help','trace_aware_hint_only','full_trace_aware_attention_planner','retry_k_then_ask'}

def load_binding(path,expected_sha256):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('Attention control binding digest changed')
    value=json.loads(raw)
    if value.get('schema_version')!='attentionbench.attention-control.v1' or not value.get('binding_id'):
        raise ValueError('invalid Attention control binding')
    if not value.get('allowed_policies') or not set(value['allowed_policies'])<=SWITCHABLE:
        raise ValueError('unsupported online Attention policies')
    for file,digest in value.get('sources',{}).items():
        if hashlib.sha256(Path(file).read_bytes()).hexdigest()!=digest:raise ValueError('Attention control source changed: '+file)
    return {**value,'binding_sha256':expected_sha256}

def boundary_event_sink(runner,events):
    """Run the callback after the synchronous SDK command and its receipt."""
    def emit(event):
        events.append(event)
        hook=getattr(runner,'attention_boundary_hook',None)
        if hook and event.get('status')=='completed' and event.get('event_type') in {'sdk.arm_command','sdk.gripper_command','sdk.base_command'}:hook(event)
    return emit

def validate_launch_binding(binding,profile):
    for bound,approved in [('config_sha256','approved_config_sha256'),('action_policy_sha256','approved_policy_sha256'),('graph_sha256','skill_graph_sha256')]:
        if binding.get(bound)!=profile.get(approved):
            raise ValueError(f'Attention binding disagrees with {approved}')

class AttentionControl:
    def __init__(self,store):self.store=store

    @staticmethod
    def _run(db,run_id):
        row=db.execute('SELECT payload FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row:raise StateConflictError('run not found')
        return json.loads(row[0])

    @staticmethod
    def _rows(db,run_id,kind='attention_control'):
        return [json.loads(row[0]) for row in db.execute('SELECT payload FROM events WHERE entity_type=? AND entity_id=? ORDER BY sequence',(kind,run_id))]

    @staticmethod
    def _append(db,run_id,key,kind,payload,entity='attention_control'):
        db.execute('INSERT INTO events(event_key,entity_type,entity_id,event_type,payload) VALUES (?,?,?,?,?)',
                   (key,entity,run_id,kind,json.dumps(payload,sort_keys=True,separators=(',',':'))))

    def bind(self,run_id,binding):
        with self.store._transaction() as db:
            run=self._run(db,run_id);rows=self._rows(db,run_id,'attention_binding')
            if rows:
                if rows[0]['binding']!=binding:raise StateConflictError('Attention binding is immutable')
                return rows[0]
            if binding.get('schema_version')!='attentionbench.attention-control.v1' or run['policy_id'] not in binding.get('allowed_policies',[]):
                raise ValueError('initial Attention policy is outside engineering binding')
            value={'binding':binding,'initial_policy':run['policy_id'],'bound_at':run['created_at']}
            self._append(db,run_id,'attention-binding:'+run_id,'attention.bound',value,'attention_binding')
            return value

    def rename(self,run_id,name,*,now):
        if not isinstance(name,str) or not 1<=len(name.strip())<=160 or any(ord(c)<32 for c in name):raise ValueError('name requires 1–160 printable characters')
        with self.store._transaction() as db:
            self._run(db,run_id)
            from uuid import uuid4
            value={'display_name':name.strip(),'edited_at':now}
            self._append(db,run_id,'run-name:'+uuid4().hex,'run.name_updated',value,'run_identity')
        return value

    def ensure_identity(self,run_id):
        identity=run_identity(self.store,run_id)
        with self.store._transaction() as db:
            if not self._rows(db,run_id,'run_identity'):
                self._append(db,run_id,'run-name-initial:'+run_id,'run.name_created',identity,'run_identity')
        return identity

    def link(self,run_id,source_run_id,relation,*,now):
        if relation not in {'copy','retry','policy_change'} or run_id==source_run_id:raise ValueError('invalid run provenance')
        value={'source_run_id':source_run_id,'source_relation':relation,'linked_at':now}
        with self.store._transaction() as db:
            self._run(db,run_id);self._run(db,source_run_id)
            old=db.execute('SELECT payload FROM events WHERE event_key=?',('run-source:'+run_id,)).fetchone()
            if old:
                if json.loads(old[0])!=value:raise StateConflictError('run provenance is immutable')
                return value
            self._append(db,run_id,'run-source:'+run_id,'run.source_linked',value,'run_identity')
        return value

    def _snapshot(self,db,run_id):
        run=self._run(db,run_id);bindings=self._rows(db,run_id,'attention_binding');events=self._rows(db,run_id)
        segments=[{'policy_id':run['policy_id'],'generation':0,'effective_at':run['created_at'],'boundary_id':'run_started'}]
        for e in events:
            if e.get('state')=='Active' and not e.get('no_change'):
                segments.append({'policy_id':e['policy_id'],'generation':e['generation'],'effective_at':e['at'],'boundary_id':e['boundary_id'],'boundary_kind':e['boundary_kind'],'request_id':e['request_id'],
                    'trace_seconds':e.get('trace_seconds'),'attempt_id':e.get('attempt_id')})
        latest={}
        for e in events:latest[e['request_id']]=e
        pending=next((x for x in reversed(list(latest.values())) if x['state']=='Pending'),None)
        return {'initial_policy':run['policy_id'],'active_policy':segments[-1]['policy_id'],'generation':segments[-1]['generation'],
                'binding':bindings[0]['binding'] if bindings else None,'read_only':not bindings or run['status']!='running' or bool(self.store._interrupt_status_in(db,run_id)),
                'stop_requested':bool(self.store._interrupt_status_in(db,run_id)),
                'request':pending or (events[-1] if events else None),'events':events,'segments':segments,
                'switch_count':len(segments)-1,'statistical_class':'Mixed policy' if len(segments)>1 else run['policy_id']}

    def snapshot(self,run_id):
        with self.store._connect() as db:return self._snapshot(db,run_id)

    def request(self,run_id,policy_id,*,request_id,expected_generation,now,timeout_seconds=60):
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',request_id):raise ValueError('invalid switch request ID')
        if type(expected_generation) is not int or expected_generation<0:raise ValueError('invalid policy generation')
        if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or not 1<=timeout_seconds<=120:raise ValueError('invalid switch timeout')
        if not isinstance(policy_id,str):raise ValueError('invalid Attention policy')
        with self.store._transaction() as db:
            snap=self._snapshot(db,run_id);prior=[e for e in snap['events'] if e['request_id']==request_id]
            if prior:
                if prior[0]['policy_id']!=policy_id or prior[0]['expected_generation']!=expected_generation:raise StateConflictError('idempotency key reused with different selection')
                return prior[-1]
            base={'request_id':request_id,'policy_id':policy_id,'expected_generation':expected_generation,'at':now,'expires_at':now+timeout_seconds}
            self._append(db,run_id,f'policy:{run_id}:{request_id}:Requested','attention.switch_requested',{**base,'state':'Requested'})
            reason=None
            if not snap['binding']:reason='frozen binding; create an associated engineering run'
            elif snap['stop_requested']:reason='stop requested; policy changes are fenced'
            elif snap['read_only']:reason='run is terminal; history is read-only'
            elif policy_id not in snap['binding']['allowed_policies']:reason='Attention policy is outside this engineering binding'
            elif expected_generation!=snap['generation']:reason='stale policy generation; refresh and retry'
            elif snap['request'] and snap['request']['state']=='Pending':reason='another switch is pending'
            result={**base,'state':'Rejected' if reason else 'Pending','reason':reason or 'awaiting executor safe boundary'}
            self._append(db,run_id,f"policy:{run_id}:{request_id}:{result['state']}",'attention.switch_'+result['state'].lower(),result)
            return result

    def cancel(self,run_id,request_id,*,now):
        with self.store._transaction() as db:
            snap=self._snapshot(db,run_id);rows=[e for e in snap['events'] if e['request_id']==request_id]
            if not rows:raise StateConflictError('switch request not found')
            if rows[-1]['state']=='Cancelled':return rows[-1]
            if rows[-1]['state']!='Pending':raise StateConflictError('switch already resolved')
            value={**rows[-1],'state':'Cancelled','at':now,'reason':'operator cancelled before activation'}
            self._append(db,run_id,f'policy:{run_id}:{request_id}:Cancelled','attention.switch_cancelled',value)
            return value

    def expire(self,run_id,*,now):
        """Reject an expired request without touching the action in progress."""
        with self.store._transaction() as db:
            pending=self._snapshot(db,run_id)['request']
            if pending and pending['state']=='Pending' and now>=pending['expires_at']:
                value={**pending,'state':'Rejected','at':now,'boundary_id':'request-timeout','boundary_kind':'request_timeout','reason':'safe-boundary timeout'}
                self._append(db,run_id,f"policy:{run_id}:{pending['request_id']}:Rejected",'attention.switch_rejected',value)
                return value
            return pending

    def boundary(self,run_id,*,boundary_id,kind,now,unresolved_hint=False,trace_seconds=None,attempt_id=None):
        if kind not in SAFE_KINDS:raise ValueError('unsafe Attention handover boundary')
        with self.store._transaction() as db:
            snap=self._snapshot(db,run_id);pending=snap['request']
            if not pending or pending['state']!='Pending':return snap
            if kind=='run_ended' or snap['read_only'] or now>=pending['expires_at']:
                value={**pending,'state':'Rejected','at':now,'boundary_id':boundary_id,'boundary_kind':kind,'reason':'stop requested before activation' if snap['stop_requested'] else 'run ended before activation' if kind=='run_ended' or snap['read_only'] else 'safe-boundary timeout'}
            elif unresolved_hint:
                return snap
            else:
                # Keep counters, reservations, task progress and cached policy state.
                value={**pending,'state':'Active','at':now,'boundary_id':boundary_id,'boundary_kind':kind,
                       'generation':snap['generation']+(pending['policy_id']!=snap['active_policy']),
                       'trace_seconds':trace_seconds,'attempt_id':attempt_id,
                       'no_change':pending['policy_id']==snap['active_policy'],'reason':'executor confirmed safe boundary'}
            self._append(db,run_id,f"policy:{run_id}:{pending['request_id']}:{value['state']}",'attention.switch_'+value['state'].lower(),value)
            return self._snapshot(db,run_id)

    def policy_at(self,run_id,at):
        return next((s['policy_id'] for s in reversed(self.snapshot(run_id)['segments']) if s['effective_at']<=at),self.store.get_run(run_id)['policy_id'])

    def unresolved_hint(self,run_id):
        # An answered Hint remains owned by its issuing generation until its
        # execution association is recorded. No response is carried across a
        # handover and no operator draft is submitted by this controller.
        with self.store._connect() as db:
            requests=[json.loads(r[0]) for r in db.execute('SELECT payload FROM requests')]
            uses={e['payload']['request_id']:e['payload'] for e in self.store.events() if e['event_type'] in {'response.used','response.execution_linked'}}
        for r in requests:
            if r['run_id']!=run_id:continue
            if r['state'] in {'pending','fallback'}:return True
            if r['state']=='answered':
                use=uses.get(r['request_id'])
                if not use or (use.get('use') in {'guidance','demo'} and not use.get('execution_id')):return True
        return False

def run_identity(store,run_id):
    run=store.get_run(run_id)
    if run is None:raise StateConflictError('run not found')
    cfg=run.get('robot_configuration') or {};robot=cfg.get('robot') or {}
    capability='Mobile' if cfg.get('base') else 'Fixed base' if cfg.get('base_status')=='fixed' else 'Base not recorded'
    started=datetime.fromtimestamp(run['created_at'],timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
    suffix=run_id.removeprefix('run:')
    name=f"{robot.get('model','Unknown robot')} · {robot.get('execution_arm','unknown arm').title()} · {capability} · {started} · {suffix[-12:]}"
    value={'display_name':name,'started_at':run['created_at'],'robot':robot or None,'base':cfg.get('base'),'base_status':cfg.get('base_status'),'seed':run['seed'],'initial_policy':run['policy_id']}
    # Deterministic collision-resistant names for arbitrary imported IDs too.
    value['display_name']+= ' · '+hashlib.sha256(run_id.encode()).hexdigest()[:6]
    with store._connect() as db:
        for e in AttentionControl._rows(db,run_id,'run_identity'):value.update(e)
    return value
