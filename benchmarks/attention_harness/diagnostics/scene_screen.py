"""Free, read-only development scene screen for PickPlaceCounterToSink.

Reset uses MuJoCo truth without a physics step. IK uses the official diagnostic
planner on a GPU, but never sends robot commands or calls a learned model.
"""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
import json
import math
from pathlib import Path
import signal
from types import SimpleNamespace

MAX_GRIP_WIDTH_M = 0.0805  # Measured two-finger maximum: seed-101 physical gripper audit.
BASE_START_MIN_M = 0.003  # openarm365_dev.base_motion.BASE_MIN_DISTANCE_M.
J7_MARGIN_MIN_RAD = 0.10
BASIN_INSET_M = 0.05
RELEASE_INSET_M = 0.06
RELEASE_ABOVE_RIM_M = 0.08
GRID_M = 0.05
GRASP_DY = (0.20, 0.25, 0.30, 0.35)
GRASP_DYAW = (-0.15, 0.0, 0.15)
SCREEN_DYAW = (0.0,)
GRASP_LIFT_M = (0.0, 0.03, 0.06)
GRID_BOUNDS_M = (-0.40, 0.60, -0.90, 0.60)  # Mount offsets from reset; bounded diagnostic search.
REASONS = ('width_over_opening', 'start_clearance_below_3mm',
           'reset_missing', 'grasp_ik_not_found', 'carry_not_found',
           'ik_not_run', 'ik_error', 'timeout')
VERDICT_EXPLANATIONS = {'feasible': '仅表示端点 IK 可行；不含 TCP 水平平移路径、连续持物轨迹或原生成功'}


def orientation_key(quaternion):
    rounded = tuple(round(float(value), 6) for value in quaternion)
    # q and -q encode the same rotation; use one representation for both.
    sign = next((1 if value > 0 else -1 for value in rounded if value), 1)
    return tuple(0. if value == 0 else sign*value for value in rounded)


def theta_from_world_quaternion(quaternion, tolerance=1e-6):
    """Probe theta for a vertical world-xyzw TCP pose, including the reversed axis."""
    q = tuple(float(value) for value in quaternion)
    if len(q) != 4 or any(not math.isfinite(value) for value in q):
        raise ValueError('invalid world xyzw quaternion')
    norm = math.sqrt(sum(value*value for value in q))
    if abs(norm-1.) > tolerance:
        raise ValueError('world xyzw quaternion must be unit length')
    if abs(q[2]) > tolerance or abs(q[3]) > tolerance:
        return None  # A pitched/rolled TCP cannot be expressed by probe --theta-deg.
    theta = (90.-2.*math.degrees(math.atan2(q[0], q[1]))+180.) % 360.-180.
    return theta


def select_carry_candidates(passed, limit):
    """Rank complete grasp layers by their weakest margin, keeping one per pose."""
    selected, seen = [], set()
    for candidate in sorted(passed, key=lambda row: -min(row['grasp_margins'])):
        key = orientation_key(candidate['quaternion_wxyz'])
        if key not in seen:
            selected.append(candidate)
            seen.add(key)
            if len(selected) == limit:
                break
    return selected


def rank_carry_cells(cells, target_base, grasp_base, shift, yaw, shifted_point, limit):
    """Keep the existing target XY alignment order, then apply the cell budget."""
    grasp_xy = shifted_point(grasp_base, shift)[:2]
    return sorted(cells, key=lambda cell: math.dist(
        shifted_point(target_base, (cell[0], cell[1], yaw))[:2], grasp_xy))[:limit]


class CarryIKCache:
    """One seed's accepted endpoint result; cache negative results as well."""

    def __init__(self, seed):
        self.seed = seed
        self.results = {}
        self.hits = 0

    def check(self, quaternion, cell, point_number, solve):
        key = (self.seed, orientation_key(quaternion), tuple(cell), point_number)
        if key in self.results:
            self.hits += 1
            return self.results[key], True
        result = solve()  # (qualified, maximum j7 margin or None)
        self.results[key] = result
        return result, False


def parse_seeds(raw):
    try:
        values = tuple(int(part.strip()) for part in raw.split(','))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('--seeds requires comma-separated integers') from exc
    if not values or len(set(values)) != len(values) or any(seed < 101 or seed > 125 for seed in values):
        raise argparse.ArgumentTypeError('only distinct development seeds 101–125 are allowed; held-out 1001–1010 are forbidden')
    return values


def parse_float_list(raw):
    try:
        values = tuple(float(part.strip()) for part in raw.split(','))
    except ValueError as exc:
        raise argparse.ArgumentTypeError('expected comma-separated finite numbers') from exc
    if not values or any(not math.isfinite(value) for value in values):
        raise argparse.ArgumentTypeError('expected comma-separated finite numbers')
    return values


def grasp_search_options(args):
    if args.full_grasp_search:
        return GRASP_DY, GRASP_DYAW, (0, 20, 40, 60), False
    return args.dy_list, args.dyaw_list, ((0,) if args.pitch_zero_only else (0, 20, 40, 60)), True


def width_result(width_m):
    if not math.isfinite(width_m) or width_m < 0:
        raise ValueError('invalid object width')
    margin = MAX_GRIP_WIDTH_M - width_m
    return {'narrowest_width_m': width_m, 'max_finger_opening_m': MAX_GRIP_WIDTH_M,
            'width_margin_m': margin, 'graspable_by_width': margin >= 0}


def joint_margin(q, joint_names, limits, joint='openarm_left_joint7'):
    i = joint_names.index(joint)
    lo, hi = limits[i]
    return min(float(q[i])-lo, hi-float(q[i]))


def release_rectangle(region, inset=RELEASE_INSET_M):
    """World-XY rectangle inset from the native interior's reported bounds."""
    bounds = []
    for lo, hi in region['xy_aabb_world_m']:
        middle = (lo + hi)/2
        bounds.append([lo+inset, hi-inset] if hi-lo > 2*inset else [middle, middle])
    return bounds


def release_points(rectangle, rim_top, grasp_xy):
    """Nine grid positions plus the closest rectangle point to the grasp start."""
    (x0, x1), (y0, y1) = rectangle
    nearest = (min(max(grasp_xy[0], x0), x1), min(max(grasp_xy[1], y0), y1))
    xy = [nearest] + [(x, y) for x in (x0, (x0+x1)/2, x1)
                      for y in (y0, (y0+y1)/2, y1)]
    unique = list(dict.fromkeys(xy))
    return [[x, y, rim_top+RELEASE_ABOVE_RIM_M] for x, y in unique]


def first_feasible_release(margins):
    """Return the first endpoint with the unchanged joint-7 margin threshold."""
    return next(((i, margin) for i, margin in enumerate(margins)
                 if margin is not None and margin >= J7_MARGIN_MIN_RAD), None)


def basin_geometry(truth):
    """Use the same four native interior points as obj_inside_of(partial_check=True)."""
    import numpy as np
    regions = []
    for name, raw in truth['sink_int_sites_world_m'].items():
        p0, px, py, pz = (np.asarray(p, float) for p in raw)
        u, v, w = px-p0, py-p0, pz-p0
        if min(np.linalg.norm(u), np.linalg.norm(v), np.linalg.norm(w)) <= 2*BASIN_INSET_M:
            raise ValueError(f'Basin {name} too narrow for 5 cm inset')
        object_xy = np.asarray(truth['position_world_m'][:2], float)
        # Interior sites are an orthogonal box in the fixture's rotated world frame.
        unit_u, unit_v = u[:2]/np.linalg.norm(u[:2]), v[:2]/np.linalg.norm(v[:2])
        lengths = (np.linalg.norm(u[:2]), np.linalg.norm(v[:2]))
        uv = (np.dot(object_xy-p0[:2], unit_u), np.dot(object_xy-p0[:2], unit_v))
        nearest = [min(max(float(t), 0.), float(length)) for t, length in zip(uv, lengths)]
        nearest_xy = p0[:2] + nearest[0]*unit_u + nearest[1]*unit_v
        clipped = [min(max(float(t), BASIN_INSET_M), float(length)-BASIN_INSET_M)
                   for t, length in zip(uv, lengths)]
        target_xy = p0[:2] + clipped[0]*unit_u + clipped[1]*unit_v
        all_xy = [p0[:2], px[:2], py[:2], (px+py-p0)[:2]]
        xy_range = [[float(min(p[i] for p in all_xy)), float(max(p[i] for p in all_xy))]
                    for i in (0, 1)]
        z_range = [float(min(p0[2], pz[2])), float(max(p0[2], pz[2]))]
        regions.append({'name': name, 'points_world_m': raw, 'xy_aabb_world_m': xy_range,
                        'z_range_world_m': z_range, 'safe_xy_world_m': target_xy.tolist(),
                        'distance_object_to_safe_xy_m': float(np.linalg.norm(object_xy-target_xy)),
                        'distance_object_to_interior_m': float(math.dist(
                            truth['position_world_m'], [*nearest_xy, min(max(truth['position_world_m'][2], z_range[0]), z_range[1])]))})
    if not regions:
        raise ValueError('No basin region')
    selected = min(regions, key=lambda row: row['distance_object_to_safe_xy_m'])
    # Select main-body collision boxes at the native interior's top edge.
    # The faucet/handle sits above this band and must never define the rim.
    high = selected['z_range_world_m'][1]
    candidates = []
    for box in truth['sink_main_collision_boxes_world_m']:
        c, h = box['center_world_m'], box['half_size_world_m']
        top = c[2]+h[2]
        overlap = all(c[i]+h[i] >= selected['xy_aabb_world_m'][i][0]-0.03 and
                      c[i]-h[i] <= selected['xy_aabb_world_m'][i][1]+0.03 for i in (0, 1))
        if overlap and high-0.08 <= top <= high+0.03 and c[2]-h[2] < high:
            candidates.append((top, box['name']))
    if not candidates:
        raise ValueError('No basin-wall collision box near native interior top')
    rim, rim_name = max(candidates)
    z = min(high-0.01, rim-0.03)
    if z <= selected['z_range_world_m'][0]:
        raise ValueError('No safe release center inside native basin')
    return {'regions': regions, 'chosen_region': selected['name'],
            'safe_object_center_world_m': [*selected['safe_xy_world_m'], z],
            'carry_tcp_world_m': [*selected['safe_xy_world_m'], rim+0.05],
            'rim_top_world_m': rim, 'rim_source_collision_geom': rim_name,
            'distance_object_to_interior_m': selected['distance_object_to_interior_m'],
            'distance_object_to_safe_xy_m': selected['distance_object_to_safe_xy_m'],
            'native_success_rule': 'obj_inside_of(partial_check=True) and gripper_obj_far(th=0.25 m)'}


def reset_row(fixture):
    truth = fixture['diagnostic_truth']
    width = width_result(truth['narrowest_5_degree_sample']['object_width_m'])
    audit = truth['base_start_exact_audit']
    clearance = audit['start_minimum_clearance_m']
    # The original checker prunes AABB pairs beyond its 20 mm search skin.
    # No reported pair therefore gives a conservative >=20 mm bound.
    lower_bound = audit['required_clearance_m'] if clearance is None else clearance
    asset = Path(truth['target_asset_path'])
    return {'seed': fixture['seed'], 'target_name': asset.parent.parent.name,
            'target_instance': asset.parent.name, 'target_asset_path': str(asset),
            'target_z_range_world_m': truth['z_range_world_m'],
            'target_position_world_m': truth['position_world_m'], 'target_geometry': {
                'axis_aligned_ranges_world_m': truth['axis_aligned_ranges_world_m'],
                'axis_aligned_bbox_dimensions_m': truth['axis_aligned_bbox_dimensions_m'],
                'narrowest_5_degree_sample': truth['narrowest_5_degree_sample'],
                'layered_horizontal_5mm_5deg': truth['layered_horizontal_5mm_5deg'],
                'proxy_cuboids': [box for box in fixture['world']['cuboids']
                                  if box['name'] in truth['proxy_names']]},
            **width, 'start_clearance_m': clearance, 'start_clearance_lower_bound_m': lower_bound,
            'start_clearance_is_lower_bound': clearance is None,
            'start_clearance_pair': audit['start_minimum_clearance_pair'],
            'start_clearance_ok': lower_bound >= BASE_START_MIN_M and audit['status'] == 'clear',
            'start_exact_audit': audit,
            'start_chassis_proxy_audit': truth['base_start_chassis_proxy_audit'],
            'start_chassis_proxy_clearance_m': truth['base_start_chassis_proxy_audit']['minimum_clearance_m'],
            'sink': basin_geometry(truth), 'physics_steps': fixture['physics_steps']}


def bounded_grid_bfs(start, bounds, edge_ok):
    """8-neighbor 5 cm BFS; edge_ok checks the same chassis path rule as SDK."""
    key = lambda p: (round(p[0], 4), round(p[1], 4))
    start = key(start)
    seen = {start: None}
    queue = deque([start])
    moves = ((1, 0), (-1, 0), (0, 1), (0, -1),
             (1, 1), (1, -1), (-1, 1), (-1, -1))
    while queue:
        p = queue.popleft()
        for dx, dy in moves:
            q = key((p[0]+dx*GRID_M, p[1]+dy*GRID_M))
            if q in seen or not (bounds[0] <= q[0] <= bounds[1] and bounds[2] <= q[1] <= bounds[3]):
                continue
            if edge_ok(p, q):
                seen[q] = p
                queue.append(q)
    return seen


class GridSearchCache:
    """Directed chassis edges and complete BFS results, scoped to one seed."""

    def __init__(self):
        self.edges = {}
        self.searches = {}

    def search(self, yaw, start, bounds, edge_ok, progress=None):
        start = (round(start[0], 4), round(start[1], 4))
        key = (yaw, start)
        edges = self.edges.setdefault(yaw, {})
        if key in self.searches:
            return self.searches[key], True, 0
        before = len(edges)

        def cached_edge(a, b):
            edge = (a, b)
            if edge not in edges:
                edges[edge] = edge_ok(a, b)
                if progress is not None and (len(edges)-before) % 100 == 0:
                    progress(len(edges)-before, len(edges))
            return edges[edge]

        cells = bounded_grid_bfs(start, bounds, cached_edge)
        self.searches[key] = cells
        return cells, False, len(edges)-before


class SeedTimeout(BaseException):
    """Wall-clock seed budget expired; bypass library Exception handlers."""


@contextmanager
def seed_deadline(seconds):
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def expired(_signum, _frame):
        raise SeedTimeout()

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def verdict(reset, ik=None):
    if reset is None:
        return 'unknown:reset_missing'
    if not reset['graspable_by_width']:
        return 'infeasible:width_over_opening'
    if not reset['start_clearance_ok']:
        return 'infeasible:start_clearance_below_3mm'
    if ik is None:
        return 'unknown:ik_not_run'
    if ik.get('timeout'):
        return 'unknown:timeout'
    if 'error' in ik:
        return 'unknown:ik_error'
    if not ik['grasp_ik_feasible']:
        return 'unknown:grasp_ik_not_found'
    if not ik['carry_ik_feasible']:
        return 'unknown:carry_not_found'
    return 'feasible'


def format_table(rows):
    lines = ['| Seed | 目标 | 宽度可抓 | 起点余量 mm | 抓取 IK | 搬运 IK | 判定 |',
             '| ---: | --- | :---: | ---: | :---: | :---: | --- |']
    for row in rows:
        reset, ik = row.get('reset'), row.get('ik')
        yn = lambda value: '是' if value else '否'
        cell = lambda value: '—' if value is None else yn(value)
        clearance = ('—' if not reset else
                     f"{reset['start_clearance_m']*1000:.2f}" if reset['start_clearance_m'] is not None else
                     f"≥{reset['start_clearance_lower_bound_m']*1000:.2f}")
        lines.append(f"| {row['seed']} | {reset['target_name'] if reset else '—'} | "
                     f"{cell(reset['graspable_by_width'] if reset else None)} | {clearance} | "
                     f"{cell(ik['grasp_ik_feasible'] if ik else None)} | "
                     f"{cell(ik['carry_ik_feasible'] if ik else None)} | {verdict(reset, ik)} |")
    return '\n'.join(lines)+'\n'


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def write_text(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(value)
    temporary.replace(path)


def write_summary(out, seeds, reset_by_seed, ik_by_seed):
    rows = [{'seed': seed, 'reset': reset_by_seed.get(seed), 'ik': ik_by_seed.get(seed)} for seed in seeds]
    write_text(out/'summary.md', '# 场景筛查汇总\n\n使用仿真真值，仅诊断。IK 阴性只代表有界采样未找到解。\n\n'
               + format_table(rows))


def load_reset(out):
    path = out/'reset.json'
    if not path.exists():
        return {}
    rows = {int(row['seed']): row for row in json.loads(path.read_text())['rows']}
    if any(seed not in range(101, 126) for seed in rows):
        raise ValueError('Existing reset.json contains a forbidden non-development seed')
    return rows


def run_reset(args):
    from .object_proxy_measure import reset_diagnostic_fixture
    rows = load_reset(args.out)
    ik_rows = load_ik(args.out)
    for seed in args.seeds:
        ik_rows.pop(seed, None)
    if (args.out/'ik.json').exists():
        write_json(args.out/'ik.json', {'stage': 'ik', 'task': 'PickPlaceCounterToSink',
                                       'verdict_explanations': VERDICT_EXPLANATIONS,
                                       'rows': [ik_rows[k] for k in sorted(ik_rows)]})
        (args.out/'ik.md').write_text('# IK 筛查\n\n选定种子已重新 reset，旧 IK 结果已失效。\n\n'
                                       + format_table([{'seed': k, 'reset': None if k in args.seeds else rows.get(k),
                                                        'ik': ik_rows.get(k)}
                                                       for k in sorted(set(rows) | set(args.seeds))]))
    old = args.out/'reset.json'
    errors = {} if not old.exists() else json.loads(old.read_text()).get('errors', {})
    for seed in args.seeds:
        fixture_dir = args.out/'fixtures'/str(seed)
        try:
            fixture = reset_diagnostic_fixture(seed, fixture_dir, args.robot_config, include_sink=True)
            write_json(fixture_dir/'fixture.json', fixture)
            rows[seed] = reset_row(fixture)
            errors.pop(str(seed), None)
            print(json.dumps({'seed': seed, 'target': rows[seed]['target_name'],
                              'width_m': rows[seed]['narrowest_width_m'],
                              'graspable_by_width': rows[seed]['graspable_by_width'],
                              'start_clearance_m': rows[seed]['start_clearance_m'],
                              'start_clearance_ok': rows[seed]['start_clearance_ok'],
                              'sink_safe_point_world_m': rows[seed]['sink']['safe_object_center_world_m'],
                              'sink_rim_top_world_m': rows[seed]['sink']['rim_top_world_m'],
                              'physics_steps': 0}, ensure_ascii=False), flush=True)
        except Exception as exc:
            rows.pop(seed, None)
            errors[str(seed)] = f'{type(exc).__name__}: {exc}'
            print(json.dumps({'seed': seed, 'error': errors[str(seed)],
                              'verdict': 'unknown:reset_missing'}, ensure_ascii=False), flush=True)
        write_json(args.out/'reset.json', {'stage': 'reset', 'task': 'PickPlaceCounterToSink',
                                          'seeds': sorted(set(rows) | set(args.seeds)),
                                          'rows': [rows[k] for k in sorted(rows)], 'errors': errors})
        (args.out/'reset.md').write_text('# Reset 筛查\n\n仅 reset；physics_steps=0。宽度为目标碰撞几何全高度的 5° 水平投影最窄值。\n\n'
                                           + format_table([{'seed': k, 'reset': rows.get(k)}
                                                           for k in sorted(set(rows) | set(args.seeds))])
                                           + ('\n测量错误：\n\n' + '\n'.join(f'- seed {k}: {v}' for k, v in sorted(errors.items())) + '\n'
                                              if errors else ''))
        write_summary(args.out, sorted(set(rows) | set(args.seeds)), rows, ik_rows)


def load_ik(out):
    path = out/'ik.json'
    rows = {} if not path.exists() else {int(row['seed']): row for row in json.loads(path.read_text())['rows']}
    if any(seed not in range(101, 126) for seed in rows):
        raise ValueError('Existing ik.json contains a forbidden non-development seed')
    return rows


def solve_pose(core, body, tool, target, seeds=32):
    """Official multi-seed IK, with the same endpoint and geometry acceptance as wrist_margin_scan."""
    import numpy as np
    current = core.named(body['current_q'], body['joint_names'])
    targets = {tool: target}
    core.check_targets(targets)
    planner, checker = core._select(current, body['active_joints'], tool_frames=[tool],
                                    locked_joints=body['locked_joints'])
    result = planner.ik_solver.solve_pose(core._goal(targets),
        current_state=core._state(current[None], planner), return_seeds=seeds)
    if result is None or result.solution is None:
        return []
    values = result.solution[0].detach().cpu().numpy()
    successes = result.success[0].detach().cpu().numpy()
    accepted = []
    for vector, success in zip(values, successes):
        if not success or not np.isfinite(vector).all():
            continue
        candidate = current.copy()
        for name, value in zip(planner.joint_names, vector):
            candidate[core.joint_names.index(name)] = value
        if (np.any(candidate < core.limits[:, 0]) or np.any(candidate > core.limits[:, 1])
                or not core.validate(candidate[None], planner, checker, measured_start=False)
                or not core._validate_tool_endpoints(candidate, targets, planner)):
            continue
        accepted.append(candidate)
    return accepted


def run_one_ik(args, seed, reset):
    from . import reachability_map as reach
    import numpy as np
    from scipy.spatial.transform import Rotation
    from openarm365_dev.base_motion import check_chassis_path
    from curobo_v2.client import PlanningRejected
    fixture_path = args.out/'fixtures'/str(seed)/'fixture.json'
    fixture = json.loads(fixture_path.read_text())
    if fixture['seed'] != seed or fixture['physics_steps'] != 0:
        raise ValueError('Fixture seed or zero-step contract differs')
    dy_list, dyaw_list, pitches, reject_chassis = grasp_search_options(args)
    shifts = [(0., dy, yaw) for dy in dy_list for yaw in dyaw_list]
    reach_args = SimpleNamespace(robot_config=args.robot_config, assets_dir=args.assets_dir,
        fixture=fixture_path, side='left', exclude_object=True, seed=seed)
    # Reuse the established two-pose grasp search and chassis check as the coarse filter.
    coarse = reach.run_grasp_search(reach_args, fixture, shifts, pitches=pitches,
                                    reject_chassis_before_ik=reject_chassis)
    candidates = [row for row in coarse['summary']['matrix']
                  if row['chassis']['path_clear'] and row['chassis']['endpoint_clear']
                  and abs(row['pitch_deg']) < 1e-9]
    candidates.sort(key=lambda row: not row['both_success'])
    print(json.dumps({'seed': seed, 'event': 'coarse_done', 'candidates': len(candidates),
                      'matrix_rows': len(coarse['summary']['matrix'])}), flush=True)
    prepared = reach.prepare(reach_args, fixture)
    adapter, core, _, body, world, tool, _ = prepared
    ik_world, excluded = reach.collision_world(world, True)
    names = body['active_joints']
    limits = [core.limits[core.joint_names.index(name)].tolist() for name in names]
    transform = np.asarray(fixture['world']['planner_from_world'])
    truth = fixture['diagnostic_truth']
    center = truth['position_world_m']
    zmin, zmax = truth['z_range_world_m']
    grasp_z = (zmin+zmax)/2+0.005
    tested_grasp = 0
    tested_carry = 0
    passed = []
    cache = CarryIKCache(seed)
    grid_cache = GridSearchCache()
    carry_cells_tested = 0
    carry_cells_skipped_geometry = 0
    sink = reset['sink']
    region = next(row for row in sink['regions'] if row['name'] == sink['chosen_region'])
    release_rect = release_rectangle(region)
    release_targets = release_points(release_rect, sink['rim_top_world_m'], center[:2])
    release_results = [{'point_world_m': point, 'cell_margins_rad': []}
                       for point in release_targets]
    release_checks = 0
    for candidate_number, row in enumerate(candidates, 1):
        shift = tuple(row['base_shift'])
        moved = reach.shifted_world(ik_world, shift)
        adapter.push_world(body['env_id'], moved)
        core.set_collision_world(moved)
        core.set_attachment(None)
        world_q = Rotation.from_quat(row['quaternion_world_xyzw'])
        base_q = Rotation.from_euler('z', -shift[2])*Rotation.from_matrix(transform[:3, :3])*world_q
        q_wxyz = [base_q.as_quat()[3], *base_q.as_quat()[:3]]
        grasp_margins = []
        for lift in GRASP_LIFT_M:
            point = (transform @ [center[0], center[1], grasp_z+lift, 1.])[:3]
            target = {'position': reach.shifted_point(point, shift), 'quaternion_wxyz': q_wxyz}
            solutions = solve_pose(core, body, tool, target)
            tested_grasp += 1
            qualified = [joint_margin(q, core.joint_names, core.limits) for q in solutions]
            qualified = [margin for margin in qualified if margin >= J7_MARGIN_MIN_RAD]
            print(json.dumps({'seed': seed, 'event': 'grasp_layer', 'candidate': candidate_number,
                              'candidates': len(candidates), 'base_shift': shift,
                              'lift_m': lift, 'qualified_solutions': len(qualified)}), flush=True)
            if not qualified:
                break
            grasp_margins.append(max(qualified))
        if len(grasp_margins) == len(GRASP_LIFT_M):
            passed.append({'candidate_number': candidate_number, 'row': row,
                           'quaternion_wxyz': q_wxyz, 'grasp_margins': grasp_margins})
    selected = select_carry_candidates(passed, args.max_grasp_candidates)
    print(json.dumps({'seed': seed, 'event': 'carry_selection',
                      'grasp_passed': len(passed), 'carry_candidates_tested': len(selected),
                      'selected_candidates': [item['candidate_number'] for item in selected]}), flush=True)
    carry_candidates_tested = 0
    for item in selected:
        carry_candidates_tested += 1
        candidate_number, row = item['candidate_number'], item['row']
        shift = tuple(row['base_shift'])
        q_wxyz = item['quaternion_wxyz']
        grasp_margins = item['grasp_margins']
        # A fixed-yaw 5 cm chassis BFS, including diagonal edges. Every edge
        # invokes the same complete-world chassis path checker as reachability_map.
        yaw = shift[2]
        last_origin = None
        last_snapshot = None
        def edge_ok(a, b):
            nonlocal last_origin, last_snapshot
            c, s = math.cos(yaw), math.sin(yaw)
            dx, dy = b[0]-a[0], b[1]-a[1]
            local = (c*dx+s*dy, -s*dx+c*dy, 0.)
            if a != last_origin:
                last_snapshot = reach.chassis_snapshot(reach.shifted_world(world, (a[0], a[1], yaw)))
                last_origin = a
            try:
                check_chassis_path(last_snapshot, reach_args.chassis_binding, local)
                return True
            except PlanningRejected:
                return False
        cached = (yaw, (round(shift[0], 4), round(shift[1], 4))) in grid_cache.searches
        print(json.dumps({'seed': seed, 'event': 'bfs_start', 'candidate': candidate_number,
                          'yaw': yaw, 'start': shift[:2], 'cached': cached}), flush=True)
        def bfs_progress(new_edges, yaw_edges):
            print(json.dumps({'seed': seed, 'event': 'bfs_progress', 'candidate': candidate_number,
                              'new_edges_checked': new_edges, 'yaw_edges_checked': yaw_edges}), flush=True)
        cells, reused, new_edges = grid_cache.search(yaw, shift[:2], GRID_BOUNDS_M, edge_ok, bfs_progress)
        print(json.dumps({'seed': seed, 'event': 'bfs_done', 'candidate': candidate_number,
                          'reachable_cells': len(cells), 'new_edges_checked': new_edges,
                          'yaw_edges_checked': len(grid_cache.edges[yaw]), 'cached': reused}), flush=True)
        # Prefer cells closest to the analytic mount shift that aligns target XY.
        target_base = (transform @ [*release_targets[0], 1.])[:3]
        grasp_base = (transform @ [center[0], center[1], grasp_z, 1.])[:3]
        ranked = rank_carry_cells(cells, target_base, grasp_base, shift, yaw,
                                  reach.shifted_point, args.max_carry_cells)
        # No geometry radius gate: the checked-in URDF gives link offsets, but
        # neither it nor reachability_map establishes 0.20–0.75 m as a safe
        # horizontal TCP interval across these heights and orientations.
        # A guessed lower bound could discard valid folded-arm solutions.
        for cell in ranked:
            carry_cells_tested += 1
            if carry_cells_tested == 1 or carry_cells_tested % 25 == 0:
                print(json.dumps({'seed': seed, 'event': 'carry_progress',
                                  'candidate': candidate_number, 'cells_tested': carry_cells_tested,
                                  'reachable_cells': len(cells), 'selected_cells': len(ranked),
                                  'cache_hit': cache.hits}), flush=True)
            carry_shift = (cell[0], cell[1], yaw)
            def point_margins():
                nonlocal release_checks
                for point_number, point_world in enumerate(release_targets):
                    def solve_carry():
                        nonlocal tested_carry
                        moved = reach.shifted_world(ik_world, carry_shift)
                        adapter.push_world(body['env_id'], moved)
                        core.set_collision_world(moved)
                        point = (transform @ [*point_world, 1.])[:3]
                        target = {'position': reach.shifted_point(point, carry_shift),
                                  'quaternion_wxyz': q_wxyz}
                        solutions = solve_pose(core, body, tool, target)
                        tested_carry += 1
                        qualified = [joint_margin(q, core.joint_names, core.limits) for q in solutions]
                        qualified = [margin for margin in qualified if margin >= J7_MARGIN_MIN_RAD]
                        return (bool(qualified), max(qualified) if qualified else None)
                    (_, margin), hit = cache.check(q_wxyz, carry_shift, point_number, solve_carry)
                    release_checks += 1
                    release_results[point_number]['cell_margins_rad'].append({
                        'candidate': candidate_number, 'base_cell': list(cell),
                        'margin_rad': margin, 'cache_hit': hit})
                    yield margin
            feasible = first_feasible_release(point_margins())
            if feasible is not None:
                point_number, margin = feasible
                theta = theta_from_world_quaternion(row['quaternion_world_xyzw'])
                print(json.dumps({'seed': seed, 'event': 'carry_cells_done',
                                  'candidate': candidate_number, 'cells_tested': carry_cells_tested,
                                  'cache_hit': cache.hits, 'success': True}), flush=True)
                return {'seed': seed, 'grasp_ik_feasible': True, 'carry_ik_feasible': True,
                    'grasp_base_shift': list(shift), 'carry_base_cell': list(cell),
                    'selected_candidate_number': candidate_number,
                    'grasp_quaternion_world_xyzw': row['quaternion_world_xyzw'],
                    'grasp_theta_deg': theta,
                    'grasp_theta_note': None if theta is not None else
                        'world quaternion has pitch/roll; probe theta formula only represents vertical-down TCP poses',
                    'object_height_m': reset['target_z_range_world_m'][1]-reset['target_z_range_world_m'][0],
                    'target_z_range_world_m': list(reset['target_z_range_world_m']),
                    'coarse_grasp_candidates': len(candidates),
                    'coarse_both_success_candidates': sum(candidate['both_success'] for candidate in candidates),
                    'grasp_j7_min_margin_rad': min(grasp_margins), 'carry_j7_min_margin_rad': margin,
                    'release_point_world_m': release_targets[point_number],
                    'release_rectangle_xy_world_m': release_rect,
                    'release_point_count': len(release_targets),
                    'release_point_checks': release_checks,
                    'release_point_results': release_results,
                    'ik_scope': '端点可行性,不含路径',
                    'reachable_chassis_cells': len(cells),
                    'chassis_edges_checked': sum(map(len, grid_cache.edges.values())),
                    'grasp_targets_checked': tested_grasp, 'carry_targets_checked': tested_carry,
                    'carry_candidates_tested': carry_candidates_tested, 'carry_cells_tested': carry_cells_tested,
                    'carry_cells_skipped_geometry': carry_cells_skipped_geometry,
                    'carry_cache_hits': cache.hits,
                    'excluded_target_proxy_cuboids': len(excluded), 'physics_steps': 0}
        print(json.dumps({'seed': seed, 'event': 'carry_cells_done',
                          'candidate': candidate_number, 'cells_tested': carry_cells_tested,
                          'cache_hit': cache.hits, 'success': False}), flush=True)
    return {'seed': seed, 'grasp_ik_feasible': bool(passed),
            'carry_ik_feasible': False, 'coarse_grasp_candidates': len(candidates),
            'coarse_both_success_candidates': sum(row['both_success'] for row in candidates),
            'release_point_world_m': None, 'release_rectangle_xy_world_m': release_rect,
            'release_point_count': len(release_targets), 'release_point_checks': release_checks,
            'release_point_results': release_results, 'ik_scope': '端点可行性,不含路径',
            'grasp_targets_checked': tested_grasp, 'carry_targets_checked': tested_carry,
            'carry_candidates_tested': carry_candidates_tested, 'carry_cells_tested': carry_cells_tested,
            'carry_cells_skipped_geometry': carry_cells_skipped_geometry,
            'carry_cache_hits': cache.hits,
            'physics_steps': 0}


def persist_ik(out, seeds, reset, ik):
    write_json(out/'ik.json', {'stage': 'ik', 'task': 'PickPlaceCounterToSink',
                              'verdict_explanations': VERDICT_EXPLANATIONS,
                              'rows': [ik[k] for k in sorted(ik)]})
    write_text(out/'ik.md', '# IK 筛查\n\n底盘 5 cm 有界格点；抓取高度到 +0.06 m，j7 余量 ≥0.10 rad。'
               '释放 TCP 在盆沿上方 0.08 m；feasible 仅表示端点 IK 可行，'
               '不含 TCP 水平平移路径、连续持物轨迹或原生成功。\n\n'
               + format_table([{'seed': k, 'reset': reset.get(k), 'ik': ik.get(k)}
                               for k in sorted(set(reset) | set(seeds) | set(ik))]))
    write_summary(out, sorted(set(reset) | set(seeds) | set(ik)), reset, ik)


def run_ik(args):
    reset = load_reset(args.out)
    ik = load_ik(args.out)
    for seed in args.seeds:
        if args.skip_done and seed in ik:
            print(json.dumps({'seed': seed, 'event': 'skip_done',
                              'verdict': verdict(reset.get(seed), ik[seed])}, ensure_ascii=False), flush=True)
            continue
        print(json.dumps({'seed': seed, 'event': 'seed_start',
                          'timeout_seconds': args.seed_timeout_seconds}), flush=True)
        try:
            with seed_deadline(args.seed_timeout_seconds):
                if seed not in reset:
                    result = {'seed': seed, 'skipped': 'reset_missing',
                              'grasp_ik_feasible': None, 'carry_ik_feasible': None, 'physics_steps': 0}
                elif not reset[seed]['graspable_by_width'] or not reset[seed]['start_clearance_ok']:
                    result = {'seed': seed, 'skipped': 'reset_infeasible',
                              'grasp_ik_feasible': None, 'carry_ik_feasible': None, 'physics_steps': 0}
                else:
                    result = run_one_ik(args, seed, reset[seed])
        except SeedTimeout:
            result = {'seed': seed, 'timeout': True, 'timeout_seconds': args.seed_timeout_seconds,
                      'grasp_ik_feasible': None, 'carry_ik_feasible': None, 'physics_steps': 0}
        except Exception as exc:
            result = {'seed': seed, 'error': f'{type(exc).__name__}: {exc}',
                      'grasp_ik_feasible': None, 'carry_ik_feasible': None, 'physics_steps': 0}
        ik[seed] = result
        persist_ik(args.out, args.seeds, reset, ik)
        print(json.dumps({'event': 'seed_done', **result,
                          'verdict': verdict(reset.get(seed), result)}, ensure_ascii=False), flush=True)


def positive_seconds(raw):
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('timeout must be a positive finite number') from exc
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError('timeout must be a positive finite number')
    return value


def positive_int(raw):
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('limit must be a positive integer') from exc
    if value <= 0:
        raise argparse.ArgumentTypeError('limit must be a positive integer')
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds', required=True, type=parse_seeds)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--stage', required=True, choices=('reset', 'ik'))
    p.add_argument('--robot-config', type=Path)
    p.add_argument('--assets-dir', type=Path)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--dy-list', type=parse_float_list, default=GRASP_DY)
    p.add_argument('--dyaw-list', type=parse_float_list, default=SCREEN_DYAW)
    p.add_argument('--pitch-zero-only', action=argparse.BooleanOptionalAction, default=True)
    p.add_argument('--full-grasp-search', action='store_true',
                   help='restore the original 12 shifts and 48 orientations, including IK before chassis filtering')
    p.add_argument('--skip-done', action='store_true', help='skip seeds already recorded in ik.json')
    p.add_argument('--max-grasp-candidates', type=positive_int, default=3,
                   help='different complete grasp orientations to try for carry (default: 3)')
    p.add_argument('--max-carry-cells', type=positive_int, default=80,
                   help='top XY-aligned reachable cells per carry candidate (default: 80)')
    p.add_argument('--seed-timeout-seconds', type=positive_seconds, default=1500.,
                   help='wall-clock budget per IK seed (default: 1500)')
    args = p.parse_args(argv)
    if args.dry_run:
        dy_list, dyaw_list, pitches, reject_chassis = grasp_search_options(args)
        shifts = len(dy_list)*len(dyaw_list)
        orientations = 3*2*len(pitches)*(1 if pitches == (0,) else 2)
        vertical_orientations = 3*2*(1 if pitches == (0,) else 2)
        grid_cells = (round((GRID_BOUNDS_M[1]-GRID_BOUNDS_M[0])/GRID_M)+1) * (
            round((GRID_BOUNDS_M[3]-GRID_BOUNDS_M[2])/GRID_M)+1)
        print(json.dumps({'stage': args.stage, 'seeds': args.seeds,
            'reset_count': len(args.seeds) if args.stage == 'reset' else 0,
            'grasp_base_shifts_per_seed': shifts if args.stage == 'ik' else 0,
            'grasp_orientations_per_shift': orientations if args.stage == 'ik' else 0,
            'coarse_grasp_ik_targets_max_per_seed': shifts*orientations*2 if args.stage == 'ik' else 0,
            'vertical_grasp_candidates_max_per_seed': shifts*vertical_orientations if args.stage == 'ik' else 0,
            'chassis_reject_before_ik': reject_chassis if args.stage == 'ik' else False,
            'lift_layers_per_candidate': len(GRASP_LIFT_M) if args.stage == 'ik' else 0,
            'carry_targets_per_reachable_cell_max': 10 if args.stage == 'ik' else 0,
            'grid_cells_upper_bound_per_grasp_candidate': grid_cells if args.stage == 'ik' else 0,
            'max_grasp_candidates': args.max_grasp_candidates,
            'max_carry_cells': args.max_carry_cells,
            'carry_ik_targets_upper_bound_per_grasp_candidate': 10*min(grid_cells, args.max_carry_cells) if args.stage == 'ik' else 0,
            'carry_ik_targets_upper_bound_per_seed': 10*args.max_grasp_candidates*min(grid_cells, args.max_carry_cells) if args.stage == 'ik' else 0,
            'release_inset_m': RELEASE_INSET_M, 'release_above_rim_m': RELEASE_ABOVE_RIM_M,
            'verdict_explanations': VERDICT_EXPLANATIONS,
            'geometry_prefilter_enabled': False,
            'grid_pitch_m': GRID_M, 'grid_bounds_m': GRID_BOUNDS_M,
            'seed_timeout_seconds': args.seed_timeout_seconds, 'skip_done': args.skip_done,
            'note': 'No reset, cuRobo import, GPU, physical step, or model call'}, ensure_ascii=False))
        return
    if args.robot_config is None or (args.stage == 'ik' and args.assets_dir is None):
        p.error('real reset requires --robot-config; real ik also requires --assets-dir')
    args.out.mkdir(parents=True, exist_ok=True)
    if args.stage == 'reset':
        run_reset(args)
    else:
        run_ik(args)


if __name__ == '__main__':
    main()
