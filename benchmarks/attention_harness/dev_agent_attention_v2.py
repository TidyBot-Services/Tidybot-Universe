"""Version-locked agent help controller; all policy inputs are canonical public data.

The hash-chained decision journal and ledger are authoritative. attention.json is
only a recoverable projection. Claimed/unknown provider work is never reissued.
"""
import copy
import difflib
import hashlib
import json
import threading
import time
from dataclasses import asdict

from . import dev_agent_policies as policies
from .dev_agent_attention import Helper, flat_usage, request_view
from . import dev_agent_human_reply as human_reply
DEV_AUTHORED_VIEW_FIELDS = frozenset({'diff', 'reasoning', 'question'})
from .dev_agent_eval import feedback
from .dev_agent_workspace import atomic_write, encode, read_beneath, shared_name
from .dev_help import public_screen

LIMIT = 32 * 1024 * 1024
MAPS = ('versions', 'executions', 'requests')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def policy_state(value):
    fields = policies.PolicyState.__dataclass_fields__
    data = {k: value[k] for k in fields}
    data['completed_execution_ids'] = tuple(data['completed_execution_ids'])
    if data['pending_ask']:
        data['pending_ask'] = policies.PendingAsk(**data['pending_ask'])
    return policies.PolicyState(**data)


def execution_evidence(value):
    data = dict(value)
    data['signature'] = policies.FailureSignature(**data['signature'])
    data['artifacts'] = tuple(policies.ArtifactRef(**v) for v in data['artifacts'])
    for key in ('event_ids', 'unassociated_planner_codes'):
        data[key] = tuple(data[key])
    return policies.ExecutionEvidence(**data)


def version_evidence(value):
    data = dict(value)
    data['executions'] = tuple(execution_evidence(v) for v in data['executions'])
    data['rejected_probes'] = tuple(policies.ProbeEvidence(**dict(v,
        signature=policies.FailureSignature(**v['signature']))) for v in data['rejected_probes'])
    for key in ('execution_histogram', 'probe_histogram'):
        data[key] = tuple(tuple(v) for v in data[key])
    data['probe_only_signatures'] = tuple(data['probe_only_signatures'])
    return policies.VersionEvidence(**data)


class HelperV2(Helper):
    def __init__(self, workspace, ledger, *, policy, parameters=None, config=None, client=None,
                 demo=None, quota=None, ask_provider=None, view_builder=request_view, memory=None,
                 agent_policy_version=policies.AGENT_POLICY_VERSION):
        from .dev_advisor_proxy import ask
        if (workspace.private/'replay-origin.json').exists():
            raise ValueError('file replay cannot resume policy; sealed policy evidence required')
        if agent_policy_version != policies.AGENT_POLICY_VERSION:
            raise ValueError('policy state version mismatch')
        self.workspace, self.ledger, self.policy = workspace, ledger, policy
        self.agent_policy_version = agent_policy_version
        self.parameters = policies.normalize_parameters(policy, parameters)
        self.config, self.client, self.demo, self.quota = config or {}, client, demo, quota
        self.ask_provider = ask_provider or ask
        self.view_builder = public_request_view if view_builder is request_view else view_builder
        self.memory = memory if policies.memory_exposed(policy) else None
        self.lock = threading.RLock()
        self.path = workspace.private / 'attention.json'
        self.journal = workspace.private / 'policy-journal'
        self.journal.mkdir(exist_ok=True)
        self.state = dict(asdict(policies.initial_state(policy, self.parameters)),
                          completed_execution_count=0, active_help=None, versions={}, executions={}, requests={})
        saved = None
        if self.path.exists():
            try:saved = json.loads(read_beneath(workspace.private, 'attention.json', LIMIT))
            except (ValueError, OSError):pass
        if saved is not None and not isinstance(saved,dict):saved=None
        if saved and (saved.get('schema') != self.state['schema'] or saved.get('agent_policy_version') != agent_policy_version or
                      saved.get('policy_id') != policy or
                      saved.get('parameters_sha256') != self.state['parameters_sha256']):
            raise ValueError('policy state version/parameters mismatch')
        self.seal = None
        self.serial = 0
        paths = sorted(self.journal.glob('*.json'))
        for number, path in enumerate(paths, 1):
            row = json.loads(read_beneath(self.journal, path.name, LIMIT))
            if (path.name != '%06d.json' % number or row['previous'] != self.seal or
                    row['agent_policy_version'] != agent_policy_version or
                    row['policy_id'] != policy or row['parameters_sha256'] != self.state['parameters_sha256']):
                raise ValueError('policy journal mismatch')
            seal = row.pop('sha256')
            if digest(encode(row)) != seal:raise ValueError('policy journal digest mismatch')
            for key, value in row['changes'].items():
                if key in MAPS:self.state[key].update(value)
                else:self.state[key] = value
            self.serial, self.seal = number, seal
        if not paths and (saved or any(j['kind'] in ('help','request') for j in ledger.jobs()) or
                          any((workspace.canonical/'executions').glob('*/execution.json'))):
            raise ValueError('sealed policy evidence required for recovery')
        self._sealed = copy.deepcopy(self.state)
        if not paths:self._commit('bootstrap_state')
        # A settled ledger receipt wins over a crash before publishing/projection.
        for job in ledger.jobs():
            if not job['id'].startswith('help:'):continue
            if job['id']=='help:demo':
                if job['state']=='settled':
                    self._transition(policies.PolicyEvent('demo_dispatched'))
                    workspace.publish('events/demo.json', job['receipt'])
                continue
            eid = job['id'][5:]
            if eid not in self.state['executions']:raise ValueError('help execution evidence missing')
            if job['state']=='settled':
                receipt = job['receipt']
                if receipt.get('requested') and not self.state['requests'].get(job['id'], {}).get('requested'):
                    self._transition(policies.PolicyEvent('help_dispatched', execution_id=eid))
                self.state['requests'][job['id']] = receipt
                self.state['active_help'] = None
            elif job['state'] in ('reserved', 'released'):
                if job['state']=='reserved':ledger.release_unclaimed(job['id'])
                self.state['active_help']=None
            elif job['state'] in ('claimed', 'unknown'):
                self.state['active_help'] = dict(help_id=job['id'], execution_id=eid,
                    job_id=job['id'], phase='unknown')
        # Re-read sealed evidence refs; writable ws never participates in recovery.
        for entry in self.state['executions'].values():
            self._read_ref(entry['evidence_ref']);self._read_ref(entry['context_ref'])
            self._read_bytes_ref(entry['source_ref'])
        for entry in self.state['versions'].values():
            self._read_ref(entry['evidence_ref']);self._read_ref(entry['decision_ref'])
        self._commit('ledger_reconcile')
        self._publish_requests()

    @property
    def pending_ask(self):
        return self.state['pending_ask']

    def _commit(self, kind):
        changes = {}
        for key, value in self.state.items():
            if key in MAPS:
                delta = {k:v for k,v in value.items() if self._sealed.get(key, {}).get(k) != v}
                if delta:changes[key] = delta
            elif self._sealed.get(key) != value:changes[key] = value
        if changes or self.serial == 0:
            # First seal includes the immutable identity and zero counters.
            if self.serial == 0:changes = copy.deepcopy(self.state)
            row = dict(kind=kind, previous=self.seal, agent_policy_version=self.agent_policy_version,
                policy_id=self.policy, parameters_sha256=self.state['parameters_sha256'], changes=changes)
            self.seal = digest(encode(row));row['sha256'] = self.seal
            self.serial += 1
            atomic_write(self.journal, '%06d.json' % self.serial, encode(row))
            self._sealed = copy.deepcopy(self.state)
        atomic_write(self.workspace.private, 'attention.json', encode(self.state))

    def _transition(self, event, evidence=None):
        transition = policies.decide_agent(self.policy, self.parameters, policy_state(self.state), event, evidence,
            help_remaining=self.ledger.snapshot()['remaining']['help'],
            request_pending=self.state['active_help'] is not None, demo_available=bool(self.demo))
        self.state.update(asdict(transition.next_state))
        self.state['completed_execution_count'] = transition.next_state.completed_execution_count
        return transition

    def _ref(self, name):
        raw = read_beneath(self.workspace.canonical, name, LIMIT)
        return dict(path=name, sha256=digest(raw))

    def _read_bytes_ref(self, ref):
        raw = read_beneath(self.workspace.canonical, ref['path'], LIMIT)
        if digest(raw) != ref['sha256']:raise ValueError('public evidence digest mismatch')
        return raw

    def _read_ref(self, ref):
        return json.loads(self._read_bytes_ref(ref))

    def _artifact(self, metadata, name):
        base = 'executions/' + metadata['execution_id'] + '/'
        ref = metadata['artifacts'].get(name)
        if ref is None:return None, policies.ArtifactRef(base+name, status='missing')
        raw = read_beneath(self.workspace.canonical, base+name, LIMIT, shared=shared_name(base+name))
        if digest(raw) != ref['sha256']:raise ValueError('public artifact digest mismatch')
        withheld = b'[withheld]' in raw
        return (None if withheld else raw), policies.ArtifactRef(base+name, digest(raw), 'withheld' if withheld else 'present')

    def _public_execution(self, metadata):
        values, refs = {}, []
        for name in ('result.json', 'trace.json', 'diagnostics/planner.json', 'stdout.txt', 'stderr.txt', 'policy.py', 'frames.json'):
            raw, ref = self._artifact(metadata, name);refs.append(ref)
            values[name] = raw
        load = lambda name, default: json.loads(values[name]) if values[name] is not None else default
        public = policies.PublicExecution(status=load('result.json', {}).get('status', 'unknown'),
            trace=tuple(load('trace.json', [])), planner=tuple(load('diagnostics/planner.json', [])),
            stdout=(values['stdout.txt'] or b'').decode(), stderr=(values['stderr.txt'] or b'').decode(),
            artifacts=tuple(refs), sequence=metadata['sequence'], op=metadata['op'])
        return policies.summarize_execution(metadata['execution_id'], public)

    def register_execution(self, metadata):
        with self.lock:
            eid = metadata['execution_id']
            if eid in self.state['executions']:return
            if not metadata['execution_dispatched']:return
            evidence = self._public_execution(metadata)
            name = 'executions/'+eid+'/evidence.json'
            self.workspace.publish(name, asdict(evidence))
            self.state['executions'][eid] = dict(evidence_ref=self._ref(name),
                source_ref=dict(metadata['artifacts']['policy.py']), context_ref=self._ref('executions/'+eid+'/execution.json'), help_id=None)
            self._transition(policies.PolicyEvent('execution', execution_id=eid, op=metadata['op']))
            self._commit('execution')

    def capture(self, executor):
        pass  # v2 never reads an executor's private episode or timing context.

    def initial(self):
        with self.lock:
            transition = self._transition(policies.PolicyEvent('bootstrap'))
            if transition.action != 'use_demo':return None
            jid = 'help:demo';old = next((j for j in self.ledger.jobs() if j['id']==jid), None)
            if old and old['state']!='reserved':return old.get('receipt')
            if self.quota:self.quota.check('before_demo')
            self.ledger.reserve(jid, 'help', {'help':1});self.ledger.claim(jid)
            value = dict(status='delivered', eligible=True, requested=False, not_used=False,
                system_injected=True, delivered=True, content=self.demo['content'], help_count=1, hint_human_seconds=0)
            self.ledger.settle(jid, value, usage={})
            self._transition(policies.PolicyEvent('demo_dispatched'));self._commit('demo')
            self.workspace.publish('events/demo.json', value)
            return value

    def boundary(self, execution, *, deadline):
        with self.lock:
            vid = execution['version_id']
            if vid in self.state['versions']:return self._version_row(vid)
            sequence = execution['sequence'];start = self.state['last_submit_sequence']
            previous = tuple(version_evidence(self._read_ref(v['evidence_ref'])) for v in self.state['versions'].values())
            executions = tuple(execution_evidence(self._read_ref(v['evidence_ref']))
                for v in self.state['executions'].values())
            executions = tuple(e for e in executions if start < e.sequence <= sequence)
            probes = []
            # The probe set is extensible; only genuine plan_dry_run refusals count as evidence.
            for path in sorted((self.workspace.canonical/'probes').glob('p*.json')):
                value = json.loads(read_beneath(self.workspace.canonical, 'probes/'+path.name, LIMIT))
                if (value.get('op') != 'plan_dry_run' or not start < value['sequence'] <= sequence or
                    value.get('probe_error') or not (value.get('planning_accepted') is False or
                    value.get('accepted') is False or value.get('status')=='rejected')):continue
                code = policies._code_value(value.get('diagnostics') or {}) or policies._code_value(value)
                signature = policies.failure_signature('planning', code={'source':'planner','value':code} if code else None)
                probes.append(policies.ProbeEvidence(path.stem, value['sequence'], signature))
            passed = execution.get('eval', {}).get('passed')
            verdict = 'passed' if passed is True else 'failed' if passed is False else 'unavailable'
            evidence = policies.build_version_evidence(vid, executions, tuple(probes), execution['source_sha256'], previous,
                eval_verdict=verdict, submit_sequence=sequence)
            self.workspace.publish('versions/'+vid+'/evidence.json', evidence.artifact())
            eid = execution.get('execution_id') if execution.get('execution_dispatched') else None
            transition = self._transition(policies.PolicyEvent('submit', vid, eid, sequence, verdict, 'submit'), evidence)
            memory_error = None
            if transition.action == 'check_memory':
                matched = False
                if self.memory:
                    source=''
                    try:
                        # Memory's authority binds the original frozen submitted
                        # source, even when its public code copy was withheld.
                        metadata = self._read_ref(self.state['executions'][eid]['context_ref'])
                        raw = read_beneath(self.workspace.private, 'sources/'+metadata['request_id']+'.py', LIMIT)
                        if digest(raw)!=execution['source_sha256']:raise ValueError('Memory source digest mismatch')
                        source = raw.decode()
                        self.memory.open(int(vid[1:])+1, source, deliver_notes=False)
                        self.memory.recheck()
                        packet = self.memory.packet
                        matched = bool(packet.get('explicit_registry_authorization') and packet.get('entries'))
                    except Exception as exc:
                        memory_error = type(exc).__name__
                        if not isinstance(exc,PermissionError) and hasattr(self.memory,'clear'):
                            self.memory.clear(int(vid[1:])+1,source)
                transition = policies.resolve_memory(transition, matched=matched)
            decision = dict(action=transition.action, reason=transition.reason, execution_id=transition.execution_id,
                policy_id=self.policy, agent_policy_version=self.agent_policy_version, evidence_refs=list(transition.evidence_refs))
            if memory_error:decision['memory_error'] = memory_error
            name = 'versions/'+vid+'/decision.json';self.workspace.publish(name, decision)
            self.state['versions'][vid] = dict(evidence_ref=self._ref('versions/'+vid+'/evidence.json'),
                decision_ref=self._ref(name), decision=decision, execution_ids=[e.execution_id for e in executions],
                help_ids=[self.state['executions'][e.execution_id]['help_id'] for e in executions
                          if self.state['executions'][e.execution_id]['help_id']], help_cutoff_sequence=sequence)
            self._commit('submit_decision')
            if transition.action == 'request':
                self._deliver(transition.execution_id, None, deadline, system=True, origin=vid, decision=decision)
            return self._publish_version(vid)

    def _version_row(self, vid):
        entry = self.state['versions'][vid]
        row = dict(status='pending_ask' if self.pending_ask and self.pending_ask['origin_version_id']==vid else 'not_eligible',
            eligible=entry['decision']['action'] in ('pending_ask','request'), version_id=vid, origin_version_id=vid,
            agent_policy_version=self.agent_policy_version, **entry)
        row['help_refs'] = ['executions/'+h[5:]+'/help.json' for h in row['help_ids']]
        return feedback(row)

    def _publish_version(self, vid):
        row = self._version_row(vid)
        self.workspace.publish('versions/'+vid+'/help.json', row)
        return row

    def _publish_requests(self):
        rows = []
        for hid, row in self.state['requests'].items():
            self.workspace.publish('executions/'+hid[5:]+'/help.json', feedback(row));rows.append(feedback(row))
        if rows:self.workspace.publish('history/help.jsonl', b''.join(encode(r).replace(b'\n', b'')+b'\n' for r in rows))
        for vid in self.state['versions']:self._publish_version(vid)

    def ask(self, payload, *, deadline):
        with self.lock:
            question = payload.get('question')
            if not isinstance(question, str) or not question.strip() or len(question)>2000:
                return dict(status='policy_denied', reason='invalid_question')
            try:public_screen(question)
            except ValueError:return dict(status='policy_denied', reason='invalid_question')
            transition = self._transition(policies.PolicyEvent('ask_help'))
            if transition.action != 'request':return dict(status='not_eligible', reason=transition.reason)
            metadata=self._read_ref(self.state['executions'][transition.execution_id]['context_ref'])
            origin = (self.pending_ask['origin_version_id'] if self.pending_ask else
                      metadata['generation_id'] if metadata['op']=='submit' else None)
            decision = dict(action='request', reason=transition.reason)
            return self._deliver(transition.execution_id, question, deadline, system=False, origin=origin, decision=decision)

    def _deliver(self, eid, question, deadline, *, system, origin, decision, fallback=None):
        if not eid or eid not in self.state['executions']:return dict(status='not_eligible', reason='no_completed_execution')
        hid = 'help:'+eid
        old = next((j for j in self.ledger.jobs() if j['id']==hid), None)
        if old and old['state'] in ('claimed','unknown','settled'):
            return old.get('receipt') or dict(status='needs_review', help_id=hid, reason='provider_handoff_unknown')
        if self.state['active_help']:return dict(status='not_eligible', reason='request_already_pending')
        row = dict(status='pending', help_id=hid, execution_id=eid, after_execution_id=eid,
            origin_version_id=origin, version_id=origin, requested=False, delivered=False, help_count=0,
            system_injected=system, forced_fallback=bool(fallback), fallback_trigger=fallback,
            decision=decision, agent_policy_version=self.agent_policy_version,
            flags=dict(system_injected=system,forced_fallback=bool(fallback),fallback_trigger=fallback),
            receipt_ref='executions/'+eid+'/help.json')
        def rejected(reason):
            row.update(status='unavailable', reason=reason)
            self.state['requests'][hid]=row;self._commit('help_rejected');self._publish_requests()
            return feedback(row)
        if time.monotonic()>=deadline or self.ledger.snapshot()['wall_remaining']<=0:return rejected('wall_stop')
        if self.quota:
            try:self.quota.check('before_help')
            except Exception as exc:return rejected('quota_stop' if str(exc)=='quota_stop' else 'quota_unavailable')
        if self.ledger.snapshot()['remaining']['help']<=0:return rejected('assistance_budget_exhausted')
        try:
            view = self.view_builder(self, eid, decision, origin=origin, question=question)
            # User 11-08: Dev's own words (its code diff, run log and code, its question) are not screened for
            # evaluation terms; Dev never sees private evaluation, so a word like "evaluator" there is Dev's own.
            public_screen({k:v for k,v in view.items() if k not in DEV_AUTHORED_VIEW_FIELDS})
        except Exception as exc:
            atomic_write(self.workspace.private, 'help/'+eid+'/error.json', encode(dict(error=str(exc))))
            return rejected('public_view_unavailable')
        from .dev_request_view import human_seconds, shown_words, READ_WPS, WRITE_WPS
        reserve = view['shown_video_seconds']+shown_words(view)/READ_WPS+768/WRITE_WPS
        atomic_write(self.workspace.private, 'help/'+eid+'/request-view.json', encode(view))
        self.state['requests'][hid]=row
        self.state['active_help']=dict(help_id=hid, execution_id=eid, job_id=hid, phase='intent')
        self._commit('help_intent')
        # Released reservations cannot be claimed again. Reopen only unclaimed
        # work through a ledger transaction which proves that no handoff occurred.
        try:
            if old and old['state']=='released':self.ledger.reopen_unclaimed(hid)
            else:self.ledger.reserve(hid, 'help', {'help':1})
        except Exception as exc:
            self.state['active_help']=None
            return rejected(str(exc) if str(exc) in ('wall_stop','operator_paused','budget_exhausted') else 'reservation_unavailable')
        if time.monotonic()>=deadline:
            self.ledger.release_unclaimed(hid);self.state['active_help']=None
            return rejected('wall_stop')
        try:self.ledger.claim(hid)
        except Exception as exc:
            job=next(j for j in self.ledger.jobs() if j['id']==hid)
            if job['state']=='reserved':self.ledger.release_unclaimed(hid)
            self.state['active_help']=None
            return rejected(str(exc) if str(exc) in ('wall_stop','operator_paused') else 'handoff_unavailable')
        row.update(status='dispatching', help_count=1, hint_human_seconds=reserve)
        self.state['active_help']['phase']='claimed'
        self._commit('provider_claimed')
        reply = None if fallback else self._human_reply(eid, view, deadline)
        record=None;usage=None
        if reply is not None:
            row.update(status='delivered', delivered=True, provider='human', content=reply, **human_seconds(view, reply))
            atomic_write(self.workspace.private, 'help/'+eid+'/human.json', encode(dict(content=reply)))
        else:
            try:
                record = self.ask_provider(view, self.config['advisor_proxy'], self.client,
                    cache_dir=self.workspace.private/'advisor-cache')
                public_screen(record['feedback'])
                row.update(status='delivered', delivered=True, provider='advisor', content=record['feedback'],
                    cache_hit=record.get('cache_hit', False), **human_seconds(view, record['feedback']))
                atomic_write(self.workspace.private, 'help/'+eid+'/advisor.json', encode(record))
                try:usage=flat_usage(record.get('usage'))
                except (ValueError, TypeError, AttributeError):usage=None;row['usage_record']='unknown'
            except Exception as exc:
                record=None;usage=None
                atomic_write(self.workspace.private, 'help/'+eid+'/error.json', encode(dict(error=str(exc),error_class=type(exc).__name__)))
                row.update(status='unavailable', error=type(exc).__name__)
        # A return or ordinary provider exception proves invocation. A crash in
        # the claim/call gap or during the call keeps the obligation plus an
        # unknown active job; recovery cannot pretend it was asked or reissue it.
        row['requested']=True
        self._transition(policies.PolicyEvent('help_dispatched', execution_id=eid))
        self.state['executions'][eid]['help_id']=hid
        for vid, entry in self.state['versions'].items():
            if eid in entry['execution_ids'] and hid not in entry['help_ids']:entry['help_ids'].append(hid)
        self.state['active_help']['phase']='handoff_confirmed'
        self._commit('provider_handoff')
        try:self.ledger.settle(hid, feedback(row), usage=usage)
        except ValueError:
            row['usage_record']='unknown';self.ledger.settle(hid, feedback(row))
        self.state['active_help']=None;self._commit('help_settled');self._publish_requests()
        # Version exports retain their original cutoff. Late help lives only in
        # execution receipts/history; no sealed vN export is rewritten.
        if self.quota and record:
            for name in ('quota_before','quota_after'):self.quota.event(record.get(name) or {})
        return feedback(row)

    def _human_reply(self, eid, view, deadline):
        """A person's words if this run asks one first and they answer in time; otherwise None and the Advisor answers."""
        seconds = min(self.config.get('human_reply_seconds') or 0, deadline-time.monotonic(), self.ledger.snapshot()['wall_remaining'])
        if seconds <= 0:return None
        try:return human_reply.answer(self.workspace.private, eid, question=view['question'], seconds=seconds)
        except OSError:return None

    def force_pending(self, *, trigger, deadline):
        with self.lock:
            pending = policy_state(self.state).pending_ask
            fallback = policies.forced_fallback(pending, policies.TerminationReason(trigger))
            if not pending or trigger=='pause':return None
            if not fallback.can_attempt:
                row = dict(status='pending_ask', forced_fallback=True, requested=False,
                    forced_fallback_blocked=fallback.forced_fallback_blocked, fallback_trigger=trigger,
                    origin_version_id=pending.origin_version_id, execution_id=pending.execution_id,
                    agent_policy_version=self.agent_policy_version, help_count=0)
                self.workspace.publish('events/forced-fallback.json', row)
                self.state['forced_fallback']=row;self._commit('fallback_blocked')
                return row
            result = self._deliver(pending.execution_id, None, deadline, system=True,
                origin=pending.origin_version_id, decision=dict(action='request',reason=pending.reason), fallback=trigger)
            if not result.get('requested'):
                result['forced_fallback_blocked']=result.get('reason','unavailable')
                self.workspace.publish('events/forced-fallback.json', result)
                self.state['forced_fallback']=result;self._commit('fallback_blocked')
            return result

    def finish(self, reason='run_end', deadline=None):
        return self.force_pending(trigger=reason, deadline=deadline if deadline is not None else time.monotonic()+self.ledger.snapshot()['wall_remaining'])


def public_request_view(helper, eid, decision, *, origin=None, question=None):
    """Advisor packet from hash-verified public artifacts, without episode readers."""
    import base64
    import io
    import math
    from PIL import Image
    from .dev_request_view import build_request_view
    from .moment_localization import select_moments, validate_selected_moments
    metadata = helper._read_ref(helper.state['executions'][eid]['context_ref'])
    metadata['execution_id']=eid
    values = {}
    for name in ('trace.json','stdout.txt','stderr.txt','policy.py','frames.json','result.json'):
        values[name] = helper._artifact(metadata, name)[0]
    trace = json.loads(values['trace.json'] or b'[]')
    source = (values['policy.py'] or b'').decode()
    generation = int(metadata['generation_id'][1:])
    previous = 'versions/v%04d/bundle.py' % (generation-1)
    try:old = read_beneath(helper.workspace.canonical, previous, LIMIT).decode()
    except FileNotFoundError:old = helper.config.get('initial_source', '')
    diff = ''.join(difflib.unified_diff(old.splitlines(True),source.splitlines(True),fromfile='previous',tofile=eid))
    frame_rows = json.loads(values['frames.json'] or b'[]');verified=[];frame_bytes={}
    for frame in frame_rows:
        name = 'executions/'+eid+'/'+frame['filename']
        raw = read_beneath(helper.workspace.canonical, name, LIMIT, shared=shared_name(name))
        if digest(raw)!=frame['sha256']:raise ValueError('public frame digest mismatch')
        verified.append(frame)
        frame_bytes[frame['filename']] = raw
    events = [dict(row, event_id=str(row.get('event_id') or f'{eid}:{i}')) for i,row in enumerate(trace)]
    ids = [row['event_id'] for row in events if row.get('status') in ('failed','rejected','blocked')][:1]
    # Only an explicit common relative trace clock supports targeted selection.
    external = [r for r in verified if r.get('view') in ('external','overview')]
    reliable = bool(external) and all(r.get('timestamp_basis')=='relative' and
        type(r.get('trace_seconds')) in (int,float) and math.isfinite(r['trace_seconds']) for r in external)
    timing = dict(basis='relative',frame_trace_seconds=[r['trace_seconds'] for r in external],
        frame_video_seconds=[r['sim_seconds'] for r in external]) if reliable else None
    selected, localization = select_moments(events, ids, timing, available_views=['external'],
        max_frames=helper.config['advisor_proxy']['max_selected_frames'])
    validate_selected_moments(selected, localization)
    frames = [external[m['frame_index']] for m in selected] if selected else verified[:helper.config['advisor_proxy']['max_selected_frames']]
    # Match dev_agent_attention.request_view's v1 encoding exactly, after selection.
    visual = []
    for frame in frames:
        with Image.open(io.BytesIO(frame_bytes[frame['filename']])) as image:
            picture=image.convert('RGB')
            if picture.width>768:picture=picture.resize((768,max(1,round(picture.height*768/picture.width))))
            output=io.BytesIO();picture.save(output,format='JPEG',quality=80)
        visual.append(dict(frame, data_url='data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode()))
    frames = visual
    tails = 'stdout:\n'+(values['stdout.txt'] or b'').decode()[-980:]+'\nstderr:\n'+(values['stderr.txt'] or b'').decode()[-980:]
    entry = helper.state['versions'].get(origin)
    if entry: evidence = helper._read_ref(entry['evidence_ref'])
    else:evidence = helper._read_ref(helper.state['executions'][eid]['evidence_ref'])
    reasoning = 'Execution: '+eid+'\nCode:\n'+source+'\n'+tails+'\nEvidence:\n'+json.dumps(evidence,sort_keys=True)
    task = json.loads(read_beneath(helper.workspace.canonical, 'task.json', LIMIT))
    # Advisor's original public request schema and human_seconds formula stay shared.
    return build_request_view(task=task, question=question or policies.question_for_agent(decision['reason'],None),
        reasoning=reasoning, frames=frames, events=events, diff=diff,
        eval_review=None, localization=localization,
        shown_video_seconds=max((f.get('sim_seconds',0) for f in frames),default=0))
