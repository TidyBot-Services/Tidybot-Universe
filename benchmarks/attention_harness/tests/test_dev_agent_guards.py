"""Identical-source guard and the Eval/native disagreement stop (10-29)."""
from benchmarks.attention_harness.dev_agent_loop import DISAGREEMENT_LIMIT, disagreement_streak
from benchmarks.attention_harness.dev_agent_workspace import atomic_write
from benchmarks.attention_harness.tests.test_dev_agent_d1 import broker, request, settled, workspace  # noqa: F401


def test_identical_formal_source_is_rejected_without_charge(workspace):
    b = broker(workspace)
    try:
        b.accept(request('r1', 'submit', {'entry': 'work/main.py'}));assert settled(b, 'r1')['status'] == 'completed'
        value = b.accept(request('r2', 'submit', {'entry': 'work/main.py'}))
        assert value['status'] == 'rejected' and value['error'] == 'unchanged_source'
        assert 'run --no-eval' in value['hint']
        assert b.budget.counts == dict(versions=1, executions=1, probes=0)
        # A no-eval run of the same code is still allowed.
        b.accept(request('r3', 'run', {'entry': 'work/main.py'}));assert settled(b, 'r3')['status'] == 'completed'
        # Any change is accepted as the next version.
        atomic_write(workspace.ws, 'work/main.py', (workspace.ws/'work/main.py').read_bytes()+b'\n# changed\n')
        b.accept(request('r4', 'submit', {'entry': 'work/main.py'}));r = settled(b, 'r4')
        assert r['status'] == 'completed' and r['version_id'] == 'v0002'
    finally:b.close()


def test_invalid_previous_submission_does_not_block_resubmit(workspace):
    atomic_write(workspace.ws, 'work/bad.py', b'bad syntax !!!')
    b = broker(workspace)
    try:
        b.accept(request('r1', 'submit', {'entry': 'work/bad.py'}));settled(b, 'r1')
        b.accept(request('r2', 'submit', {'entry': 'work/main.py'}));assert settled(b, 'r2')['status'] == 'completed'
        b.accept(request('r3', 'submit', {'entry': 'work/main.py'}))
        assert b.records['r3']['response']['error'] == 'unchanged_source'
    finally:b.close()


def row(v, verdict, native):
    return dict(version=v, eval_verdict=verdict, native_success=native)


def test_disagreement_streak_counts_only_consecutive_pass_native_false():
    assert DISAGREEMENT_LIMIT == 2
    assert disagreement_streak([row(1, 'fail', False), row(2, 'pass', False)]) == 1
    assert disagreement_streak([row(2, 'pass', False), row(1, 'fail', False), row(3, 'pass', False)]) == 2
    assert disagreement_streak([row(1, 'pass', False), row(2, 'fail', False), row(3, 'pass', False)]) == 1
    # Unknown native or unavailable Eval breaks the streak.
    assert disagreement_streak([row(1, 'pass', False), row(2, 'pass', None)]) == 0
    assert disagreement_streak([row(1, 'pass', False), row(2, 'unavailable', False)]) == 0
    # d7e2e2 shape: v2..v7 pass/false would have stopped at v3.
    rows = [row(1, 'fail', False)]+[row(v, 'pass', False) for v in range(2, 8)]
    assert next(v for v in range(1, 8) if disagreement_streak(rows[:v]) >= DISAGREEMENT_LIMIT) == 3


def test_drive_stops_after_two_eval_pass_native_fail_versions(tmp_path):
    from benchmarks.attention_harness.dev_agent_workspace import Workspace
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG, SOURCE, PassEval, run_fake
    ws = Workspace(tmp_path/'agent').prepare({'task_prompt': 'Exercise the fake tools.'}, CONFIG)
    atomic_write(ws.ws, 'work/main.py', SOURCE)
    result, session, ledger = run_fake(ws, ["tool('submit')", "tool('submit'); time.sleep(20)", "tool('submit')"],
                                       evaluator=PassEval())
    assert result['reason'] == 'eval_native_disagreement' and result['state'] == 'failed'
    assert 'fatal_cause' not in result
    assert ledger.snapshot()['used']['formal_versions'] == 2
    prohibited = ('native_success', 'object_in_sink', 'eval_native_disagreement')
    for path in ws.ws.rglob('*'):
        if path.is_file(): assert not any(w in path.read_text(errors='ignore') for w in prohibited), path


def test_unchanged_only_turns_count_as_idle(tmp_path):
    from benchmarks.attention_harness.dev_agent_workspace import Workspace
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG, SOURCE, run_fake
    ws = Workspace(tmp_path/'agent').prepare({'task_prompt': 'Exercise the fake tools.'}, CONFIG)
    atomic_write(ws.ws, 'work/main.py', SOURCE)
    # The fake helper changes code on resubmit; bypass it to resubmit byte-identical code.
    raw = "import subprocess;subprocess.run(['/usr/bin/python3','docs/devtool.py','submit','--summary','Same code again.'],capture_output=True)"
    result, session, ledger = run_fake(ws, ["tool('submit')", raw])
    assert result['reason'] == 'agent_idle', result
    assert ledger.snapshot()['used']['formal_versions'] == 1
