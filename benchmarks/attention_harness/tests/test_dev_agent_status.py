"""10-30: the operator banner/captions follow each version's native verdict (sol3 stores it under
version['agent']); an Eval that did not run is labelled with its recorded reason, never "awaiting"."""
import json
from benchmarks.attention_harness.dev_agent_ui_projection import version_evidence, native_of
from benchmarks.attention_harness.dev_agent_workspace import atomic_write, encode


def cell(tmp_path, state, versions):
    return dict(key='k'*64, experiment_id='devexp-x', state=state, policy_id='reactive_help',
                artifact_dir=str(tmp_path), task_id='t', seed=102, versions=versions)


def run(c):
    return [version_evidence(c, v, media=False, instruments=False) for v in c['versions']]


def test_sol3_style_versions_show_native_solved_and_eval_did_not_run(tmp_path):
    private = tmp_path/'agent/private'; private.mkdir(parents=True)
    atomic_write(private, 'eval/v0003/error.json', encode(dict(error_class='OSError', error_text='[Errno 28] No space left on device')))
    unavailable = dict(status='unavailable', passed=None, error='OSError', reason='Eval unavailable for v0003: OSError')
    versions = [dict(version=1, version_id='v0001', valid=True, eval=dict(status='completed', passed=False, reason='Not achieved.'), agent=dict(native_success=False)),
                dict(version=2, version_id='v0002', valid=True, eval=dict(status='completed', passed=False, reason='Not achieved.'), agent=dict(native_success=False)),
                dict(version=3, version_id='v0003', valid=True, eval=unavailable, agent=dict(native_success=True))]
    rows = run(cell(tmp_path, 'completed', versions))
    assert [r['native_success'] for r, _ in rows] == [False, False, True]
    assert [r['story']['short'] for r, _ in rows] == ['not solved', 'not solved', 'solved']
    assert rows[2][0]['eval_unavailable'] == 'disk full (ENOSPC)'
    assert all('awaiting' not in json.dumps(r['story']) for r, _ in rows)


def test_r6_style_top_level_native_is_unchanged(tmp_path):
    versions = [dict(version=1, version_id='v0001', valid=True, native_success=False, eval=dict(status='completed', passed=False, reason='Grasp not confirmed: no bilateral contact.')),
                dict(version=3, version_id='v0003', valid=True, native_success=True, eval=dict(status='completed', passed=True, reason='Inside the sink.'))]
    rows = run(cell(tmp_path, 'completed', versions))
    assert [r['story']['short'] for r, _ in rows] == ['grasp not confirmed', 'solved']
    assert not any('eval_unavailable' in r for r, _ in rows)


def test_terminal_run_without_verdict_never_says_awaiting_and_eval_pass_follows_native(tmp_path):
    versions = [dict(version=1, version_id='v0001', valid=True, eval={}, agent=dict(native_success=False)),
                dict(version=2, version_id='v0002', valid=True, eval=dict(status='completed', passed=True, reason='Looks done.'), agent=dict(native_success=False))]
    ended = run(cell(tmp_path, 'cancelled', versions))
    assert ended[0][0]['story']['short'] == 'Eval did not run' and ended[0][0]['eval_unavailable'] == 'no verdict was recorded'
    assert ended[1][0]['story']['short'] == 'Eval passed, task check failed'
    live = run(cell(tmp_path, 'running', versions[:1]))
    assert live[0][0]['story']['short'] == 'awaiting evaluation'   # only while the run is still going


def test_native_of_reads_both_layouts():
    assert native_of(dict(native_success=True)) is True
    assert native_of(dict(agent=dict(native_success=False))) is False
    assert native_of(dict(agent={})) is None
