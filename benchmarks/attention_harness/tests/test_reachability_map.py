"""Pure-data tests: never construct the planner or call GPU IK."""
import json
import math
from types import SimpleNamespace

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
    assert info['estimated_ik_seconds_range'] == [768.3, 1024.4]


def test_shift_inverse_includes_mount_yaw_and_rotated_cuboid():
    shift = (1., 2., math.pi/2)
    assert reach.shifted_point([2., 2., 3.], shift) == pytest.approx([0., -1., 3.])
    box = {'name': 'mujoco:1:test', 'center': [2., 2., 3.],
           'half_size': [.1, .2, .3], 'quat_wxyz': [1., 0., 0., 0.]}
    moved = reach.shifted_world([box], shift)[0]
    assert moved['center'] == pytest.approx([0., -1., 3.])
    assert moved['quat_wxyz'] == pytest.approx([math.sqrt(.5), 0., 0., -math.sqrt(.5)])
    assert moved['half_size'] == box['half_size']
    assert box['center'] == [2., 2., 3.]


def test_sweep_spec_and_compact_grid():
    shifts = reach.parse_sweep_spec(reach.DEFAULT_SWEEP_SPEC)
    assert len(shifts) == 28
    assert shifts[0] == (0., 0., 0.)
    assert shifts[-1] == (-.1, .4, .15)
    assert reach.parse_sweep_spec('dx=0,-.1;dy=.05;dyaw=0') == [(0., .05, 0.), (-.1, .05, 0.)]
    with pytest.raises(Exception):
        reach.parse_sweep_spec('dx=0;dy=nan;dyaw=0')
    points = reach.small_grid([1., 2., 3.])
    assert len(points) == 27
    assert next(p for p in points if p['id'] == 'x+0_y+0_z1')['position_base_m'] == [1., 2., 3.05]


def test_sweep_summary_and_markdown_from_handwritten_rows():
    poses = [{'name': 'initial'}, {'name': 'forward_down_45_roll_0'}]
    points = reach.small_grid([0., 0., 0.])
    rows = []
    for mode in ('without_collision', 'with_collision'):
        for pose in poses:
            for point in points:
                passed = mode == 'with_collision' and pose['name'] == 'initial' and point['id'] in {
                    'x+0_y+0_z0', 'x+0_y+0_z1'}
                rows.append({'mode': mode, 'orientation': pose['name'], 'point': point['id'],
                             'position_base_m': point['position_base_m'], 'success': passed})
    summary = reach.summarize_sweep_rows(rows, poses, points)
    assert summary['best_success_with_collision'] == 2
    assert summary['center_with_collision'] and summary['above_5cm_with_collision']
    chassis = {'path_clear': True, 'endpoint_clear': True, 'single_step': False,
               'steps_required': 2}
    aggregate = reach.sweep_summary([{'base_shift': [0., .3, 0.], 'summary': summary,
                                      'chassis': chassis}])
    assert aggregate['minimum_shift_center_and_5cm'] == [0., .3, 0.]
    md = reach.render_sweep_markdown({'summary': aggregate})
    assert '| 0,0.3,0 | initial | 2/27 | 是 | 是 | 是 | 是 | 需多步（2） |' in md


def test_zero_shift_keeps_legacy_data_and_rendering():
    world = [{'name': 'mujoco:1:a', 'center': [1., 2., 3.],
              'half_size': [1., 1., 1.], 'quat_wxyz': [1., 0., 0., 0.]}]
    assert reach.shifted_world(world, (0., 0., 0.)) is world
    assert reach.shifted_point([1., 2., 3.], (0., 0., 0.)) == [1., 2., 3.]
    report = {'summary': {'matrix': [], 'step6': [], 'promising': []},
              'fk_initial_base': {},
              'object_proxy': {'center_base_m': [1, 2, 3], 'distance_to_recorded_m': 0.},
              'world_to_base_quaternion_wxyz': [1, 0, 0, 0],
              'step6_target_base_m': [1, 2, 3]}
    expected = '''# Seed 101 左臂 IK 可达性图

坐标系：目标、网格和范围均为机器人底座系，单位 m；姿态为 wxyz。
“无碰撞”列仅清空外部世界；planner core 仍检查自碰撞和终点几何。
初始 FK（底座系）：{}
物体网格中心：[1, 2, 3]（obj_g* 碰撞盒中心的范围中点，仅为物体位置代理）
物体盒中心与给定世界物体位置换算值相距 0.0000 m。
世界到机器人底座旋转（wxyz）：[1, 0, 0, 0]。
第 6 步目标（底座系）：[1, 2, 3]；原始世界系位置和位移为四舍五入记录。
服务默认容差：位置 ≤0.004 m，姿态 ≤0.03 rad；单点 core.solve_ik，q0 每次相同。

| 姿态 | 无碰撞 | 带碰撞 | 无碰撞可达范围 min → max (m) | 带碰撞可达范围 min → max (m) |
| --- | ---: | ---: | --- | --- |

## 第 6 步目标

| 姿态 | 无碰撞 | 带碰撞 |
| --- | --- | --- |

## 带碰撞成功数排名前五


结论：本次网格中无碰撞成功 0 点姿态组合，带碰撞成功 0 点姿态组合；单点 IK 失败只能说明本次求解未获服务认可的解，不能单凭失败判定关节极限、障碍物或姿态是唯一原因。

逐目标结果和失败原因见 results.json。
'''
    assert reach.render_markdown(report) == expected


def test_sweep_dry_run_counts(capsys):
    reach.main(['--sweep', '--dry-run'])
    info = json.loads(capsys.readouterr().out)
    assert info['base_shifts'] == 28
    assert info['grid_positions'] == 27
    assert info['extra_step6_positions'] == 0
    assert info['ik_targets'] == 19656
    assert info['estimated_ik_seconds_range'] == [2948.4, 3931.2]


def test_chassis_multi_step_and_endpoint_from_handwritten_world():
    binding = {'center_in_planner_frame': [0., 0., 0.], 'half_size': [.1, .1, .1]}
    clear = reach.chassis_check([], binding, (.4, 0., 0.))
    assert clear['path_clear'] and clear['endpoint_clear']
    assert not clear['single_step'] and clear['steps_required'] == 2
    world = [{'name': 'mujoco:1:box', 'center': [.4, 0., 0.],
              'half_size': [.05, .05, .05], 'quat_wxyz': [1., 0., 0., 0.]}]
    blocked = reach.chassis_check(world, binding, (.4, 0., 0.))
    assert not blocked['path_clear'] and not blocked['endpoint_clear']
    assert 'box' in blocked['endpoint_reason']


def test_exclude_object_uses_exact_proxy_group_and_preserves_default():
    world = [
        {'name': 'mujoco:1:obj_g0', 'center': [0., 0., 0.]},
        {'name': 'mujoco:2:table', 'center': [2., 0., 0.]},
        {'name': 'mujoco:3:not_obj_g1', 'center': [3., 0., 0.]},
        {'name': 'mujoco:4:obj_g15', 'center': [1., 0., 0.]},
    ]
    assert reach.object_bbox_center(world) == ([.5, 0., 0.],
        ['mujoco:1:obj_g0', 'mujoco:4:obj_g15'])
    default, default_names = reach.collision_world(world)
    assert default is world and default_names == []
    filtered, names = reach.collision_world(world, True)
    assert names == ['mujoco:1:obj_g0', 'mujoco:4:obj_g15']
    assert filtered == [world[1], world[2]]
    assert all(filtered[i] is world[j] for i, j in enumerate((1, 2)))


def test_excluded_world_reaches_only_collision_mode_and_is_labeled(monkeypatch):
    world = [
        {'name': 'mujoco:1:obj_g0', 'center': [0., 0., 0.]},
        {'name': 'mujoco:2:table', 'center': [2., 0., 0.]},
    ]
    scenes = []
    class Core:
        last_failure = 'no_solution'
        last_solver_diagnostics = []
        def named(self, *_):
            return []
        def set_collision_world(self, scene):
            scenes.append(scene)
        def set_attachment(self, *_):
            pass
        def check_targets(self, *_):
            pass
        def solve_ik(self, **_):
            return None
    core = Core()
    body = {'current_q': [], 'joint_names': [], 'active_joints': [],
            'locked_joints': {}, 'env_id': 0}
    initial = {'position': [0., 0., 0.], 'quaternion_wxyz': [1., 0., 0., 0.]}
    prepared = (None, core, {'frame': 'robot base'}, body, world, 'tool', initial)
    monkeypatch.setattr(reach, 'grid', lambda _: [{'id': 'center', 'position_base_m': [0., 0., 0.]}])
    monkeypatch.setattr(reach, 'orientations', lambda _: [{'name': 'initial', 'quaternion_wxyz': [1., 0., 0., 0.]}])
    monkeypatch.setattr(reach, 'world_to_base', lambda *_: ([0., 0., 0.], [1., 0., 0., 0.]))
    args = SimpleNamespace(exclude_object=True, fixture=reach.FIXTURE, side='left')
    report = reach.run(args, {}, world, [0., 0., 0.], prepared=prepared)
    assert scenes == [[], [world[1]]]
    assert report['world_cuboids'] == 1
    assert report['object_proxy']['cuboids'] == 1
    assert report['excluded_object'] == {'count': 1, 'names': ['mujoco:1:obj_g0']}
    single_md = reach.render_markdown(report)
    assert single_md.startswith('# Seed 101 左臂 IK 可达性图（已排除目标物体）\n')
    assert '已排除 1 个目标物体盒：mujoco:1:obj_g0' in single_md
    sweep_md = reach.render_sweep_markdown({'excluded_object': report['excluded_object'],
        'summary': {'table': [], 'minimum_shift_center_and_5cm': None}})
    assert sweep_md.startswith('# Seed 101 左臂底座位移 IK 扫掠（已排除目标物体）\n')
    assert '已排除 1 个目标物体盒：mujoco:1:obj_g0' in sweep_md
    scenes.clear()
    args.exclude_object = False
    default_report = reach.run(args, {}, world, [0., 0., 0.], prepared=prepared)
    assert scenes == [[], world]
    assert 'excluded_object' not in default_report
    assert reach.render_markdown(default_report).startswith('# Seed 101 左臂 IK 可达性图\n')


@pytest.mark.parametrize('extra', [[], ['--sweep', '--sweep-spec', 'dx=0;dy=0.2,0.3,0.4;dyaw=0']])
def test_exclude_object_dry_run_count(capsys, extra):
    reach.main(['--dry-run', '--exclude-object', *extra])
    info = json.loads(capsys.readouterr().out)
    assert info['object_cuboids'] == info['excluded_object_cuboids'] == 16
    assert info['ik_targets'] == (2106 if extra else 5122)


def test_sweep_result_files_record_exclusion(tmp_path, monkeypatch):
    monkeypatch.setattr(reach, 'prepare', lambda *_: object())
    def fake_run(*_, **kwargs):
        return {'base_shift': list(kwargs['shift']),
                'excluded_object': {'count': 1, 'names': ['mujoco:1:obj_g0']},
                'summary': {'best_orientation': 'initial', 'best_success_with_collision': 0,
                            'center_with_collision': False, 'above_5cm_with_collision': False},
                'chassis': {'path_clear': True, 'endpoint_clear': True, 'single_step': True}}
    monkeypatch.setattr(reach, 'run', fake_run)
    reach.main(['--sweep', '--sweep-spec', 'dx=0;dy=0.2;dyaw=0', '--exclude-object',
                '--robot-config', 'unused.yml', '--assets-dir', 'unused-assets',
                '--out', str(tmp_path)])
    result = json.loads((tmp_path/'results.json').read_text())
    assert result['excluded_object'] == {'count': 1, 'names': ['mujoco:1:obj_g0']}
    assert result['shifts'][0]['excluded_object'] == result['excluded_object']
    md = (tmp_path/'results.md').read_text()
    assert md.startswith('# Seed 101 左臂底座位移 IK 扫掠（已排除目标物体）\n')
    assert 'mujoco:1:obj_g0' in md
