"""Remote time-share: several runs open, one simulator token taken per execution, no time limit, a no-progress stop.

Each test that changes Remote behaviour has a twin showing the local (8769) path is unchanged. No simulator, model or
network: the token is a real flock on a file in tmp_path, the session is d2's fake Codex, the executor is a fake.
"""
import fcntl
import json
import threading
import time

import pytest

from benchmarks.attention_harness import daily_dev_v2 as daily
from benchmarks.attention_harness import dev_agent_loop
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_agent_adapters import FakeProber
from benchmarks.attention_harness.dev_agent_ledger import AgentLedger, NO_PROGRESS_SECONDS
from benchmarks.attention_harness.dev_agent_loop import drive, NO_PROGRESS_MESSAGE
from benchmarks.attention_harness.sim_timeshare import SimToken, SimWaitCancelled, read_state
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity  # noqa: F401 (fixtures)
from benchmarks.attention_harness.tests.test_daily_byok_keys import fake_process, materialize, selection  # noqa: F401 (fixtures)
from benchmarks.attention_harness.tests.test_dev_agent_d2 import Executor, fake_session, ws  # noqa: F401 (fixture)


def locked_elsewhere(path):
    """Whether someone holds the lock, asked without keeping it."""
    probe = open(path, 'a')
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:return True
    finally:probe.close()
    return False


# ---- the token ----

def test_one_execution_at_a_time_first_come_first_served_and_a_returning_run_goes_to_the_back(tmp_path):
    lock = tmp_path / 'sim.lock';order = [];inside = [];most = [0];lock_held = []
    a, b, c = (SimToken(lock, label=name, poll=.01) for name in 'abc')
    release_a = threading.Event();b_waiting = threading.Event();c_waiting = threading.Event()

    def use(token, name, hold=0., waiting=None):
        with token.hold(on_wait=lambda ahead:waiting and waiting.set()):
            inside.append(name);most[0] = max(most[0], len(inside));order.append(name)
            lock_held.append(locked_elsewhere(lock))
            time.sleep(hold)
            inside.remove(name)

    def a_twice():
        with a.hold():
            order.append('a');lock_held.append(locked_elsewhere(lock))
            release_a.wait(5)
        c_waiting.wait(5)
        use(a, 'a-again')  # Gave it back, wants it again: behind b and c, who were already waiting.
    first = threading.Thread(target=a_twice);first.start()
    while not a.line():time.sleep(.01)
    second = threading.Thread(target=use, args=(b, 'b', .05, b_waiting));second.start();b_waiting.wait(5)
    third = threading.Thread(target=use, args=(c, 'c', .05, c_waiting));third.start();c_waiting.wait(5)
    assert [row['label'] for row in a.line()] == ['a', 'b', 'c']
    release_a.set()
    for thread in (first, second, third):thread.join(10)
    assert order == ['a', 'b', 'c', 'a-again'] and most[0] == 1 and all(lock_held)
    assert a.line() == [] and not locked_elsewhere(lock)


def test_waiting_says_how_many_are_ahead_and_a_stop_while_waiting_runs_nothing(tmp_path):
    lock = tmp_path / 'sim.lock';holder = SimToken(lock, label='holder', poll=.01);waiter = SimToken(lock, label='waiter', poll=.01)
    seen = [];stop = threading.Event();ran = []
    with holder.hold():
        def wait():
            try:
                with waiter.hold(cancelled=stop.is_set, on_wait=seen.append):ran.append(True)
            except SimWaitCancelled:ran.append(False)
        thread = threading.Thread(target=wait);thread.start()
        while not seen:time.sleep(.01)
        stop.set();thread.join(5)
    assert seen == [1] and ran == [False] and [row['label'] for row in holder.line()] == []


def test_a_dead_runs_ticket_never_blocks_the_line_and_another_tool_holding_the_lock_does(tmp_path):
    lock = tmp_path / 'sim.lock'
    dead = SimToken(lock, label='dead', identity=dict(pid=999999999, start_ticks='1', boot_id='gone'))
    with dead.tx() as db:db.execute("INSERT INTO tickets (label, owner, requested_at) VALUES ('dead', ?, 0)", (json.dumps(dead.identity),))
    live = SimToken(lock, label='live', poll=.01);entered = threading.Event()
    other = open(lock, 'a');fcntl.flock(other, fcntl.LOCK_EX)  # e.g. scripts/attentionbench/run.py on the same host
    def go():
        with live.hold():entered.set()
    thread = threading.Thread(target=go, daemon=True);thread.start()
    time.sleep(.2);assert not entered.is_set()
    other.close();thread.join(5)
    assert entered.is_set() and [row['label'] for row in live.line()] == []


def test_a_left_over_state_file_of_a_dead_run_is_not_shown(tmp_path):
    from benchmarks.attention_harness.sim_timeshare import write_state
    from benchmarks.attention_harness.dev_loop import process_identity
    write_state(tmp_path, dict(state='waiting', ahead=2, since=1, owner=process_identity()))
    assert read_state(tmp_path) == dict(state='waiting', ahead=2, since=1)
    write_state(tmp_path, dict(state='waiting', ahead=2, since=1, owner=dict(pid=999999999, start_ticks='1', boot_id='x')))
    assert read_state(tmp_path) is None
    write_state(tmp_path, None);assert read_state(tmp_path) is None


# ---- the clock ----

def clocked(tmp_path, **options):
    now = [1000.];ledger = AgentLedger(tmp_path / 'ledger.sqlite', 'cell', clock=lambda:now[0], budget=dict(versions=3, wall_seconds=1200), **options)
    ledger.reserve('setup', 'setup', {});ledger.claim('setup')
    return ledger, now


def test_remote_runs_have_no_time_limit_and_waiting_for_the_robot_does_not_count(tmp_path):
    ledger, now = clocked(tmp_path, wall_unlimited=True)
    now[0] += 100;ledger.sim_wait_begin();now[0] += 900;ledger.sim_wait_end();now[0] += 50
    snap = ledger.snapshot()
    assert snap['active_seconds'] == 150 and snap['sim_wait_seconds'] == 900 and snap['idle_seconds'] == 150
    now[0] += 5000  # Far past the recorded 1200 s: nothing refuses work.
    ledger.reserve('later', 'request', {'moving_executions': 1});ledger.claim('later')
    assert ledger.snapshot()['wall_remaining'] > 10 ** 8


def test_the_no_progress_clock_counts_active_time_since_the_last_execution_version_or_help(tmp_path):
    ledger, now = clocked(tmp_path, wall_unlimited=True)
    now[0] += 3000;ledger.mark_progress()
    now[0] += 1000;ledger.sim_wait_begin();now[0] += 5000  # A long wait for the robot is not "no progress".
    assert ledger.snapshot()['idle_seconds'] == 1000
    ledger.sim_wait_end();now[0] += NO_PROGRESS_SECONDS - 1000
    snap = ledger.snapshot();assert snap['idle_seconds'] == NO_PROGRESS_SECONDS and snap['no_progress_seconds'] == 7200


def test_local_runs_keep_their_time_limit_and_have_no_no_progress_clock(tmp_path):
    ledger, now = clocked(tmp_path)
    ledger.sim_wait_begin();now[0] += 500;ledger.sim_wait_end()  # Not a Remote run: these change nothing.
    snap = ledger.snapshot()
    assert 'idle_seconds' not in snap and 'sim_wait_seconds' not in snap and snap['wall_remaining'] == 700
    now[0] += 701
    with pytest.raises(ValueError, match='wall_stop'):ledger.reserve('late', 'request', {'moving_executions': 1})


def test_a_run_record_cannot_switch_between_local_and_remote_timing(tmp_path):
    clocked(tmp_path, wall_unlimited=True)
    with pytest.raises(ValueError, match='immutable_agent_budget'):AgentLedger(tmp_path / 'ledger.sqlite', 'cell', budget=dict(versions=3, wall_seconds=1200))


# ---- one run end to end (fake Codex, fake executor) ----

class LockCheckingExecutor(Executor):
    def __init__(self, lock):super().__init__();self.lock = lock;self.held = []
    def execute(self, source, directory, *, deadline):
        self.held.append(self.lock is not None and locked_elsewhere(self.lock))
        return super().execute(source, directory, deadline=deadline)


def run(ws, scripts, *, lock=None, ledger=None, control=lambda:None, session=None):
    ledger = ledger or AgentLedger(ws.private / 'experiment.sqlite', 'cell', wall_unlimited=lock is not None)
    session = session or fake_session(ws, scripts)
    executor = LockCheckingExecutor(lock)
    token = SimToken(lock, label='me', poll=.02) if lock else None
    result = drive(ws, session, ledger, executor=executor, prober=FakeProber(), control=control, sim_token=token)
    return result, ledger, executor


def test_each_execution_holds_the_robot_and_a_wait_for_it_is_not_counted(ws, tmp_path):
    lock = tmp_path / 'sim.lock';other = open(lock, 'a');fcntl.flock(other, fcntl.LOCK_EX)
    seen = []
    def release():
        while not read_state(ws.private):time.sleep(.02)
        seen.append(read_state(ws.private));time.sleep(1.);other.close()
    threading.Thread(target=release, daemon=True).start()
    result, ledger, executor = run(ws, ["tool('observe'); tool('run','--no-eval')", "pass"], lock=lock)
    snap = ledger.snapshot()
    assert executor.held == [True] and seen[0]['state'] == 'waiting' and seen[0]['ahead'] == 1
    assert snap['sim_wait_seconds'] >= .9 and snap['used']['moving_executions'] == 1 and snap['used']['read_only_probes'] == 1
    assert read_state(ws.private) is None and not locked_elsewhere(lock) and result['reason'] == 'agent_idle'
    assert 'no wall-clock limit' in (ws.private / 'turns/t000001/input.json').read_text()


def test_a_local_run_never_touches_the_robot_lock(ws, tmp_path):
    lock = tmp_path / 'sim.lock';other = open(lock, 'a');fcntl.flock(other, fcntl.LOCK_EX)  # Held all along: a local run does not wait for it.
    result, ledger, executor = run(ws, ["tool('run','--no-eval')", "pass"])
    assert executor.held == [False] and ledger.snapshot()['used']['moving_executions'] == 1 and result['reason'] == 'agent_idle'
    assert not (ws.private / 'sim-token.json').exists() and 'sim_wait_seconds' not in ledger.snapshot()
    assert '21600 active wall seconds' in (ws.private / 'turns/t000001/input.json').read_text()
    other.close()


def test_two_hours_without_progress_stops_the_run_and_says_why(ws, tmp_path):
    now = [time.time()];ledger = AgentLedger(ws.private / 'experiment.sqlite', 'cell', wall_unlimited=True, clock=lambda:now[0])
    session = fake_session(ws, ["tool('run','--no-eval')", "pass"])
    def control():
        if session.turn >= 2:now[0] = time.time() + NO_PROGRESS_SECONDS + 60  # Turn 2 makes no progress for over 2 hours.
    result, ledger, executor = run(ws, None, lock=tmp_path / 'sim.lock', ledger=ledger, session=session, control=control)
    assert result['reason'] == 'no_progress' and result['state'] == 'failed' and result['stop_detail'] == NO_PROGRESS_MESSAGE
    assert 'fatal_cause' not in result and result['reason'] != 'operator_cancel'


def test_a_silent_model_turn_is_stopped_by_the_watchdog(ws, tmp_path, monkeypatch):
    monkeypatch.setattr(dev_agent_loop, 'NO_PROGRESS_POLL', .1)
    now = [time.time()];ledger = AgentLedger(ws.private / 'experiment.sqlite', 'cell', wall_unlimited=True, clock=lambda:now[0])
    session = fake_session(ws, ["tool('run','--no-eval')", "time.sleep(60)"]);armed = []
    def control():
        if session.turn >= 2 and not armed:
            armed.append(threading.Timer(.5, lambda:now.__setitem__(0, time.time() + NO_PROGRESS_SECONDS + 60)));armed[0].start()
    started = time.monotonic()
    result, *_ = run(ws, None, lock=tmp_path / 'sim.lock', ledger=ledger, session=session, control=control)
    assert result['reason'] == 'no_progress' and time.monotonic() - started < 30


def test_a_local_run_has_no_no_progress_stop(ws):
    now = [time.time()];ledger = AgentLedger(ws.private / 'experiment.sqlite', 'cell', clock=lambda:now[0])
    session = fake_session(ws, ["tool('run','--no-eval')", "pass"])
    def control():
        if session.turn >= 2:now[0] = time.time() + 3 * 3600  # Long past 2 hours, well inside the local 6-hour limit.
    result, *_ = run(ws, None, ledger=ledger, session=session, control=control)
    assert result['reason'] == 'agent_idle'


# ---- the run host: several runs open on Remote, one at a time locally ----

def alive_workers(monkeypatch):
    """fake_process's pid has no /proc entry; give each fake worker an identity that reads as alive."""
    monkeypatch.setattr(daily, 'process_identity', lambda pid=None:dict(pid=pid, start_ticks='fake', boot_id='fake'))
    monkeypatch.setattr(daily, 'owner_alive', lambda owner:isinstance(owner, dict) and owner.get('start_ticks') == 'fake')


def started(bridge, count):
    made = [bridge.create(dict(selection=dict(selection(byok=False), request_key=f'timeshare-run-{n:03d}', goal_key=f'timeshare-goal-{n:03d}'), start=False))['experiment_id'] for n in range(count)]
    return made


def test_remote_opens_runs_up_to_the_slots_and_each_worker_gets_the_lock(tmp_path, monkeypatch, materialize):
    calls = fake_process(monkeypatch);alive_workers(monkeypatch)
    bridge = daily.DailyDevV2(tmp_path / 'daily', [], per_run_budget=True, sim_lock=tmp_path / 'sim.lock', run_slots=2)
    a, b, c = started(bridge, 3)
    assert bridge.start(a)['worker_started'] and bridge.start(b)['worker_started']
    with pytest.raises(DevConflict, match='run slots are in use'):bridge.start(c)
    assert all(argv[-2:] == ['--sim-lock', str((tmp_path / 'sim.lock').resolve())] for argv, _ in calls) and len(calls) == 2


def test_local_still_runs_one_at_a_time_and_its_worker_has_no_lock(tmp_path, monkeypatch, materialize):
    calls = fake_process(monkeypatch);alive_workers(monkeypatch)
    bridge = daily.DailyDevV2(tmp_path / 'daily', [], per_run_budget=True)
    a, b = started(bridge, 2)
    assert bridge.start(a)['worker_started']
    with pytest.raises(DevConflict, match='Another daily scheduler process is active'):bridge.start(b)
    assert len(calls) == 1 and '--sim-lock' not in calls[0][0]


def test_time_share_is_refused_where_it_does_not_belong(tmp_path):
    with pytest.raises(ValueError):daily.DailyDevV2(tmp_path / 'a', [], per_run_budget=False, sim_lock=tmp_path / 'l', run_slots=2)
    with pytest.raises(ValueError):daily.DailyDevV2(tmp_path / 'b', [], per_run_budget=True, sim_lock=tmp_path / 'l')
    from benchmarks.attention_harness import ui_server
    with pytest.raises(ValueError, match='public-gateway-mode'):
        ui_server.create_server(port=0, dev_v2_root=tmp_path / 'c', dev_v2_per_run_budget=True, sim_lock=tmp_path / 'l', run_slots=2)


# ---- an execution that times out keeps the robot until its simulator has stopped ----

@pytest.mark.parametrize('stops', [True, False])
def test_a_timed_out_execution_hands_the_robot_on_only_once_its_simulator_has_stopped(tmp_path, stops):
    from benchmarks.attention_harness.dev_agent_adapters import FakeExecutor
    from benchmarks.attention_harness.tests.test_dev_agent_d1 import broker, request, settled
    from benchmarks.attention_harness.dev_agent_workspace import Workspace, atomic_write
    from benchmarks.attention_harness.tests.test_dev_agent_d1 import CONFIG, SOURCE
    from benchmarks.attention_harness import sim_timeshare
    ws = Workspace(tmp_path / 'agent').prepare({'task_id': 'fixture', 'task_prompt': 'Move safely.'}, CONFIG);atomic_write(ws.ws, 'work/main.py', SOURCE)
    gate = threading.Event()
    class Hung(FakeExecutor):
        def execute(self, *a, **kw):gate.wait(5);return super().execute(*a, **kw)
        def stopped(self, wait=60.):return stops  # Whether the simulator is known to have exited after cancel.
    lock = tmp_path / 'sim.lock';kept = len(sim_timeshare.KEPT)
    b = broker(ws, executor=Hung(unsafe_test_transport=True), timeouts={'run': .05}, sim_token=SimToken(lock, label='me', poll=.01))
    try:
        b.accept(request(op='run', payload={'entry': 'work/main.py'}));receipt = settled(b)
        if stops:
            assert receipt['status'] == 'completed' and receipt['result']['execution_error']['error_class'] == 'TimeoutError'
            assert not locked_elsewhere(lock) and len(sim_timeshare.KEPT) == kept
        else:
            assert receipt['status'] == 'needs_review' and b.quarantined
            assert locked_elsewhere(lock) and len(sim_timeshare.KEPT) == kept + 1  # Held until this process exits.
    finally:
        gate.set();b.close()
        while len(sim_timeshare.KEPT) > kept:sim_timeshare.KEPT.pop().close()
