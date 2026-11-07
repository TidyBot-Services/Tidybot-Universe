"""Idle home page backend: fake bridge and fake PARCC answers only, no run is started for real."""
import io
import json
import ssl
import time
import urllib.error
from pathlib import Path

import pytest

from benchmarks.attention_harness import home_status as home

@pytest.mark.parametrize('row,expected', [
    ({'reason': 'eval_passed', 'state': 'completed'}, 'Solved'),
    ({'reason': 'ParccError: parcc/GLM unreachable after 5 attempts: connect_error', 'state': 'needs_review'}, 'could not be reached'),
    ({'reason': 'DevConflict: provider interrupted/deadline; paid request retained for review', 'state': 'needs_review', 'versions': [1] * 5}, 'writing v6'),
    ({'reason': 'input_bound_exceeded: 73189', 'state': 'completed'}, 'too long'),
    ({'reason': 'resource source drift: /x/ui/index.html', 'state': 'needs_review'}, 'program file changed'),
    ({'reason': 'target_G_E_reached', 'state': 'completed', 'versions': [1] * 10}, 'All 10 attempts'),
])
def test_why_a_run_ended_in_plain_words(row, expected):
    assert expected in home.plain_reason(row)


def test_today_counts_only_today_and_only_real_solves():
    now = time.time()
    rows = [{'created_at': now - 60, 'state': 'completed', 'reason': 'eval_passed', 'versions': [1, 2]},
            {'created_at': now - 120, 'state': 'completed', 'reason': 'target_G_E_reached', 'versions': [1] * 10},
            {'created_at': now - 3 * 86400, 'state': 'completed', 'reason': 'eval_passed', 'versions': [1]}]
    assert home.today_summary(rows, now) == {'runs': 2, 'solved': 1, 'attempts': 12}


def test_glm_that_answers_now_but_failed_today_reads_unstable(tmp_path):
    probe = {'result': {'glm': {'state': 'ready', 'detail': 'ok'}, 'qwen': {'state': 'blocked', 'detail': 'blocked'}}}
    r = home.readiness(probe, {'used_percent': 21.0}, 3, tmp_path)
    assert r['glm']['state'] == 'unstable' and '3 runs' in r['glm']['detail']
    assert r['qwen']['state'] == 'blocked' and r['codex'] == {'state': 'ready', 'detail': '21% of this week used.'}
    assert r['disk']['value'].endswith('GB')
    checking = home.readiness({'result': None}, None, 0, tmp_path)
    assert checking['glm']['state'] == checking['qwen']['state'] == 'checking' and checking['codex']['state'] == 'unknown'


class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.mark.parametrize('answer,state', [
    (lambda: FakeResponse(b'{"choices":[]}'), 'ready'),
    (lambda: (_ for _ in ()).throw(urllib.error.HTTPError('u', 403, 'x', {}, io.BytesIO(b'{"error":{"message":"Model is blocked"}}'))), 'blocked'),
    (lambda: (_ for _ in ()).throw(urllib.error.URLError('connect_error')), 'down'),
    (lambda: (_ for _ in ()).throw(urllib.error.URLError(ssl.SSLCertVerificationError(1, 'unable to get local issuer certificate'))), 'down'),
])
def test_probe_maps_parcc_answers_to_states(monkeypatch, answer, state):
    monkeypatch.setattr(home.urllib.request, 'urlopen', lambda request, timeout: answer())
    probe = home.ProviderProbe(url='http://parcc.invalid/v1/chat/completions', key='k')
    assert probe._one('parcc/GLM')['state'] == state
    assert home.ProviderProbe(url='http://x', key='')._one('parcc/GLM')['state'] == 'unknown'


def test_probe_says_plainly_when_the_parcc_certificate_cannot_be_verified(monkeypatch):
    def refuse(request, timeout):raise urllib.error.URLError(ssl.SSLCertVerificationError(1, 'unable to get local issuer certificate'))
    monkeypatch.setattr(home.urllib.request, 'urlopen', refuse)
    assert "cannot verify PARCC's security certificate" in home.ProviderProbe(url='http://x', key='k')._one('parcc/GLM')['detail']


class FakeBridge:
    def __init__(self, root, rows):self.root, self.rows = Path(root), rows
    def list(self):return [dict(r) for r in self.rows]


def fake_row(root, eid='devexp-old', task='pick_place_counter_to_sink', created=None, scene=False):
    cell = root / eid / 'cells' / 'cell-old'
    if scene:
        frame = cell / 'v1' / 'executions' / 'ep' / 'camera_initial.png';frame.parent.mkdir(parents=True, exist_ok=True);frame.write_bytes(b'png')
    return {'experiment_id': eid, 'key': 'cell-' + eid, 'created_at': created or time.time(), 'state': 'needs_review', 'owner': None,
            'reason': 'ParccError: parcc/GLM unreachable after 5 attempts: connect_error', 'versions': [], 'task_id': task,
            'seed': 102, 'policy_id': 'autonomous', 'artifact_dir': str(cell), 'locked': {'config': {}}}


def test_status_is_general_and_explains_recent_runs(tmp_path):
    class Probe:
        def snapshot(self, force=False):return {'result': {'glm': {'state': 'down', 'detail': 'No answer: URLError.'}, 'qwen': {'state': 'blocked', 'detail': 'b'}}, 'checked_at': 1.0, 'checking': False}
    s = home.status(FakeBridge(tmp_path, [fake_row(tmp_path)]), Probe(), {'used_percent': 21.0})
    assert s['running'] == [] and s['today']['runs'] == 1 and s['planner_seconds'] is None and s['scenes'] == []
    assert s['readiness']['glm']['state'] == 'down' and s['readiness']['codex']['state'] == 'ready'
    assert s['recent'][0]['why'] == 'The PARCC model service could not be reached.' and s['recent'][0]['task'] == 'Pick Place: Counter → Sink'
    assert 'setup' not in s and 'last_run' not in s          # the cover is not tied to one task or run


def test_scenes_take_the_newest_frame_of_each_task_and_refuse_unlisted_runs(tmp_path):
    now = time.time()
    rows = [fake_row(tmp_path, 'devexp-a', created=now - 10, scene=True), fake_row(tmp_path, 'devexp-b', created=now - 20, scene=True),
            fake_row(tmp_path, 'devexp-c', task='open_cabinet', created=now - 30, scene=True), fake_row(tmp_path, 'devexp-d', task='counter_to_sink', created=now - 40)]
    side = tmp_path / 'devexp-a' / 'cells' / 'cell-old' / 'v1' / 'executions' / 'ep' / 'recording_views' / 'side' / '000000.jpg'
    side.parent.mkdir(parents=True);side.write_bytes(b'jpg')
    assert home.scenes(rows, tmp_path) == [{'experiment_id': 'devexp-a', 'view': 'overview'}, {'experiment_id': 'devexp-c', 'view': 'overview'},
                                           {'experiment_id': 'devexp-a', 'view': 'side'}]
    assert home.scene_for(rows, tmp_path, 'devexp-c').name == 'camera_initial.png'
    assert home.scene_for(rows, tmp_path, 'devexp-a', 'side').name == '000000.jpg'
    assert home.scene_for(rows, tmp_path, 'devexp-zzz') is None and home.scene_for(rows, tmp_path, 'devexp-a', '../../etc') is None
    assert home.scene_image(rows[0], tmp_path / 'elsewhere') is None   # frames outside the Dev v2 root are never served


def test_a_stopped_experiment_wins_over_a_cell_still_marked_planned():
    assert home.shown_state({'state': 'planned', 'experiment_state': 'needs_review'}) == 'needs_review'
    assert home.shown_state({'state': 'running', 'experiment_state': 'running'}) == 'running'
    assert home.shown_state({'state': 'failed', 'experiment_state': 'completed'}) == 'failed'


def test_attempts_and_status_flags_match_the_main_ui():
    # Attempts are executions that ran (used.executions), not written versions.
    row = {'state': 'needs_review', 'reason': 'DevConflict: Native execution infrastructure/unresolved', 'versions': [1],
           'used': {'executions': 0}, 'budget': {'executions': 5}, 'jobs': []}
    assert home.attempts_used(row) == 0 and home.attempts_limit(row) == 5
    assert home.SYSTEM_PROBLEM.search(row['reason'])
    failed = {'state': 'completed', 'jobs': [{'kind': 'execute', 'receipt': {'execution_status': 'failed'}}] * 2}
    assert home.all_attempts_failed(failed)
    assert not home.all_attempts_failed({**failed, 'jobs': [{'kind': 'execute', 'receipt': {'execution_status': 'completed'}}]})
    assert not home.all_attempts_failed({'state': 'completed', 'jobs': []})



def test_an_agent_run_gives_the_cover_its_scene(tmp_path):
    frame = tmp_path/'devexp-a/artifacts/devexp-a/cells/c/agent/private/executions/e000001/openarm365-dev-x/camera_initial.png'
    frame.parent.mkdir(parents=True);frame.write_bytes(b'png')
    assert home.scene_image({'experiment_id': 'devexp-a', 'key': 'c'}, tmp_path) == frame.resolve()
