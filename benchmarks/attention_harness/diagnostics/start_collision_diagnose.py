"""Replay a recorded /plan/ik_path start and diagnose its proxy collision on GPU.

Dry-run and imports use only the Python standard library. The real run uses the
same planner core, world update, attachment and validate methods as the service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execution-dir', type=Path, required=True)
    parser.add_argument('--robot-config', type=Path)
    parser.add_argument('--assets-dir', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if not args.dry_run and (args.robot_config is None or args.assets_dir is None):
        parser.error('GPU runs require --robot-config and --assets-dir')
    return args


def read_recording(execution_dir: Path):
    planner_dir = execution_dir / 'planner'
    log = planner_dir / 'planner_http.jsonl'
    last = None
    with log.open() as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                if str(row.get('path', '')).startswith('/plan'):
                    last = row
    if last is None:
        raise ValueError(f'No /plan* request in {log}')
    request = last['request']
    if isinstance(request, str):
        request = json.loads(request)
    if not isinstance(request, dict):
        raise ValueError('Recorded request must be a JSON object')
    worlds = sorted(planner_dir.glob('world-*.json'))
    if not worlds:
        raise ValueError(f'No world-*.json in {planner_dir}')
    world_path = worlds[-1]
    payload = json.loads(world_path.read_text())
    cuboids = payload['cuboids'] if isinstance(payload, dict) else payload
    if not isinstance(cuboids, list) or not cuboids:
        raise ValueError('World snapshot has no cuboids')
    if len({c['name'] for c in cuboids}) != len(cuboids):
        raise ValueError('World snapshot has duplicate cuboid names')
    response = last.get('response')
    if isinstance(response, str):
        response = json.loads(response)
    expected_hash = response.get('world_sha256') if isinstance(response, dict) else None
    actual_hash = hashlib.sha256(json.dumps({'cuboids': cuboids, 'robot_pos': None},
                                    sort_keys=True, allow_nan=False).encode()).hexdigest()
    if expected_hash and expected_hash != actual_hash:
        raise ValueError('Last world snapshot does not match recorded plan response world_sha256')
    return request, cuboids, last['path'], log, world_path, response


def _quat_rotate_inverse(q, vector):
    # Unit wxyz quaternion, inverse rotation of a vector.
    w, x, y, z = q
    ux, uy, uz = -x, -y, -z
    tx = 2 * (uy * vector[2] - uz * vector[1])
    ty = 2 * (uz * vector[0] - ux * vector[2])
    tz = 2 * (ux * vector[1] - uy * vector[0])
    return (vector[0] + w * tx + uy * tz - uz * ty,
            vector[1] + w * ty + uz * tx - ux * tz,
            vector[2] + w * tz + ux * ty - uy * tx)


def cuboid_sphere_clearance(cuboid: dict, sphere) -> float:
    """Signed Euclidean distance from sphere surface to an oriented cuboid."""
    center = cuboid['center']
    local = _quat_rotate_inverse(cuboid.get('quat_wxyz', [1, 0, 0, 0]),
                                 [sphere[i] - center[i] for i in range(3)])
    outside = [max(abs(local[i]) - cuboid['half_size'][i], 0.0) for i in range(3)]
    if any(outside):
        return math.sqrt(sum(v * v for v in outside)) - sphere[3]
    return max(abs(local[i]) - cuboid['half_size'][i] for i in range(3)) - sphere[3]


def rank_cuboids(cuboids: list[dict], spheres, excluded_names=(), limit=40):
    """Rank static cuboids by proximity to held-object and hand sphere envelope."""
    excluded = set(excluded_names)
    spheres = [s for s in spheres if s[3] > 0]
    if not spheres:
        raise ValueError('No active object or hand spheres for proximity ranking')
    rows = [{'name': c['name'], 'distance_m': min(cuboid_sphere_clearance(c, s) for s in spheres)}
            for c in cuboids if c['name'] not in excluded]
    return sorted(rows, key=lambda row: (row['distance_m'], row['name']))[:limit]


def summarize_trials(ranked, single_trials, prefix_trials):
    singles = [row['name'] for row in ranked if single_trials.get(row['name']) is True]
    first = next((n for n in range(1, len(ranked) + 1) if prefix_trials.get(n) is True), None)
    return {'single_removal_pass_names': singles, 'first_prefix_pass_n': first,
            'nearest_10': ranked[:10], 'tested_count': len(ranked)}


def format_markdown(result):
    rep = result.get('reproduction', {})
    if result['status'] == 'dry_run':
        return (f"# 起点碰撞诊断 dry-run\n\n请求：`{result['request_path']}`；"
                f"世界形状：{result['world_cuboids']}。GPU 碰撞验证未运行。\n")
    lines = ['# 起点碰撞诊断', '', f"状态：{result['status']}",
             f"复现：proxy={rep.get('proxy_collision_free')}，exact={rep.get('exact_geometry_collision_free')}，"
             f"joint_limits={rep.get('joint_limits')}", '']
    if result['status'] != 'ok':
        lines.append(result.get('message', '起点失败未复现，已停止。'))
        return '\n'.join(lines) + '\n'
    summary = result['leave_one_out']
    groups = result['sphere_groups']
    lines += [f"单独移除即可使 proxy 通过：{', '.join(summary['single_removal_pass_names']) or '无'}",
              f"按距离移除前 N 个首次通过：{summary['first_prefix_pass_n'] if summary['first_prefix_pass_n'] is not None else '前 40 个内未通过'}",
              f"普通球世界碰撞通过：{groups['ordinary']['world_collision_free']}；"
              f"携带球世界碰撞通过：{groups['carried']['world_collision_free']}；"
              f"关节界限与自碰撞通过：{groups['bounds_and_self_collision_free']}", '',
              '## 最近 10 个障碍', '', '| 名称 | 包络距离 (m) |', '| --- | ---: |']
    lines += [f"| {row['name']} | {row['distance_m']:.6f} |" for row in summary['nearest_10']]
    lines += ['', '距离是形状到当前携带球及左手爪球包络的几何距离，用于排序；碰撞结论来自 planner core。']
    if result.get('notes'):
        lines += ['', *result['notes']]
    return '\n'.join(lines) + '\n'


def write_result(out: Path, result):
    out.mkdir(parents=True, exist_ok=True)
    (out / 'results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (out / 'results.md').write_text(format_markdown(result))


def _prepare_core(args, log_path, world_path):
    # Mirrors ik_path_bench.py's diagnostic identity; no deployment registration.
    import curobo
    import curobo_v2.identity as identity
    import yaml
    from .ik_path_bench import diagnostic_files

    config = args.robot_config.resolve()
    assets = args.assets_dir.resolve()
    files = diagnostic_files(config, assets, yaml.safe_load(config.read_text()), log_path)
    files[str(world_path.resolve())] = hashlib.sha256(world_path.read_bytes()).hexdigest()
    identity._service_context = ({'assets_root': str(assets), 'files': files,
                                  'library_root': str(Path(curobo.__file__).resolve().parent.parent),
                                  'diagnostic_mode': 'diagnostic-read-only'}, str(config))
    from curobo_v2.service.curobo_service.planner import PlannerAdapter
    from curobo_v2.service.curobo_service.world import WorldStore
    return PlannerAdapter('cuda:0', WorldStore(max_envs=8))


def _validate_start(adapter, request, cuboids, *, removed=()):
    # Repeat the service adapter sequence: world upload, named q, attachment,
    # selection for the requested TCP, then CuroboPlanner.validate.
    removed = set(removed)
    world = [c for c in cuboids if c['name'] not in removed]
    attachment = dict(request['attachment'])
    attachment['support_names'] = [n for n in attachment['support_names'] if n not in removed]
    adapter.push_world(request['env_id'], world)
    core = adapter._planner
    q = core.named(request['current_q'], request.get('joint_names'))
    core.set_attachment(attachment)
    tool = core.check_ik_path_request(request['targets'], request['active_joints'],
                                      request.get('max_joint_jump', .35),
                                      request.get('minimum_duration', .5),
                                      ik_candidates=request.get('ik_candidates', 1))
    planner, checker = core._select(q, request['active_joints'],
                                    tool_frames=[tool], locked_joints=request.get('locked_joints'))
    core.validate(q[None], planner, checker)
    return q, planner, checker, dict(core.last_validation)


def _sphere_indices(checker, attachment):
    params = checker.kinematics.config.kinematics_config
    carried = params.get_sphere_index_from_link_name(attachment.link).cpu().tolist()
    hand = []
    for name in (f'openarm_{attachment.holder}_hand',
                 f'openarm_{attachment.holder}_left_finger',
                 f'openarm_{attachment.holder}_right_finger'):
        hand.extend(params.get_sphere_index_from_link_name(name).cpu().tolist())
    if not carried or not hand:
        raise ValueError('Could not identify both carried and left hand spheres')
    return carried, hand


def _group_collision(checker, active, carried_indices):
    # Official checker APIs, with only scene query sphere radii masked. Its
    # bounds and self collision components are evaluated on the complete robot.
    state = checker.get_kinematics(active)
    spheres = state.robot_spheres
    count = spheres.shape[-2]
    if max(carried_indices) >= count:
        raise ValueError('Carried sphere index exceeds checker state')
    bounds = checker.get_bound(active)
    self_cost = checker.get_self_collision(spheres)
    shared_ok = bool((bounds.sum() == 0).item() and (self_cost.sum() == 0).item())
    groups = {}
    for label, keep in [('ordinary', set(range(count)) - set(carried_indices)),
                        ('carried', set(carried_indices))]:
        masked = spheres.clone()
        off = [i for i in range(count) if i not in keep]
        masked[..., off, 3] = -100.
        world_cost = checker.get_collision_constraint(masked)
        world_ok = bool((world_cost.sum() == 0).item())
        groups[label] = {'sphere_count': len(keep), 'world_collision_free': world_ok,
                         'proxy_with_shared_constraints_free': bool(world_ok and shared_ok)}
    return {'bounds_and_self_collision_free': shared_ok, **groups}, spheres[0, 0].detach().cpu().tolist()


def run_gpu(args, request, cuboids, path, log_path, world_path, recorded_response):
    adapter = _prepare_core(args, log_path, world_path)
    core = adapter._planner
    recorded_robot = (recorded_response or {}).get('robot', {})
    for key in ('robot_config_sha256', 'urdf_sha256', 'joint_names', 'tool_frames'):
        if key in recorded_robot and core.identity[key] != recorded_robot[key]:
            raise ValueError(f'Robot identity differs from recorded plan: {key}')
    q, planner, checker, validation = _validate_start(adapter, request, cuboids)
    reproduction = {'proxy_collision_free': validation.get('proxy_collision_free'),
                    'exact_geometry_collision_free': validation.get('exact_geometry', {}).get('collision_free'),
                    'joint_limits': validation.get('joint_limits'), 'validation': validation}
    result = {'status': 'not_reproduced', 'request_path': path, 'world_cuboids': len(cuboids),
              'reproduction': reproduction}
    if validation.get('proxy_collision_free') is not False:
        result['message'] = 'Recorded proxy failure did not reproduce; no removal trials were run.'
        return result
    active = core._state(q[None], planner).position[:, None, :].contiguous()
    carried, hand = _sphere_indices(checker, core.attachment)
    groups, spheres = _group_collision(checker, active, carried)
    # Shape names are reparented by _scene(), so never remove them. Support
    # names may be removed, but must also leave attachment.support_names.
    ranked = rank_cuboids(cuboids, [spheres[i] for i in carried + hand],
                          excluded_names=core.attachment.names)
    single, prefix = {}, {}
    for row in ranked:
        _, _, _, trial = _validate_start(adapter, request, cuboids, removed=(row['name'],))
        single[row['name']] = trial.get('proxy_collision_free')
    for n in range(1, len(ranked) + 1):
        _, _, _, trial = _validate_start(adapter, request, cuboids,
                                         removed=[row['name'] for row in ranked[:n]])
        prefix[n] = trial.get('proxy_collision_free')
        if prefix[n] is True:
            break
    result.update(status='ok', sphere_groups=groups,
                  leave_one_out={**summarize_trials(ranked, single, prefix),
                                 'single_trials': single, 'prefix_trials': prefix},
                  notes=['移除 support 形状时同步从 attachment.support_names 删除该名称，以满足服务端完整性检查。',
                         '球组归因调用官方 checker 的世界碰撞约束；关节界限与自碰撞仍对完整机器人评估。'])
    return result


def main(argv=None):
    args = parse_args(argv)
    request, cuboids, path, log_path, world_path, recorded_response = read_recording(args.execution_dir)
    if args.dry_run:
        result = {'status': 'dry_run', 'request_path': path, 'world_cuboids': len(cuboids),
                  'attachment_shapes': len(request.get('attachment', {}).get('shape_names', [])),
                  'support_shapes': len(request.get('attachment', {}).get('support_names', [])),
                  'message': 'Inputs loaded; GPU collision validation was not run.'}
    else:
        result = run_gpu(args, request, cuboids, path, log_path, world_path, recorded_response)
    write_result(args.out, result)
    print(json.dumps({'status': result['status'], 'out': str(args.out)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
