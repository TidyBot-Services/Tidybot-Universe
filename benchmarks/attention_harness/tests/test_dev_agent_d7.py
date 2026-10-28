"""D7: synthetic grants/episodes only; no model, service or simulator starts."""
import copy
import hashlib
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_planner_memory as shared
from benchmarks.attention_harness import dev_agent_attention as attention
from benchmarks.attention_harness import dev_v2_loop, dev_v2_memory_import, memory_auto_import
from benchmarks.attention_harness.dev_agent_memory import Memory, MemoryRevoked
from benchmarks.attention_harness.dev_agent_export import Exporter
from benchmarks.attention_harness.dev_agent_workspace import Workspace, atomic_write
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger, LedgerBudget
from benchmarks.attention_harness.dev_agent_broker import Broker, FakeEvaluator
from benchmarks.attention_harness.dev_agent_adapters import FakeProber
from benchmarks.attention_harness.dev_agent_loop import drive
from benchmarks.attention_harness.dev_agent_diagnostics import FORBIDDEN
from benchmarks.attention_harness.dev_help import EMPTY_MEMORY, POLICY_IDS
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_v2_loop import immutable
from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG, SOURCE, fake_session
from benchmarks.attention_harness.tests.test_dev_agent_d1 import request, settled
from benchmarks.attention_harness.tests.test_memory_allmodes import Gateway
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity

SETTINGS = dict(store_id='synthetic', namespace='test-only', base_url='http://127.0.0.1:1')


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    store = DevV2Store(tmp_path / 'ledger.sqlite3')
    root = tmp_path / 'cell'; root.mkdir()
    workspace = Workspace(root / 'agent').prepare({'task_prompt': 'Move safely.'}, CONFIG)
    atomic_write(workspace.ws, 'work/main.py', SOURCE)
    config = dict(CONFIG, seed=102, task_id='fake', robot='fake', dev_version_history=True)
    lock = immutable(tmp_path / 'lock.json', {})
    manifest = immutable(tmp_path / 'manifest.json', dict(identity_lock=dict(path=lock['uri'], sha256=lock['sha256']),
        dev_mode='agent', memory_authority=SETTINGS))
    with store.tx() as db:
        store.put(db, 'experiments', 'exp', dict(manifest=manifest,selection={}))
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)', ('cell', 'exp', json.dumps(dict(
            key='cell', experiment_id='exp', dev_mode='agent', policy_id='autonomous',
            artifact_dir=str(root), memory_authority=SETTINGS, mode='interactive'))))
    monkeypatch.setattr(store, 'start_cell_clock', lambda key: None)
    context = dict(task_id='fake', benchmark='AttentionBench', suite='robocasa365', robot='fake',
                   controller='fake', perception_mode='sim_gt', namespace='test-only', seed=102)
    gateway = Gateway.__new__(Gateway)
    gateway.context, gateway.rows, gateway.levels, gateway.grants = context, [], [], {}
    gateway.store_id = lambda: SETTINGS['store_id']
    for i, status in enumerate(('trusted', 'provisional')):
        gateway.rows.append(dict(memory_id=f'n{i}', version=1, guidance=f'Lift carefully {i}',
            status=status, match_level='same_scene', trusted_here=status == 'trusted',
            source_seed=102, applicability=context, created_at=i))
    monkeypatch.setattr(shared, 'context', lambda *a, **kw: context)
    monkeypatch.setattr(shared, 'memory_scope', lambda *a: dict(level='scene'))
    monkeypatch.setattr(shared, 'client_for', lambda settings: gateway)
    memory = Memory(store, 'cell', workspace, config, SOURCE.decode())
    ledger = AgentLedger(store.path, 'cell')
    return SimpleNamespace(store=store, root=root, ws=workspace, config=config, gateway=gateway,
                           memory=memory, ledger=ledger, context=context)


def events(f, kind):
    with f.store.tx() as db:
        return [json.loads(raw) for raw, in db.execute('SELECT payload FROM v2_events WHERE kind=?', (kind,))]


def test_generation_binding_and_cross_generation_reuse(fixture):
    f = fixture
    f.memory.start()
    assert all(v == 0 for v in f.ledger.snapshot()['used'].values())
    packet = copy.deepcopy(f.memory.packet)
    assert all(e['development_id'] == 'cell:v1' and e['source_policy_sha256'] == hashlib.sha256(SOURCE).hexdigest()
               for e in packet['entries'])
    next_source = SOURCE.decode() + '\n# formal v1\n'
    f.memory.open(2, next_source)
    assert all(e['development_id'] == 'cell:v2' and e['source_policy_sha256'] == hashlib.sha256(next_source.encode()).hexdigest()
               for e in f.memory.packet['entries'])
    f.memory.packet = packet
    with pytest.raises(PermissionError): f.memory.recheck()
    # Exercise the unchanged loader's disk reuse check too.
    with pytest.raises(PermissionError, match='different generation'):
        shared.load_memory(f.store, 'cell', 2, next_source, f.config, SETTINGS, f.ws.private / 'memory/v1', True)


def test_delivery_is_bounded_screened_and_private(fixture):
    f = fixture
    f.memory.start()
    value = json.loads((f.ws.ws / 'memory/v1.json').read_bytes())
    assert value == dev_v2_loop.prompt_memory_view(dict(memory=f.memory.packet['entries']))
    assert value['memory'][0]['content'] == 'Lift carefully 0'
    assert any('Unvalidated:' in e.get('title', '') for e in value['memory'])
    assert events(f, 'planner_memory_unvalidated_used')
    public = json.dumps(value)
    assert not FORBIDDEN.search(public)
    for term in ('grant_id', 'development_id', 'source_policy_sha256', 'trusted_here', 'evidence_refs', 'authority'):
        assert term not in public
    # get_dev_use can return prose containing fields prohibited specifically by
    # the agent boundary; the unchanged shared loader still accepts this term.
    f.gateway.rows[0]['guidance'] = 'object_in_sink'
    f.memory.open(2, SOURCE.decode())
    assert json.loads((f.ws.ws / 'memory/v2.json').read_bytes())['memory'][0]['content'] == '[withheld]'


@pytest.mark.parametrize('layered', [False, True])
@pytest.mark.parametrize('policy', POLICY_IDS)
def test_decide_memory_parity(fixture, monkeypatch, layered, policy):
    f = fixture
    f.memory.start(); f.memory.shared = layered
    default = dict(EMPTY_MEMORY, visibility='frozen default')
    f.memory.default = default
    helper = attention.Helper(f.ws, f.ledger, policy=policy, memory=f.memory)
    captured = []
    def decide(*args, **kw):
        captured.append(copy.deepcopy(kw['memory']))
        return dict(action='continue')
    monkeypatch.setattr(attention, 'decide', decide)
    for n, passed in enumerate((False, False, True, None), 1):
        f.ws.publish(f'executions/e{n}/trace.json', [])
        helper.boundary(dict(version_id=f'v{n:04d}', execution_id=f'e{n}',
            eval=dict(passed=passed), outcome=dict(status='completed')), deadline=time.monotonic() + 5)
    expected = f.memory.packet if not layered or policy == 'full_trace_aware_attention_planner' else default
    assert captured == [expected] * 4


@pytest.mark.parametrize('layered', [False, True])
@pytest.mark.parametrize('price', [0, 1])
def test_retrieve_memory_is_private_and_cache_accounting(fixture, layered, price):
    f = fixture; f.memory.start(); f.memory.shared = layered
    f.memory.help_pricing={'cache':dict(count=price,credits=price)}
    helper = attention.Helper(f.ws, f.ledger, policy='full_trace_aware_attention_planner', memory=f.memory)
    f.ws.publish('executions/e1/trace.json', [])
    value = helper.boundary(dict(version_id='v0001', execution_id='e1', eval=dict(passed=True),
        outcome=dict(status='failed')), deadline=time.monotonic() + 5)
    assert value['decision']['action'] == 'retrieve_memory'
    assert 'grant_id' not in json.dumps(value) and 'trusted_here' not in json.dumps(value)
    cache = [j for j in f.store.cell('cell')['jobs'] if j['kind'] == 'cache']
    assert len(cache) == (0 if layered else 1)
    assert f.ledger.snapshot()['used']['help'] == (0 if layered else price)
    if cache: assert cache[0]['actual'] == dict(help_count=price,help_credits=price,help_wait_seconds=0)


class Executor:
    """A formal runner episode, deliberately separate from public workspace copies."""
    def __init__(self): self.calls = 0
    def execute(self, source, directory, *, deadline):
        self.calls += 1
        self.episode = directory / 'episode'; self.episode.mkdir()
        trace = dict(status='completed', sdk_events=[dict(source='base', operation='move_delta',
            status='completed', arguments=dict(args=[0, 0, .1], kwargs={}))])
        trace_ref = immutable(self.episode / 'trace.json', trace)
        for label in ('initial', 'final'):
            immutable(self.episode / f'{label}_public_observation.json', dict(robot={'robot0_x': [0.]}, perception=dict(values={})))
        immutable(self.episode / 'scene-condition.json', dict(matched=True))
        self.execution_usage = dict(sdk_calls=1, control_steps=2, wall_seconds=.5)
        self.result = dict(schema_version='attentionbench.formal-runner-result.v1', status='completed',
            artifact_dir=str(self.episode), artifacts=dict(trace=trace_ref))
        return dict(status='completed', stdout='', trace=[], diagnostics=[])
    def cancel(self): pass


def test_revoked_request_does_not_move_or_stop_probes(fixture):
    f = fixture; f.memory.start(); ex = Executor()
    b = Broker(f.ws, executor=ex, prober=FakeProber(), evaluator=FakeEvaluator(),
               agent_mode=True, budget=LedgerBudget(f.ledger), memory=f.memory)
    try:
        rows = f.gateway.rows; f.gateway.rows = []
        answer = b.accept(request('revoked', op='run', payload=dict(entry='work/main.py')))
        assert answer['status'] == 'rejected' and answer['error'] == 'memory_revoked'
        assert ex.calls == 0 and f.ledger.snapshot()['used']['moving_executions'] == 0
        b.accept(request('probe')); assert settled(b, 'probe')['status'] == 'completed'
        f.gateway.rows = rows
        b.accept(request('moving', op='run', payload=dict(entry='work/main.py')))
        assert settled(b, 'moving')['status'] == 'completed' and ex.calls == 1
        assert not b.quarantined
    finally: b.close()


def test_post_execution_recheck_rejects_and_continues(fixture, monkeypatch):
    f = fixture; f.memory.start(); ex = Executor(); rows = f.gateway.rows
    original = ex.execute
    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        f.gateway.rows = []
        return result
    monkeypatch.setattr(ex, 'execute', revoke)
    b = Broker(f.ws, executor=ex, prober=FakeProber(), evaluator=FakeEvaluator(),
               agent_mode=True, budget=LedgerBudget(f.ledger), memory=f.memory)
    try:
        b.accept(request('moving', op='run', payload=dict(entry='work/main.py')))
        until = time.monotonic() + 5
        while b.active is not None and time.monotonic() < until: time.sleep(.01)
        answer = b.records['moving']['response']
        assert answer['status'] == 'rejected' and answer['result']['reason'] == 'memory_revoked'
        assert ex.calls == 1 and not b.quarantined
        b.accept(request('probe')); assert settled(b, 'probe')['status'] == 'completed'
        f.gateway.rows = rows
        monkeypatch.setattr(ex, 'execute', original)
        b.accept(request('again', op='run', payload=dict(entry='work/main.py')))
        assert settled(b, 'again')['status'] == 'completed' and ex.calls == 2
    finally: b.close()


def test_revocation_during_snapshot_cannot_dispatch(fixture):
    f=fixture;f.memory.start();ex=Executor()
    def cut(kind,response):
        if kind=='pre-submit':f.gateway.rows=[]
    b=Broker(f.ws,executor=ex,prober=FakeProber(),evaluator=FakeEvaluator(),agent_mode=True,
             budget=LedgerBudget(f.ledger),memory=f.memory,on_cut=cut)
    try:
        b.accept(request('race',op='submit',payload=dict(entry='work/main.py')))
        until=time.monotonic()+5
        while b.active is not None and time.monotonic()<until:time.sleep(.01)
        answer=b.records['race']['response']
        assert answer['status']=='rejected' and answer['error']=='memory_revoked'
        assert answer['execution_dispatched'] is False and ex.calls==0
        job=next(j for j in f.ledger.jobs() if j['id']=='request:race')
        assert job['execution_usage']==dict(sdk_calls=0,control_steps=0,wall_seconds=0)
        assert not b.quarantined
        b.accept(request('probe'));assert settled(b,'probe')['status']=='completed'
    finally:b.close()


def test_run_notes_merge_and_generation_resume(fixture):
    f = fixture
    from benchmarks.attention_harness import memory_run_notes
    note = dict(note_id='run-note', cell='cell', help_version=1, title='Lift first', content='Lift carefully',
                failure_signature={}, unvalidated=True)
    ref = immutable(f.root / 'note.json', note)
    with f.store.tx() as db:
        f.store.event(db, 'planner_memory_run_note_created', dict(cell='cell', version=1,
            note_id=note['note_id'], help_version=1, note=ref))
    f.store.update_cell('cell', run_notes=True)
    memory = Memory(f.store, 'cell', f.ws, f.config, SOURCE.decode())
    memory.open(2, SOURCE.decode())
    entries = memory_run_notes.merge(memory.packet['entries'], memory_run_notes.delivered(f.ws.private / 'memory/v2'))
    assert json.loads((f.ws.ws / 'memory/v2.json').read_bytes()) == dev_v2_loop.prompt_memory_view(dict(memory=entries))
    assert events(f, 'planner_memory_run_notes_delivered')
    source = immutable(f.root / 'formal.py', SOURCE.decode())
    f.store.version('cell', dict(version=1, source=source, formal_eligible=False))
    resumed = Memory(f.store, 'cell', f.ws, f.config, SOURCE.decode())
    resumed.start()
    assert resumed.number == 2 and resumed.packet == memory.packet
    assert len(events(f, 'planner_memory_retrieved')) == 1
    # Reusing a persisted generation against changed preceding code is refused.
    with pytest.raises(PermissionError): resumed.open(2, SOURCE.decode() + '# changed\n')


def test_auto_import_requires_exported_settled_formal_version(fixture):
    f = fixture
    with f.store.tx() as db:
        f.store.event(db, 'planner_memory_offline_queued', dict(cell='cell', version=1,
            kind='provisional_outcome', queue={}))
    for marker in (None, 'v1'):
        f.store.update_cell('cell', agent_export=marker)
        with pytest.raises(memory_auto_import.Refused, match='unsupported'):
            list(memory_auto_import.pending(f.store.path))
    # The cell-wide flag cannot admit a version whose job never settled.
    with f.store.tx() as db:
        db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', ('job', 'cell', 1, 'execute', 'claimed',
            json.dumps(dict(version=1, kind='execute', receipt=dict(bundle={'uri': 'unused'})))))
    with pytest.raises(memory_auto_import.Refused, match='unsupported'):
        list(memory_auto_import.pending(f.store.path))


def test_drive_generations_export_and_resume_messages(fixture):
    f = fixture; ex = Executor(); exporter = Exporter(f.store, 'cell', f.ws, f.config, f.ledger)
    key_path=f.root/'synthetic-importer-key';key_path.write_text('synthetic fixture')
    launches=[]
    def popen(*args,**kwargs):launches.append(args);return 'fake-process'
    environment={memory_auto_import.ENV:str(key_path)}
    assert memory_auto_import.launch_after_run(f.store.path.parent,environ=environment,popen=popen) is None
    session = fake_session(f.ws, ["tool('observe');tool('run','--no-eval');tool('submit')",
                                 "tool('run','--no-eval');tool('submit')", 'pass'])
    result = drive(f.ws, session, f.ledger, executor=ex, prober=FakeProber(),
        memory=f.memory, exporter=exporter, completion_reader=lambda *a: False)
    assert result['reason'] == 'agent_idle'
    packets = [json.loads((f.ws.private / f'memory/v{n}/memory-input.json').read_bytes()) for n in (1, 2, 3)]
    rows = f.store.cell('cell')['versions']
    for n, packet in enumerate(packets, 1):
        digest = hashlib.sha256(SOURCE).hexdigest() if n == 1 else rows[n - 2]['source']['sha256']
        assert all(e['development_id'] == f'cell:v{n}' and e['source_policy_sha256'] == digest for e in packet['entries'])
    assert len(events(f, 'planner_memory_retrieved')) == 3
    assert len(f.store.cell('cell')['jobs']) == 2
    assert f.ledger.snapshot()['used']['moving_executions'] == 4
    assert 'Memory for v1: memory/v1.json' in session.messages[0]
    assert 'Memory for v2: memory/v2.json' in session.messages[1]
    public='\n'.join(session.messages)
    for term in ('native_success','object_in_sink','native_result','check_success','reward','grant_id','/home/','/tmp/'):
        assert term not in public
    for n, row in enumerate(rows, 1):
        assert json.loads((f.root / f'v{n}/version.json').read_bytes()) == row
        assert row['agent']['execution_id'] in ('e000002', 'e000004')
    assert not (f.root / 'v3').exists()
    from benchmarks.attention_harness.dev_agent_view import projection
    visible=projection(f.store,f.store.cell('cell'))
    assert [v['native_success'] for v in visible['versions']]==[False,False]
    assert 'native_result' not in json.dumps(visible)
    assert list(memory_auto_import.pending(f.store.path))
    assert memory_auto_import.launch_after_run(f.store.path.parent,environ=environment,popen=popen)=='fake-process'
    assert len(launches)==1
    imports=[]
    def record(**payload):imports.append(payload);return dict(memory_id='synthetic')
    client=SimpleNamespace(record_provisional_outcome=record)
    assert memory_auto_import.run([f.store.path],client=client,namespace='test-only',out=lambda line:None)['outcomes']==4
    assert memory_auto_import.run([f.store.path],client=client,namespace='test-only',out=lambda line:None)['outcomes']==0
    assert {r['development_id'] for r in imports}=={'cell:v1','cell:v2'}


def test_export_failure_is_recorded_not_fatal(fixture):
    # r6 policy: export is a per-version step; the run outcome stands and the
    # failure is surfaced on the cell/result (the importer refuses that version).
    f=fixture
    class BrokenExport:
        def capture(self,*a):pass
        def settle(self,*a):raise ValueError('synthetic export failure')
    session=fake_session(f.ws,["tool('submit');time.sleep(20)"])
    result=drive(f.ws,session,f.ledger,executor=Executor(),prober=FakeProber(),
        exporter=BrokenExport(),completion_reader=lambda *a:True)
    assert result['reason']=='native_success' and 'fatal_cause' not in result
    assert any('synthetic export failure' in e['error'] for e in result['export_errors'])


@pytest.mark.parametrize('history', [False, True])
@pytest.mark.parametrize('validation', [False, True])
def test_export_projection_import_and_validation_gate(fixture, monkeypatch, history, validation):
    f = fixture; f.config['dev_version_history'] = history
    f.gateway.rows = f.gateway.rows[:1]
    f.memory.start(); ex = Executor()
    directory = f.ws.private / 'executions/e000001'; directory.mkdir(parents=True)
    ex.execute(SOURCE, directory, deadline=time.monotonic() + 5)
    atomic_write(f.ws.private, 'sources/r.py', b'from robot_sdk import left_arm\nleft_arm.move_delta(0,0,.01)\n')
    f.ws.publish('versions/v0001/help.json', dict(status='not_requested'))
    f.ws.publish('executions/e000001/trace.json', [{'tampered': True}])
    f.ledger.reserve('request:r', 'request', dict(formal_versions=1, moving_executions=1), execution=True)
    f.ledger.claim('request:r'); f.ledger.record_execution('request:r', ex.execution_usage)
    response = dict(op='submit', request_id='r', version_id='v0001', execution_id='e000001', status='completed')
    f.ledger.settle('request:r', response)
    exporter = Exporter(f.store, 'cell', f.ws, f.config, f.ledger, memory_validation=validation)
    exporter.capture(response, dict(version=1, valid=True, eval_verdict='fail', agreement=False), ex)
    row = exporter.settle(response, f.memory)
    f.ws.publish('versions/v0001/help.json',dict(status='delivered',content='A later manual hint'))
    exporter.settle(response, f.memory)  # immutable replay, no double accounting
    bundle = json.loads((f.root / 'v1/bundle.json').read_bytes())
    assert bundle['channel'] == 'dev_agent'
    assert bundle['public_base_yaw_visible'] is (history and not validation)
    assert json.loads((f.root / 'v1/version.json').read_bytes()) == f.store.cell('cell')['versions'][0] == row
    assert f.ledger.snapshot()['used']['moving_executions'] == 1
    projected = json.loads((f.root / 'v1/public-feedback.json').read_bytes())
    expected = dev_v2_loop.public_feedback(json.loads((ex.episode / 'trace.json').read_bytes()), base_yaw_visible=history and not validation)
    expected.update({label + '_observation': dict(robot={'robot0_x': [0.]}, objects={}) for label in ('initial', 'final')})
    assert projected == expected
    assert row['valid'] is True and row['config_sha256'] == exporter.config_ref['sha256']
    monkeypatch.setattr(shared, 'sdk_identity', lambda *a: {})
    from benchmarks.attention_harness import dev_planner, memory_source_drift
    monkeypatch.setattr(memory_source_drift, 'current_identity', lambda config, lock, out: (lock, None))
    monkeypatch.setattr(f.store, 'planner_snapshot', lambda key: {}, raising=False)
    dev_v2_memory_import.prepare(f.store, 'cell', 1, 'test-only', f.root / 'import', f.root.parent)
    descriptor = json.loads((f.root / 'import/descriptor.json').read_bytes())
    assert descriptor['execute_job_id'] == 'cell:1:execute'
    assert descriptor['agent_execution'] == dict(request_id='request:r')
    assert descriptor['permission_scope']['scope']['operations'] == ['left_arm.move_delta']
    assert descriptor['permission_scope']['action'] == 'agent_dev_tier_motion'
    assert 'actor' not in descriptor['permission_scope']
    assert 'approval_id' not in descriptor
    assert set(descriptor['public_observations']) == {'initial', 'final'}
    with pytest.raises(Exception, match='unsupported'):
        dev_v2_memory_import.prepare(f.store, 'cell', 1, 'test-only', f.root / 'origin', f.root.parent, source={"origin": True})
    immutable(f.root / 'v1/memory-validation-input.json', {})
    with pytest.raises(Exception, match='unsupported'):
        dev_v2_memory_import.prepare(f.store, 'cell', 1, 'test-only', f.root / 'validation', f.root.parent)


def test_uniform_probe_and_motion_shapes(monkeypatch):
    from benchmarks.attention_harness.tests.test_dev_agent_d5 import owner
    from openarm365_dev_v2.agent_probe import probe
    from openarm365_dev_v2.agent_sdk import build_sdk
    from openarm365_dev.owner import build_sdk as one_call
    env, executor, planner, *_ = owner(monkeypatch)
    observation = {'robot0_left_joint_pos': [0.] * 7, 'robot0_base_pos': [0.] * 3}
    perception = dict(values={'cup_pos': [1., 2., 3.]})
    value = probe(env, executor, planner, lambda: dict(robot=observation, perception=perception), dict(op='observe', payload={}))
    assert value['observation'] == observation and value['perception'] == perception
    assert 'robot' not in value['observation']
    fake = SimpleNamespace(rpc=lambda group, *a, **kw: observation if group == 'sensors' else None)
    events = []; sdk = build_sdk(fake, events.append)
    assert sdk.sensors.get_observation() == value['observation']
    assert sdk.left_arm.move_delta(0, 0, .01) == dict(ok=True, rejected=False)
    # The old SDK module is byte-identical to the committed baseline.
    path = Path('openarm365_dev/owner.py')
    assert path.read_bytes() == subprocess.check_output(['git', 'show', '0c395d4:' + str(path)])
    assert one_call(fake, lambda e: None).left_arm.move_delta(0, 0, .01) is None


def test_export_does_not_follow_dev_replacement_links(fixture,monkeypatch):
    f=fixture
    exporter=Exporter(f.store,'cell',f.ws,f.config,f.ledger)
    atomic_write(f.ws.private,'sources/r.py',SOURCE)
    f.ws.publish('versions/v0001/help.json',dict(status='not_requested'))
    trap=f.root/'trap.json';trap.write_text('{"private":"must not read"}')
    original=f.ws.publish
    def publish(name,value):
        result=original(name,value)
        if name=='versions/v1/help.json':
            target=f.ws.ws/name;target.unlink();target.symlink_to(trap)
        return result
    monkeypatch.setattr(f.ws,'publish',publish)
    response=dict(op='submit',request_id='r',version_id='v0001',execution_dispatched=False)
    exporter.capture(response,dict(version=1),SimpleNamespace())
    row=exporter.settle(response)
    assert row['help']['uri']==str(f.ws.ws/'versions/v1/help.json')
    assert row['help']['sha256']==hashlib.sha256((f.ws.canonical/'versions/v1/help.json').read_bytes()).hexdigest()
    with pytest.raises(Exception,match='digest'):
        dev_v2_loop.checked(row['help'])


def test_config_accepts_memory_but_refuses_controls():
    from benchmarks.attention_harness.dev_agent_config import normalize
    from benchmarks.attention_harness.tests.test_dev_agent_configuration import SELECTION
    normalize(dict(SELECTION, memory_authority=SETTINGS, memory_family=True, run_notes=True))
    for field in ('validation_experiment', 'positive_control', 'planner_features', 'fork'):
        with pytest.raises(ValueError): normalize(dict(SELECTION, **{field: True}))


def test_creation_freezes_memory_selection(tmp_path,offline_dev_identity):
    from benchmarks.attention_harness import dev_experiment
    from benchmarks.attention_harness.tests.test_dev_agent_configuration import SELECTION
    store=DevV2Store(tmp_path/'create.sqlite')
    selection=dict(SELECTION,memory_authority=SETTINGS,memory_family=True,run_notes=True)
    experiment=dev_experiment.create(store,tmp_path/'runs',selection)
    manifest=dev_experiment.verify_manifest(experiment)
    cell=store.cell(experiment['cells'][0]['key'])
    for value in (manifest,cell):
        assert value['memory_authority']==SETTINGS
        assert value['memory_family'] is True and value['run_notes'] is True


def test_formal_identity_after_no_eval(fixture):
    from benchmarks.attention_harness.dev_agent_adapters import Executor as Adapter
    class BoundExecutor(Executor):
        bind_execution = Adapter.bind_execution
        def __init__(self):
            super().__init__()
            self.identities = []
        def execute(self, *args, **kwargs):
            self.identities.append(dict(self.execution_identity))
            return super().execute(*args, **kwargs)
    f = fixture; executor = BoundExecutor()
    session = fake_session(f.ws, ["tool('run','--no-eval');tool('submit')",
                                 "tool('run','--no-eval');tool('submit')", 'pass'])
    drive(f.ws, session, f.ledger, executor=executor, prober=FakeProber(), completion_reader=lambda *a: False)
    assert executor.identities == [
        dict(run_id='run:cell', attempt_id='probe:cell:e000001'),
        dict(run_id='run:cell', attempt_id='attempt:cell:0'),
        dict(run_id='run:cell', attempt_id='probe:cell:e000003'),
        dict(run_id='run:cell', attempt_id='attempt:cell:1')]
