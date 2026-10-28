#!/usr/bin/env python3
"""Read-only cabinet scenes: one fresh process per seed, two canonical resets.

Simulator entry point for the operator, never invoked by offline unit tests.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TASK = 'pick_place_counter_to_cabinet'


def parse_seeds(text):
    values = []
    for item in text.split(','):
        ends = item.split('-')
        values.extend(range(int(ends[0]), int(ends[1])+1) if len(ends) == 2 else [int(item)])
    if not values or len(set(values)) != len(values) or any(s not in range(101, 126) for s in values):
        raise argparse.ArgumentTypeError('unique development seeds 101–125 required')
    return values


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                               allow_nan=False)+'\n')


def check_environment():
    forbidden = [k for k in os.environ if k.startswith('ATTN_DIAG_') or k in
                 ('ATTN_PLANNER_MODE', 'ATTN_IK_CANDIDATES', 'ATTN_IK_MAX_JOINT_JUMP')]
    if forbidden:
        raise ValueError('ordinary r4 settings required; unset '+', '.join(sorted(forbidden)))


def interior_regions(cab):
    """Use the same oriented p0/px/py/pz regions as obj_inside_of, not visual doors."""
    import numpy as np
    result = {}
    for name, sites in cab.get_int_sites(relative=False).items():
        p0, px, py, pz = np.asarray(sites, float)
        axes = [px-p0, py-p0, pz-p0]
        corners = np.array([p0+i*axes[0]+j*axes[1]+k*axes[2]
                            for i in (0, 1) for j in (0, 1) for k in (0, 1)])
        # Fixture local -Y is the cabinet front; py points into its depth.
        front = p0+axes[0]/2
        inward = axes[1]/np.linalg.norm(axes[1])
        result[str(name)] = dict(sites_world_m=np.asarray(sites).tolist(),
            aabb_world_m=[corners.min(axis=0).tolist(), corners.max(axis=0).tolist()],
            center_world_m=corners.mean(axis=0).tolist(), shelf_front_world_m=front.tolist(),
            inward_world=inward.tolist(), depth_m=float(np.linalg.norm(axes[1])))
    if not result:
        raise ValueError('cabinet has no native interior regions')
    return result


def geometry(env):
    import numpy as np
    from benchmarks.attention_harness.diagnostics.object_proxy_measure import geom_ids_under, geom_vertices
    from openarm365_dev_v2.environment import mount_pose
    from openarm365_dev_v2.worker import cabinet_public_values
    model, data = env.sim.model, env.sim.data
    obj = env.objects['obj']
    bid = env.obj_body_id['obj']
    gids = geom_ids_under(model, bid)
    if not gids:
        raise ValueError('target has no collision geometry')
    points = np.concatenate([geom_vertices(model, data, gid) for gid in gids])
    lo, hi = points.min(axis=0), points.max(axis=0)
    widths = [(float(np.ptp(points @ np.array([math.cos(a), math.sin(a), 0.]))), a)
              for a in np.linspace(0, math.pi, 180, endpoint=False)]
    width, angle = min(widths)
    cfg = next(c for c in env.object_cfgs if c['name'] == 'obj')
    regions = interior_regions(env.cab)
    mount = mount_pose(env)
    origin = mount[:3, 3]
    def distance(point):
        delta = np.asarray(point)-origin
        return dict(horizontal_m=float(np.linalg.norm(delta[:2])), height_difference_m=float(delta[2]))
    ext = np.asarray(env.cab.get_ext_sites(all_points=True, relative=False), float)
    bottom = float(ext[:, 2].min())
    cabinet_type = 'upper/wall' if bottom > .5 else 'lower/base'
    public = {n: data.body_xpos[b].tolist() for n, b in env.obj_body_id.items()}
    public.update({k[:-4]: v for k, v in cabinet_public_values(env).items()})
    return dict(target=dict(public_name='obj', asset_name=obj.name,
                category=cfg.get('info', {}).get('cat'), language=env.get_obj_lang(),
                position_world_m=data.body_xpos[bid].tolist(),
                aabb_world_m=[lo.tolist(), hi.tolist()], aabb_size_m=(hi-lo).tolist(),
                narrowest_graspable_width_m=width, grasp_axis_world_xy_rad=angle,
                fits_80_5mm=bool(width <= .0805), gripper_opening_m=.0805,
                width_method='whole collision geometry horizontal projection; 1 degree samples; geometric fit only, not grasp feasibility'),
        cabinet=dict(public_name='cabinet', fixture_name=env.cab.name,
            fixture_class=type(env.cab).__name__, type=cabinet_type,
            type_source='inferred from fixture exterior bottom > 0.5 m (upper/wall); inspect fixture name/class',
            exterior_bottom_world_m=bottom, position_world_m=np.asarray(env.cab.pos).tolist(),
            interior_source='fixture.get_int_sites(relative=False), same regions as obj_inside_of',
            native_check='obj_inside_of(partial_check=False, th=0.05)',
            interior_regions=regions,
            door_state={k: float(v) for k, v in env.cab.get_door_state(env=env).items()}),
        distractors={n: data.body_xpos[b].tolist() for n, b in env.obj_body_id.items() if n != 'obj'},
        mount_pose_world=mount.tolist(), mount_to_object=distance(data.body_xpos[bid]),
        mount_to_cabinet_interiors={n: distance(r['center_world_m']) for n, r in regions.items()},
        public_objects=public, public_names=sorted(public))


def probe_once(seed, out, planner_source=None):
    check_environment()
    from benchmarks.attention_harness.dev_tasks import validate_selection
    validate_selection(TASK, seed)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    for key, folder in [('XDG_CACHE_HOME', 'cache'), ('NUMBA_CACHE_DIR', 'numba-cache')]:
        os.environ[key] = str(out/folder)
    from openarm365_dev_v2 import dev_config
    from openarm365_dev_v2.environment import create_env, PHYSICAL_TO_NATIVE
    from openarm365_dev_v2.scene_contract import capture_state, fingerprint
    from openarm365.environment import validate_physical_model, configure_display_camera
    from openarm365_dev.identity import OfficialEnvironmentIdentity
    from benchmarks.attention_harness.diagnostics.object_proxy_measure import writable_mjcf_assets
    source = dict(kind='dev-local', source_root=str(Path(planner_source or ROOT).resolve()))
    settings = dev_config.new_execution_settings(planner_source=source)
    lock = dev_config.make_lock(out/'identity.json', execution_settings=settings, planner_source=source)
    config = dev_config.make_dev_config(TASK, seed, lock, execution_settings=settings, planner_source=source)
    config['scene_reset_seed'] = seed
    dev_config.validate_dev_config(config)
    write_json(out/'config.json', config)
    identity = OfficialEnvironmentIdentity({'robocasa': dev_config.ROBocasa,
        'robosuite': dev_config.ROBOSUITE}, dev_config.ASSETS).verify_identity()
    env = None
    try:
        # Canonical rng_epoch + counter-region sorting is provided by create_env.
        with writable_mjcf_assets(out):
            env = create_env(config['official_env'], robots='OpenArm365', seed=seed,
                obj_instance_split=config['obj_instance_split'], layout_and_style_ids=[(11, 34)],
                camera_widths=128, camera_heights=128, randomize_cameras=False,
                generative_textures=None, control_freq=config['control_freq'], horizon=config['horizon'],
                scene_reset_seed=seed)
            rows = []
            for index in range(2):
                env.reset()
                validate_physical_model(env)
                configure_display_camera(env)
                state = capture_state(env)
                gains = {side: env.robots[0].composite_controller.part_controllers[native].kp.tolist()
                         for side, native in PHYSICAL_TO_NATIVE.items()}
                if any(not v or any(k != 1500 for k in v) for v in gains.values()):
                    raise ValueError('expected default kp=1500 on both arms')
                rows.append(dict(reset_index=index, state=state, kp=gains,
                    normalized_sha256=fingerprint(state, True), raw_sha256=fingerprint(state),
                    scene=geometry(env)))
        result = dict(schema='attentionbench.m11-cabinet-probe.v1', task_id=TASK, seed=seed,
            scene_reset_seed=seed, layout_style=[11, 34], pid=os.getpid(),
            execution_settings=settings.to_dict(), identity=identity, resets=rows,
            same_normalized_public_state=rows[0]['normalized_sha256']==rows[1]['normalized_sha256'],
            motion_calls=0, planner_calls=0, diagnostic_only=True)
        write_json(out/'probe.json', result)
        return result
    finally:
        if env is not None:
            env.close()


def run_process(seed, folder, planner_source=None):
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--seed', str(seed), '--out', str(folder)]
    if planner_source:
        command += ['--planner-source', str(planner_source)]
    folder.parent.mkdir(parents=True, exist_ok=True)
    if folder.exists():
        raise ValueError('refusing to overwrite '+str(folder))
    with folder.with_suffix('.log').open('x') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, 'PYTHONHASHSEED': '0', 'MUJOCO_GL': 'egl', 'PYTHONDONTWRITEBYTECODE': '1'})
        try:
            code = process.wait(timeout=900)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            raise RuntimeError('probe timed out: '+str(folder)) from None
    if code:
        raise RuntimeError('probe failed; see '+str(folder.with_suffix('.log')))
    value = json.loads((folder/'probe.json').read_text())
    if value['pid'] != process.pid:
        raise ValueError('probe PID mismatch')
    write_json(folder/'receipt.json', dict(pid=process.pid, returncode=code,
        probe_sha256=hashlib.sha256((folder/'probe.json').read_bytes()).hexdigest()))
    return value


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--seeds', type=parse_seeds)
    group.add_argument('--seed', type=int, choices=range(101, 126), help=argparse.SUPPRESS)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--planner-source', type=Path)
    args = p.parse_args(argv)
    check_environment()
    if args.seed is not None:
        value = probe_once(args.seed, args.out, args.planner_source)
        return 0 if value['same_normalized_public_state'] else 1
    for seed in args.seeds:
        run_process(seed, args.out/f'seed{seed}', args.planner_source)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
