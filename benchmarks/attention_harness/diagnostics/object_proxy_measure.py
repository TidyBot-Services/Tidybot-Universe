"""Read-only development-seed MuJoCo geometry measurement; no policy or physics steps."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.spatial import ConvexHull
from scipy.spatial.transform import Rotation

DEFAULT_ROBOT_CONFIG = Path('/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime/project/config/openarm_fixed.yml')
DEFAULT_ASSETS = Path('/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-05/runtime/project/curobo_v2/service/curobo_service/assets')


def hull_volume(points):
    points = np.asarray(points, float)
    return float(ConvexHull(points).volume)


def axis_bounds(points, axes=None):
    p = np.asarray(points, float)
    if axes is not None:
        p = p @ np.asarray(axes, float)
    return np.ptp(p, axis=0)


def projection_width(points, direction):
    direction = np.asarray(direction, float)
    direction /= np.linalg.norm(direction)
    return float(np.ptp(np.asarray(points, float) @ direction))


def layered_horizontal_widths(points, window_height_m, pad_gap_m, sphere_gap_m,
                              step_m=.005, angle_step_deg=5):
    """Sample XY widths of vertices near successive world-Z levels.

    Each center has a +/- half-window-height selection. Empty windows are
    reported, never treated as zero-width grasp candidates.
    """
    p = np.asarray(points, float)
    if p.ndim != 2 or p.shape[1] != 3 or len(p) == 0:
        raise ValueError('points must be a nonempty N x 3 array')
    if window_height_m <= 0 or step_m <= 0 or 180 % angle_step_deg:
        raise ValueError('window, step and angle step must be positive; angle step divides 180')
    zmin, zmax = float(p[:, 2].min()), float(p[:, 2].max())
    centers = np.arange(zmin, zmax + 1e-12, step_m)
    if centers[-1] < zmax - 1e-10:
        centers = np.r_[centers, zmax]
    angles = list(range(0, 180, angle_step_deg))
    axes = np.array([[math.cos(math.radians(a)), math.sin(math.radians(a))]
                     for a in angles])
    layers = []
    for center in centers:
        selected = p[np.abs(p[:, 2] - center) <= window_height_m / 2 + 1e-12]
        row = {'center_world_z_m': float(center), 'vertex_count': len(selected),
               'window_fully_within_object_z_range': bool(center-window_height_m/2 >= zmin-1e-12
                                                          and center+window_height_m/2 <= zmax+1e-12)}
        if len(selected):
            projections = selected[:, :2] @ axes.T
            widths = np.ptp(projections, axis=0)
            minimum = int(np.argmin(widths))
            row.update(narrowest_width_m=float(widths[minimum]),
                       narrowest_angle_world_xy_deg=angles[minimum],
                       widths_m_by_angle={str(a): float(w) for a, w in zip(angles, widths)},
                       angles_within_pad_gap_deg=[a for a, w in zip(angles, widths) if w <= pad_gap_m + 1e-12],
                       angles_within_sphere_gap_deg=[a for a, w in zip(angles, widths) if w <= sphere_gap_m + 1e-12])
        layers.append(row)
    populated = [row for row in layers if row['vertex_count']]
    best = min(populated, key=lambda row: row['narrowest_width_m'])
    return {'step_m': step_m, 'window_height_m': window_height_m,
            'window_half_height_m': window_height_m / 2,
            'pad_comparison_threshold_m': pad_gap_m,
            'sphere_comparison_threshold_m': sphere_gap_m,
            'angle_step_deg': angle_step_deg, 'angle_domain_deg': [0, 180],
            'object_world_z_range_m': [zmin, zmax], 'object_vertical_height_m': zmax-zmin,
            'layers': layers,
            'narrowest_local': {'center_world_z_m': best['center_world_z_m'],
                                'width_m': best['narrowest_width_m'],
                                'angle_world_xy_deg': best['narrowest_angle_world_xy_deg'],
                                'vertex_count': best['vertex_count']},
            'layers_within_pad_gap': [row['center_world_z_m'] for row in populated
                                      if row['angles_within_pad_gap_deg']],
            'layers_within_sphere_gap': [row['center_world_z_m'] for row in populated
                                         if row['angles_within_sphere_gap_deg']],
            'interior_layers_within_pad_gap': [row['center_world_z_m'] for row in populated
                                                if row['window_fully_within_object_z_range']
                                                and row['angles_within_pad_gap_deg']],
            'method': 'World collision-geometry vertices in each center +/- half window; XY max-min projection. Vertex-only slices can underestimate a continuous surface, especially near window boundaries.'}


def oriented_bounds(points):
    """Numerical minimum-volume 3D box, seeded by convex-hull face/edge frames."""
    p = np.asarray(points, float)
    hull = ConvexHull(p)
    vertices = p[hull.vertices]
    best = (math.inf, None, None)
    seen = set()

    def consider(matrix):
        nonlocal best
        key = tuple(np.round(matrix[:, 2], 4)) + tuple(np.round(matrix[:, 0], 4))
        if key in seen:
            return
        seen.add(key)
        dims = axis_bounds(vertices, matrix)
        volume = float(np.prod(dims))
        if volume < best[0]:
            best = (volume, dims, matrix)

    consider(np.eye(3))
    centered = vertices - np.mean(vertices, axis=0)
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    consider(vh.T)
    for triangle in hull.simplices:
        a, b, c = p[triangle]
        normal = np.cross(b-a, c-a)
        norm = np.linalg.norm(normal)
        if norm < 1e-12:
            continue
        z = normal/norm
        for edge in (b-a, c-a, c-b):
            x = edge - z*np.dot(edge, z)
            length = np.linalg.norm(x)
            if length < 1e-12:
                continue
            x /= length
            consider(np.column_stack((x, np.cross(z, x), z)))

    def objective(angles):
        matrix = Rotation.from_euler('xyz', angles).as_matrix()
        return float(np.prod(axis_bounds(vertices, matrix)))

    starts = [Rotation.from_matrix(best[2]).as_euler('xyz'), np.zeros(3)]
    for start in starts:
        opt = minimize(objective, start, method='Nelder-Mead',
                       options={'maxiter': 700, 'xatol': 1e-9, 'fatol': 1e-12})
        consider(Rotation.from_euler('xyz', opt.x).as_matrix())
    return {'dimensions_m': best[1].tolist(), 'volume_m3': best[0],
            'axes_world_columns': best[2].tolist(),
            'method': 'convex-hull face/edge candidates plus local numerical refinement'}


def ratios(proxy_volume, hull_volume_m3, proxy_dims, real_dims):
    return {'proxy_volume_over_real_hull': float(proxy_volume/hull_volume_m3),
            'axis_dimension_ratios': (np.asarray(proxy_dims)/np.asarray(real_dims)).tolist()}


def box_vertices(box):
    from .reachability_map import rotate
    center = np.asarray(box['center'], float)
    half = np.asarray(box['half_size'], float)
    return np.array([center + rotate(box['quat_wxyz'], half*np.array([i, j, k]))
                     for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)])


def _pose(data, position_attr, rotation_attr, object_id):
    """Read a world pose from either robosuite MjData or native mujoco.MjData."""
    missing = [name for name in (position_attr, rotation_attr) if not hasattr(data, name)]
    if missing:
        raise TypeError(f'{type(data).__name__} lacks pose attributes {missing}; '
                        f'expected {position_attr}/{rotation_attr}')
    position = np.asarray(getattr(data, position_attr)[object_id], float)
    rotation = np.asarray(getattr(data, rotation_attr)[object_id], float).reshape(3, 3)
    return position, rotation


def body_pose(data, body_id):
    if hasattr(data, 'body_xpos'):
        return _pose(data, 'body_xpos', 'body_xmat', body_id)
    return _pose(data, 'xpos', 'xmat', body_id)


def geom_pose(data, geom_id):
    return _pose(data, 'geom_xpos', 'geom_xmat', geom_id)


def site_pose(data, site_id):
    return _pose(data, 'site_xpos', 'site_xmat', site_id)


def _check_attributes(label, obj, names):
    status = {name: hasattr(obj, name) for name in names}
    print(f'MuJoCo interface {label} ({type(obj).__name__}): '
          + ', '.join(f'{name}={"yes" if present else "MISSING"}'
                      for name, present in status.items()))
    missing = [name for name, present in status.items() if not present]
    if missing:
        raise TypeError(f'MuJoCo interface {label} ({type(obj).__name__}) '
                        f'missing attributes: {missing}')


def check_sim_interfaces(env):
    """Fail after reset if a wrapper/native MuJoCo API changed."""
    import mujoco
    _check_attributes('robosuite sim', env.sim, ('model', 'data'))
    model, data = env.sim.model, env.sim.data
    _check_attributes('robosuite model', model,
                      ('_model', 'body_names', 'joint_names', 'site_names',
                       'body_name2id', 'body_id2name', 'geom_id2name',
                       'get_joint_qpos_addr', 'ngeom', 'geom_type', 'geom_size',
                       'jnt_type', 'jnt_qposadr',
                       'geom_dataid', 'geom_contype', 'geom_conaffinity',
                       'geom_bodyid', 'body_parentid', 'mesh_vertadr',
                       'mesh_vertnum', 'mesh_vert'))
    _check_attributes('robosuite data', data,
                      ('_data', 'body_xpos', 'body_xmat', 'geom_xpos',
                       'geom_xmat', 'site_xpos', 'site_xmat', 'qpos'))
    native_model, native_data = model._model, data._data
    if not isinstance(native_model, mujoco.MjModel):
        raise TypeError(f'robosuite model._model must be mujoco.MjModel, got {type(native_model).__name__}')
    if not isinstance(native_data, mujoco.MjData):
        raise TypeError(f'robosuite data._data must be mujoco.MjData, got {type(native_data).__name__}')
    _check_attributes('native model', native_model, ('nbody', 'ngeom', 'nsite', 'nq'))
    native_fields = ('xpos', 'xmat', 'geom_xpos', 'geom_xmat',
                     'site_xpos', 'site_xmat', 'qpos', 'qvel',
                     'act', 'mocap_pos', 'mocap_quat')
    _check_attributes('native data', native_data, native_fields)
    _check_attributes('copied native data', copy.copy(native_data), native_fields)


@contextmanager
def writable_mjcf_assets(out):
    """Give RoboCasa's temporary object XML a writable sibling of symlinked assets."""
    from robocasa.utils import env_utils
    original = env_utils.MJCFObject
    from robocasa.models.objects import objects
    asset_root = (Path(objects.__file__).resolve().parents[1]/'assets'/'objects').resolve()
    shadow_root = out.resolve()/'mjcf-shadow'

    def from_shadow(*args, **kwargs):
        source = Path(kwargs['mjcf_path']).resolve(strict=True)
        if not source.is_relative_to(asset_root):
            raise ValueError(f'MJCF object XML is outside verified object assets: {source}')
        shadow = shadow_root/hashlib.sha256(str(source.parent).encode()).hexdigest()[:16]
        shadow.mkdir(parents=True, exist_ok=True)
        for child in source.parent.iterdir():
            link = shadow/child.name
            if link.is_symlink():
                if link.resolve() != child.resolve():
                    raise ValueError(f'MJCF shadow asset points to a different source: {link}')
            elif link.exists():
                raise ValueError(f'MJCF shadow asset is not a source symlink: {link}')
            else:
                link.symlink_to(child, target_is_directory=child.is_dir())
        kwargs['mjcf_path'] = str(shadow/source.name)
        return original(*args, **kwargs)

    env_utils.MJCFObject = from_shadow
    try:
        yield
    finally:
        env_utils.MJCFObject = original


def geom_vertices(model, data, gid):
    """World-space convex geometry points. Curved primitives use a documented sample."""
    import mujoco
    kind = int(model.geom_type[gid])
    size = np.asarray(model.geom_size[gid], float)
    mesh = int(mujoco.mjtGeom.mjGEOM_MESH)
    box = int(mujoco.mjtGeom.mjGEOM_BOX)
    if kind == mesh:
        mid = int(model.geom_dataid[gid])
        start, count = int(model.mesh_vertadr[mid]), int(model.mesh_vertnum[mid])
        local = np.asarray(model.mesh_vert[start:start+count], float)
    elif kind == box:
        local = np.array([[i*size[0], j*size[1], k*size[2]]
                          for i in (-1, 1) for j in (-1, 1) for k in (-1, 1)])
    else:
        # Surface sampling makes overall convex hull conservative for spheres,
        # ellipsoids, cylinders and capsules; exact analytic volume is separate.
        directions = np.array([[math.cos(a)*math.sin(b), math.sin(a)*math.sin(b), math.cos(b)]
                               for b in np.linspace(0, math.pi, 17)
                               for a in np.linspace(0, 2*math.pi, 33, endpoint=False)])
        if kind == int(mujoco.mjtGeom.mjGEOM_SPHERE):
            local = directions*size[0]
        elif kind == int(mujoco.mjtGeom.mjGEOM_ELLIPSOID):
            local = directions*size[:3]
        elif kind == int(mujoco.mjtGeom.mjGEOM_CYLINDER):
            ring = np.array([[size[0]*math.cos(a), size[0]*math.sin(a), z*size[1]]
                             for z in (-1, 1) for a in np.linspace(0, 2*math.pi, 64, endpoint=False)])
            local = ring
        elif kind == int(mujoco.mjtGeom.mjGEOM_CAPSULE):
            local = directions*size[0]
            local[:, 2] += np.sign(directions[:, 2])*size[1]
        else:
            raise ValueError(f'Unsupported target/finger collision geom type {kind} at geom {gid}')
    position, rotation = geom_pose(data, gid)
    return local @ rotation.T + position


def geom_ids_under(model, root):
    from openarm365_dev.world import is_descendant
    return [gid for gid in range(model.ngeom)
            if (model.geom_contype[gid] or model.geom_conaffinity[gid])
            and is_descendant(model, int(model.geom_bodyid[gid]), root)]


def geom_record(model, data, gid):
    import mujoco
    points = geom_vertices(model, data, gid)
    position, rotation = geom_pose(data, gid)
    kind = int(model.geom_type[gid])
    record = {'geom_id': gid, 'name': model.geom_id2name(gid),
              'type': mujoco.mjtGeom(kind).name.removeprefix('mjGEOM_').lower(),
              'size_m': np.asarray(model.geom_size[gid], float).tolist(),
              'world_position_m': position.tolist(),
              'world_rotation_matrix': rotation.tolist()}
    if kind == int(mujoco.mjtGeom.mjGEOM_MESH):
        record['convex_hull_volume_m3'] = hull_volume(points)
        record['axis_aligned_bbox_volume_m3'] = float(np.prod(axis_bounds(points)))
        record['oriented_bbox'] = oriented_bounds(points)
    return record, points


def _unique_native_name(names, suffix, kind):
    """Resolve a physical-left gripper part in the native-right MuJoCo namespace."""
    available = [str(name) for name in names]
    matches = [name for name in available
               if name.startswith('gripper0_right_') and name.endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f'Expected one physical-left {kind} ending {suffix!r} under '
                         f'gripper0_right_, found {matches!r}; available {kind} names: {available!r}')
    return matches[0]


def physical_left_body_names(model):
    names = model.body_names
    return (_unique_native_name(names, 'openarm_left_right_finger', 'body'),
            _unique_native_name(names, 'openarm_left_left_finger', 'body'),
            _unique_native_name(names, 'openarm_left_hand', 'body'))


def physical_left_finger_joint_addresses(model):
    return [model.get_joint_qpos_addr(_unique_native_name(
                model.joint_names, f'openarm_left_finger_joint{i}', 'joint'))
            for i in (1, 2)]


def physical_left_tcp_site_id(env):
    from openarm365_dev_v2.environment import PHYSICAL_TO_NATIVE
    model = env.sim.model
    sid = int(env.robots[0].eef_site_id[PHYSICAL_TO_NATIVE['left']])
    available = [str(name) for name in model.site_names]
    if not 0 <= sid < len(available) or not available[sid].startswith('gripper0_right_'):
        raise ValueError(f'Physical-left TCP site id {sid} does not belong to native-right '
                         f'gripper; available site names: {available!r}')
    return sid


def kinematic_data_at(env, finger_value):
    """Copy reset state; optionally set both finger slides; forward only, no mj_step."""
    import mujoco
    model = env.sim.model
    native_model = model._model
    data = copy.copy(env.sim.data._data)
    indices = physical_left_finger_joint_addresses(model)
    if finger_value is not None:
        for index in indices:
            data.qpos[index] = finger_value
    mujoco.mj_forward(native_model, data)
    return data, indices


def finger_measure(env, value):
    model = env.sim.model
    data, indices = kinematic_data_at(env, value)
    names = physical_left_body_names(model)[:2]
    bodies = [model.body_name2id(n) for n in names]
    centers = [body_pose(data, bid)[0] for bid in bodies]
    axis = centers[1]-centers[0]
    axis /= np.linalg.norm(axis)
    pads = []
    pad_lengths = []
    full_lengths = []
    finger_axes = []
    for bid in bodies:
        ids = geom_ids_under(model, bid)
        # Finger body has its own single collision mesh; descendant fixtures are excluded.
        ids = [gid for gid in ids if int(model.geom_bodyid[gid]) == bid]
        if not ids:
            raise ValueError(f'No collision pad for finger body {model.body_id2name(bid)}')
        all_points = np.concatenate([geom_vertices(model, data, gid) for gid in ids])
        position, rotation = body_pose(data, bid)
        local = (all_points-position) @ rotation
        threshold = float(np.min(local[:, 2])+.67*np.ptp(local[:, 2]))
        distal = all_points[local[:, 2] >= threshold]
        if len(distal) < 4:
            raise ValueError('Insufficient distal finger-pad geometry points')
        pads.append(distal)
        pad_lengths.append(projection_width(distal, rotation[:, 2]))
        full_lengths.append(projection_width(all_points, rotation[:, 2]))
        finger_axes.append(rotation[:, 2])
    left_max = float(np.max(pads[0] @ axis))
    right_min = float(np.min(pads[1] @ axis))
    gap = right_min-left_max
    sid = physical_left_tcp_site_id(env)
    tcp = np.eye(4)
    tcp[:3, 3], tcp[:3, :3] = site_pose(data, sid)
    tcp_axis = tcp[:3, :3].T @ axis
    finger_axes_tcp = [(tcp[:3, :3].T @ direction).tolist() for direction in finger_axes]
    palm = body_pose(data, model.body_name2id(physical_left_body_names(model)[2]))[0]
    roots = [body_pose(data, bid)[0] for bid in bodies]
    qpos = [float(data.qpos[i]) for i in indices]
    return {'joint_command_m_each': value, 'joint_qpos_m_each': qpos,
            'total_channel_opening_m': sum(qpos), 'pad_gap_m': gap,
            'pad_thickness_m': [projection_width(p, axis) for p in pads],
            'pad_contact_region_length_along_finger_m': pad_lengths,
            'full_collision_finger_length_m': full_lengths,
            'finger_length_axes_tcp': finger_axes_tcp,
            'grip_axis_world': axis.tolist(), 'grip_axis_tcp': tcp_axis.tolist(),
            'tcp_world_position_m': tcp[:3, 3].tolist(),
            'tcp_world_rotation_matrix': tcp[:3, :3].tolist(),
            'palm_to_finger_root_m': float(np.linalg.norm(np.mean(roots, axis=0)-palm)),
            'palm_to_finger_root_along_tcp_z_m': float(np.dot(np.mean(roots, axis=0)-palm, tcp[:3, 2])),
            'pad_inner_limits_world_m': [left_max, right_min],
            'pad_region': 'distal third of collision-finger vertices along local +Z'}, data


def target_from_snapshot(env, world):
    """Use task graspability and the snapshot inventory to identify target shapes."""
    from openarm365_dev.world import is_descendant
    cfgs = [cfg for cfg in env.object_cfgs
            if cfg.get('graspable') is True and cfg.get('name') in env.obj_body_id]
    if len(cfgs) != 1:
        raise ValueError(f'Expected one graspable task target in object_cfgs, found {[c.get("name") for c in cfgs]}')
    cfg = cfgs[0]
    target = cfg['name']
    root = env.obj_body_id[target]
    model = env.sim.model
    inventory = [row for row in world['inventory']
                 if is_descendant(model, int(row['body_id']), root)]
    if not inventory:
        raise ValueError(f'No snapshot collision shapes for target {target!r}')
    names = {row['name'] for row in inventory}
    proxies = [box for box in world['cuboids'] if box['name'] in names]
    geom_ids = geom_ids_under(model, root)
    if len(proxies) != len(inventory) or {int(row['geom_id']) for row in inventory} != set(geom_ids):
        raise ValueError(f'Snapshot target shape mismatch for {target!r}')
    return cfg, root, geom_ids, proxies


def reset_target_geometry(env, world):
    """Read target truth at reset; this is diagnostic-only, not public SDK data."""
    cfg, root, geom_ids, proxies = target_from_snapshot(env, world)
    points = np.concatenate([geom_vertices(env.sim.model, env.sim.data, gid) for gid in geom_ids])
    pose_position, pose_rotation = body_pose(env.sim.data, root)
    center = points.mean(axis=0)
    z_range = [float(points[:, 2].min()), float(points[:, 2].max())]
    widths = [{'angle_world_xy_deg': angle,
               'object_width_m': projection_width(points, [math.cos(math.radians(angle)),
                                                              math.sin(math.radians(angle)), 0.])}
              for angle in range(0, 180, 5)]
    narrow = min(widths, key=lambda row: row['object_width_m'])
    opened, _ = finger_measure(env, .043)
    grip_axis_tcp = opened['grip_axis_tcp']
    if abs(grip_axis_tcp[1]) < .99 or abs(grip_axis_tcp[0]) > .05 or abs(grip_axis_tcp[2]) > .05:
        raise ValueError(f'Reset TCP grip axis is not local y: {grip_axis_tcp}')
    layered = layered_horizontal_widths(points,
        min(opened['pad_contact_region_length_along_finger_m']), .080, .0875)
    return {'source': '使用仿真真值,仅诊断', 'name': cfg['name'],
            'position_world_m': pose_position.tolist(),
            'quaternion_world_xyzw': Rotation.from_matrix(pose_rotation).as_quat().tolist(),
            'geometry_center_world_m': center.tolist(), 'z_range_world_m': z_range,
            'narrowest_5_degree_sample': narrow,
            'layered_horizontal_5mm_5deg': layered,
            'measured_grip_axis_tcp': grip_axis_tcp,
            'proxy_names': [box['name'] for box in proxies]}


def reset_diagnostic_fixture(seed, out, robot_config, *, task='PickPlaceCounterToSink'):
    """Construct the real seed world once and read it without a physics step."""
    import yaml
    from openarm365_dev.identity import OfficialEnvironmentIdentity
    from openarm365_dev_v2.environment import create_env
    from openarm365_dev.world import snapshot
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for variable, directory in (('XDG_CACHE_HOME', 'cache'), ('NUMBA_CACHE_DIR', 'numba-cache')):
        cache = (out/directory).resolve(); cache.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(cache)
    identity = OfficialEnvironmentIdentity({
        'robocasa': '/home/truares/Documents/Codex/2026-10-09/new-chat/work/upstream/robocasa',
        'robosuite': '/home/truares/Documents/Codex/2026-10-09/new-chat/work/upstream/robosuite'},
        '/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365/assets-manifest.json').verify_identity()
    env = create_env(task, robots='OpenArm365', seed=seed,
                     obj_instance_split='pretrain', layout_and_style_ids=[(11, 34)],
                     camera_widths=128, camera_heights=128, randomize_cameras=False,
                     generative_textures=None, control_freq=20, horizon=2400, scene_reset_seed=seed)
    try:
        with writable_mjcf_assets(out):
            env.reset()
        config = yaml.safe_load(Path(robot_config).read_text())
        world = snapshot(env, chassis_binding=config['service']['native_chassis'])
        truth = reset_target_geometry(env, world)
        from openarm365_dev_v2.environment import mount_pose
        transform = np.asarray(world['planner_from_world'])
        truth['position_base_m'] = (transform @ np.r_[truth['position_world_m'], 1.])[:3].tolist()
        truth['geometry_center_base_m'] = (transform @ np.r_[truth['geometry_center_world_m'], 1.])[:3].tolist()
        names = [f'openarm_{side}_joint{i}' for side in ('left', 'right') for i in range(1, 8)]
        model, data = env.sim.model, env.sim.data
        def joint(name):
            matched = [native for native in model.joint_names if native.endswith(name)]
            if len(matched) != 1:
                raise ValueError(f'Ambiguous or missing native joint: {name}')
            return float(data.qpos[model.get_joint_qpos_addr(matched[0])])
        lock_names = [f'openarm_{side}_finger_joint1' for side in ('left', 'right')]
        lock_names += [f'openarm_right_joint{i}' for i in range(1, 8)]
        return {'seed': seed, 'diagnostic_truth': truth, 'identity': identity,
                'world': world, 'joint_names': names, 'q0': [joint(n) for n in names],
                'locked_joints': {n: joint(n) for n in lock_names},
                'mount_pose_world': mount_pose(env).tolist(), 'physics_steps': 0}
    finally:
        env.close()


def measure(env, robot_config, assets_dir, *, task='PickPlaceCounterToSink', seed=101):
    import yaml
    from openarm365_dev.world import snapshot
    from openarm365_dev_v2.environment import PHYSICAL_TO_NATIVE
    from openarm365.environment import validate_physical_model
    validate_physical_model(env)
    config = yaml.safe_load(robot_config.read_text())
    binding = config['service']['native_chassis']
    world = snapshot(env, chassis_binding=binding)
    model, data = env.sim.model, env.sim.data
    target_cfg, root, geom_ids, proxies = target_from_snapshot(env, world)
    target = target_cfg['name']
    records, chunks = zip(*(geom_record(model, data, gid) for gid in geom_ids))
    points = np.concatenate(chunks)
    real_hull = hull_volume(points)
    real_obb = oriented_bounds(points)
    real_aabb = axis_bounds(points)
    proxy_records = []
    corners = []
    for box in proxies:
        half = np.asarray(box['half_size'], float)
        dims = 2*half
        proxy_records.append({'name': box['name'], 'center_base_m': box['center'],
                              'quaternion_base_wxyz': box['quat_wxyz'],
                              'dimensions_m': dims.tolist(), 'volume_m3': float(np.prod(dims))})
        corners.append(box_vertices(box))
    proxy_pts_base = np.concatenate(corners)
    proxy_dims_base = axis_bounds(proxy_pts_base)
    transform = np.asarray(world['planner_from_world'], float)
    proxy_pts_world = proxy_pts_base @ transform[:3, :3] + (-transform[:3, 3] @ transform[:3, :3])
    proxy_dims_world = axis_bounds(proxy_pts_world)
    inverse_rotation = transform[:3, :3].T
    for box, record in zip(proxies, proxy_records):
        center_world = inverse_rotation @ (np.asarray(box['center'])-transform[:3, 3])
        q = np.asarray(box['quat_wxyz'], float)
        box_rotation = Rotation.from_quat([q[1], q[2], q[3], q[0]]).as_matrix()
        rotation_world = inverse_rotation @ box_rotation
        record['center_world_m'] = center_world.tolist()
        record['rotation_world_matrix'] = rotation_world.tolist()
    total_volume = sum(p['volume_m3'] for p in proxy_records)
    reset_open, _ = finger_measure(env, None)
    opening_values = [.005, .01, .015, .02, .025, .03, .035, .04, .043]
    opening_curve = []
    open_data = None
    for value in opening_values:
        reading, pose_data = finger_measure(env, value)
        opening_curve.append({'joint_command_m_each': value, 'pad_gap_m': reading['pad_gap_m']})
        if value == .043:
            opened, open_data = reading, pose_data
    closed, _ = finger_measure(env, .001)
    directions = []
    for degrees in range(0, 360, 15):
        direction = [math.cos(math.radians(degrees)), math.sin(math.radians(degrees)), 0.]
        width = projection_width(points, direction)
        directions.append({'angle_world_xy_deg': degrees, 'axis_world': direction,
                           'object_width_m': width, 'physical_clearance_m': opened['pad_gap_m']-width})
    narrowest = min(directions, key=lambda row: row['object_width_m'])
    horizontal_5 = [{'angle_world_xy_deg': degrees,
                     'object_width_m': projection_width(points,
                         [math.cos(math.radians(degrees)), math.sin(math.radians(degrees)), 0.])}
                    for degrees in range(0, 180, 5)]
    narrowest_5 = min(horizontal_5, key=lambda row: row['object_width_m'])
    tcp_axis = np.asarray(opened['grip_axis_world'])
    actual_width = projection_width(points, tcp_axis)
    # The reachability scan's forward_down_45 poses are defined in robot-base frame.
    # Compute tool-X/Y grip-axis directions for each roll after mount rotation.
    from .reachability_map import orientation_for_axis, rotate
    from openarm365_dev_v2.environment import mount_pose
    mount_rotation = mount_pose(env)[:3, :3]
    forward_cases = []
    tcp_local = np.asarray(opened['grip_axis_tcp'])
    for roll in (0, 180):
        q = orientation_for_axis((1., 0., -1.), roll)
        grip_base = np.asarray(rotate(q, tcp_local))
        grip_world = mount_rotation @ grip_base
        width = projection_width(points, grip_world)
        forward_cases.append({'roll_deg': roll, 'grip_axis_world': grip_world.tolist(),
                              'object_width_m': width, 'physical_clearance_m': opened['pad_gap_m']-width,
                              'source': 'forward_down_45 target TCP orientation, not a solved IK pose'})
    kin = config['robot_cfg']['kinematics']
    sphere_map = kin['collision_spheres']
    buffer_m = float(kin.get('collision_sphere_buffer', 0.))
    sphere_records = []
    tip_limits = {}
    for body_name, link_name in zip(physical_left_body_names(model)[:2],
                                    ('openarm_left_right_finger', 'openarm_left_left_finger')):
        bid = model.body_name2id(body_name)
        translation, rotation = body_pose(open_data, bid)
        ids = [gid for gid in geom_ids_under(model, bid) if int(model.geom_bodyid[gid]) == bid]
        pad_points = np.concatenate([geom_vertices(model, open_data, gid) for gid in ids])
        local_pad = (pad_points-translation) @ rotation
        zmin, zmax = float(np.min(local_pad[:, 2])), float(np.max(local_pad[:, 2]))
        tip_limits[link_name] = zmin + (zmax-zmin)*.67
        for sphere in sphere_map.get(link_name, []):
            local = np.asarray(sphere['center'], float)
            center = rotation @ local + translation
            sphere_records.append({'link': link_name, 'center_link_m': local.tolist(),
                                   'center_world_m': center.tolist(),
                                   'radius_m': float(sphere['radius']),
                                   'radius_with_buffer_m': float(sphere['radius'])+buffer_m,
                                   'near_tip': bool(local[2] >= tip_limits[link_name])})
    if not sphere_records:
        raise ValueError('Robot configuration has no left finger collision spheres')
    # Inner envelope of finger spheres at the opened pose.
    left = [s for s in sphere_records if s['link'] == 'openarm_left_right_finger' and s['near_tip']]
    right = [s for s in sphere_records if s['link'] == 'openarm_left_left_finger' and s['near_tip']]
    if not left or not right:
        raise ValueError('No collision spheres found in distal third of each finger pad')
    left_bound = max(np.dot(s['center_world_m'], tcp_axis)+s['radius_with_buffer_m'] for s in left)
    right_bound = min(np.dot(s['center_world_m'], tcp_axis)-s['radius_with_buffer_m'] for s in right)
    effective_gap = float(right_bound-left_bound)
    contact_length = min(opened['pad_contact_region_length_along_finger_m'])
    # Compare against the requested conservative round-number limits. The
    # measured 0.043 m kinematic gap and sphere envelope remain separate.
    layered = layered_horizontal_widths(points, contact_length, .080, .0875)
    return {'task': task, 'seed': seed, 'target_object': target,
            'target_category': target_cfg.get('info', {}).get('cat'),
            'world_sha256': world['world_sha256'],
            'real_object': {'collision_geoms': list(records), 'convex_hull_volume_m3': real_hull,
                            'axis_aligned_bbox_dimensions_world_m': real_aabb.tolist(),
                            'oriented_bbox': real_obb,
                            'curved_geom_sampling': '17 polar x 33 azimuth points; overall hull is a sampled approximation if curved geoms exist'},
            'planner_proxy': {'boxes': proxy_records, 'box_count': len(proxies),
                              'sum_box_volumes_m3': total_volume,
                              'union_bbox_dimensions_base_m': proxy_dims_base.tolist(),
                              'union_bbox_dimensions_world_m': proxy_dims_world.tolist(),
                              'union_bbox_volume_world_m3': float(np.prod(proxy_dims_world)),
                              'ratios_world_aabb': ratios(total_volume, real_hull, proxy_dims_world, real_aabb)},
            'left_gripper': {'physical_side_to_native': PHYSICAL_TO_NATIVE['left'],
                             'reset_measured': reset_open,
                             'open_0p043_kinematic': opened, 'closed_0p001_kinematic': closed,
                             'opening_curve': opening_curve,
                             'reset_joint_qpos_m': np.asarray(env.sim.data.qpos[env.robots[0]._ref_gripper_joint_pos_indexes[PHYSICAL_TO_NATIVE['left']]]).tolist(),
                             'method': 'Reset geometry measured directly; 0.043/0.001 copies set both independent finger slides; mj_forward only; no mj_step'},
            'grasp_widths': {'horizontal_15_degree_samples': directions,
                             'narrowest_sample': narrowest,
                             'horizontal_5_degree_samples': horizontal_5,
                             'narrowest_5_degree_sample': narrowest_5,
                             'layered_horizontal_5mm_5deg': layered,
                             'reset_tcp_axis': {'axis_world': tcp_axis.tolist(), 'object_width_m': actual_width,
                                                'physical_clearance_m': opened['pad_gap_m']-actual_width},
                             'forward_down_45': forward_cases},
            'left_finger_spheres': {'robot_config': str(robot_config), 'assets_dir': str(assets_dir),
                                    'collision_sphere_buffer_m': buffer_m, 'spheres': sphere_records,
                                    'sphere_envelope_gap_m': effective_gap,
                                    'opening_lost_to_spheres_m': opened['pad_gap_m']-effective_gap,
                                    'effective_gap_m': effective_gap,
                                    'clearance_for_narrowest_sample_m': effective_gap-narrowest['object_width_m'],
                                    'clearance_for_forward_down_45_m': [effective_gap-r['object_width_m'] for r in forward_cases],
                                    'method': 'Inner envelope of distal-third left finger spheres at 0.043 m, including collision_sphere_buffer; axial alignment is approximate'}}


def render_markdown(result):
    real, proxy = result['real_object'], result['planner_proxy']
    grip, widths, spheres = result['left_gripper'], result['grasp_widths'], result['left_finger_spheres']
    layered = widths['layered_horizontal_5mm_5deg']
    lines = [f"# Seed {result['seed']} {result['task']} 目标碰撞几何与左手爪只读测量", '',
             f"目标物体：{result['target_object']}；类别：{result['target_category']}；代理盒：{proxy['box_count']} 个。",
             f"真实几何整体凸包体积：{real['convex_hull_volume_m3']:.8g} m³；数值最小定向盒三轴：{real['oriented_bbox']['dimensions_m']} m。",
             f"代理盒体积总和：{proxy['sum_box_volumes_m3']:.8g} m³；代理世界轴包围尺寸：{proxy['union_bbox_dimensions_world_m']} m。",
             f"代理体积/真实凸包体积：{proxy['ratios_world_aabb']['proxy_volume_over_real_hull']:.4g}；世界 XYZ 包围尺寸比：{proxy['ratios_world_aabb']['axis_dimension_ratios']}。",
             '', '## 真实碰撞 geom', '',
             '| 名称 | 类型 | MuJoCo 尺寸 m | 世界中心 m | mesh 凸包 m³ | mesh AABB m³ | mesh 定向盒 m³ |',
             '| --- | --- | --- | --- | ---: | ---: | ---: |']
    for geom in real['collision_geoms']:
        lines.append(f"| {geom['name']} | {geom['type']} | {geom['size_m']} | {geom['world_position_m']} | "
                     f"{geom.get('convex_hull_volume_m3', float('nan')):.8g} | "
                     f"{geom.get('axis_aligned_bbox_volume_m3', float('nan')):.8g} | "
                     f"{geom.get('oriented_bbox', {}).get('volume_m3', float('nan')):.8g} |")
    lines += ['', '## 规划器代理盒', '',
              '| 名称 | 世界中心 m | 三轴尺寸 m | 体积 m³ |', '| --- | --- | --- | ---: |']
    for box in proxy['boxes']:
        lines.append(f"| {box['name']} | {box['center_world_m']} | {box['dimensions_m']} | {box['volume_m3']:.8g} |")
    lines += [
             '', '## 左手爪', '',
             f"reset 实测两指关节位置 {grip['reset_measured']['joint_qpos_m_each']} m，指垫间距 {grip['reset_measured']['pad_gap_m']:.5g} m。",
             f"两个独立指通道各置 0.043 m 时，总通道开量 0.086 m，指垫运动学间距 {grip['open_0p043_kinematic']['pad_gap_m']:.5g} m；各置 0.001 m 时闭合间距 {grip['closed_0p001_kinematic']['pad_gap_m']:.5g} m。",
             f"指垫厚度：{grip['open_0p043_kinematic']['pad_thickness_m']} m；TCP 系夹持轴：{grip['open_0p043_kinematic']['grip_axis_tcp']}；手掌至指根：{grip['open_0p043_kinematic']['palm_to_finger_root_m']:.5g} m。",
             f"指垫远端接触区域沿手指长度：{grip['open_0p043_kinematic']['pad_contact_region_length_along_finger_m']} m；完整碰撞指 geom 长度：{grip['open_0p043_kinematic']['full_collision_finger_length_m']} m；手指长度方向在 TCP 系：{grip['open_0p043_kinematic']['finger_length_axes_tcp']}。",
             f"TCP 世界位置：{grip['open_0p043_kinematic']['tcp_world_position_m']} m；世界夹持轴：{grip['open_0p043_kinematic']['grip_axis_world']}。",
             f"球包络估计可用开口：{spheres['effective_gap_m']:.5g} m；相对实体开口损失 {spheres['opening_lost_to_spheres_m']:.5g} m；对 15° 采样最窄宽度余量 {spheres['clearance_for_narrowest_sample_m']:.5g} m。",
             '', '### 关节开度与运动学指垫间距', '',
             '| 每指关节 m | 指垫间距 mm |', '| ---: | ---: |']
    lines += [f"| {row['joint_command_m_each']:.3f} | {row['pad_gap_m']*1000:.2f} |"
              for row in grip['opening_curve']]
    lines += [
             '', '## 水平夹持宽度', '', '| 世界 XY 角度 | 目标宽度 m | 实体余量 m |', '| ---: | ---: | ---: |']
    lines += [f"| {r['angle_world_xy_deg']}° | {r['object_width_m']:.5g} | {r['physical_clearance_m']:.5g} |"
              for r in widths['horizontal_15_degree_samples']]
    narrow = widths['narrowest_sample']
    lines += ['', f"15° 采样最窄宽度 {narrow['object_width_m']:.5g} m，方向 {narrow['angle_world_xy_deg']}°。",
              f"5° 采样整体最窄水平宽度 {widths['narrowest_5_degree_sample']['object_width_m']*1000:.2f} mm，方向 {widths['narrowest_5_degree_sample']['angle_world_xy_deg']}°。",
              f"初始 TCP 夹持轴宽度 {widths['reset_tcp_axis']['object_width_m']:.5g} m，实体余量 {widths['reset_tcp_axis']['physical_clearance_m']:.5g} m。",
              'forward_down_45 的 0/180° 滚转按目标 TCP 姿态和当前夹持轴估计，未运行 IK：']
    lines += [f"- roll {r['roll_deg']}°：宽度 {r['object_width_m']:.5g} m，实体余量 {r['physical_clearance_m']:.5g} m。"
              for r in widths['forward_down_45']]
    lines += ['', '## 世界 z 分层局部水平宽度', '',
              f"层中心步长 {layered['step_m']*1000:g} mm；每层取中心 ±{layered['window_half_height_m']*1000:.2f} mm 的碰撞顶点（窗口全高取双指远端接触区域较短者）。每 5° 在世界 XY 算 max−min；180° 周期方向等价。",
              f"全局最窄局部宽度 {layered['narrowest_local']['width_m']*1000:.2f} mm，中心世界 z={layered['narrowest_local']['center_world_z_m']:.5f} m，方向 {layered['narrowest_local']['angle_world_xy_deg']}°，顶点 {layered['narrowest_local']['vertex_count']} 个。",
              f"手指轴竖直夹持时的物体世界 z 高度参考：{layered['object_vertical_height_m']*1000:.2f} mm。",
              '| 中心世界 z m | 顶点数 | 窗口完整落在物体 z 范围 | 最窄 mm | 方向 ° | ≤ 指垫开口的方向 ° | ≤ 球包络开口的方向 ° |',
              '| ---: | ---: | --- | ---: | ---: | --- | --- |']
    for row in layered['layers']:
        if row['vertex_count']:
            lines.append(f"| {row['center_world_z_m']:.5f} | {row['vertex_count']} | {'是' if row['window_fully_within_object_z_range'] else '否'} | {row['narrowest_width_m']*1000:.2f} | {row['narrowest_angle_world_xy_deg']} | {row['angles_within_pad_gap_deg'] or '无'} | {row['angles_within_sphere_gap_deg'] or '无'} |")
        else:
            lines.append(f"| {row['center_world_z_m']:.5f} | 0 | {'是' if row['window_fully_within_object_z_range'] else '否'} | 无数据 | — | 无 | 无 |")
    lines += [f"≤ {layered['pad_comparison_threshold_m']*1000:.2f} mm（保守取整的指垫开口）层：{layered['layers_within_pad_gap'] or '无'}。",
              f"≤ {layered['sphere_comparison_threshold_m']*1000:.2f} mm（保守取整的球包络开口）层：{layered['layers_within_sphere_gap'] or '无'}。",
              f"窗口完整落在物体 z 范围且 ≤ 指垫开口的层：{layered['interior_layers_within_pad_gap'] or '无'}。边缘层可能只覆盖部分物体高度，靠近台面时还需考虑指垫与台面碰撞。",
              '仅在窗口内的离散顶点上投影；稀疏顶点、窗口边界和未做接触/可达性验证均可能使窄值偏乐观。']
    lines += ['', '## 左指碰撞球', '',
              f"配置：{spheres['robot_config']}；sphere buffer {spheres['collision_sphere_buffer_m']:.5g} m。",
              '| 连杆 | 连杆系中心 m | 世界中心 m | 半径 m | 指尖附近 |',
              '| --- | --- | --- | ---: | --- |']
    for sphere in spheres['spheres']:
        lines.append(f"| {sphere['link']} | {sphere['center_link_m']} | {sphere['center_world_m']} | "
                     f"{sphere['radius_m']:.6g} | {'是' if sphere['near_tip'] else '否'} |")
    lines += ['', '各 geom 与代理盒的完整世界旋转矩阵、定向盒三轴和球包络计算数据见 results.json。',
              '体积总和会重复计入相交代理盒；定向盒为数值搜索结果。运动学测量只用 mj_forward，未步进物理。']
    return '\n'.join(lines)+'\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--seed', type=int, default=101, choices=(101, 102, 103))
    parser.add_argument('--task', default='PickPlaceCounterToSink')
    parser.add_argument('--out', type=Path, default=Path('object-proxy-measure-results'))
    parser.add_argument('--robot-config', type=Path, default=DEFAULT_ROBOT_CONFIG)
    parser.add_argument('--assets-dir', type=Path, default=DEFAULT_ASSETS)
    args = parser.parse_args(argv)
    if args.dry_run:
        print(json.dumps({'task': args.task, 'seed': args.seed,
                          'robot_config': str(args.robot_config), 'assets_dir': str(args.assets_dir),
                          'output': str(args.out), 'physics_steps': 0, 'policy_runs': 0,
                          'finger_kinematic_positions_m_each': [.001, .005, .01, .015, .02, .025, .03, .035, .04, .043]}, ensure_ascii=False))
        return
    args.out.mkdir(parents=True, exist_ok=True)
    for variable, directory in (('XDG_CACHE_HOME', 'cache'),
                                ('NUMBA_CACHE_DIR', 'numba-cache')):
        cache = (args.out/directory).resolve()
        cache.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(cache)
    from openarm365_dev.identity import OfficialEnvironmentIdentity
    from openarm365_dev_v2.environment import create_env
    identity = OfficialEnvironmentIdentity({
        'robocasa': '/home/truares/Documents/Codex/2026-10-09/new-chat/work/upstream/robocasa',
        'robosuite': '/home/truares/Documents/Codex/2026-10-09/new-chat/work/upstream/robosuite'},
        '/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365/assets-manifest.json').verify_identity()
    env = create_env(args.task, robots='OpenArm365', seed=args.seed,
                     obj_instance_split='pretrain', layout_and_style_ids=[(11, 34)],
                     camera_widths=128, camera_heights=128, randomize_cameras=False,
                     generative_textures=None, control_freq=20, horizon=2400, scene_reset_seed=args.seed)
    try:
        _check_attributes('environment', env, ('reset', 'close', 'sim', 'robots'))
        with writable_mjcf_assets(args.out):
            env.reset()
        check_sim_interfaces(env)
        result = measure(env, args.robot_config.resolve(), args.assets_dir.resolve(),
                         task=args.task, seed=args.seed)
        result['identity'] = identity
    finally:
        env.close()
    (args.out/'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    (args.out/'results.md').write_text(render_markdown(result))
    print(f"Wrote {args.out/'results.json'} and {args.out/'results.md'}")


if __name__ == '__main__':
    main()
