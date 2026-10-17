"""Standard-library-only tests for the recorded start-collision diagnostic."""
import hashlib
import json

import pytest

from benchmarks.attention_harness.diagnostics import start_collision_diagnose as diag


def _recording(tmp_path):
    execution = tmp_path / 'execution'
    planner = execution / 'planner'
    planner.mkdir(parents=True)
    world = [{'name': 'box', 'center': [0, 0, 0], 'half_size': [1, 1, 1]}]
    digest = hashlib.sha256(json.dumps({'cuboids': world, 'robot_pos': None},
                                       sort_keys=True, allow_nan=False).encode()).hexdigest()
    (planner / 'world-0001.json').write_text(json.dumps({'cuboids': []}))
    (planner / 'world-0002.json').write_text(json.dumps({'cuboids': world}))
    rows = [{'path': '/identity', 'request': '{}'},
            {'path': '/plan/ik_path', 'request': json.dumps({'current_q': [0],
                   'attachment': {'shape_names': ['held'], 'support_names': ['box']}}),
             'response': json.dumps({'world_sha256': digest})}]
    (planner / 'planner_http.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
    return execution


def test_arguments_and_last_recording(tmp_path):
    execution = _recording(tmp_path)
    args = diag.parse_args(['--execution-dir', str(execution), '--out', str(tmp_path / 'out'), '--dry-run'])
    assert args.dry_run
    assert diag.read_recording(execution)[:3][2] == '/plan/ik_path'
    with pytest.raises(SystemExit):
        diag.parse_args(['--execution-dir', str(execution), '--out', str(tmp_path / 'out')])
    (execution / 'planner' / 'world-0002.json').write_text(json.dumps({'cuboids': [{'name': 'other'}]}))
    with pytest.raises(ValueError, match='does not match'):
        diag.read_recording(execution)


def test_ranking_and_summary():
    cuboids = [{'name': name, 'center': center, 'half_size': [.5, .5, .5]}
               for name, center in [('far', [3, 0, 0]), ('near', [1, 0, 0]), ('held', [0, 0, 0])]]
    ranked = diag.rank_cuboids(cuboids, [[0, 0, 0, .1]], excluded_names={'held'})
    assert [r['name'] for r in ranked] == ['near', 'far']
    assert ranked[0]['distance_m'] == pytest.approx(.4)
    summary = diag.summarize_trials(ranked, {'near': False, 'far': True}, {1: False, 2: True})
    assert summary['single_removal_pass_names'] == ['far']
    assert summary['first_prefix_pass_n'] == 2
    result = {'status': 'ok', 'reproduction': {'proxy_collision_free': False},
              'leave_one_out': summary,
              'sphere_groups': {'ordinary': {'world_collision_free': False},
                                'carried': {'world_collision_free': True},
                                'bounds_and_self_collision_free': True}}
    assert '| near | 0.400000 |' in diag.format_markdown(result)
    result['support_exemption'] = {'proxy_collision_free': True,
                                   'exact_geometry_collision_free': True}
    assert '启用支撑面豁免后：proxy=True，exact=True' in diag.format_markdown(result)
    rotated = {'name': 'rotated', 'center': [0, 0, 0], 'half_size': [1, .1, .1],
               'quat_wxyz': [2 ** -.5, 0, 0, 2 ** -.5]}
    assert diag.cuboid_sphere_clearance(rotated, [0, 1.5, 0, .1]) == pytest.approx(.4)
    assert diag.summarize_trials(ranked, {}, {1: False, 2: False})['first_prefix_pass_n'] is None


def test_dry_run_writes_both_reports_without_gpu(tmp_path, monkeypatch):
    execution = _recording(tmp_path)
    monkeypatch.setattr(diag, 'run_gpu', lambda *args: pytest.fail('dry-run imported GPU path'))
    out = tmp_path / 'results'
    diag.main(['--execution-dir', str(execution), '--out', str(out), '--dry-run'])
    assert json.loads((out / 'results.json').read_text())['status'] == 'dry_run'
    assert 'GPU 碰撞验证未运行' in (out / 'results.md').read_text()
