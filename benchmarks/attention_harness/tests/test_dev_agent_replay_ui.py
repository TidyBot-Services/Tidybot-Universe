"""Region 03: authored plans and trace-derived stages, entirely offline."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from benchmarks.attention_harness.dev_agent_ui_projection import (
    attempt_plan, plan_sentence, plain_sentence, replay_stages, snapshot_notes, version_evidence,
)
from benchmarks.attention_harness.tests.test_dev_agent_d1 import workspace, broker, request, settled

R6 = json.loads((Path(__file__).parent/'fixtures/agent-timeline-r6.json').read_text())


@pytest.mark.parametrize('index,failure', [(0, 'Close'), (1, 'Descent'), (2, None)])
def test_r6_stage_strip_from_actual_trace(index, failure):
    row = R6['versions'][index]
    events = R6['evidence'][f"executions/{row['execution_id']}/trace.json"]
    stages = replay_stages(events, row['evaluator_review'], row['native_success'])
    assert 3 <= len(stages) <= 5
    assert [s['label'] for s in stages if s['state'] == 'failed'] == ([failure] if failure else [])
    assert stages == row['eval_stages']
    if failure:
        failed = next(i for i, s in enumerate(stages) if s['state'] == 'failed')
        assert all(s['state'] == 'completed' for s in stages[:failed])
        assert all(s['state'] == 'unreached' for s in stages[failed+1:])
        assert 2 <= len(stages[failed]['cause'].split()) <= 4
    else:
        assert all(s['state'] == 'completed' for s in stages)


def notes_cut(root, number, text, cut='pre-submit'):
    raw = text.encode(); digest = hashlib.sha256(raw).hexdigest()
    (root/'objects').mkdir(parents=True, exist_ok=True)
    (root/'objects'/digest).write_bytes(raw)
    (root/f'{cut}-v{number}.json').write_text(json.dumps({'files': {'work/notes.md': {'sha256': digest, 'bytes': len(raw)}}}))


def test_r6_plan_uses_only_notes_added_between_pre_submit_snapshots(tmp_path, monkeypatch):
    cell = {'artifact_dir': str(tmp_path), 'key': 'fixture'}
    root = tmp_path/'agent/private/snapshots'
    for n, note in R6['notes'].items():notes_cut(root, int(n), note)
    for row in R6['versions']:
        plan = attempt_plan(cell, row, {})
        previous = R6['notes'].get(str(row['version']-1), '')
        expected = R6['notes'][str(row['version'])][len(previous):].strip()
        from benchmarks.attention_harness.dev_agent_view import clean
        assert plan['note'] == clean(expected)
        assert plan['summary'] == plan_sentence(clean(expected).splitlines()[-1], row['version'])
        assert plan['source'] == 'notes'
        assert plan['summary'] != row['evaluator_review']['reason']
        monkeypatch.setattr('benchmarks.attention_harness.dev_agent_ui_projection.public',
                            lambda cell, name, default=None: row['evaluator_review'] if name.endswith('eval.json') else default)
        version, _ = version_evidence(cell, dict(version=row['version']))
        assert version['reason'] == plan['summary'] != row['evaluator_review']['reason']


@pytest.mark.parametrize('number,start', [
    (1, 'Formal v1 submitted: higher grasp .965'),
    (2, 'v0002 now running: use that pregrasp, straight descent .945'),
    (3, 'v0003 submitted using these three joint poses'),
])
def test_r6_plan_submission_paragraph_expected_starts(tmp_path, number, start):
    cell = {'artifact_dir': str(tmp_path)}; root = tmp_path/'agent/private/snapshots'
    for n, note in R6['notes'].items():notes_cut(root, int(n), note)
    for n, note in R6['post_exec_notes'].items():notes_cut(root, int(n), note, 'post-exec')
    plan = attempt_plan(cell, {'version': number}, {})
    assert plan['summary'].startswith(start)
    assert '\n' not in plan['summary']
    assert plan == R6['versions'][number-1]['plan']
    assert 'execution completed all 9' not in plan['note']
    assert attempt_plan(cell, {'version': number}, {'summary': 'Use the authored summary.'})['summary'] == 'Use the authored summary.'


@pytest.mark.parametrize('paragraph,expected', [
    ('Previous failure. v0002 uses a straight descent. Then close.', 'v0002 uses a straight descent.'),
    ('Previous failure. v2 uses a straight descent. Then close.', 'v2 uses a straight descent.'),
    ('v20 was different. Lower the wrist\nthen close.', 'Lower the wrist then close.'),
])
def test_plan_sentence_prefers_this_version_otherwise_last(paragraph, expected):
    assert plan_sentence(paragraph, 2) == expected


def test_post_exec_plan_recovery_ignores_outcome_and_corrupt_notes(tmp_path):
    cell = {'artifact_dir': str(tmp_path)}; root = tmp_path/'agent/private/snapshots'
    notes_cut(root, 1, 'Recap.\nCurrent plan. Lower the wrist.\n')
    notes_cut(root, 1, 'Recap.\nCurrent plan. Lower the wrist.\nv0001 execution completed.\n', 'post-exec')
    assert attempt_plan(cell, {'version': 1}, {})['summary'] == 'Lower the wrist.'
    manifest = json.loads((root/'post-exec-v1.json').read_text())
    (root/'objects'/manifest['files']['work/notes.md']['sha256']).write_text('v0001 submitted tampered plan.')
    assert attempt_plan(cell, {'version': 1}, {})['summary'] == 'Lower the wrist.'


def test_summary_wins_and_notes_diff_missing_or_corrupt_snapshots_fail_closed(tmp_path):
    cell = {'artifact_dir': str(tmp_path), 'key': 'fixture'}; root = tmp_path/'agent/private/snapshots'
    notes_cut(root, 1, 'Old plan.\nKeep this.\n')
    notes_cut(root, 2, 'Revised plan.\nKeep this.\n')
    plan = attempt_plan(cell, {'version': 2, 'summary': 'Lower the wrist.'}, {})
    assert plan == dict(summary='Lower the wrist.', note='Revised plan.', source='summary')
    assert attempt_plan(cell, {'version': 3, 'reason': 'Eval must never be plan'}, {})['note'] == ''
    manifest = json.loads((root/'pre-submit-v2.json').read_text())
    (root/'objects'/manifest['files']['work/notes.md']['sha256']).write_text('tampered')
    assert snapshot_notes(cell, 2) is None
    assert attempt_plan(cell, {'version': 2}, {})['source'] == 'unavailable'


def test_suggestion_strips_citations_and_keeps_one_sentence():
    assert plain_sentence('Adjust the wrist (stdout.txt:10–11). Then lift (trace.json).') == 'Adjust the wrist.'


@pytest.mark.parametrize('summary,error', [(None, 'invalid_summary'), ('', 'invalid_summary'),
    ('  ', 'invalid_summary'), ('x'*81, 'invalid_summary'), (42, 'invalid_summary'),
    ('line\nbreak', 'invalid_summary'), ('native_success secret', 'unsafe_summary'),
    ('Read /etc/passwd', 'unsafe_summary')])
def test_broker_rejects_missing_long_or_unscreened_summary_before_charge(workspace, summary, error):
    b = broker(workspace)
    try:
        payload = {'entry': 'work/main.py'}
        if summary is not None:payload['summary'] = summary
        req = request(op='submit');req['payload'] = payload
        assert b.accept(req)['error'] == error
        assert b.budget.counts == {'versions': 0, 'executions': 0, 'probes': 0}
    finally:b.close()


def test_broker_accepts_80_char_summary_and_publishes_it(workspace):
    b = broker(workspace)
    try:
        b.accept(request(op='submit', payload={'summary': '移'*80}))
        assert settled(b)['status'] == 'completed'
        assert json.loads((workspace.canonical/'versions/v0001/submission.json').read_text())['summary'] == '移'*80
    finally:b.close()


@pytest.mark.parametrize('summary', [None, '', 'x'*81])
def test_devtool_requires_summary_before_writing_request(workspace, monkeypatch, summary):
    from benchmarks.attention_harness import dev_agent_tools as cli
    monkeypatch.setattr(cli, '__file__', str(workspace.ws/'docs/devtool.py'))
    monkeypatch.setattr(sys, 'argv', ['devtool.py', 'submit'] + ([] if summary is None else ['--summary', summary]))
    with pytest.raises(SystemExit) as error:cli.main()
    assert error.value.code == 2
    assert not list((workspace.ws/'rpc/requests').iterdir())


def test_devtool_serializes_summary_and_no_eval_run_remains_unchanged(workspace, monkeypatch):
    from benchmarks.attention_harness import dev_agent_tools as cli
    monkeypatch.setattr(cli, '__file__', str(workspace.ws/'docs/devtool.py'))
    monkeypatch.setattr(cli, 'wait', lambda *a: {'status': 'completed'})
    for op, flags in [('submit', ['--summary', 'Adjust wrist.']), ('run', ['--no-eval'])]:
        monkeypatch.setattr(sys, 'argv', ['devtool.py', op, '--request-id', op, '--entry', 'work/main.py', *flags])
        assert cli.main() == 0
        payload = json.loads((workspace.ws/f'rpc/requests/{op}.json').read_text())['payload']
        assert payload == {'entry': 'work/main.py', 'reason': '', **({'summary': 'Adjust wrist.'} if op == 'submit' else {})}


def test_snapshot_missing_authored_file_reaches_operator_as_4xx_json(workspace,tmp_path,monkeypatch):
    import io
    from types import SimpleNamespace
    from benchmarks.attention_harness import ui_server
    from benchmarks.attention_harness.dev_agent_replay import freeze_origin,provision_replay
    (workspace.ws/'scratch/grasp.png').write_bytes(b'authored drawing')
    cut=freeze_origin(workspace,'post-exec-v1')
    manifest=json.loads(Path(cut['path']).read_bytes())
    (workspace.private/'snapshots/objects'/manifest['files']['scratch/grasp.png']['sha256']).unlink()
    class Bridge:
        def post(self,path,body,**kwargs):
            return provision_replay(cut,tmp_path/'replay')
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',lambda address,handler:SimpleNamespace(handler=handler))
    monkeypatch.setattr(ui_server.secrets,'token_urlsafe',lambda size:'test-control-token')
    server=ui_server.create_server(dev_v2_bridge=Bridge(),port=0)
    handler=object.__new__(server.handler)
    handler.server=server
    handler.path='/api/dev-v2/experiments'
    handler.headers={'X-Attention-Token':'test-control-token','Content-Type':'application/json','Content-Length':'2'}
    handler.rfile=io.BytesIO(b'{}')
    result=[];handler._json=lambda value,status=200:result.append((value,status))
    handler.do_POST()
    assert result==[({'error':'snapshot_object_missing: scratch/grasp.png (file was deleted; continue-from-attempt is unavailable for this cut)'},409)]
