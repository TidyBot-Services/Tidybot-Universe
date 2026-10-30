"""P3b: real broker/export/staging/import verifiers; synthetic motion only."""
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from attention_memory_service.agent_provisional import verify
from attention_memory_service.memory_service import MemoryService
from benchmarks.attention_harness import dev_agent_memory as agent, memory_auto_import as importer
from benchmarks.attention_harness.dev_agent_attention import Helper
from benchmarks.attention_harness.dev_agent_broker import Broker, FakeEvaluator
from benchmarks.attention_harness.dev_agent_export import Exporter
from benchmarks.attention_harness.dev_agent_help_interval import interval_help, freeze_help
from benchmarks.attention_harness.dev_agent_ledger import LedgerBudget
from benchmarks.attention_harness.dev_agent_workspace import atomic_write
from benchmarks.attention_harness.tests.test_dev_agent_d1 import request
from benchmarks.attention_harness.tests.test_dev_agent_d7 import fixture, Executor, events
from benchmarks.attention_harness.tests.test_dev_agent_memory_phase1 import prepare
from benchmarks.attention_harness.tests.test_dev_agent_policy_v2_flow import Prober, SOURCE
from benchmarks.attention_harness.tests.test_memory_provisional import REJECTED, TIMEOUT, ADVICE

V2 = 'agent-policy-v2'


@pytest.fixture
def rig(fixture, monkeypatch):
    f = fixture
    prepare(f, monkeypatch)
    # This fixture originally has a one-help budget; expand its synthetic run.
    f.ledger.limits['help'] = 10
    with f.ledger.tx() as db:
        run = f.ledger._run(db); run['limits']['help'] = 10
        f.ledger._put_run(db, run)

    brokers = []
    def make(policy='agent_initiated_help', parameters=None):
        f.store.update_cell('cell', policy_id=policy, agent_policy_version=V2)
        class Moving(Executor):
            def execute(self, *args, **kwargs):
                value = super().execute(*args, **kwargs)
                public = REJECTED if self.calls == 1 else TIMEOUT
                path = self.episode / 'trace.json'; path.chmod(0o644)
                path.write_text(json.dumps(dict(status='failed', error=public['public_error'], sdk_events=public['action_trace'])))
                self.result['status'] = 'failed'
                return dict(value, status='failed', error=public['public_error'], stdout=public['public_error'])
        ex = Moving()
        provider = SimpleNamespace(cache_hit=False)
        def ask(*a, **kw):
            return dict(status='completed', kind='hint', feedback=ADVICE['content'],
                        cache_hit=provider.cache_hit, usage={})
        def view(*a, **kw):
            return dict(question=kw.get('question') or 'How?', reasoning='', diff='', steps=[],
                eval_review=dict(passed=False, reason='', reference_suggestion=''), frames=[],
                shown_video_seconds=0, task='Move cup', memory=[])
        helper = Helper(f.ws, f.ledger, policy=policy, parameters=parameters, config=dict(advisor_proxy={}),
                        view_builder=view, ask_provider=ask, agent_policy_version=V2)
        exporter = Exporter(f.store, 'cell', f.ws, f.config, f.ledger)
        def capture(response, value):
            exporter.capture(response, dict(version=int(response['version_id'][1:]), valid=True,
                execution_id=response['execution_id'], eval_verdict='fail'), ex)
        def export(response):
            record = exporter.settle(response)
            if record:
                agent.stage_provisional(f.store, 'cell', record['version'], f.root/f"v{record['version']}", f.ws, f.ledger)
        b = Broker(f.ws, executor=ex, prober=Prober(), evaluator=FakeEvaluator(), helper=helper,
            budget=LedgerBudget(f.ledger), agent_mode=True, agent_policy_version=V2,
            on_version=capture, on_settled=export)
        brokers.append(b)
        counter = 0
        def call(op, payload=None):
            nonlocal counter
            counter += 1
            atomic_write(f.ws.ws, 'work/main.py', SOURCE+f'# change {counter}\n'.encode())
            rid = f'r{counter}'
            b.accept(request(rid, op, payload))
            until = time.monotonic()+15
            while b.active is not None and time.monotonic()<until:
                time.sleep(.005)
            assert b.active is None, 'broker did not settle'
            response = b.records[rid]['response']
            assert response['status'] == 'completed', (response, b.failure)
            return response
        f.helper, f.exporter, f.call, f.provider, f.executor, f.broker = helper, exporter, call, provider, ex, b
        return f
    yield make
    for b in brokers:
        b.close()


def proposal(f):
    queued = [e for e in events(f, 'planner_memory_offline_queued') if e['kind'] == 'provisional']
    assert len(queued) == 1, events(f, 'planner_memory_provisional_skipped')
    return json.loads(Path(queued[0]['queue']['uri']).read_bytes())


@pytest.mark.parametrize('mode', ['manual', 'run', 'forced', 'fallback', 'random', 'trace', 'full_trace'])
def test_v2_stages_and_real_service_accepts(rig, tmp_path, mode):
    policy, parameters = {
        'manual': ('agent_initiated_help', None), 'run': ('agent_initiated_help', None),
        'forced': ('reactive_help', None),
        'fallback': ('reactive_help', None),
        'random': ('budget_matched_random_escalation', dict(target_request_count=1,total_failure_slots=1,seed=101)),
        'trace': ('trace_aware_hint_only', dict(m=1,n=1)),
        'full_trace': ('full_trace_aware_attention_planner', dict(m=1,n=1)),
    }[mode]
    f = rig(policy, parameters)
    f.call('submit')
    if mode == 'run':
        f.call('run')
    if mode in ('manual', 'run', 'forced'):
        answer = f.call('ask_help', {'question':'How to lift?'})['result']
        assert answer['delivered']
    if mode == 'fallback':
        answer = f.helper.force_pending(trigger='run_end', deadline=time.monotonic()+10)
        assert answer['delivered'] and answer['forced_fallback']
    f.call('submit')
    value = proposal(f)
    payload = importer.provisional_payload(value, 'test')
    evidence = importer.agent_evidence(value)
    verify(payload['origin'], payload['guidance'], evidence)
    sealed = json.loads(evidence['help']['help_job'])
    frozen = json.loads(evidence['refs']['help'])
    assert frozen['help_execution_id'] == ('e000002' if mode == 'run' else 'e000001')
    assert frozen['execution_help'] == sealed['execution_job']['receipt']
    if mode == 'run':
        assert frozen['execution_help']['version_id'] is None
    delivery = json.loads(evidence['help']['delivery'])
    if mode in ('fallback', 'random', 'trace', 'full_trace'):
        assert delivery == sealed['receipt'] and delivery['system_injected'] is True
    else:
        assert delivery['execution_delivery']['result'] == frozen['execution_help']
        assert delivery['result'] == frozen
    assert len(list(importer.pending(f.store.path))) >= 1
    service = MemoryService(tmp_path/'svc.sqlite')
    counts = importer.run([f.store.path], client=service, namespace='test', out=lambda *a:None)
    assert counts['created'] == 1 and counts['refused'] == counts['deferred'] == 0, counts
    if mode == 'trace':
        assert not (f.ws.canonical/'memory').exists()
        assert not (f.root/'v2/memory-input.json').exists()
        view = json.loads(evidence['help']['request_view'])
        assert view['memory'] == []


def test_v2_multiple_and_zero_help(rig):
    f = rig()
    f.call('submit')
    f.call('submit')
    assert agent.stage_provisional(f.store,'cell',2,f.root/'v2',f.ws,f.ledger) is None
    assert not events(f,'planner_memory_provisional_skipped')
    f.call('ask_help',{'question':'Help first?'})
    f.call('run')
    f.call('ask_help',{'question':'Help second?'})
    f.call('submit')
    assert agent.stage_provisional(f.store,'cell',3,f.root/'v3',f.ws,f.ledger) is None
    assert events(f,'planner_memory_provisional_skipped')[-1]['reason'] == 'multiple_helps_in_interval'
    assert f.store.cell('cell')['agent_memory_staging_failure'] == dict(version=3,reason='multiple_helps_in_interval')
    from benchmarks.attention_harness.dev_agent_view import projection
    view = projection(f.store, f.store.cell('cell'))
    assert any(e['source'] == 'Memory staging' and e['error']['reason'] == 'multiple_helps_in_interval'
               for e in view['agent']['errors'])
    assert not events(f,'planner_memory_offline_queued')


@pytest.mark.parametrize('mode', ['cache', 'before_origin', 'after_help_version'])
def test_v2_excludes_ineligible_or_outside_help(rig, mode):
    f = rig()
    if mode == 'before_origin':
        f.call('run'); f.call('ask_help',{'question':'Practice?'})
    f.call('submit')
    if mode == 'cache':
        f.provider.cache_hit = True
        f.call('ask_help',{'question':'Cached?'})
    f.call('submit')
    if mode == 'after_help_version':
        f.call('ask_help',{'question':'Too late?'})
    assert interval_help(f.ledger.jobs(),2) == []
    assert agent.stage_provisional(f.store,'cell',2,f.root/'v2',f.ws,f.ledger) is None
    assert not events(f,'planner_memory_offline_queued')


def test_v2_frozen_export_survives_later_help_and_replay(rig):
    f = rig(); f.call('submit'); f.call('ask_help',{'question':'Help?'})
    response = f.call('submit')
    before = {p.name:p.read_bytes() for p in (f.root/'v2').iterdir() if p.is_file()}
    f.call('ask_help',{'question':'Later help?'})
    f.exporter.settle(response)
    assert {p.name:p.read_bytes() for p in (f.root/'v2').iterdir() if p.is_file()} == before
    assert agent.stage_provisional(f.store,'cell',2,f.root/'v2',f.ws,f.ledger)


def test_v2_importer_rechecks_actual_execution_job(rig):
    f = rig(); f.call('submit'); f.call('run'); f.call('ask_help',{'question':'Help?'})
    f.call('submit'); proposal(f)
    assert list(importer.pending(f.store.path))
    with f.ledger.tx() as db:
        row = next(j for j in f.ledger._jobs(db) if j['id'].startswith('help:e'))
        row['receipt']['content'] = 'Changed guidance'
        f.ledger._put_job(db,row['id'],'settled',row)
    with pytest.raises(importer.Refused,match='digest drift'):
        list(importer.pending(f.store.path))


@pytest.mark.parametrize('policy,parameters',[
    ('trace_aware_hint_only',dict(m=1,n=1)),('full_trace_aware_attention_planner',dict(m=1,n=1))])
def test_actual_v2_loop_stages_once_per_helped_version(rig, tmp_path,policy,parameters):
    from benchmarks.attention_harness.dev_agent_loop import drive
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import fake_session
    f = rig(policy,parameters); f.broker.close()
    class NoMemory:
        def __getattr__(self, name):
            raise AssertionError('hint_only accessed Memory: '+name)
    session = fake_session(f.ws, ["tool('submit')", "tool('submit')", 'pass'])
    result = drive(f.ws, session, f.ledger, executor=f.executor, prober=Prober(),
        helper=f.helper, memory=NoMemory() if policy=='trace_aware_hint_only' else None, exporter=f.exporter,
        completion_reader=lambda *a:False, agent_policy_version=V2, policy=policy)
    assert not result.get('export_errors'), result
    assert not (f.ws.canonical/'memory').exists()
    value = proposal(f)
    assert agent.stage_provisional(f.store,'cell',2,f.root/'v2',f.ws,f.ledger)
    assert agent.stage_provisional(f.store,'cell',2,f.root/'v2',f.ws,f.ledger)
    assert proposal(f)==value
    payload = importer.provisional_payload(value, 'test')
    verify(payload['origin'],payload['guidance'],payload['agent_evidence'])
    counts = importer.run([f.store.path], client=MemoryService(tmp_path/'hint-svc.sqlite'),
                          namespace='test', out=lambda *a:None)
    assert counts['created'] == 1 and counts['refused'] == 0


def test_interval_filters_and_equal_clock_ticks(rig):
    from copy import deepcopy
    f = rig(); f.call('submit'); f.call('ask_help',{'question':'Help?'}); f.call('submit')
    jobs = f.ledger.jobs()
    for j in jobs:
        for field in ('reserved_at','claimed_at','settled_at'):
            j[field] = 1000.
    assert len(interval_help(jobs,2)) == 1
    for change in (dict(provider='cache'), dict(delivered=False), dict(status='unavailable'),
                   dict(rejected=True), dict(cache_hit=True)):
        changed = deepcopy(jobs)
        next(j for j in changed if j['kind']=='help')['receipt'].update(change)
        assert interval_help(changed,2) == []
    raw = freeze_help(f.ws, f.ledger.jobs(),2)
    path = f.ws.canonical/'executions/e000001/help.json'; path.unlink()
    assert freeze_help(f.ws, f.ledger.jobs(),2) == raw
