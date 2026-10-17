"""Pure-data tests: never construct the planner or call GPU IK."""
import json
import math

import pytest

from benchmarks.attention_harness.diagnostics import reachability_map as reach


def test_grid_and_fixture_object_proxy():
    fixture = json.loads(reach.FIXTURE.read_text())
    center, names = reach.object_bbox_center(fixture['world'])
    assert len(names) == 16
    assert all(name.split(':')[-1].startswith('obj_g') for name in names)
    points = reach.grid(center)
    assert len(points) == 196
    assert len({p['id'] for p in points}) == 196
    assert points[0]['offset_m'] == [-.12, -.12, 0.]
    assert points[-1]['offset_m'] == [.12, .12, .15]
    assert all(math.dist(p['position_base_m'],
                         [center[i] + p['offset_m'][i] for i in range(3)]) < 1e-8
               for p in points)


def test_orientations_and_world_transform():
    poses = reach.orientations((1., 0., 0., 0.))
    assert len(poses) == 13
    assert len({p['name'] for p in poses}) == 13
    for p in poses:
        q = p['quaternion_wxyz']
        assert math.sqrt(reach.dot(q, q)) == pytest.approx(1.)
        assert reach.rotate(q, (0., 0., 1.)) == pytest.approx(p['tool_z_base'], abs=1e-12)
    for prefix in ('forward_horizontal', 'forward_down_45', 'vertical_down'):
        assert [p['name'] for p in poses if p['name'].startswith(prefix)] == [
            f'{prefix}_roll_{r}' for r in reach.ROLLS_DEG]
    world_q = (reach.WORLD_INITIAL_XYZW[3], *reach.WORLD_INITIAL_XYZW[:3])
    fake_fk = {'position': [1., 2., 3.], 'quaternion_wxyz': world_q}
    converted, rotation = reach.world_to_base(reach.WORLD_MILK, fake_fk)
    assert converted == pytest.approx(reach.add(fake_fk['position'],
                                                reach.sub(reach.WORLD_MILK, reach.WORLD_INITIAL_TCP)))
    assert abs(rotation[0]) == pytest.approx(1.)


def test_summary_and_markdown_with_handmade_results():
    poses = [{'name': 'initial'}, {'name': 'vertical_down_roll_0'}]
    points = [{'id': 'near'}, {'id': 'far'}, {'id': 'step6'}]
    passes = {('without_collision', 'initial', 'near'),
              ('without_collision', 'initial', 'step6'),
              ('with_collision', 'vertical_down_roll_0', 'far')}
    rows = []
    for mode in ('without_collision', 'with_collision'):
        for pose in poses:
            for point in points:
                key = (mode, pose['name'], point['id'])
                rows.append({'mode': mode, 'orientation': pose['name'], 'point': point['id'],
                             'position_base_m': [0. if point['id'] == 'near' else 1., 2., 3.],
                             'success': key in passes,
                             'reason': None if key in passes else 'official_ik_non_success'})
    summary = reach.summarize(rows, poses, points)
    assert summary['matrix'][0]['without_collision']['success'] == 1
    assert summary['matrix'][0]['without_collision']['total'] == 2
    assert summary['matrix'][0]['without_collision']['reachable_bounds_base_m'] == {
        'min': [0., 2., 3.], 'max': [0., 2., 3.]}
    assert summary['matrix'][0]['with_collision']['reachable_bounds_base_m'] is None
    assert summary['promising'][0]['orientation'] == 'vertical_down_roll_0'
    assert summary['step6'][0]['without_collision']['success']
    md = reach.render_markdown({'summary': summary, 'fk_initial_base': {},
        'object_proxy': {'center_base_m': [0, 0, 0], 'distance_to_recorded_m': .01},
        'world_to_base_quaternion_wxyz': [1, 0, 0, 0], 'step6_target_base_m': [1, 2, 3]})
    assert '| initial | 1/2 (50.0%) | 0/2 (0.0%) |' in md
    assert '| initial | 成功 | 失败：official_ik_non_success |' in md
    assert '带碰撞成功 1 点姿态组合' in md


def test_dry_run_uses_no_robot_config_or_gpu(capsys):
    reach.main(['--dry-run', '--side', 'left'])
    info = json.loads(capsys.readouterr().out)
    assert info['grid_positions'] == 196
    assert info['orientations'] == 13
    assert info['ik_targets'] == 5122
