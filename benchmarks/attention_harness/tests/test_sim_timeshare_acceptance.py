"""Acceptance checks for Remote time-share that test_sim_timeshare.py leaves out (TESTPLAN-remote-sim-timeshare-20261108, A1-A9).

A1 with ten real processes instead of threads; A5 a probe while someone else holds the robot; A6 the probe owner's
6-hour limit; A8 the other stops still end a Remote run with their own reason; A9 a freed slot is filled again.
"""
import fcntl
import json
import multiprocessing
import os
import time

import pytest

from benchmarks.attention_harness import daily_dev_v2 as daily
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_agent_adapters import FakeProber, Prober
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger
from benchmarks.attention_harness.dev_agent_loop import drive
from benchmarks.attention_harness.dev_agent_quota import QuotaMonitor
from benchmarks.attention_harness.sim_timeshare import SimToken, read_state
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity  # noqa: F401 (fixtures)
from benchmarks.attention_harness.tests.test_daily_byok_keys import fake_process, materialize, selection  # noqa: F401 (fixtures)
from benchmarks.attention_harness.tests.test_dev_agent_d1 import CONFIG
from benchmarks.attention_harness.tests.test_dev_agent_d2 import fake_session, ws  # noqa: F401 (fixture)
from benchmarks.attention_harness.tests.test_sim_timeshare import LockCheckingExecutor, alive_workers, locked_elsewhere, started


# ---- A1: ten processes at once, never two on the robot ----

def _hold_once(lock, out, n, go):
    go.wait(10)
    token = SimToken(lock, label=f'p{n}', poll=.005)
    with token.hold():
        began = time.time();time.sleep(.03);ended = time.time()
    with open(out, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX);f.write(json.dumps(dict(n=n, began=began, ended=ended)) + '\n')


def test_ten_processes_asking_at_once_never_overlap_and_all_get_their_turn(tmp_path):
    lock, out = tmp_path / 'sim.lock', tmp_path / 'spans.jsonl'
    SimToken(lock, label='setup')  # Makes the line table before ten processes race to.
    ctx = multiprocessing.get_context('fork');go = ctx.Event()
    procs = [ctx.Process(target=_hold_once, args=(lock, out, n, go)) for n in range(10)]
    for p in procs:p.start()
    go.set()
    for p in procs:p.join(60)
    assert [p.exitcode for p in procs] == [0] * 10
    spans = sorted((json.loads(l) for l in out.read_text().splitlines()), key=lambda s:s['began'])
    assert sorted(s['n'] for s in spans) == list(range(10))
    assert all(a['ended'] <= b['began'] for a, b in zip(spans, spans[1:])), spans
    assert SimToken(lock, label='after').line() == [] and not locked_elsewhere(lock)


# ---- A5: a read-only probe never waits for the robot ----

def run(ws, scripts, *, lock, ledger=None, quota=None, control=lambda:None, successes=(), quota_interval=60):
    ledger = ledger or AgentLedger(ws.private / 'experiment.sqlite', 'cell', wall_unlimited=True)
    executor = LockCheckingExecutor(lock);executor.successes = successes
    result = drive(ws, fake_session(ws, scripts), ledger, executor=executor, prober=FakeProber(), quota=quota, quota_interval=quota_interval, control=control,
                   sim_token=SimToken(lock, label='me', poll=.02))
    return result, ledger, executor


def test_a_probe_answers_while_someone_else_holds_the_robot(ws, tmp_path):
    lock = tmp_path / 'sim.lock';other = open(lock, 'a');fcntl.flock(other, fcntl.LOCK_EX)  # Held for the whole run.
    began = time.monotonic()
    result, ledger, executor = run(ws, ["tool('observe'); tool('observe')", "pass"], lock=lock)
    snap = ledger.snapshot()
    assert result['reason'] == 'agent_idle' and time.monotonic() - began < 30
    assert snap['used']['read_only_probes'] == 2 and snap['used']['moving_executions'] == 0 and snap['sim_wait_seconds'] == 0
    assert executor.held == [] and read_state(ws.private) is None
    other.close()


# ---- A6: the probe owner has no 6-hour limit on Remote ----

def test_the_probe_owner_has_no_six_hour_limit_on_remote_and_keeps_it_locally(tmp_path):
    remote = Prober(CONFIG, sim_python='/bin/false', private_dir=tmp_path, run_seconds=None)
    local = Prober(CONFIG, sim_python='/bin/false', private_dir=tmp_path)
    try:
        assert remote.run_deadline - time.monotonic() > 365 * 86400
        assert 21500 < local.run_deadline - time.monotonic() <= 21600
    finally:
        remote.spawner.shutdown();local.spawner.shutdown()
    # The worker builds its prober with no limit exactly when it has the robot lock.
    source = (daily.Path(__file__).parents[1] / 'dev_agent_loop.py').read_text()
    assert 'run_seconds=None if sim_lock is not None else 21600' in source


# ---- A8: the other stops still end a Remote run, each with its own reason ----

def test_a_remote_run_with_its_versions_used_up_is_refused_another(ws, tmp_path):
    ledger = AgentLedger(ws.private / 'experiment.sqlite', 'cell', wall_unlimited=True, budget=dict(versions=1, wall_seconds=1200))
    result, ledger, _ = run(ws, ["p=tool('submit'); tool('submit'); Path('work/second.txt').write_text(p.stdout)", "pass"], lock=tmp_path / 'sim.lock', ledger=ledger)
    assert ledger.snapshot()['used']['formal_versions'] == 1
    assert result['reason'] == 'budget_exhausted' and result['state'] == 'failed'
    assert not locked_elsewhere(tmp_path / 'sim.lock')


def test_a_remote_run_stops_at_the_quota_line_says_quota_and_holds_no_robot(ws, tmp_path):
    ledger = AgentLedger(ws.private / 'experiment.sqlite', 'cell', wall_unlimited=True);reset = time.time() + 1000
    calls = []
    def reading():
        calls.append(1)
        return dict(used_percent=78 if len(calls) > 1 else 60, window_minutes=10080, resets_at=reset, observed_at=time.time())
    quota = QuotaMonitor(ws.private, ledger, reader=reading, stop_percent=77)
    result, *_ = run(ws, ["time.sleep(20)"], lock=tmp_path / 'sim.lock', ledger=ledger, quota=quota, quota_interval=.1)
    assert result['reason'] == 'quota_stop' and result['state'] == 'failed'
    assert result['reason'] != 'no_progress' and not locked_elsewhere(tmp_path / 'sim.lock')


def test_a_remote_run_paused_for_low_disk_runs_nothing_and_holds_no_robot(ws, tmp_path):
    # run_cell's control() returns 'pause' when dev_disk.pause_if_low finds the disk low (reason disk_low, test_dev_disk.py).
    result, ledger, executor = run(ws, ["tool('run','--no-eval')", "pass"], lock=tmp_path / 'sim.lock', control=lambda:'pause')
    assert result['state'] == 'planned' and result['reason'] == 'operator_paused'
    assert ledger.snapshot()['used']['moving_executions'] == 0 and executor.held == []
    assert not locked_elsewhere(tmp_path / 'sim.lock')
    source = (daily.Path(__file__).parents[1] / 'dev_agent_loop.py').read_text()
    control = source[source.index('def control():'):source.index('def before_step(kind):')]
    assert 'pause_if_low(store' in control and 'sim_lock' not in control  # The disk check is not skipped on Remote.


# ---- A9: a slot that frees up is filled again ----

def test_a_freed_run_slot_lets_the_next_run_start(tmp_path, monkeypatch, materialize):
    fake_process(monkeypatch);alive_workers(monkeypatch)
    bridge = daily.DailyDevV2(tmp_path / 'daily', [], per_run_budget=True, sim_lock=tmp_path / 'sim.lock', run_slots=2)
    a, b, c = started(bridge, 3)
    bridge.start(a);bridge.start(b)
    with pytest.raises(DevConflict, match='run slots are in use'):bridge.start(c)
    bridge.workers[a].poll = lambda:0  # a's worker has exited.
    for receipt in (tmp_path / 'daily').glob(f'{a}/scheduler-process-*.json'):os.unlink(receipt)
    assert bridge.start(c)['worker_started'] and bridge.live_runs() == {b, c}


# ---- fault: the process holding the robot is killed ----

def _hold_forever(lock, ready):
    token = SimToken(lock, label='victim', poll=.005)
    with token.hold():
        ready.set();time.sleep(600)


def test_a_holder_killed_mid_execution_gives_the_robot_to_the_next_in_line(tmp_path):
    import signal
    lock = tmp_path / 'sim.lock';SimToken(lock, label='setup')
    ctx = multiprocessing.get_context('fork');ready = ctx.Event()
    victim = ctx.Process(target=_hold_forever, args=(lock, ready));victim.start()
    assert ready.wait(10) and locked_elsewhere(lock)
    waiter = SimToken(lock, label='next', poll=.01);seen = [];got = []
    import threading
    def wait():
        with waiter.hold(on_wait=seen.append):got.append(time.monotonic())
    thread = threading.Thread(target=wait, daemon=True);thread.start()
    while not seen:time.sleep(.01)
    time.sleep(.3);assert not got  # Still waiting behind the live holder.
    killed = time.monotonic();os.kill(victim.pid, signal.SIGKILL);victim.join(10)
    thread.join(10)
    assert got and got[0] - killed < 5, 'the dead holder kept the robot'
    assert seen[0] == 1 and waiter.line() == [] and not locked_elsewhere(lock)
