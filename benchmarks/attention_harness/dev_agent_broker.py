"""Trusted per-cell broker. D1 counters; no service endpoints exposed to Dev.

All collaborators receive absolute monotonic deadlines and must implement cancel().
An unacknowledged timeout quarantines the cell, preserving its charge and excluding
subsequent work. The caller never retries an ambiguous moving execution.
"""
from contextlib import nullcontext
import concurrent.futures
import fcntl
import hashlib
import json
import math
import os
import re
import threading
import time
from .dev_agent_workspace import read_beneath, atomic_write, encode, REQUEST_LIMIT, SOURCE_LIMIT
from .dev_agent_diagnostics import screen_text, public_observation, project_planner_result
from .dev_agent_feedback import public_outcome
from .dev_agent_eval import feedback, unavailable

SCHEMA = 'attentionbench.dev-agent-request.v1'
OPS = {'submit', 'run', 'observe', 'find_objects', 'ik_check', 'plan_dry_run', 'ask_help', 'status'}
PROBES = {'observe', 'find_objects', 'ik_check', 'plan_dry_run'}
TIMEOUTS = dict(validation=5., observe=10., find_objects=10., ik_check=240.,
                plan_dry_run=240., run=1920., submit=1920., evaluate=900., ask_help=120.,
                # A cold probe owner builds the simulator scene first (~55 s measured).
                probe_startup=300.)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class Budget:
    def __init__(self, *, versions=10, executions=40, probes=300, wall_seconds=21600):
        self.limits = dict(versions=versions, executions=executions, probes=probes)
        self.counts = dict.fromkeys(self.limits, 0)
        self.deadline = time.monotonic() + min(wall_seconds, 21600)
        if any(type(v) is not int or not 0 <= v <= cap for v, cap in
               zip((versions, executions, probes), (10, 40, 300))):
            raise ValueError('invalid_budget')

    def charge(self, **resources):
        if time.monotonic() + .05 >= self.deadline: raise ValueError('wall_stop')
        if any(self.counts[k] + v > self.limits[k] for k, v in resources.items()):
            raise ValueError('budget_exhausted')
        for key, value in resources.items(): self.counts[key] += value


class Helper:
    def ask(self, payload, *, deadline):
        return dict(status='policy_denied', reason='help_integration_unavailable')
    def cancel(self): pass


class FakeEvaluator:
    def evaluate(self, execution, *, deadline):
        return dict(passed=False, reason='Fake evaluation', evidence=[], advice='Inspect the public trace.')
    def cancel(self): pass


class Broker:
    def __init__(self, workspace, *, executor, prober, evaluator, helper=None, budget=None,
                 agent_mode=False, timeouts=None, quota=None, on_cut=None, on_version=None,
                 on_execution=None, on_settled=None, quiesce=nullcontext):
        if agent_mode is not True: raise ValueError('agent_mode_required')
        self.workspace = workspace
        self.quota=quota; self.stop_reason=None
        self.on_cut=on_cut; self.on_version=on_version; self.on_execution=on_execution
        self.on_settled=on_settled; self.quiesce=quiesce
        self.executor, self.prober, self.evaluator = executor, prober, evaluator
        self.helper, self.budget = helper or Helper(), budget or Budget()
        self.timeouts = {**TIMEOUTS, **(timeouts or {})}
        self.lock = threading.RLock(); self.closing = threading.Event()
        self.records = {}; self.active = None; self.quarantined = False; self.failure = None
        self.histories = {k: [] for k in ('versions', 'executions', 'probes')}
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='agent-broker')
        self.lease = (workspace.private / 'broker.lock').open('a')
        try: fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException: self.lease.close(); self.pool.shutdown(); raise
        # D3 supplies durable accounting. Refuse ambiguous restart in D1.
        self.journal = workspace.private / 'journal'; self.journal.mkdir(exist_ok=True)
        if any(self.journal.iterdir()):
            if not hasattr(self.budget,'ledger'):
                self.lease.close(); self.pool.shutdown(); raise ValueError('recovery_requires_review')
            jobs={r['id']:r for r in self.budget.ledger.jobs()}
            for path in sorted(self.journal.glob('*.json')):
                record=json.loads(path.read_bytes()); self.records[path.stem]=record
                job=jobs.get('request:'+path.stem,{})
                if job.get('state')=='settled':record['response']=job['receipt']
                if record['response']['status'] in ('accepted','running','needs_review'):
                    self.quarantined=True
            for kind in self.histories:
                path=workspace.canonical/'history'/(kind+'.jsonl')
                if path.exists():self.histories[kind]=[json.loads(line) for line in path.read_bytes().splitlines()]
            workspace.published={str(p.relative_to(workspace.canonical)) for p in workspace.canonical.rglob('*') if p.is_file()}
            workspace.regenerate()
            for record in self.records.values():self._publish(record['response'])

    def status(self):
        return dict(counts=dict(self.budget.counts), limits=dict(self.budget.limits),
                    active=self.active, quarantined=self.quarantined)

    def _publish(self, response):
        rid = response['request_id']
        atomic_write(self.journal, rid + '.json', encode(self.records[rid]))
        if response['status'] in ('completed','rejected','needs_review'):
            self._regenerate_history()
            self.workspace.regenerate()
        self.workspace.publish('rpc/responses/' + rid + '.json', response)
        self.workspace.publish('rpc/status.json', self.status())
        return response

    def accept(self, request):
        # Freeze the complete acceptance cut, including source validation and
        # private copies. Background tools cannot change code between the bundle
        # and pre-submit snapshot. Reentrant quiesce also covers snapshot hooks.
        with self.quiesce():return self._accept(request)

    def _accept(self, request):
        started = time.monotonic()
        if (not isinstance(request, dict) or set(request) != {'schema', 'request_id', 'op', 'payload'}
                or request.get('schema') != SCHEMA or request.get('op') not in OPS
                or not isinstance(request.get('request_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', request['request_id'])
                or not isinstance(request.get('payload'), dict)):
            raise ValueError('invalid_request')
        data = canonical(request)
        if len(data) > REQUEST_LIMIT: raise ValueError('request_oversize')
        rid, op, payload = request['request_id'], request['op'], request['payload']
        digest = hashlib.sha256(data).hexdigest()
        with self.lock:
            if rid in self.records:
                if self.quota:
                    try:self.quota.check('broker:retry:'+op)
                    except Exception as exc:
                        self.stop_reason='quota_stop' if str(exc)=='quota_stop' else 'quota_unavailable'
                record = self.records[rid]
                if record['digest'] != digest:
                    # Do not overwrite the first request's authoritative receipt.
                    return dict(schema='attentionbench.dev-agent-response.v1', request_id=rid,
                                digest=digest, op=op, status='rejected', error='request_id_conflict')
                return self._publish(record['response'])
            response = dict(schema='attentionbench.dev-agent-response.v1', request_id=rid,
                            digest=digest, op=op, status='accepted', sequence=len(self.records)+1, charged={})
            self.records[rid] = dict(digest=digest, request=request, response=response)
            def reject(reason):
                response.update(status='rejected', error=reason)
                return self._publish(response)
            if self.quota:
                try:self.quota.check('broker:'+op)
                except Exception as exc:
                    self.stop_reason='quota_stop' if str(exc)=='quota_stop' else 'quota_unavailable'
                    return reject(self.stop_reason)
            if self.stop_reason and op!='status':return reject(self.stop_reason)
            if op == 'status':
                response.update(status='completed', result=self.status())
                return self._publish(response)
            if self.quarantined: return reject('needs_review')
            if self.active is not None: return reject('busy')
            allowed = ({'entry', 'reason'} if op in ('submit', 'run') else
                       {'question'} if op == 'ask_help' else {'names'} if op == 'find_objects' else
                       {'target', 'start_joints', 'base_offset', 'held_object', 'attachment'} if op == 'ik_check' else
                       {'action', 'start_joints', 'base_offset', 'held_object', 'attachment'} if op == 'plan_dry_run' else set())
            if set(payload) - allowed: return reject('invalid_payload')
            source = None; invalid = False; frozen = None; validation_error=None
            if hasattr(self.budget,'request_id'):self.budget.request_id=rid
            try:
                if op in ('submit', 'run'):
                    entry = payload.get('entry')
                    if entry is not None and (not isinstance(entry,str) or not entry.startswith('work/') or not entry.endswith('.py')):
                        return reject('invalid_entry')
                    try:
                        from .dev_agent_bundle import bundle
                        with self.quiesce():frozen=bundle(self.workspace.ws,entry)
                        source=frozen.script
                    except (ValueError, SyntaxError, UnicodeError, OSError) as exc:
                        invalid=True;validation_error=screen_text(str(exc))
                        response['validation_error']=validation_error
                        # Invalid submissions consume a version but never dispatch.
                        # Preserve readable authored entry as evidence, not executable code.
                        try:source=read_beneath(self.workspace.ws,entry or 'work/main.py',SOURCE_LIMIT)
                        except (ValueError,OSError):source=b''
                    if op == 'run' and invalid: return reject('invalid_source')
                    if time.monotonic() - started > self.timeouts['validation']: return reject('validation_timeout')
                    self.budget.charge(**({'versions': 1} if op == 'submit' else {}),
                                       **({'executions': 1} if not invalid else {}))
                    response['charged'] = dict(versions=int(op=='submit'), executions=int(not invalid))
                    if op == 'submit': response['version_id'] = 'v%04d' % self.budget.counts['versions']
                    if not invalid: response['execution_id'] = 'e%06d' % self.budget.counts['executions']
                elif op in PROBES:
                    self.prober.validate(op, payload)
                    self.budget.charge(probes=1)
                    response['probe_id'] = 'p%06d' % self.budget.counts['probes']
                    response['charged'] = {'probes':1}
                else: self.budget.charge()
            except (ValueError, OSError, TypeError) as exc:
                return reject(str(exc) if str(exc) in {'budget_exhausted','wall_stop'} else 'invalid_request')
            self.active = rid
            try:
                self.records[rid]['validation_error']=validation_error
                self.records[rid]['source_sha256'] = hashlib.sha256(source).hexdigest() if source is not None else None
                if source is not None:
                    atomic_write(self.workspace.private, 'sources/' + rid + '.py', source)
                if frozen:
                    record=self.records[rid]
                    record['bundle_map']=frozen.source_map
                    for name,data in frozen.sources.items():
                        atomic_write(self.workspace.private,'sources/'+rid+'/'+name,data)
                    atomic_write(self.workspace.private,'sources/'+rid+'/bundle-map.json',encode(frozen.source_map))
                if self.on_cut and op=='submit':self.on_cut('pre-submit',response)
                self._publish(response)
                self.pool.submit(self._dispatch, rid, source, invalid)
            except BaseException:
                self.active=None;self.quarantined=True
                response.update(status='needs_review',error='acceptance_failure')
                atomic_write(self.journal,rid+'.json',encode(self.records[rid]))
                try:self._publish(response)
                except (OSError,ValueError):pass
            return dict(response)

    def _bounded(self, collaborator, method, *args, timeout, quarantine=True):
        deadline = min(self.budget.deadline, time.monotonic() + timeout)
        done = threading.Event(); result = []
        def invoke():
            try: result.append((True, method(*args, deadline=deadline)))
            except BaseException as exc: result.append((False, exc))
            finally: done.set()
        threading.Thread(target=invoke, daemon=True).start()
        while not done.wait(min(.05, max(0, deadline-time.monotonic()))):
            if self.closing.is_set() or time.monotonic() >= deadline: break
        if not done.is_set():
            if quarantine:self.quarantined = True
            # Never wait indefinitely for cancellation, nor reuse this simulator.
            threading.Thread(target=collaborator.cancel, daemon=True).start()
            raise TimeoutError('collaborator_timeout after %.1f s' % timeout)
        ok, value = result[0]
        if not ok: raise value
        return value

    def _history(self, kind, row):
        self.histories[kind].append(row)
        self.workspace.publish('history/' + kind + '.jsonl',
            b''.join(canonical(r) + b'\n' for r in self.histories[kind]))
        self._regenerate_history()

    def _regenerate_history(self):
        from .dev_version_history import bounded
        for row in self.histories['versions']:
            path=self.workspace.canonical/'versions'/row['version_id']/'help.json'
            if path.is_file():row['help']=json.loads(path.read_bytes())
        for kind,rows in self.histories.items():
            self.workspace.publish('history/'+kind+'.jsonl',b''.join(canonical(r)+b'\n' for r in rows))
        entries=[dict(version_id=r['version_id'],execution_id=r.get('execution_id'),
                      outcome=r.get('outcome'),eval=r.get('eval'),help=r.get('help'),
                      source_sha256=r.get('source_sha256'),source_bytes=r.get('source_bytes')) for r in self.histories['versions']]
        self.workspace.publish('history/version_history.json',bounded(entries))

    def _execution(self, eid, source, result):
        base = 'executions/' + eid + '/'
        artifacts = {}
        for name, value in (
            ('policy.py', feedback(source.decode()).encode()),
            ('stdout.txt', feedback(result.get('stdout', '')).encode()),
            ('stderr.txt', feedback(result.get('stderr', '')).encode()),
            ('trace.json', feedback(result.get('trace', []))),
            ('state_log.json', feedback(result.get('state_log', []))),
            ('final_observation.json', public_observation(result.get('final_observation', {}))),
            ('initial_observation.json', public_observation(result.get('initial_observation', {}))),
            ('diagnostics/planner.json', [project_planner_result(d) for d in result.get('diagnostics', [])])):
            artifacts[name] = self.workspace.publish(base + name, value)
        frames=[];export_deadline=self.budget.deadline
        for row in result.get('frames',[]):
            if self.closing.is_set() or time.monotonic()>=export_deadline:raise TimeoutError('recording_export_timeout')
            data=row['data'];entry={k:v for k,v in row.items() if k!='data'}
            entry['sha256']=hashlib.sha256(data).hexdigest()
            self.workspace.publish(base+entry['filename'],data);frames.append(entry)
        artifacts['frames.json']=self.workspace.publish(base+'frames.json',frames)
        outcome = public_outcome(result.get('status'), result.get('trace', []), error=result.get('error'))
        artifacts['result.json'] = self.workspace.publish(base + 'result.json', outcome)
        self._history('executions', dict(execution_id=eid, outcome=outcome, artifacts=artifacts))
        return dict(outcome=outcome, artifacts=artifacts)

    def _dispatch(self, rid, source, invalid):
        record = self.records[rid]; response = record['response']; request = record['request']
        op, payload = request['op'], request['payload']
        try:
            with self.lock:
                if hasattr(self.budget,'claim'):self.budget.claim(rid)
                response['status'] = 'running'; self._publish(response)
            if op in PROBES:
                timeout = self.timeouts[op] + (self.timeouts['probe_startup'] if getattr(self.prober, 'cold', False) else 0)
                value = self._bounded(self.prober, self.prober.probe, op, payload, timeout=timeout)
                value = public_observation(value)
                artifact = self.workspace.publish('probes/' + response['probe_id'] + '.json', value)
                self._history('probes', dict(probe_id=response['probe_id'], artifact=artifact))
            elif op in ('submit', 'run'):
                value = dict(outcome=dict(status='invalid_submission',error=record.get('validation_error')))
                if not invalid:
                    self.prober.close()
                    eid = response['execution_id']
                    directory = self.workspace.private / 'executions' / eid
                    directory.mkdir(parents=True)
                    result = self._bounded(self.executor, self.executor.execute, source, directory,
                                           timeout=self.timeouts[op])
                    if hasattr(self.budget,'ledger'):
                        self.budget.ledger.record_execution('request:'+rid,getattr(self.executor,'execution_usage',None))
                    value = self._execution(eid, source, result)
                    if hasattr(self.helper,'capture'):self.helper.capture(self.executor)
                    if self.on_execution:self.on_execution(response,directory,result)
                    if self.on_cut and op=='submit':self.on_cut('post-exec',response)
                elif self.on_cut and op=='submit':self.on_cut('post-exec',response)
                if op == 'submit':
                    vid = response['version_id']; base = 'versions/' + vid + '/'
                    self.workspace.publish(base + 'policy.py', feedback(source.decode('utf-8', errors='replace')).encode())
                    self.workspace.publish(base + 'submission.json', dict(version_id=vid,
                        execution_id=response.get('execution_id'), source_sha256=record['source_sha256']))
                    self.workspace.publish(base+'bundle.py',feedback(source.decode('utf-8',errors='replace')).encode())
                    self.workspace.publish(base+'bundle-map.json',record.get('bundle_map',{}))
                    for path in sorted((self.workspace.private/'sources'/rid).glob('*.py')):
                        self.workspace.publish(base+'sources/'+path.name,feedback(path.read_text()).encode())
                    value.update(version_id=vid,execution_id=response.get('execution_id'))
                    if invalid:
                        verdict=dict(status='not_run_invalid_submission',passed=None)
                    else:
                        ledger=getattr(self.budget,'ledger',None);eval_id='eval:'+rid
                        if ledger:
                            ledger.reserve(eval_id,'eval',{'eval_runs':1});ledger.claim(eval_id)
                        try:
                            verdict = self._bounded(self.evaluator, self.evaluator.evaluate, value,
                                                    timeout=self.timeouts['evaluate'],quarantine=False)
                        except BaseException as exc:
                            verdict=unavailable(vid,'timeout' if isinstance(exc,TimeoutError) else type(exc).__name__)
                        if ledger:ledger.settle(eval_id,verdict,usage=getattr(self.evaluator,'usage',None))
                    value['eval'] = feedback(verdict)
                    self.workspace.publish(base + 'eval.json', value['eval'])
                    if self.on_cut:self.on_cut('post-feedback',response)
                    value['help'] = dict(status='not_requested')
                    self.workspace.publish(base + 'help.json', value['help'])
                    self._history('versions', dict(source_sha256=record['source_sha256'],source_bytes=len(source),**value))
                    # Record Eval and completion before requesting any new help.
                    # A completed run terminates here, even when Eval disagrees.
                    if self.on_version:self.on_version(response,value)
                    if self.stop_reason not in ('run_complete','run_error'):
                        value['help'] = (self._bounded(self.helper,self.helper.boundary,value,timeout=self.timeouts['ask_help'])
                            if hasattr(self.helper,'boundary') else dict(status='policy_denied'))
                        if value['help'].get('decision',{}).get('action')=='stop':self.stop_reason='help_policy_stop'
                        self.workspace.publish(base + 'help.json', value['help'])
            else:
                value = self._bounded(self.helper, self.helper.ask, payload, timeout=self.timeouts['ask_help'])
                if value.get('version_id'):
                    for row in self.histories['versions']:
                        if row['version_id']==value['version_id']:row['help']=value
            response.update(status='needs_review' if self.quarantined else 'completed', result=value)
            if self.quarantined:response['error']='timeout'
        except TimeoutError as exc:
            self.quarantined = True
            self.failure = dict(request_id=rid, op=op, error='timeout', detail=str(exc))
            atomic_write(self.workspace.private,'errors/'+rid+'.json',encode(dict(self.failure,error_class='TimeoutError')))
            response.update(status='needs_review', error='timeout')
        except BaseException as exc:
            self.quarantined = True
            self.failure = dict(request_id=rid, op=op, error='collaborator_failure', detail=type(exc).__name__+': '+str(exc))
            atomic_write(self.workspace.private,'errors/'+rid+'.json',encode(dict(error=str(exc),error_class=type(exc).__name__)))
            response.update(status='needs_review', error='collaborator_failure')
        finally:
            with self.lock:
                try:
                    if hasattr(self.budget,'settle'):self.budget.settle(rid,response)
                    self._regenerate_history()
                    self.workspace.regenerate()
                    if self.on_settled:self.on_settled(response)
                    self._publish(response)
                except BaseException as exc:
                    self.quarantined=True
                    response.update(status='needs_review',error='settlement_failure')
                    atomic_write(self.workspace.private,'settlement-error.json',encode(dict(error=str(exc))))
                finally:self.active = None

    def poll(self):
        from .dev_agent_workspace import directory
        fd = directory(self.workspace.ws, ('rpc', 'requests'))
        try: names = os.listdir(fd)
        finally: os.close(fd)
        for name in sorted(names):
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}\.json', name): continue
            try:
                raw = read_beneath(self.workspace.ws, 'rpc/requests/' + name, REQUEST_LIMIT)
                request = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))
                if request.get('request_id') + '.json' != name: continue
                response = self.accept(request)
                inbox = directory(self.workspace.ws, ('rpc','requests'))
                try: os.unlink(name,dir_fd=inbox)
                except FileNotFoundError: pass
                finally: os.close(inbox)
                if response.get('error') == 'request_id_conflict':
                    self.workspace.publish('rpc/responses/' + request['request_id'] + '.json', response)
            except (ValueError, OSError, TypeError, AttributeError):
                continue

    def serve(self, stop):
        while not stop.wait(.05): self.poll()

    def close(self):
        self.closing.set()
        self.pool.shutdown(wait=True)
        self.prober.close()
        self.lease.close()
