"""Pre-started execution workers (try speed-up 2, item 10 "prespawn" route; user OK 11-14).

Fakes only: no simulator or planner. Covers the bind protocol (deadline may only tighten),
the owner handshake against a real child process, the runner adopting a pre-started owner,
and the Executor/broker rules that discard it on any mismatch.
"""
import hashlib
import io
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openarm365_dev_v2 import prespawn as ps
from openarm365_dev_v2 import runner as runner_module
from openarm365_dev_v2.tests.test_planner_features import SENSOR, setup
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest


# --- bind message --------------------------------------------------------------------------

def test_bind_tightens_only():
    assert ps.parse_bind(json.dumps(dict(kind='bind', deadline=50.)), 100., 10.) == 50.
    with pytest.raises(ps.PrespawnError, match='extend'):
        ps.parse_bind(json.dumps(dict(kind='bind', deadline=150.)), 100., 10.)
    with pytest.raises(ps.PrespawnError, match='passed'):
        ps.parse_bind(json.dumps(dict(kind='bind', deadline=5.)), 100., 10.)


@pytest.mark.parametrize('line', ['nope', json.dumps(dict(kind='bind')), json.dumps(dict(kind='bind', deadline=50., x=1)),
                                  json.dumps(dict(kind='other', deadline=50.)), json.dumps(dict(kind='bind', deadline=True)),
                                  json.dumps(dict(kind='bind', deadline='50')), '{"kind":"bind","deadline":NaN}'])
def test_bind_rejects_anything_else(line):
    with pytest.raises(ps.PrespawnError):
        ps.parse_bind(line, 100., 10.)


def test_wait_for_bind_reads_one_line_and_respects_stop_eof_and_deadline():
    r, w = os.pipe()
    reader = os.fdopen(r, 'r')
    os.write(w, (json.dumps(dict(kind='bind', deadline=time.monotonic() + 30)) + '\n').encode())
    assert ps.wait_for_bind(reader, time.monotonic() + 60, threading.Event(), poll=.01) > time.monotonic()
    stop = threading.Event(); stop.set()
    with pytest.raises(ps.PrespawnError, match='stopped'):
        ps.wait_for_bind(reader, time.monotonic() + 60, stop, poll=.01)
    with pytest.raises(ps.PrespawnError, match='launch deadline'):
        ps.wait_for_bind(reader, time.monotonic() + .05, threading.Event(), poll=.01)
    os.close(w)
    with pytest.raises(ps.PrespawnError, match='closed'):
        ps.wait_for_bind(reader, time.monotonic() + 5, threading.Event(), poll=.01)


def test_tighten_never_extends():
    planner = SimpleNamespace(owner=SimpleNamespace(deadline=100.), client=SimpleNamespace(deadline=90.))
    ps.tighten(planner, 95.)
    assert (planner.owner.deadline, planner.client.deadline) == (95., 90.)


# --- owner handshake against a real child process ---------------------------------------------

FAKE_WORKER = r'''
import argparse, json, sys
p = argparse.ArgumentParser()
for a in ('--config', '--evidence', '--deadline', '--owner-pid'): p.add_argument(a)
p.add_argument('--agent-mode', action='store_true'); p.add_argument('--prespawn', action='store_true')
a = p.parse_args()
assert a.agent_mode
if a.prespawn:
    print(json.dumps({'kind': 'spawn', 'pid': 999999}), flush=True)
    print(json.dumps({'kind': 'prespawned'}), flush=True)
    bind = json.loads(sys.stdin.readline())
    assert bind['kind'] == 'bind' and bind['deadline'] <= float(a.deadline)
    print(json.dumps({'kind': 'ready', 'bound_deadline': bind['deadline']}), flush=True)
else:
    print(json.dumps({'kind': 'ready', 'bound_deadline': None}), flush=True)
sys.stdin.readline()
'''


@pytest.fixture
def fake_project(tmp_path):
    root = tmp_path / 'project'
    (root / 'openarm365_dev_v2').mkdir(parents=True)
    (root / 'openarm365_dev_v2' / '__init__.py').write_text('')
    (root / 'openarm365_dev_v2' / 'worker.py').write_text(FAKE_WORKER)
    return root


def owner(tmp_path, root, **kw):
    from openarm365_dev_v2.owner import NativeOwnerV2
    ev = tmp_path / ('ev-%d' % len(list(tmp_path.glob('ev-*'))))
    ev.mkdir()
    (ev / 'config.json').write_text('{}')
    return NativeOwnerV2(python=sys.executable, config=ev / 'config.json', evidence=ev, deadline=time.monotonic() + 60,
                         cancel_check=lambda: False, source_root=root, agent_mode=True, **kw)


def test_owner_prespawn_then_bind_returns_ready_with_the_tighter_deadline(tmp_path, fake_project):
    o = owner(tmp_path, fake_project, prespawn=True)
    try:
        assert o.start() == {'kind': 'prespawned'} and o.ready is None and o.child_ids == [999999]
        argv = json.loads((o.evidence / 'worker-launch.json').read_text())['argv']
        assert '--prespawn' in argv and '--agent-mode' in argv
        with pytest.raises(ValueError, match='extend'):
            o.bind(o.deadline + 10)
        target = time.monotonic() + 30
        ready = o.bind(target)
        assert ready['kind'] == 'ready' and ready['bound_deadline'] == target and o.deadline == target
        with pytest.raises(RuntimeError, match='unbound'):
            o.bind(target)
    finally:
        o.process.kill(); o.process.wait()


def test_owner_without_prespawn_is_unchanged(tmp_path, fake_project):
    o = owner(tmp_path, fake_project)
    try:
        assert o.start()['kind'] == 'ready'
        assert '--prespawn' not in json.loads((o.evidence / 'worker-launch.json').read_text())['argv']
        with pytest.raises(RuntimeError):
            o.bind(o.deadline)
    finally:
        o.process.kill(); o.process.wait()


def test_prespawn_requires_agent_mode(tmp_path):
    from openarm365_dev_v2.owner import NativeOwnerV2
    with pytest.raises(ValueError):
        NativeOwnerV2(python='x', config=tmp_path / 'c', evidence=tmp_path, deadline=1., cancel_check=lambda: False,
                      prespawn=True)


# --- runner adopts a pre-started owner -------------------------------------------------------

@pytest.fixture
def dev(tmp_path, monkeypatch):
    s, e, c, cfg, _, _ = setup(tmp_path)
    assert cfg['budget_tier'] == 'dev' and cfg['runner_boundary'] == 'dev_loop'
    config = tmp_path / 'config.json'
    config.write_text(json.dumps(cfg, sort_keys=True))
    policy = tmp_path / 'policy.py'
    policy.write_text(SENSOR)
    from openarm365_dev_v2 import agent_sandbox, agent_sdk
    monkeypatch.setattr(runner_module, 'probe_sandbox', lambda: {'controlled': True})
    monkeypatch.setattr(runner_module, 'finalize_recording', lambda *args: {})
    monkeypatch.setattr(agent_sdk, 'build_sdk', lambda *a, **k: None)
    monkeypatch.setattr(agent_sandbox, 'execute_agent_policy',
                        lambda **kw: SimpleNamespace(status='completed', error=None, call_count=1))
    return SimpleNamespace(cfg=cfg, config=config, policy=policy, tmp=tmp_path)


class FakeOwner:
    def __init__(self, config, **kw):
        self.config = Path(config); self.ledger = []; self.sent = {}; self.unresolved = set()
        self.calls = []; self.cancel_check = None
        self.process = SimpleNamespace(poll=lambda: None)

    def start(self):
        self.calls.append('start')
        return {'kind': 'prespawned'}

    def bind(self, deadline):
        self.calls.append(('bind', deadline))
        return {'initial_observation': {'robot': {}, 'perception': {'values': {}}}, 'capabilities': {}}

    def rpc(self, group, method, *a, **kw):
        return {'robot': {}, 'perception': {'values': {}}} if method == 'observe' else {'evaluated': True, 'native_success': False}

    def stop(self, reason='normal_cleanup'):
        self.calls.append(('stop', reason))
        return dict(leader_reaped=True, process_group_gone=True, planner_process_groups_gone=True)


def request(dev, root):
    sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
    return FormalRunRequest(suite='robocasa365', task_id=dev.cfg['task_id'], seed=dev.cfg['seed'],
        policy_code_path=dev.policy, policy_sha256=sha(dev.policy), config_path=dev.config, config_sha256=sha(dev.config),
        artifact_root=root, overall_deadline_seconds=300, attempt_deadline_seconds=120, sdk_call_limit=40,
        run_id='run:t', attempt_id='attempt:t:0')


def prestarted(root, dev):
    episode = root / 'openarm365-dev-aaaaaaaaaaaa'
    episode.mkdir(parents=True)
    (episode / 'config.json').write_bytes(dev.config.read_bytes())
    return dict(episode=episode, owner=FakeOwner(episode / 'config.json'), spawn_started=time.monotonic() - 40,
                prespawned_at=time.monotonic() - 10)


def test_runner_binds_the_prestarted_owner_and_records_it(dev):
    root = dev.tmp / 'executions' / 'e000002'
    pre = prestarted(root, dev)
    runner = runner_module.OpenArm365DevRunnerV2(service_source_root=None, sim_python='', sim_source_root=None,
                                                 task_source_root=None, asset_manifest=None, agent_mode=True)
    before = time.monotonic()
    result = runner.execute(request(dev, root), prespawned=pre)
    owner = pre['owner']
    assert 'start' not in owner.calls and owner.calls[0][0] == 'bind'
    assert before + 120 - 1 < owner.calls[0][1] <= time.monotonic() + 120      # this execution's own deadline
    assert owner.cancel_check == runner.cancel_event.is_set
    assert Path(result['artifact_dir']) == pre['episode'].resolve()
    receipt = json.loads((pre['episode'] / 'sandbox_receipt.json').read_bytes())
    assert receipt['prespawn']['used'] is True and receipt['prespawn']['started_before_bind_seconds'] >= 40
    assert (pre['episode'] / 'policy.py').read_bytes() == dev.policy.read_bytes()
    assert json.loads((pre['episode'] / 'recording_context.json').read_bytes())['attempt_id'] == 'attempt:t:0'


def test_runner_refuses_a_prestarted_owner_for_another_folder_or_config(dev):
    runner = runner_module.OpenArm365DevRunnerV2(service_source_root=None, sim_python='', sim_source_root=None,
                                                 task_source_root=None, asset_manifest=None, agent_mode=True)
    pre = prestarted(dev.tmp / 'executions' / 'e000002', dev)
    with pytest.raises(ValueError, match='not in this execution directory'):
        runner.execute(request(dev, dev.tmp / 'executions' / 'e000003'), prespawned=pre)
    (pre['episode'] / 'config.json').write_text('{}')
    with pytest.raises(ValueError, match='config differs'):
        runner.execute(request(dev, dev.tmp / 'executions' / 'e000002'), prespawned=pre)


def test_runner_refuses_prespawn_outside_agent_mode(dev):
    runner = runner_module.OpenArm365DevRunnerV2(service_source_root=None, sim_python='', sim_source_root=None,
                                                 task_source_root=None, asset_manifest=None)
    pre = prestarted(dev.tmp / 'executions' / 'e000002', dev)
    with pytest.raises(ValueError, match='agent mode'):
        runner.execute(request(dev, dev.tmp / 'executions' / 'e000002'), prespawned=pre)


# --- Executor: use only for exactly the predicted execution ----------------------------------

@pytest.fixture
def executor(dev, monkeypatch):
    from benchmarks.attention_harness import dev_agent_adapters as adapters
    from openarm365_dev_v2 import owner as owner_module
    made = []

    class Owner(FakeOwner):
        def __init__(self, **kw):
            super().__init__(kw['config']); self.kw = kw; made.append(self)

    monkeypatch.setattr(owner_module, 'NativeOwnerV2', Owner)
    seen = []

    def execute(self, request, prespawned=None):
        seen.append(prespawned)
        return dict(status='completed')

    monkeypatch.setattr(runner_module.OpenArm365DevRunnerV2, 'execute', execute)
    ex = adapters.Executor(dev.cfg, sim_python='py')
    return SimpleNamespace(ex=ex, made=made, seen=seen, private=dev.tmp / 'private', log=dev.tmp / 'private' / 'prespawn-log.jsonl')


def wait_started(ex):
    ex.pending['future'].result(timeout=10)


def test_executor_uses_the_prestarted_worker_for_the_predicted_execution(executor):
    e = executor
    d = e.private / 'executions' / 'e000001'
    assert e.ex.prespawn(d, log=e.log) is True and e.ex.prespawn(d, log=e.log) is False   # one at a time
    wait_started(e.ex)
    owner = e.made[0]
    assert owner.kw['prespawn'] is True and owner.kw['agent_mode'] is True and owner.calls == ['start']
    assert e.ex.claim_prespawn(d) is e.ex.pending
    e.ex.execute(b'print(1)\n', d, deadline=time.monotonic() + 600)
    assert e.seen[-1]['owner'] is owner and e.ex.pending is None
    assert ('stop', 'normal_cleanup') in owner.calls                       # never left running or reused
    events = [json.loads(line)['event'] for line in e.log.read_text().splitlines()]
    assert events == ['started', 'used']


def test_executor_discards_on_another_execution_id(executor):
    e = executor
    predicted = e.private / 'executions' / 'e000002'
    e.ex.prespawn(predicted, log=e.log); wait_started(e.ex)
    actual = e.private / 'executions' / 'e000003'
    assert e.ex.claim_prespawn(actual) is None and e.ex.pending is None
    actual.mkdir(parents=True)
    e.ex.execute(b'print(1)\n', actual, deadline=time.monotonic() + 600)
    assert e.seen[-1] is None                                               # cold start, as before
    assert ('stop', 'prespawn_discarded') in e.made[0].calls
    assert not predicted.exists() and len(list((e.private / 'prespawn-discarded').iterdir())) == 1
    rows = [json.loads(line) for line in e.log.read_text().splitlines()]
    assert rows[-1]['event'] == 'discarded' and rows[-1]['reason'] == 'directory_mismatch'


@pytest.mark.parametrize('problem', ['worker_exited', 'config_changed', 'start_failed'])
def test_executor_falls_back_to_a_cold_start(executor, problem, monkeypatch):
    e = executor
    d = e.private / 'executions' / 'e000001'
    if problem == 'start_failed':
        monkeypatch.setattr(FakeOwner, 'start', lambda self: (_ for _ in ()).throw(RuntimeError('boom')))
    e.ex.prespawn(d, log=e.log)
    try:e.ex.pending['future'].result(timeout=10)
    except RuntimeError:pass
    if problem == 'worker_exited':
        e.made[0].process = SimpleNamespace(poll=lambda: 1)
    if problem == 'config_changed':
        e.ex.config = dict(e.ex.config, seed=e.ex.config['seed'])   # same values ...
        e.ex.pending['config_sha256'] = 'f' * 64                     # ... but a different recorded digest
    e.ex.execute(b'print(1)\n', d, deadline=time.monotonic() + 600)
    assert e.seen[-1] is None and ('stop', 'prespawn_discarded') in e.made[0].calls
    assert (d / 'policy.py').is_file() and (d / 'config.json').is_file()   # folder recreated for the cold start
    row = json.loads(e.log.read_text().splitlines()[-1])
    assert row['event'] == 'discarded' and row['reason'].startswith(problem)


def test_executor_never_prestarts_for_memory_validation_or_custom_runners(dev, executor):
    from benchmarks.attention_harness import dev_agent_adapters as adapters
    e = executor
    e.ex.memory_validation = object()
    assert e.ex.prespawn(e.private / 'executions' / 'e000001') is False
    custom = adapters.Executor(dev.cfg, sim_python='py', runner_factory=lambda **kw: None)
    assert custom.prespawn(e.private / 'executions' / 'e000001') is False
    assert not (e.private / 'executions').exists() and e.made == []


def test_close_discards_and_moves_the_folder_out(executor):
    e = executor
    d = e.private / 'executions' / 'e000004'
    e.ex.prespawn(d, log=e.log); wait_started(e.ex)
    assert e.ex.discard_prespawn('run_closed') == 'run_closed'
    assert not d.exists() and ('stop', 'prespawn_discarded') in e.made[0].calls
    assert e.ex.discard_prespawn('again') is None


def test_cancel_while_waiting_for_the_prestart_runs_nothing(executor, monkeypatch):
    e = executor
    gate = threading.Event()

    def slow_start(self):
        while not gate.is_set() and not self.kw['cancel_check']():
            time.sleep(.01)
        if self.kw['cancel_check']():raise InterruptedError('cancelled')
        return {'kind': 'prespawned'}

    monkeypatch.setattr(FakeOwner, 'start', slow_start)
    d = e.private / 'executions' / 'e000001'
    e.ex.prespawn(d, log=e.log)
    threading.Timer(.2, e.ex.cancel).start()
    with pytest.raises(InterruptedError):
        e.ex.execute(b'print(1)\n', d, deadline=time.monotonic() + 600)
    assert e.seen == [] and ('stop', 'prespawn_discarded') in e.made[0].calls


# --- broker: predict the next execution id, local only ---------------------------------------

def broker(tmp_path, **kw):
    from benchmarks.attention_harness.dev_agent_broker import Broker
    calls = []
    ex = SimpleNamespace(prespawn=lambda d, log=None: calls.append((d, log)))
    b = SimpleNamespace(executor=ex, prespawn_enabled=True, sim_token=None, closing=threading.Event(), quarantined=False, stop_reason=None,
                        budget=SimpleNamespace(counts=dict(executions=3), limits=dict(executions=40)),
                        workspace=SimpleNamespace(private=tmp_path))
    b.__dict__.update(kw)
    Broker._prespawn_next(b)
    return calls


def test_broker_prestarts_the_next_execution_id_locally(tmp_path):
    assert broker(tmp_path) == [(tmp_path / 'executions' / 'e000004', tmp_path / 'prespawn-log.jsonl')]


@pytest.mark.parametrize('change', [dict(prespawn_enabled=False), dict(sim_token=object()), dict(quarantined=True), dict(stop_reason='run_complete'),
                                    dict(budget=SimpleNamespace(counts=dict(executions=40), limits=dict(executions=40)))])
def test_broker_does_not_prestart_on_remote_or_after_the_run(tmp_path, change):
    assert broker(tmp_path, **change) == []


def test_broker_records_a_prespawn_failure_and_continues(tmp_path):
    from benchmarks.attention_harness.dev_agent_broker import Broker
    def fail(d, log=None):raise OSError('disk full')
    b = SimpleNamespace(executor=SimpleNamespace(prespawn=fail), prespawn_enabled=True, sim_token=None, closing=threading.Event(), quarantined=False,
                        stop_reason=None, budget=SimpleNamespace(counts=dict(executions=0), limits=dict(executions=40)),
                        workspace=SimpleNamespace(private=tmp_path))
    Broker._prespawn_next(b)
    assert json.loads((tmp_path / 'errors' / 'prespawn.json').read_bytes())['error'] == 'prespawn_failed'


def test_marker_is_removed_on_use(executor):
    e = executor
    d = e.private / 'executions' / 'e000001'
    e.ex.prespawn(d, log=e.log); wait_started(e.ex)
    assert (d / '.prespawn').is_file()
    e.ex.execute(b'print(1)\n', d, deadline=time.monotonic() + 600)
    assert not (d / '.prespawn').exists()


def test_stale_folder_from_an_earlier_process_is_moved_out(executor):
    from benchmarks.attention_harness import dev_agent_adapters as adapters
    e = executor
    d = e.private / 'executions' / 'e000005'
    (d / 'openarm365-dev-old').mkdir(parents=True)
    (d / adapters.PRESPAWN_MARKER).write_text('x')
    moved = e.ex.clear_stale_prespawn(d)
    assert moved is not None and not d.exists() and (moved / 'openarm365-dev-old').is_dir()
    real = e.private / 'executions' / 'e000006'
    real.mkdir(parents=True)
    assert e.ex.clear_stale_prespawn(real) is None and real.is_dir()          # unmarked folders are never touched
    (d / 'openarm365-dev-old').mkdir(parents=True); (d / adapters.PRESPAWN_MARKER).write_text('x')
    assert e.ex.prespawn(d, log=e.log) is True                                 # a new prespawn clears it first
    wait_started(e.ex)
    assert e.ex.clear_stale_prespawn(d) is None and d.is_dir()               # its own pending folder is kept


# --- switch: opt-in for the local service only, never Remote (user decision 11-14) ----------

def test_switch_is_off_by_default_and_on_only_for_local_opt_in():
    from benchmarks.attention_harness.dev_agent_prespawn import prespawn_allowed
    local = dict(sim_lock=None, public_gateway_mode=False, validation=None)
    assert prespawn_allowed(**local, environ={}) is False
    assert prespawn_allowed(**local, environ={'ATTENTIONBENCH_PRESPAWN': '1'}) is False
    assert prespawn_allowed(**local, environ={'ATTENTIONBENCH_PRESPAWN': 'local'}) is True


@pytest.mark.parametrize('remote', [dict(sim_lock='/data/sim.lock'), dict(public_gateway_mode=True), dict(validation={'arm': 'x'})])
def test_switch_is_always_off_for_remote_or_validation_even_with_the_opt_in(remote):
    from benchmarks.attention_harness.dev_agent_prespawn import prespawn_allowed
    kw = dict(sim_lock=None, public_gateway_mode=False, validation=None, environ={'ATTENTIONBENCH_PRESPAWN': 'local'})
    kw.update(remote)
    assert prespawn_allowed(**kw) is False


def test_broker_refuses_prespawn_with_a_remote_time_share_token(tmp_path):
    from benchmarks.attention_harness.dev_agent_broker import Broker
    with pytest.raises(ValueError, match='local runs only'):
        Broker(SimpleNamespace(private=tmp_path), executor=None, prober=None, evaluator=None, agent_mode=True,
               sim_token=object(), prespawn=True)


REMOTE_UNITS = ['remote_gateway/deploy/attentionbench-public-harness.service',
                'remote_gateway/deploy/campus/attentionbench-public-harness.service',
                'remote_gateway/deploy/run_host_harness.py']


@pytest.mark.parametrize('unit', REMOTE_UNITS)
def test_remote_deployments_never_opt_in_and_always_time_share(unit):
    text = (Path(__file__).resolve().parents[2] / unit).read_text()
    assert 'ATTENTIONBENCH_PRESPAWN' not in text
    assert '--sim-lock' in text          # Remote runs always carry the host lock -> prespawn forced off


def test_the_agent_loop_passes_the_switch_from_prespawn_allowed():
    import ast
    from benchmarks.attention_harness import dev_agent_loop
    src = Path(dev_agent_loop.__file__).read_text()
    assert 'prespawn=prespawn_allowed(sim_lock=sim_lock,public_gateway_mode=public_gateway_mode,validation=validation)' in src
    assert src.count('prespawn=prespawn)') == 2      # into drive(...) and from drive into Broker(...)
