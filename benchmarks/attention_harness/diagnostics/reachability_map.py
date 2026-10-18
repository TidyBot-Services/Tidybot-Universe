"""Read-only seed-101 reachability scan using the planner core's single-target IK."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from .ik_path_bench import diagnostic_files, request_inputs

FIXTURE = Path(__file__).resolve().parents[1] / 'tests/fixtures/ik_path_world.json'
# Recorded public SDK initial pose, xyzw; see reference_ladder.py. It is not in the fixture.
WORLD_INITIAL_TCP = (0.4381810091, -2.2771536622, 1.0893940270)
WORLD_INITIAL_XYZW = (0.0773645919, 0.7601851670, -0.3816544795, -0.5200702741)
WORLD_MILK = (0.4246760939, -2.3935140080, 0.9621218952)
WORLD_STEP6_CURRENT = (0.485, -2.316, 1.022)  # rounded observed position
WORLD_STEP6_DELTA = (0.0089, -0.0083, -0.0138)  # rounded command
POSITION_TOLERANCE_M = .004
ORIENTATION_TOLERANCE_RAD = .03
ROLLS_DEG = (0, 90, 180, 270)
DEFAULT_SWEEP_SPEC = 'dx=0,-0.1;dy=0,0.05,0.10,0.15,0.20,0.30,0.40;dyaw=0,0.15'
GRASP_SWEEP_SPEC = 'dx=0;dy=0.20,0.25,0.30,0.35;dyaw=0'
IK_SECONDS_RANGE = (.15, .20)


def add(a, b):
    return tuple(x + y for x, y in zip(a, b))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def unit(v):
    n = math.sqrt(dot(v, v))
    if n < 1e-12:
        raise ValueError('zero direction')
    return tuple(x/n for x in v)


def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw*bw-ax*bx-ay*by-az*bz,
            aw*bx+ax*bw+ay*bz-az*by,
            aw*by-ax*bz+ay*bw+az*bx,
            aw*bz+ax*by-ay*bx+az*bw)


def quat_conj(q):
    return (q[0], -q[1], -q[2], -q[3])


def rotate(q, v):
    return quat_mul(quat_mul(q, (0., *v)), quat_conj(q))[1:]


def matrix_quat(x, y, z):
    """Convert column basis to unit wxyz quaternion."""
    m = ((x[0], y[0], z[0]), (x[1], y[1], z[1]), (x[2], y[2], z[2]))
    trace = m[0][0] + m[1][1] + m[2][2]
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        q = (.25*s, (m[2][1]-m[1][2])/s, (m[0][2]-m[2][0])/s, (m[1][0]-m[0][1])/s)
    else:
        i = max(range(3), key=lambda k: m[k][k])
        j, k = (i+1)%3, (i+2)%3
        s = math.sqrt(1 + m[i][i] - m[j][j] - m[k][k]) * 2
        v = [0., 0., 0.]
        v[i] = .25*s
        v[j] = (m[j][i] + m[i][j])/s
        v[k] = (m[k][i] + m[i][k])/s
        q = ((m[k][j]-m[j][k])/s, *v)
    return unit(q)


def orientation_for_axis(axis, roll_deg):
    """Tool +z follows axis; roll is around that tool z axis."""
    z = unit(axis)
    reference = (0., 0., 1.) if abs(z[2]) < .9 else (1., 0., 0.)
    x = unit(sub(reference, tuple(dot(reference, z)*v for v in z)))
    y = cross(z, x)
    base = matrix_quat(x, y, z)
    a = math.radians(roll_deg)/2
    return unit(quat_mul(base, (math.cos(a), 0., 0., math.sin(a))))


def orientations(initial_wxyz):
    answer = [{'name': 'initial', 'quaternion_wxyz': list(unit(initial_wxyz)),
               'tool_z_base': list(rotate(unit(initial_wxyz), (0., 0., 1.)))}]
    axes = {'forward_horizontal': (1., 0., 0.),
            'forward_down_45': unit((1., 0., -1.)), 'vertical_down': (0., 0., -1.)}
    for name, axis in axes.items():
        for roll in ROLLS_DEG:
            answer.append({'name': f'{name}_roll_{roll}',
                           'quaternion_wxyz': list(orientation_for_axis(axis, roll)),
                           'tool_z_base': list(axis)})
    return answer


def grasp_orientation(theta_deg, axis_sign, pitch_deg, forward_sign):
    """World TCP basis: local y closes, local z approaches, x=y cross z."""
    theta = math.radians(theta_deg)
    n = (axis_sign*math.cos(theta), axis_sign*math.sin(theta), 0.)
    f = (-math.sin(theta)*forward_sign, math.cos(theta)*forward_sign, 0.)
    pitch = math.radians(pitch_deg)
    a = (math.sin(pitch)*f[0], math.sin(pitch)*f[1], -math.cos(pitch))
    x = cross(n, a)
    q = matrix_quat(x, n, a)
    return {'theta_deg': theta_deg, 'axis_sign': axis_sign, 'pitch_deg': pitch_deg,
            'forward_sign': forward_sign, 'n_world': list(n), 'a_world': list(a),
            'quaternion_world_xyzw': [*q[1:], q[0]]}


def grasp_orientations(narrow_deg, pitches=(0, 20, 40, 60)):
    return [grasp_orientation(narrow_deg+offset, axis_sign, pitch, forward_sign)
            for offset in (-15, 0, 15) for axis_sign in (1, -1)
            for pitch in pitches for forward_sign in ((1,) if pitches == (0,) else (1, -1))]


def summarize_grasp(rows):
    matrix = []
    for row in rows:
        matrix.append({**row, 'both_success': row['pregrasp']['success'] and row['grasp']['success']})
    winners = [row for row in matrix if row['both_success'] and row['chassis']['path_clear']
               and row['chassis']['endpoint_clear']]
    winners.sort(key=lambda row: (row['pitch_deg'], math.hypot(*row['base_shift'][:2]),
                                  abs(row['base_shift'][2]), row['theta_deg'],
                                  row['axis_sign'], row['forward_sign']))
    return {'matrix': matrix, 'both_success_count': sum(row['both_success'] for row in matrix),
            'path_clear_both_success_count': len(winners), 'top_10': winners[:10]}


def run_grasp_search(args, fixture, shifts, pitches=(0, 20, 40, 60), reject_chassis_before_ik=False):
    import numpy as np
    truth = fixture.get('diagnostic_truth')
    if not truth:
        raise ValueError('--grasp-search requires a reset fixture with diagnostic truth')
    prepared = prepare(args, fixture)
    adapter, core, contract, body, full_world, tool, _ = prepared
    q0 = core.named(body['current_q'], body['joint_names'])
    ik_world, excluded = collision_world(full_world, args.exclude_object)
    center = truth['position_world_m']
    zmin, zmax = truth['z_range_world_m']
    points_world = {'pregrasp': [center[0], center[1], zmax+.10],
                    'grasp': [center[0], center[1], (zmin+zmax)/2]}
    transform = fixture['world']['planner_from_world']
    from scipy.spatial.transform import Rotation
    q_base_world = Rotation.from_matrix([row[:3] for row in transform[:3]])
    poses = grasp_orientations(truth['layered_horizontal_5mm_5deg']['narrowest_local']['angle_world_xy_deg'], pitches)
    rows = []
    ik_targets = 0
    for shift in shifts:
        if not reject_chassis_before_ik:
            adapter.push_world(body['env_id'], shifted_world(ik_world, shift))
            core.set_collision_world(shifted_world(ik_world, shift))
            core.set_attachment(None)
        chassis = chassis_check(full_world, args.chassis_binding, shift)
        if reject_chassis_before_ik and not (chassis['path_clear'] and chassis['endpoint_clear']):
            for pose in poses:
                rows.append({'base_shift': list(shift), **pose, 'chassis': chassis,
                             'skipped': 'chassis_rejected',
                             **{name: {'success': False, 'reason': 'chassis_rejected'}
                                for name in points_world}})
            continue
        if reject_chassis_before_ik:
            adapter.push_world(body['env_id'], shifted_world(ik_world, shift))
            core.set_collision_world(shifted_world(ik_world, shift))
            core.set_attachment(None)
        for pose in poses:
            q_world = Rotation.from_quat(pose['quaternion_world_xyzw'])
            q_base = q_base_world*q_world
            yaw = Rotation.from_euler('z', -shift[2])
            q_shifted = yaw*q_base
            xyzw = q_shifted.as_quat().tolist()
            results = {}
            for name, point_world in points_world.items():
                ik_targets += 1
                base = (np.asarray(transform) @ np.r_[point_world, 1.])[:3].tolist()
                target = {tool: {'position': shifted_point(base, shift),
                                 'quaternion_wxyz': [xyzw[3], *xyzw[:3]]}}
                try:
                    core.check_targets(target)
                    solution = core.solve_ik(current_q=q0, targets=target,
                        active_joints=body['active_joints'], locked_joints=body['locked_joints'])
                    success = solution is not None
                    reason = None if success else core.last_failure or 'unknown_ik_failure'
                    diagnostics = core.last_solver_diagnostics
                except Exception as exc:
                    success, reason, diagnostics = False, f'{type(exc).__name__}: {exc}', []
                results[name] = {'position_world_m': point_world, 'position_base_m': target[tool]['position'],
                                 'success': success, 'reason': reason, 'solver_diagnostics': diagnostics}
            rows.append({'base_shift': list(shift), **pose, 'chassis': chassis, **results})
    summary = summarize_grasp(rows)
    return {'seed': args.seed, 'fixture': str(args.fixture.resolve()),
            'source': '使用仿真真值,仅诊断', 'tcp_axis_convention': 'local y=grip; local z=approach; local x=y cross z',
            'tolerances': {'position_m': POSITION_TOLERANCE_M, 'orientation_rad': ORIENTATION_TOLERANCE_RAD},
            'object': truth, 'excluded_object': {'count': len(excluded), 'names': excluded},
            'ik_targets': ik_targets, 'points_world_m': points_world, 'summary': summary}


def object_cuboids(world):
    return [c for c in world if c['name'].split(':')[-1].startswith('obj_g')]


def collision_world(world, exclude_object=False, object_scale=1.0):
    """Remove exactly the cuboids used by the object proxy, if requested."""
    if exclude_object and object_scale != 1.0:
        raise ValueError('--exclude-object and --object-scale are mutually exclusive')
    if object_scale != 1.0:
        return [{**c, 'half_size': [v*object_scale for v in c['half_size']]}
                if c['name'].split(':')[-1].startswith('obj_g') else c for c in world], []
    if not exclude_object:
        return world, []
    shapes = object_cuboids(world)
    selected = {id(c) for c in shapes}
    return [c for c in world if id(c) not in selected], [c['name'] for c in shapes]


def object_bbox_center(world):
    """Fixture has one generic 'obj_g*' group, but no semantic milk pose."""
    shapes = object_cuboids(world)
    if not shapes:
        raise ValueError('Fixture has no obj_g* bounding boxes; cannot locate milk proxy')
    # Centers of rotated cuboids bound the object only approximately. The midpoint
    # of their center extrema avoids claiming that this is the physical object pose.
    center = [sum((min(c['center'][i] for c in shapes), max(c['center'][i] for c in shapes)))/2
              for i in range(3)]
    return center, [c['name'] for c in shapes]


def grid(center):
    """7 x 7 x 4 = 196 TCP positions in robot-base coordinates."""
    return [{'id': f'x{ix:+d}_y{iy:+d}_z{iz}',
             'position_base_m': [round(center[0]+ix*.04, 9),
                                 round(center[1]+iy*.04, 9),
                                 round(center[2]+iz*.05, 9)],
             'offset_m': [round(ix*.04, 9), round(iy*.04, 9), round(iz*.05, 9)]}
            for ix in range(-3, 4) for iy in range(-3, 4) for iz in range(4)]


def small_grid(center):
    """The 3 x 3 x 3 sweep grid, with a stable centre identifier."""
    return [{'id': f'x{ix:+d}_y{iy:+d}_z{iz}',
             'position_base_m': [round(center[0]+ix*.04, 9),
                                 round(center[1]+iy*.04, 9),
                                 round(center[2]+iz*.05, 9)],
             'offset_m': [round(ix*.04, 9), round(iy*.04, 9), round(iz*.05, 9)]}
            for ix in range(-1, 2) for iy in range(-1, 2) for iz in range(3)]


def parse_shift(value):
    try:
        parts = tuple(float(part.strip()) for part in value.split(','))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('base shift requires DX,DY,DYAW') from exc
    if len(parts) != 3 or not all(math.isfinite(v) for v in parts):
        raise argparse.ArgumentTypeError('base shift requires three finite values DX,DY,DYAW')
    return parts


def parse_sweep_spec(value):
    """Semicolon-separated dx,dy,dyaw lists; order is dx, then dy, then dyaw."""
    fields = {}
    try:
        for field in value.split(';'):
            name, raw = field.split('=', 1)
            name = name.strip().lower()
            if name not in ('dx', 'dy', 'dyaw') or name in fields:
                raise ValueError('unknown or repeated sweep axis')
            numbers = tuple(float(part.strip()) for part in raw.split(','))
            if not numbers or not all(math.isfinite(n) for n in numbers):
                raise ValueError('sweep axes must contain finite numbers')
            fields[name] = numbers
        if set(fields) != {'dx', 'dy', 'dyaw'}:
            raise ValueError('sweep needs dx, dy and dyaw')
    except ValueError as exc:
        raise argparse.ArgumentTypeError('sweep spec: dx=...;dy=...;dyaw=...') from exc
    return [(dx, dy, yaw) for dx in fields['dx'] for dy in fields['dy'] for yaw in fields['dyaw']]


def shifted_point(point, shift):
    """Coordinates of a fixed point after mount motion T_old_new=(Rz(yaw),[dx,dy,0])."""
    dx, dy, yaw = shift
    c, s = math.cos(yaw), math.sin(yaw)
    x, y, z = point
    return [c*(x-dx)+s*(y-dy), -s*(x-dx)+c*(y-dy), z]


def shifted_world(world, shift):
    if shift == (0., 0., 0.):
        return world
    half = -shift[2]/2
    inverse_yaw = (math.cos(half), 0., 0., math.sin(half))
    return [{**box, 'center': shifted_point(box['center'], shift),
             'quat_wxyz': list(quat_mul(inverse_yaw, box['quat_wxyz']))}
            for box in world]


def chassis_snapshot(world):
    inventory = []
    for box in world:
        geom_id, native_name = box['name'].split(':', 2)[1:]
        inventory.append({'name': box['name'], 'geom_id': int(geom_id), 'body_id': -1,
                          'body_name': native_name, 'geom_type': 6,
                          'source': 'actual MuJoCo collision geometry'})
    return {'cuboids': world, 'inventory': inventory, 'world_sha256': 'stored-seed-101'}


def chassis_check(world, binding, shift):
    """Check a straight mount path, splitting SDK-limited calls; also check its endpoint."""
    from openarm365_dev.base_motion import check_chassis_path, BASE_TRANSLATION_LIMIT_M, BASE_YAW_LIMIT_RAD
    from curobo_v2.client import PlanningRejected
    dx, dy, yaw = shift
    count = max(1, math.ceil(math.hypot(dx, dy)/BASE_TRANSLATION_LIMIT_M - 1e-10),
                math.ceil(abs(yaw)/BASE_YAW_LIMIT_RAD - 1e-10))
    failure = None
    checked = 0
    for i in range(count):
        t = i/count
        angle = yaw*t
        c, s = math.cos(angle), math.sin(angle)
        delta = ((c*dx+s*dy)/count, (-s*dx+c*dy)/count, yaw/count)
        try:
            check_chassis_path(chassis_snapshot(shifted_world(world, (dx*t, dy*t, angle))), binding, delta)
            checked += 1
        except PlanningRejected as exc:
            failure = str(exc)
            break
    try:
        check_chassis_path(chassis_snapshot(shifted_world(world, shift)), binding, (0., 0., 0.))
        endpoint_clear, endpoint_reason = True, None
    except PlanningRejected as exc:
        endpoint_clear, endpoint_reason = False, str(exc)
    return {'path_clear': failure is None, 'path_reason': failure,
            'endpoint_clear': endpoint_clear, 'endpoint_reason': endpoint_reason,
            'single_step': count == 1, 'steps_required': count, 'steps_checked': checked}


def estimate(targets):
    return [round(targets*seconds, 1) for seconds in IK_SECONDS_RANGE]


def world_to_base(point_world, fk_initial):
    """T_base_world = T_base_tcp(q0) * inverse(T_world_tcp(recorded))."""
    qb = unit(fk_initial['quaternion_wxyz'])
    qw = unit((WORLD_INITIAL_XYZW[3], *WORLD_INITIAL_XYZW[:3]))
    q_base_world = unit(quat_mul(qb, quat_conj(qw)))
    return add(fk_initial['position'], rotate(q_base_world, sub(point_world, WORLD_INITIAL_TCP))), q_base_world


def summarize(rows, poses, points):
    matrix = []
    by_key = {(r['mode'], r['orientation'], r['point']): r for r in rows}
    for pose in poses:
        cells = {}
        for mode in ('without_collision', 'with_collision'):
            selected = [by_key[(mode, pose['name'], p['id'])] for p in points if p['id'] != 'step6']
            passed = [r for r in selected if r['success']]
            coords = [r['position_base_m'] for r in passed]
            cells[mode] = {'success': len(passed), 'total': len(selected),
                           'rate': len(passed)/len(selected) if selected else 0.,
                           'reachable_bounds_base_m': None if not coords else
                           {'min': [min(p[i] for p in coords) for i in range(3)],
                            'max': [max(p[i] for p in coords) for i in range(3)]}}
        matrix.append({'orientation': pose['name'], **cells})
    ranked = sorted(matrix, key=lambda row: (row['with_collision']['success'],
                                             row['without_collision']['success']), reverse=True)[:5]
    sixth = [{'orientation': pose['name'],
              **{mode: by_key[(mode, pose['name'], 'step6')]
                 for mode in ('without_collision', 'with_collision')}} for pose in poses]
    return {'matrix': matrix, 'step6': sixth, 'promising': ranked}


def summarize_sweep_rows(rows, poses, points):
    by_key = {(r['mode'], r['orientation'], r['point']): r for r in rows}
    per_pose = []
    for pose in poses:
        name = pose['name']
        counts = {mode: sum(by_key[(mode, name, p['id'])]['success'] for p in points)
                  for mode in ('without_collision', 'with_collision')}
        per_pose.append({'orientation': name, **counts})
    best = max(per_pose, key=lambda r: (r['with_collision'], r['without_collision']))
    def reachable(point_id, mode):
        return any(by_key[(mode, pose['name'], point_id)]['success'] for pose in poses)
    return {'best_orientation': best['orientation'],
            'best_success_with_collision': best['with_collision'],
            'best_success_without_collision': max(p['without_collision'] for p in per_pose),
            'center_with_collision': reachable('x+0_y+0_z0', 'with_collision'),
            'above_5cm_with_collision': reachable('x+0_y+0_z1', 'with_collision'),
            'center_without_collision': reachable('x+0_y+0_z0', 'without_collision'),
            'above_5cm_without_collision': reachable('x+0_y+0_z1', 'without_collision'),
            'per_orientation': per_pose}


def sweep_summary(reports):
    table = [{'base_shift': r['base_shift'], **r['summary'], 'chassis': r['chassis']}
             for r in reports]
    candidates = [r for r in table if r['center_with_collision']
                  and r['above_5cm_with_collision'] and r['chassis']['path_clear']
                  and r['chassis']['endpoint_clear']]
    def sort_key(row):
        dx, dy, yaw = row['base_shift']
        return (math.hypot(dx, dy), abs(yaw), dx, dy, yaw)
    return {'table': table, 'minimum_shift_center_and_5cm':
            None if not candidates else min(candidates, key=sort_key)['base_shift'],
            'selection_rule': 'with_collision center and +5cm both reachable; path and endpoint clear; smallest translation norm, then absolute yaw'}


def render_sweep_markdown(report):
    title = '# Seed 101 左臂底座位移 IK 扫掠'
    if 'excluded_object' in report:
        title += '（已排除目标物体）'
    if 'object_scale' in report:
        title += f"（目标物体代理盒缩放 S={report['object_scale']:g}）"
    lines = [title, '',
             '位移为当前 mount 底座系的 DX,DY,DYAW（m,m,rad）；旋转绕 mount 原点。',
             '每组评估牛奶位置代理周围 3×3×3 个目标、13 个姿态及有/无外部碰撞。',
             '成功数为单个最佳姿态带碰撞结果；中心与上方 5 cm 可达指任一姿态带碰撞 IK 成功。',
             '路径列检查直线路径；超过单步上限时分为合法小段。终点另行检查。',
             '', '| DX,DY,DYAW | 最佳姿态 | 成功格点 | 中心 | +5 cm | 路径 | 终点 | 单步 |',
             '| --- | --- | ---: | --- | --- | --- | --- | --- |']
    if 'excluded_object' in report:
        excluded = report['excluded_object']
        lines.insert(2, f"带碰撞 IK 已排除 {excluded['count']} 个目标物体盒：{', '.join(excluded['names'])}；车体路径检查仍使用完整世界。")
    for row in report['summary']['table']:
        chassis = row['chassis']
        yes = lambda value: '是' if value else '否'
        step = '是' if chassis['single_step'] else f"需多步（{chassis['steps_required']}）"
        lines.append(f"| {','.join(f'{v:g}' for v in row['base_shift'])} | {row['best_orientation']} | "
                     f"{row['best_success_with_collision']}/27 | {yes(row['center_with_collision'])} | "
                     f"{yes(row['above_5cm_with_collision'])} | {yes(chassis['path_clear'])} | "
                     f"{yes(chassis['endpoint_clear'])} | {step} |")
    minimum = report['summary']['minimum_shift_center_and_5cm']
    lines += ['', '## 数据建议', '',
              '筛选：带碰撞 IK 中心及上方 5 cm 均有解，车体路径和终点通过；'
              '按平移模长、偏航绝对值排序。',
              '满足条件的最小位移：' + ('本次扫掠无' if minimum is None else str(minimum)) + '。',
              '', '逐点 IK、无碰撞对照及车体失败原因见 results.json。']
    return '\n'.join(lines) + '\n'


def render_markdown(report):
    s = report['summary']
    side_label = {'left': '左', 'right': '右'}[report.get('side', 'left')]
    title = f'# Seed 101 {side_label}臂 IK 可达性图'
    if 'excluded_object' in report:
        title += '（已排除目标物体）'
    if 'object_scale' in report:
        title += f"（目标物体代理盒缩放 S={report['object_scale']:g}）"
    lines = [title, '',
             '坐标系：目标、网格和范围均为机器人底座系，单位 m；姿态为 wxyz。',
             '“无碰撞”列仅清空外部世界；planner core 仍检查自碰撞和终点几何。',
             f"初始 FK（底座系）：{report['fk_initial_base']}",
             f"物体网格中心：{report['object_proxy']['center_base_m']}（obj_g* 碰撞盒中心的范围中点，仅为物体位置代理）",
             f"物体盒中心与给定世界物体位置换算值相距 {report['object_proxy']['distance_to_recorded_m']:.4f} m。",
             f"世界到机器人底座旋转（wxyz）：{report['world_to_base_quaternion_wxyz']}。",
             f"第 6 步目标（底座系）：{report['step6_target_base_m']}；原始世界系位置和位移为四舍五入记录。",
             f"服务默认容差：位置 ≤{POSITION_TOLERANCE_M} m，姿态 ≤{ORIENTATION_TOLERANCE_RAD} rad；单点 core.solve_ik，q0 每次相同。",
             '', '| 姿态 | 无碰撞 | 带碰撞 | 无碰撞可达范围 min → max (m) | 带碰撞可达范围 min → max (m) |',
             '| --- | ---: | ---: | --- | --- |']
    if 'base_shift' in report:
        lines.insert(2, f"底座位移 DX,DY,DYAW：{report['base_shift']}；固定世界已变换到位移后底座系。")
    if 'excluded_object' in report:
        excluded = report['excluded_object']
        lines.insert(2, f"带碰撞 IK 已排除 {excluded['count']} 个目标物体盒：{', '.join(excluded['names'])}；车体路径检查仍使用完整世界。")
    def cell(c):
        return f"{c['success']}/{c['total']} ({c['rate']:.1%})"
    def bounds(c):
        b = c['reachable_bounds_base_m']
        return '—' if b is None else f"{b['min']} → {b['max']}"
    for row in s['matrix']:
        a, b = row['without_collision'], row['with_collision']
        lines.append(f"| {row['orientation']} | {cell(a)} | {cell(b)} | {bounds(a)} | {bounds(b)} |")
    lines += ['', '## 第 6 步目标', '', '| 姿态 | 无碰撞 | 带碰撞 |', '| --- | --- | --- |']
    def result(r):
        return '成功' if r['success'] else '失败：' + str(r['reason'] or '未给出原因')
    for row in s['step6']:
        lines.append(f"| {row['orientation']} | {result(row['without_collision'])} | {result(row['with_collision'])} |")
    lines += ['', '## 带碰撞成功数排名前五', '']
    lines += [f"- {r['orientation']}：带碰撞 {cell(r['with_collision'])}；无碰撞 {cell(r['without_collision'])}。"
              for r in s['promising']]
    wins = sum(r['with_collision']['success'] for r in s['matrix'])
    free = sum(r['without_collision']['success'] for r in s['matrix'])
    lines += ['', f'结论：本次网格中无碰撞成功 {free} 点姿态组合，带碰撞成功 {wins} 点姿态组合；'
              '单点 IK 失败只能说明本次求解未获服务认可的解，不能单凭失败判定关节极限、障碍物或姿态是唯一原因。',
              '', '逐目标结果和失败原因见 results.json。']
    return '\n'.join(lines) + '\n'


def prepare(args, fixture):
    import curobo_v2.identity as identity
    import yaml
    import curobo

    config_path, assets = args.robot_config.resolve(), args.assets_dir.resolve()
    document = yaml.safe_load(config_path.read_text())
    args.chassis_binding = document['service']['native_chassis']
    files = diagnostic_files(config_path, assets, document, args.fixture)
    identity._service_context = ({'assets_root': str(assets), 'files': files,
                                  'library_root': str(Path(curobo.__file__).resolve().parent.parent),
                                  'diagnostic_mode': 'diagnostic-read-only'}, str(config_path))
    from curobo_v2.service.curobo_service.planner import PlannerAdapter
    from curobo_v2.service.curobo_service.world import WorldStore
    adapter = PlannerAdapter('cuda:0', WorldStore(max_envs=8))
    core = adapter._planner
    contract = core.identity['contract']
    if (contract['position_tolerance_m'] != POSITION_TOLERANCE_M
            or contract['orientation_tolerance_rad'] != ORIENTATION_TOLERANCE_RAD):
        raise ValueError('Runtime endpoint tolerances differ from this diagnostic report')
    body, world, attachment, tool = request_inputs(core, fixture, args.side)
    if attachment is not None:
        raise ValueError('Expected seed-101 uncarried scene; attachment is present')
    q0 = core.named(body['current_q'], body['joint_names'])
    core.check_selection(q0, body['active_joints'], [tool], body['locked_joints'])
    ik_world, _ = collision_world(world, args.exclude_object, getattr(args, 'object_scale', 1.0))
    adapter.push_world(body['env_id'], ik_world)
    initial = core.fk(q0, body['locked_joints'], body['active_joints'])[tool]
    return adapter, core, contract, body, world, tool, initial


def run(args, fixture, world, center, *, shift=(0., 0., 0.), prepared=None, sweep=False):
    if prepared is None:
        prepared = prepare(args, fixture)
    adapter, core, contract, body, original_world, tool, initial = prepared
    q0 = core.named(body['current_q'], body['joint_names'])
    object_scale = getattr(args, 'object_scale', 1.0)
    ik_world, excluded_names = collision_world(original_world, args.exclude_object, object_scale)
    world = shifted_world(ik_world, shift)
    if shift != (0., 0., 0.):
        center = shifted_point(center, shift)
    if shift != (0., 0., 0.) or sweep:
        adapter.push_world(body['env_id'], world)
    obj_base, q_base_world = world_to_base(WORLD_MILK, initial)
    if shift != (0., 0., 0.):
        obj_base = shifted_point(obj_base, shift)
        half = -shift[2]/2
        q_base_world = unit(quat_mul((math.cos(half), 0., 0., math.sin(half)), q_base_world))
    step6_world = add(WORLD_STEP6_CURRENT, WORLD_STEP6_DELTA)
    step6_base, _ = world_to_base(step6_world, initial)
    if shift != (0., 0., 0.):
        step6_base = shifted_point(step6_base, shift)
    points = small_grid(center) if sweep else grid(center) + [
        {'id': 'step6', 'position_base_m': list(step6_base), 'offset_m': None}]
    poses = orientations(initial['quaternion_wxyz'])
    # Keep the genuine scene and empty scene in separate batches.
    rows = []
    for mode, scene in (('without_collision', []), ('with_collision', world)):
        core.set_collision_world(scene)
        core.set_attachment(None)
        for pose in poses:
            for point in points:
                target = {tool: {'position': point['position_base_m'],
                                 'quaternion_wxyz': pose['quaternion_wxyz']}}
                try:
                    core.check_targets(target)
                    solution = core.solve_ik(current_q=q0, targets=target,
                                             active_joints=body['active_joints'],
                                             locked_joints=body['locked_joints'])
                    success = solution is not None
                    reason = None if success else core.last_failure or 'unknown_ik_failure'
                    diagnostics = core.last_solver_diagnostics
                except Exception as exc:
                    success, reason, diagnostics = False, f'{type(exc).__name__}: {exc}', []
                rows.append({'mode': mode, 'orientation': pose['name'], 'point': point['id'],
                             'position_base_m': point['position_base_m'], 'success': success,
                             'reason': reason, 'solver_diagnostics': diagnostics})
    discrepancy = math.sqrt(dot(sub(obj_base, center), sub(obj_base, center)))
    if discrepancy > .10:
        raise ValueError(f'Initial-pose frame registration disagrees with obj_g* proxy by {discrepancy:.3f} m')
    report = {'fixture': str(args.fixture.resolve()), 'side': args.side,
              'coordinate_frame': contract['frame'],
              'q0': body['current_q'], 'joint_names': body['joint_names'],
              'locked_joints': body['locked_joints'], 'world_cuboids': len(world),
              'fk_initial_base': initial, 'world_to_base_quaternion_wxyz': q_base_world,
              'transform_source': 'FK(q0) and recorded initial world TCP position/quaternion from reference_ladder.py',
              'object_proxy': {'source': 'obj_g* cuboid center extrema midpoint',
                               'center_base_m': center, 'cuboids': len(object_cuboids(original_world)),
                               'recorded_object_base_m': obj_base, 'distance_to_recorded_m': discrepancy},
              'step6_target_world_m': step6_world, 'step6_target_base_m': step6_base,
              'tolerances': {'position_m': POSITION_TOLERANCE_M, 'orientation_rad': ORIENTATION_TOLERANCE_RAD},
              'orientations': poses, 'points': points, 'rows': rows,
              'summary': summarize(rows, poses, points) if not sweep else None}
    if args.exclude_object:
        report['excluded_object'] = {'count': len(excluded_names), 'names': excluded_names}
    if getattr(args, 'object_scale_explicit', False) or object_scale != 1.0:
        report['object_scale'] = object_scale
    if shift != (0., 0., 0.) or sweep:
        report['base_shift'] = list(shift)
        report['chassis'] = chassis_check(original_world, args.chassis_binding, shift)
    if sweep:
        report['summary'] = summarize_sweep_rows(rows, poses, points)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true', help='Count targets without importing cuRobo or constructing a model')
    parser.add_argument('--out', type=Path, default=Path('reachability-map-results'))
    parser.add_argument('--side', choices=('left', 'right'), default='left')
    parser.add_argument('--fixture', type=Path, default=FIXTURE)
    parser.add_argument('--seed', type=int, choices=(101, 102, 103))
    parser.add_argument('--from-reset', action='store_true')
    parser.add_argument('--grasp-search', action='store_true')
    parser.add_argument('--robot-config', type=Path)
    parser.add_argument('--assets-dir', type=Path)
    parser.add_argument('--exclude-object', action='store_true',
                        help='Remove the obj_g* proxy cuboids from the collision IK world')
    parser.add_argument('--object-scale', type=float, default=1.0,
                        help='Multiply only obj_g* collision IK cuboid half sizes by S (default: 1.0)')
    parser.add_argument('--base-shift', type=parse_shift, default=(0., 0., 0.),
                        help='Mount-frame DX,DY,DYAW in m,m,rad')
    parser.add_argument('--sweep', action='store_true', help='Evaluate the compact grid at every sweep shift')
    parser.add_argument('--sweep-spec', type=parse_sweep_spec,
                        help='Override axes: dx=0,-0.1;dy=0,0.05;dyaw=0,0.15')
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    args.object_scale_explicit = any(value == '--object-scale' or value.startswith('--object-scale=')
                                     for value in raw_argv)
    if not math.isfinite(args.object_scale) or args.object_scale <= 0:
        parser.error('--object-scale requires a positive finite S')
    if args.exclude_object and args.object_scale_explicit:
        parser.error('--exclude-object and --object-scale are mutually exclusive')
    if args.sweep_spec is not None and not (args.sweep or args.grasp_search):
        parser.error('--sweep-spec requires --sweep or --grasp-search')
    if args.sweep and args.grasp_search:
        parser.error('--sweep and --grasp-search are mutually exclusive')
    if args.from_reset and args.seed is None:
        parser.error('--from-reset requires --seed')
    if args.seed is not None and not args.from_reset and args.fixture == FIXTURE:
        parser.error('--seed requires --from-reset or a reset --fixture')
    if args.grasp_search and not args.from_reset and args.fixture == FIXTURE:
        parser.error('--grasp-search requires --from-reset or a reset --fixture')
    if args.grasp_search and not args.exclude_object:
        parser.error('--grasp-search requires --exclude-object')
    if args.sweep and args.base_shift != (0., 0., 0.):
        parser.error('--base-shift and --sweep are mutually exclusive')
    if args.grasp_search and args.dry_run:
        shifts = args.sweep_spec or parse_sweep_spec(GRASP_SWEEP_SPEC)
        count = len(shifts)*len(grasp_orientations(0))*2
        display_seed = args.seed if args.from_reset else json.loads(args.fixture.read_text()).get('seed')
        print(json.dumps({'seed': display_seed, 'base_shifts': len(shifts),
                          'orientations_per_shift': len(grasp_orientations(0)),
                          'positions_per_orientation': 2, 'ik_targets': count,
                          'estimated_ik_seconds_range': estimate(count),
                          'source': '使用仿真真值,仅诊断'}, ensure_ascii=False))
        return
    if args.from_reset:
        if args.robot_config is None or args.assets_dir is None:
            parser.error('--from-reset requires --robot-config and --assets-dir')
        from .object_proxy_measure import reset_diagnostic_fixture
        fixture = reset_diagnostic_fixture(args.seed, args.out, args.robot_config)
        args.fixture = args.out/'fixture.json'
        args.fixture.write_text(json.dumps(fixture, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        truth = fixture['diagnostic_truth']
        print(json.dumps({'fixture': str(args.fixture), 'seed': args.seed,
                          'position_base_m': truth['position_base_m'],
                          'position_world_m': truth['position_world_m'],
                          'narrow_angle_world_xy_deg': truth['narrowest_5_degree_sample']['angle_world_xy_deg'],
                          'local_narrow_angle_world_xy_deg': truth['layered_horizontal_5mm_5deg']['narrowest_local']['angle_world_xy_deg'],
                          'cuboids': len(fixture['world']['cuboids'])}, ensure_ascii=False))
    else:
        fixture = json.loads(args.fixture.read_text())
        if args.grasp_search:
            if args.seed is not None and fixture.get('seed') != args.seed:
                parser.error('--seed differs from the reset fixture')
            args.seed = fixture.get('seed')
    if args.grasp_search:
        shifts = args.sweep_spec or parse_sweep_spec(GRASP_SWEEP_SPEC)
        report = run_grasp_search(args, fixture, shifts)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
        lines = [f"# Seed {args.seed} 抓取姿态可达性搜索", '', '使用仿真真值,仅诊断。',
                 f"双目标带碰撞 IK 成功：{report['summary']['both_success_count']}；车体路径与终点也通过：{report['summary']['path_clear_both_success_count']}。", '',
                 '| 位移 DX,DY,DYAW | θ° | 轴符号 | φ° | 前倾符号 | TCP 世界四元数 xyzw | n | a |',
                 '| --- | ---: | ---: | ---: | ---: | --- | --- | --- |']
        for row in report['summary']['top_10']:
            lines.append(f"| {row['base_shift']} | {row['theta_deg']} | {row['axis_sign']} | {row['pitch_deg']} | {row['forward_sign']} | {row['quaternion_world_xyzw']} | {row['n_world']} | {row['a_world']} |")
        (args.out/'results.md').write_text('\n'.join(lines)+'\n')
        print(f"Wrote {args.out/'results.json'} and {args.out/'results.md'}")
        return
    if args.from_reset:
        print(f"Wrote {args.fixture} (使用仿真真值,仅诊断)")
        return
    world = fixture['world']['cuboids'] if isinstance(fixture['world'], dict) else fixture['world']
    center, names = object_bbox_center(world)
    shifts = args.sweep_spec or parse_sweep_spec(DEFAULT_SWEEP_SPEC) if args.sweep else [args.base_shift]
    npoints, nposes = (len(small_grid(center)) if args.sweep else len(grid(center))+1), 1+3*len(ROLLS_DEG)
    if args.dry_run:
        info = {'side': args.side, 'frame': 'robot base', 'object_proxy_center_base_m': center,
                'object_cuboids': len(names), 'grid_positions': npoints if args.sweep else npoints-1,
                'extra_step6_positions': 0 if args.sweep else 1, 'orientations': nposes,
                'collision_modes': 2, 'ik_targets': len(shifts)*npoints*nposes*2}
        if args.sweep:
            info.update(base_shifts=len(shifts), targets_per_shift=npoints*nposes*2)
        elif args.base_shift != (0., 0., 0.):
            info['base_shift'] = args.base_shift
        info['estimated_ik_seconds_range'] = estimate(info['ik_targets'])
        if args.exclude_object:
            info['excluded_object_cuboids'] = len(names)
        if args.object_scale_explicit:
            info['object_scale'] = args.object_scale
        print(json.dumps(info, ensure_ascii=False))
        return
    if args.robot_config is None or args.assets_dir is None:
        parser.error('real runs require --robot-config and --assets-dir')
    if args.sweep:
        prepared = prepare(args, fixture)
        reports = [run(args, fixture, world, center, shift=shift, prepared=prepared, sweep=True)
                   for shift in shifts]
        report = {'fixture': str(args.fixture.resolve()), 'side': args.side,
                  'sweep_spec': [list(shift) for shift in shifts],
                  'grid_positions_per_shift': npoints, 'orientations_per_shift': nposes,
                  'ik_targets': len(shifts)*npoints*nposes*2,
                  'estimated_ik_seconds_range': estimate(len(shifts)*npoints*nposes*2),
                  'shifts': reports, 'summary': sweep_summary(reports)}
        if args.exclude_object:
            report['excluded_object'] = reports[0]['excluded_object']
        if args.object_scale_explicit:
            report['object_scale'] = args.object_scale
        markdown = render_sweep_markdown(report)
    else:
        report = run(args, fixture, world, center, shift=args.base_shift)
        markdown = render_markdown(report)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (args.out/'results.md').write_text(markdown)
    print(f"Wrote {args.out/'results.json'} and {args.out/'results.md'}")


if __name__ == '__main__':
    main()
