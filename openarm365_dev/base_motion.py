"""Preflight the authenticated whole robot against a complete frozen world."""
import math
import copy
import time
import numpy as np
from scipy.spatial.transform import Rotation
from curobo_v2.client import PlanningRejected
from curobo_v2.sapien_world import transformed_box

BASE_TRANSLATION_LIMIT_M = 0.20
BASE_YAW_LIMIT_RAD = 0.30
BASE_CLEARANCE_M = 0.02
BASE_SAMPLE_TRANSLATION_M = 0.02
BASE_SAMPLE_YAW_RAD = 0.05
# Allow at most 1 mm of numerical overlap at the robot/support interface.
# The 2 cm clearance skin applies to obstacles rising above this interface.
BASE_SUPPORT_HEIGHT_TOLERANCE_M = 0.001
BASE_MIN_DISTANCE_M = 0.003
BASE_NEAR_START_ALLOWANCE_M = 0.002
BASE_SETTLE_CONSECUTIVE_STEPS = 5
BASE_SETTLE_MAX_STEPS = 60
BASE_SETTLE_TRANSLATION_SPEED_MPS = 2e-4
BASE_SETTLE_YAW_SPEED_RADPS = 2e-4


class BasePathRejected(PlanningRejected):
    def __init__(self, message, collision_check):
        super().__init__(message)
        self.collision_check = collision_check


def guard_base_and_arms(base, held_base, arm, start_arm=None, *, execution_settings=None):
    if start_arm is None:
        limits = base_drift_limits(execution_settings)
        if limits is not None:
            measured = base_drift(base, held_base)
            for kind, value, limit in zip(('translation_m', 'yaw_rad'), measured, limits):
                if value > limit:
                    raise RuntimeError(f'Uncommanded base drift {kind}={value:.9g} exceeds limit={limit:.9g}')
        elif np.max(np.abs(np.asarray(base)-np.asarray(held_base))) > .001:
            raise RuntimeError('Uncommanded base drift exceeded 1mm/1mrad')
    elif np.max(np.abs(np.asarray(arm)-np.asarray(start_arm))) > .004:
        raise RuntimeError('Arm joint drift during base motion exceeded 4mrad')


def base_drift_limits(execution_settings=None):
    if execution_settings is None:
        return None
    return execution_settings.base_hold_drift_m, execution_settings.base_hold_drift_rad


def base_drift(base, held_base):
    difference = np.abs(np.asarray(base)-np.asarray(held_base))
    return float(np.max(difference[:2])), float(difference[2])


def effective_base_clearance(execution_settings=None):
    return BASE_CLEARANCE_M if execution_settings is None else execution_settings.base_path_clearance_m


def base_is_stationary(previous, current, control_timestep):
    """Check all three base speeds from adjacent control-step positions."""
    speeds = np.abs(np.asarray(current, float)-np.asarray(previous, float)) / control_timestep
    return bool(np.isfinite(speeds).all()
                and np.all(speeds[:2] <= BASE_SETTLE_TRANSLATION_SPEED_MPS)
                and speeds[2] <= BASE_SETTLE_YAW_SPEED_RADPS)


def validate_delta(dx, dy, dyaw):
    try: delta = np.asarray([dx, dy, dyaw], dtype=float)
    except (TypeError, ValueError) as exc: raise PlanningRejected('Base delta must be finite numeric metres/radians') from exc
    if delta.shape != (3,) or not np.isfinite(delta).all():
        raise PlanningRejected('Base delta must be finite numeric metres/radians')
    if np.linalg.norm(delta[:2]) > BASE_TRANSLATION_LIMIT_M + 1e-12 or abs(delta[2]) > BASE_YAW_LIMIT_RAD + 1e-12:
        raise PlanningRejected('Base delta exceeds 0.20m translation or 0.3rad yaw per SDK call')
    return delta


def base_joint_delta(delta, before, base_initial):
    """Map a mount-frame command to the native base slide/hinge coordinates."""
    theta = float(before[2] - base_initial[2])
    c, s = math.cos(theta), math.sin(theta)
    return np.array([c*delta[0]-s*delta[1], s*delta[0]+c*delta[1], delta[2]])


def effective_base_min_distance(execution_settings=None):
    return BASE_MIN_DISTANCE_M if execution_settings is None else execution_settings.base_start_clearance_m


def required_pair_distance(start_distance, *, execution_settings=None):
    return max(effective_base_min_distance(execution_settings),
               min(effective_base_clearance(execution_settings), float(start_distance)-BASE_NEAR_START_ALLOWANCE_M))


def aabb_distance_lower_bound(center, half, centers, halves):
    """Euclidean lower bound between one AABB and a vector of AABBs."""
    gaps = np.maximum(np.abs(centers-center)-halves-half, 0.)
    return np.linalg.norm(gaps, axis=1)


def _geom_aabb(data, gid, local_center, local_half):
    rotation = data.geom_xmat[gid].reshape(3, 3)
    return (data.geom_xpos[gid] + rotation @ local_center,
            np.abs(rotation) @ local_half)


def _copy_mjdata(model, source):
    import mujoco
    copier = getattr(mujoco, 'mj_copyData', None)
    if copier is not None:
        result = mujoco.MjData(model)
        copier(result, model, source)
        return result
    # MuJoCo 3.3's Python binding lacks mj_copyData; its MjData copy protocol
    # supplies an independent native data copy.
    return copy.copy(source)


def check_exact_base_path(env, world, binding, delta, base_initial, *, source_data=None, held_body_id=None, execution_settings=None):
    """Sweep native geoms with FK and exact MuJoCo distances on copied data."""
    import mujoco
    from .world import is_descendant, shape_box

    absolute_min = effective_base_min_distance(execution_settings)
    clearance = effective_base_clearance(execution_settings)
    started = time.monotonic()
    delta = validate_delta(*delta)
    sim = env.sim
    model = sim.model
    native_model = model._model
    root = model.body_name2id('robot0_openarm_body_link0')
    chassis = binding['native_geom']
    listed = world['excluded_robot_geoms']
    expected = {gid for gid in range(model.ngeom)
                if (model.geom_contype[gid] or model.geom_conaffinity[gid])
                and (is_descendant(model, int(model.geom_bodyid[gid]), root)
                     or model.geom_id2name(gid) == chassis)}
    robots = [int(row['geom_id']) for row in listed]
    if (len(robots) != len(expected) or set(robots) != expected
            or sum(model.geom_id2name(gid) == chassis for gid in robots) != 1
            or any(model.geom_id2name(int(row['geom_id'])) != row['name'] for row in listed)):
        raise PlanningRejected('Authenticated robot collision inventory is incomplete')
    inventory = world.get('unexempted_inventory', world['inventory'])
    obstacles = [int(row['geom_id']) for row in inventory]
    complete = {gid for gid in range(model.ngeom)
                if (model.geom_contype[gid] or model.geom_conaffinity[gid]) and gid not in expected}
    if len(obstacles) != len(complete) or set(obstacles) != complete:
        raise PlanningRejected('Complete unexempted collision world is missing')
    held_geoms = []
    if held_body_id is not None:
        from .world import movable_object_roots
        held_body_id = int(held_body_id)
        if held_body_id not in movable_object_roots(env).values():
            raise PlanningRejected('Held body is not a registered free object')
        held_geoms = [gid for gid in obstacles
                      if is_descendant(model, int(model.geom_bodyid[gid]), held_body_id)]
        if not held_geoms:
            raise PlanningRejected('Held object has no collision geoms in complete world')
        obstacles = [gid for gid in obstacles if gid not in held_geoms]
        robots += held_geoms
    if len(robots) == 0 or len(obstacles) == 0:
        raise PlanningRejected('Collision inventory is empty')

    start_data = _copy_mjdata(native_model, sim.data._data if source_data is None else source_data)
    trial_data = _copy_mjdata(native_model, start_data)
    mujoco.mj_forward(native_model, start_data)
    if held_geoms:
        from scipy.spatial.transform import Rotation as _Rotation
        held_joint = int(model.body_jntadr[held_body_id])
        held_qadr = int(model.jnt_qposadr[held_joint])
        root_position = start_data.xpos[root].copy()
        root_rotation = start_data.xmat[root].reshape(3, 3).copy()
        held_position = start_data.qpos[held_qadr:held_qadr+3].copy()
        held_quat = start_data.qpos[held_qadr+3:held_qadr+7].copy()
    bounds = {gid: shape_box(model, gid) for gid in robots+obstacles}
    world_boxes = [_geom_aabb(start_data, gid, *bounds[gid]) for gid in obstacles]
    world_centers = np.asarray([box[0] for box in world_boxes])
    world_halves = np.asarray([box[1] for box in world_boxes])
    world_tops = world_centers[:, 2]+world_halves[:, 2]
    names = {gid: model.geom_id2name(gid) or f'unnamed:{gid}' for gid in robots+obstacles}
    qpos_indexes = env.robots[0]._ref_base_joint_pos_indexes
    before = np.asarray(start_data.qpos[qpos_indexes], float).copy()
    native_delta = base_joint_delta(delta, before, base_initial)
    samples = max(1, math.ceil(np.linalg.norm(delta[:2])/BASE_SAMPLE_TRANSLATION_M),
                  math.ceil(abs(delta[2])/BASE_SAMPLE_YAW_RAD))
    checked_obstacles = set()
    start_distances = {}
    minimum = math.inf
    minimum_pair = None
    start_minimum = math.inf
    start_pair = None
    distance_calls = 0
    violation = None
    for index in range(samples+1):
        if index:
            trial_data.qpos[qpos_indexes] = before + native_delta*(index/samples)
            if held_geoms:
                mujoco.mj_forward(native_model, trial_data)
                rotation = trial_data.xmat[root].reshape(3, 3) @ root_rotation.T
                trial_data.qpos[held_qadr:held_qadr+3] = (
                    trial_data.xpos[root] + rotation @ (held_position-root_position))
                quat_xyzw = (_Rotation.from_matrix(rotation) *
                             _Rotation.from_quat([*held_quat[1:], held_quat[0]])).as_quat()
                trial_data.qpos[held_qadr+3:held_qadr+7] = [quat_xyzw[3], *quat_xyzw[:3]]
            mujoco.mj_forward(native_model, trial_data)
            sample_data = trial_data
        else:
            sample_data = start_data
        for robot_gid in robots:
            center, half = _geom_aabb(sample_data, robot_gid, *bounds[robot_gid])
            # Preserve the support rule: a surface wholly below the robot
            # bottom (within 1 mm) is not a lateral obstacle.
            active = np.flatnonzero(world_tops > center[2]-half[2]
                                    + BASE_SUPPORT_HEIGHT_TOLERANCE_M)
            if not len(active):
                continue
            lower = aabb_distance_lower_bound(center, half,
                                              world_centers[active], world_halves[active])
            for pos in np.argsort(lower):
                lower_bound = float(lower[pos])
                if lower_bound > max(clearance, minimum) + 1e-10:
                    break
                obstacle_gid = obstacles[int(active[pos])]
                checked_obstacles.add(obstacle_gid)
                distance = float(mujoco.mj_geomDistance(
                    native_model, sample_data, robot_gid, obstacle_gid, 1e6, None))
                distance_calls += 1
                pair = dict(robot_geom=names[robot_gid], obstacle=names[obstacle_gid],
                            sample_index=index, sample_count=samples+1, clearance_m=distance)
                if distance < minimum:
                    minimum, minimum_pair = distance, pair
                key = (robot_gid, obstacle_gid)
                if index == 0:
                    start_distances[key] = distance
                    if distance < start_minimum:
                        start_minimum, start_pair = distance, pair
                if distance <= clearance + 1e-10:
                    if key not in start_distances:
                        start_distances[key] = float(mujoco.mj_geomDistance(
                            native_model, start_data, robot_gid, obstacle_gid, 1e6, None))
                        distance_calls += 1
                    d0 = start_distances[key]
                    required = required_pair_distance(d0, execution_settings=execution_settings)
                    if d0 < absolute_min - 1e-10 or distance < required - 1e-10:
                        if violation is None or index == 0 and distance < violation[0]['clearance_m']:
                            violation = (pair, d0, required)
    audit = dict(status='rejected' if violation else 'clear', samples=samples+1,
                 robot_geoms_checked=len(robots), obstacles_checked=len(checked_obstacles),
                 world_obstacle_geoms=len(obstacles),
                 obstacle_candidates_checked=len(checked_obstacles),
                 distance_calculations=distance_calls, elapsed_seconds=time.monotonic()-started,
                 minimum_clearance_m=None if minimum_pair is None else minimum,
                 minimum_obstacle_clearance_m=None if minimum_pair is None else minimum,
                 minimum_clearance_pair=minimum_pair,
                 start_minimum_clearance_m=None if start_pair is None else start_minimum,
                 start_minimum_clearance_pair=start_pair,
                 first_violation_pair=None if violation is None else violation[0],
                 first_violation_start_distance_m=None if violation is None else violation[1],
                 first_violation_required_distance_m=None if violation is None else violation[2],
                 required_clearance_m=clearance,
                 absolute_min_distance_m=absolute_min,
                 near_start_allowance_m=BASE_NEAR_START_ALLOWANCE_M,
                 world_sha256=world.get('world_sha256_unexempted', world['world_sha256']))
    if violation:
        pair, d0, required = violation
        raise BasePathRejected(
            f"Base path clearance {pair['clearance_m']*1000:.2f} mm below "
            f"{required*1000:.2f} mm at sample {pair['sample_index']}/{samples}: "
            f"{pair['robot_geom']} versus {pair['obstacle']} "
            f"(start {d0*1000:.2f} mm)", audit)
    return audit


def robot_boxes_from_geoms(geoms, planner_from_world):
    """Pure conversion of measured geom local boxes and poses into mount boxes."""
    return [transformed_box(geom['name'], geom['local_center'], geom['half_size'],
                            geom['world_from_geom'], planner_from_world) for geom in geoms]


def measured_robot_boxes(env, world, binding):
    """Read precisely the collision geoms excluded as the authenticated robot."""
    from .world import shape_box, is_descendant
    sim = env.sim; model = sim.model
    root = model.body_name2id('robot0_openarm_body_link0')
    excluded = world['excluded_robot_geoms']
    chassis_name = binding['native_geom']
    if sum(row['name'] == chassis_name for row in excluded) != 1:
        raise PlanningRejected('Authenticated chassis shape missing or ambiguous')
    expected = {gid for gid in range(model.ngeom)
                if (model.geom_contype[gid] or model.geom_conaffinity[gid]) and
                (is_descendant(model, int(model.geom_bodyid[gid]), root) or
                 model.geom_id2name(gid) == chassis_name)}
    listed = [int(row['geom_id']) for row in excluded]
    if len(listed) != len(expected) or set(listed) != expected:
        raise PlanningRejected('Authenticated robot collision inventory is incomplete')
    geoms = []
    for row in excluded:
        gid = int(row['geom_id'])
        if gid < 0 or gid >= model.ngeom:
            raise PlanningRejected('Authenticated robot collision inventory differs from simulator')
        name = model.geom_id2name(gid) or 'unnamed'
        if (name != row['name'] or
                not (model.geom_contype[gid] or model.geom_conaffinity[gid]) or
                not (is_descendant(model, int(model.geom_bodyid[gid]), root) or name == chassis_name)):
            raise PlanningRejected('Authenticated robot collision inventory differs from simulator')
        if name == chassis_name:
            continue
        center, half = shape_box(model, gid)
        pose = np.eye(4)
        pose[:3, :3] = sim.data.geom_xmat[gid].reshape(3, 3)
        pose[:3, 3] = sim.data.geom_xpos[gid]
        geoms.append(dict(name=f'mujoco:{gid}:{name}', local_center=center,
                          half_size=half, world_from_geom=pose))
    boxes = robot_boxes_from_geoms(geoms, world['planner_from_world'])
    boxes.append(dict(name=f'chassis:{chassis_name}', center=list(binding['center_in_planner_frame']),
                      half_size=list(binding['half_size']), quat_wxyz=[1., 0., 0., 0.]))
    return boxes


def _box(box):
    center = np.asarray(box['center'], float)
    half = np.asarray(box['half_size'], float)
    rotation = Rotation.from_quat(np.asarray(box['quat_wxyz'], float)[[1, 2, 3, 0]]).as_matrix()
    if (center.shape != (3,) or half.shape != (3,) or not np.isfinite(center).all()
            or not np.isfinite(half).all() or np.any(half <= 0)):
        raise PlanningRejected('Invalid collision box')
    return center, rotation, half


def _obb_clearance(ca, ra, ha, cb, rb, hb):
    """Largest separating-axis gap; positive values certify separation."""
    axes = [*ra.T, *rb.T, *(np.cross(a, b) for a in ra.T for b in rb.T)]
    difference = cb - ca
    gap = -math.inf
    for axis in axes:
        length = np.linalg.norm(axis)
        if length < 1e-10: continue
        axis = axis / length
        reach = np.sum(ha * np.abs(ra.T @ axis)) + np.sum(hb * np.abs(rb.T @ axis))
        gap = max(gap, abs(np.dot(difference, axis)) - reach)
    return float(gap)


def _obb_overlap(ca, ra, ha, cb, rb, hb, clearance):
    return _obb_clearance(ca, ra, ha, cb, rb, hb) <= clearance + 1e-10


def _face_gaps(c, r, h, centers, rotations, halves):
    """Vectorized lower bound on SAT clearance from the six face axes."""
    difference = centers - c
    cross = np.einsum('ik,nij->nkj', r, rotations)
    a = np.abs(difference @ r) - h - np.einsum('nj,nkj->nk', halves, np.abs(cross))
    b = np.abs(np.einsum('ni,nij->nj', difference, rotations)) - halves \
        - np.einsum('k,nkj->nj', h, np.abs(cross))
    return np.maximum(a.max(axis=1), b.max(axis=1))


def check_chassis_path(world, binding, delta, robot_boxes=None):
    """Sweep fixed-joint robot boxes about the current mount yaw pivot."""
    delta = validate_delta(*delta)
    center = np.asarray(binding['center_in_planner_frame'], float)
    half = np.asarray(binding['half_size'], float)
    if center.shape != (3,) or half.shape != (3,) or np.any(half <= 0):
        raise PlanningRejected('Authenticated chassis binding is invalid')
    if robot_boxes is None:
        # Chassis-only offline diagnostics retain their old call signature.
        # NativeExecutor supplies measured full-robot boxes.
        robot_boxes = [dict(name='chassis', center=center, half_size=half,
                            quat_wxyz=[1., 0., 0., 0.])]
    cuboids = world.get('unexempted_cuboids', world['cuboids'])
    inventory = world.get('unexempted_inventory', world['inventory'])
    world_sha256 = world.get('world_sha256_unexempted', world['world_sha256'])
    if world.get('exempted_geom_count', 0) and 'unexempted_cuboids' not in world:
        raise PlanningRejected('Complete unexempted collision world is missing')
    if len(cuboids) != len(inventory):
        raise PlanningRejected('World collision inventory is incomplete')
    robots = [(box['name'], *_box(box)) for box in robot_boxes]
    if not robots:
        raise PlanningRejected('Robot collision inventory is empty')
    obstacles = [(box['name'], *_box(box)) for box in cuboids]
    obstacle_centers = np.asarray([row[1] for row in obstacles]).reshape(-1, 3)
    obstacle_rotations = np.asarray([row[2] for row in obstacles]).reshape(-1, 3, 3)
    obstacle_halves = np.asarray([row[3] for row in obstacles]).reshape(-1, 3)
    obstacle_tops = obstacle_centers[:, 2] + np.einsum(
        'ni,ni->n', np.abs(obstacle_rotations[:, 2, :]), obstacle_halves)
    samples = max(1, math.ceil(np.linalg.norm(delta[:2]) / BASE_SAMPLE_TRANSLATION_M),
                  math.ceil(abs(delta[2]) / BASE_SAMPLE_YAW_RAD))
    minimum = math.inf; closest = None; checked_obstacles = set(); violation = None
    for index in range(samples + 1):
        t = index / samples
        motion = Rotation.from_euler('z', float(delta[2] * t)).as_matrix()
        shift = np.array([delta[0] * t, delta[1] * t, 0.])
        for robot_name, rc, rr, rh in robots:
            c = motion @ rc + shift; r = motion @ rr
            bottom_z = c[2] - np.dot(np.abs(r[2, :]), rh)
            active = np.flatnonzero(obstacle_tops > bottom_z + BASE_SUPPORT_HEIGHT_TOLERANCE_M)
            if not len(active): continue
            checked_obstacles.update(obstacles[i][0] for i in active)
            bounds = _face_gaps(c, r, rh, obstacle_centers[active],
                                obstacle_rotations[active], obstacle_halves[active])
            for i, face_gap in zip(active, bounds):
                if face_gap > minimum: continue
                obstacle_name, oc, orientation, oh = obstacles[i]
                gap = _obb_clearance(c, r, rh, oc, orientation, oh)
                if gap < minimum:
                    minimum = gap
                    closest = dict(robot_geom=robot_name, obstacle=obstacle_name,
                                   sample_index=index, sample_count=samples + 1,
                                   clearance_m=gap)
                if gap <= BASE_CLEARANCE_M + 1e-10 and violation is None:
                    violation = (index, robot_name, obstacle_name)
    reported_minimum = minimum if closest is not None else None
    audit = dict(status='rejected' if violation else 'clear', samples=samples + 1, robot_geoms_checked=len(robots),
                minimum_clearance_m=reported_minimum, minimum_obstacle_clearance_m=reported_minimum,
                minimum_clearance_pair=closest, world_sha256=world_sha256,
                obstacles_checked=len(checked_obstacles))
    if violation:
        index, robot_name, obstacle_name = violation
        raise BasePathRejected(
            f'Base path clearance below {BASE_CLEARANCE_M:.2f}m at sample {index}/{samples}: '
            f'{robot_name} versus {obstacle_name}', audit)
    return audit
