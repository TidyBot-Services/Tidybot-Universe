"""Executor-confirmed Attention policy and human guidance for the Dev ledger."""
import copy
import json
import re
import time
from uuid import uuid4

from .core.dev_store import DevConflict
from .core.policies import build_policy, PolicyContext, DecisionAction
from .core.assessment import assess_trace

from .core.policies import POLICY_IDS
ALLOWED = POLICY_IDS
LEGACY_ALLOWED = ('autonomous', 'reactive_help', 'trace_aware_hint_only', 'retry_k_then_ask')


def initial_attention(now):
    return dict(active_policy='autonomous', generation=0, request=None, hint=None,
                hint_count=0, decisions={}, events=[],
                segments=[dict(policy_id='autonomous', generation=0, effective_at=now)])


def project_attention(session):
    a = session.get('attention')
    active = session['state'] in {'ready', 'running'} and not session['stop_requested'] and time.time()<session['deadline_at']
    if not a:
        return dict(binding=None, read_only=True, active_policy=None, generation=0,
                    segments=[], events=[], request=None, switch_count=0,
                    reason='Attention was not recorded for this older Dev session')
    return dict(binding={'allowed_policies': list(ALLOWED if session.get('dev_v2') else LEGACY_ALLOWED), 'channel': 'dev'},
                read_only=not active, active_policy=a['active_policy'], generation=a['generation'],
                segments=copy.deepcopy(a['segments']), events=copy.deepcopy(a['events']),
                request=copy.deepcopy(a['request']), hint=copy.deepcopy(a['hint']),
                switch_count=len(a['segments'])-1, hint_count=a['hint_count'],
                hint_limit=session['budget']['executions'],
                reason='Changes apply between Dev model/execution jobs' if active else 'Dev session has ended or requires review')


def feedback_packet(feedback, version):
    events = []
    for i, event in enumerate(feedback.get('action_trace', [])):
        error = event.get('error')
        events.append({**event, 'event_id': f'v{version}:{i}', 'sequence': i,
                       'event_type': 'sdk.call', 'error': {'message': error} if isinstance(error, str) else error})
    return {'trace_id': f'dev-public:v{version}', 'events': events, 'evidence': []}


class DevAttentionControl:
    def __init__(self, store):
        self.store = store

    def enable(self, sid):
        self.store.get_dev_session(sid)  # Explicit Dev schema initialization.
        with self.store._transaction() as db:
            s = self.store._dev_get(db, sid)
            if 'attention' not in s:
                s['attention'] = initial_attention(time.time())
                self.store._dev_save(db, s, 'attention_enabled')

    def snapshot(self, sid):
        return project_attention(self.store.get_dev_session(sid))

    @staticmethod
    def _editable(s):
        if not s.get('attention') or s['state'] not in {'ready', 'running'} or s['stop_requested'] or time.time()>=s['deadline_at']:
            raise DevConflict('This Dev session is read-only')

    def request(self, sid, policy_id, request_id, expected_generation):
        if policy_id not in (ALLOWED if self.store.get_dev_session(sid).get('dev_v2') else LEGACY_ALLOWED):
            raise ValueError('This Attention policy is not supported by Dev')
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', request_id):
            raise ValueError('valid request ID required')
        if type(expected_generation) is not int:
            raise ValueError('integer generation required')
        with self.store._transaction() as db:
            s = self.store._dev_get(db, sid); self._editable(s); a = s['attention']
            prior = [e for e in a['events'] if e.get('request_id') == request_id and e.get('kind') == 'switch']
            if prior:
                if prior[0]['policy_id'] != policy_id or prior[0]['expected_generation'] != expected_generation:
                    raise DevConflict('switch request ID reused with different conditions')
                return prior[-1]
            if expected_generation != a['generation']:
                raise DevConflict('stale Attention generation; refresh')
            if a['request'] and a['request']['state'] == 'Pending':
                raise DevConflict('another Attention switch is pending')
            r = dict(kind='switch', request_id=request_id, policy_id=policy_id,
                     expected_generation=expected_generation, state='Pending', at=time.time(),
                     reason='Waiting for the Dev executor between jobs')
            a['request'] = r; a['events'].append(r.copy())
            self.store._dev_save(db, s, 'attention_switch_requested')
            return r

    def cancel(self, sid, request_id):
        with self.store._transaction() as db:
            s = self.store._dev_get(db, sid); self._editable(s); a = s['attention']; r = a['request']
            if not r or r['request_id'] != request_id or r['state'] not in {'Pending', 'Cancelled'}:
                raise DevConflict('pending Attention switch not found')
            if r['state'] == 'Cancelled':return r
            r.update(state='Cancelled', at=time.time(), reason='Cancelled before activation')
            a['events'].append(r.copy()); self.store._dev_save(db, s, 'attention_switch_cancelled')
            return r

    def boundary(self, sid, owner, *, ending=False):
        """Only the owning worker can acknowledge, with no in-flight job."""
        with self.store._transaction() as db:
            s = self.store._dev_get(db, sid); a = s.get('attention')
            if not a or s['owner'] != owner:raise DevConflict('Attention boundary requires Dev owner')
            if not ending and db.execute("SELECT 1 FROM dev_jobs WHERE session_id=? AND state IN ('claimed','needs_review')", (sid,)).fetchone():
                return project_attention(s)
            r = a['request']; changed = False
            if r and r['state'] == 'Pending':
                stopped = ending or s['stop_requested'] or s['state'] not in {'ready', 'running'} or time.time() >= s['deadline_at']
                r.update(state='Rejected' if stopped else 'Active', at=time.time(),
                         reason='Session ended before activation' if stopped else 'Dev executor confirmed between jobs')
                if not stopped:
                    a['generation'] += 1; a['active_policy'] = r['policy_id']
                    a['segments'].append(dict(policy_id=r['policy_id'], generation=a['generation'],
                                              effective_at=r['at'], boundary_kind='dev_iteration', request_id=r['request_id']))
                a['events'].append(r.copy()); changed = True
            hint = a['hint']
            if hint and hint['state'] == 'pending' and (ending or s['stop_requested'] or hint['generation'] != a['generation']):
                hint.update(state='cancelled', reason='Session stopped or Attention policy changed')
                a['events'].append(dict(kind='hint_cancelled', request_id=hint['request_id'], at=time.time())); changed = True
            if changed:self.store._dev_save(db, s, 'attention_boundary')
            return project_attention(s)

    def decide(self, sid, owner, version, feedback, failures, prior_feedback=()):
        # The caller supplies screened public feedback; screen again at the
        # persistence boundary so native outcomes cannot leak into requests.
        from .dev_loop import screen
        screen(feedback); screen(list(prior_feedback))
        with self.store._transaction() as db:
            s = self.store._dev_get(db, sid); self._editable(s)
            if s['owner'] != owner:raise DevConflict('Attention decision requires Dev owner')
            if db.execute("SELECT 1 FROM dev_jobs WHERE session_id=? AND state IN ('claimed','needs_review')",(sid,)).fetchone():
                raise DevConflict('Attention decision must wait for the in-flight job')
            a = s['attention']; key = f'{version}:{a["generation"]}'
            if key in a['decisions']:
                return {**a['decisions'][key], 'hint': copy.deepcopy(a['hint'])}
            failed = feedback.get('execution_status') in {'failed', 'timeout', 'timed_out'}
            decision = None
            if failed:
                packet = feedback_packet(feedback, version)
                assessment = assess_trace(packet, [feedback_packet(f, i) for i, f in enumerate(prior_feedback)])
                context = PolicyContext(run_id=sid, attempt_index=version-1, failure_index=max(0, failures-1),
                    consecutive_failures=failures, assistance_remaining=max(0,s['budget']['executions']-a['hint_count']),
                    evidence_count=len(packet['events']), has_hypothesis=bool(feedback.get('public_error')), assessment=assessment)
                decision = build_policy(a['active_policy']).decide(context)
            result = dict(policy_id=a['active_policy'], generation=a['generation'],
                          action=decision.action.value if decision else 'continue', reason=decision.reason if decision else 'No public failure')
            if decision and decision.action is DecisionAction.REQUEST:
                a['hint_count'] += 1
                a['hint'] = dict(request_id='dev-hint-'+uuid4().hex, version=version, generation=a['generation'],
                                 policy_id=a['active_policy'], state='pending', reason=decision.reason,
                                 created_at=time.time(), feedback=copy.deepcopy(feedback))
                a['events'].append(dict(kind='hint_requested', request_id=a['hint']['request_id'], at=time.time(), version=version))
            else:a['hint'] = None
            a['decisions'][key] = result
            self.store._dev_save(db, s, 'attention_decision')
            return {**result, 'hint': copy.deepcopy(a['hint'])}

    def guidance(self, sid, request_id, *, content='', skip=False):
        from .dev_loop import screen
        if not isinstance(content, str) or len(content) > 2000 or (not skip and not content.strip()):
            raise ValueError('Guidance must contain 1–2000 characters')
        if skip and content.strip():raise ValueError('Skipped guidance must be empty')
        screen(content)
        with self.store._transaction() as db:
            s = self.store._dev_get(db, sid); self._editable(s); a = s['attention']; h = a['hint']
            if not h or h['request_id'] != request_id or h['generation'] != a['generation']:
                raise DevConflict('This guidance request is stale')
            state = 'skipped' if skip else 'answered'
            if h['state'] == state and h.get('content','') == content.strip():return h
            if h['state'] != 'pending':raise DevConflict('This guidance request is closed')
            h.update(state=state, content=content.strip(), answered_at=time.time())
            a['events'].append(dict(kind='hint_'+state, request_id=request_id, at=time.time()))
            self.store._dev_save(db, s, 'attention_guidance_'+state)
            return h
