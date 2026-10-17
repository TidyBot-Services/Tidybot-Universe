"""Direct, diagnostic GPU comparison. Never starts or contacts the owned service."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

CASES = {
    'up_1cm': (0, 0, .01), 'up_2cm': (0, 0, .02),
    'up_5cm': (0, 0, .05), 'forward_2cm': (.02, 0, 0),
    'side_2cm': (0, .02, 0), 'diagonal_3cm': (.02, .02, .01),
}


def parse_ik_candidates(value: str) -> tuple[int, ...]:
    parts = value.split(',')
    if not parts or any(not part.isdecimal() or int(part) < 1 for part in parts):
        raise argparse.ArgumentTypeError('--ik-candidates requires comma-separated positive integers')
    numbers = tuple(int(part) for part in parts)
    if len(set(numbers)) != len(numbers) or any(n > 64 for n in numbers):
        raise argparse.ArgumentTypeError('--ik-candidates must be distinct values at most 64')
    return numbers


def diagnostic_files(config_path: Path, assets_dir: Path, document: dict,
                     fixture_path: Path | None = None) -> dict[str, str]:
    """Inventory files read by this diagnostic run, without changing a lock."""
    config_path, assets_dir = config_path.resolve(), assets_dir.resolve()
    kin = document.get('robot_cfg', document)['kinematics']
    files: dict[str, str] = {}
    missing: list[str] = []

    def register(path: Path) -> None:
        path = path.resolve()
        if path.is_file():
            files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            missing.append(str(path))

    def resolve_file(value: str, roots: tuple[Path, ...], label: str) -> Path:
        candidates = [root / value for root in roots]
        found = next((path.resolve() for path in candidates if path.is_file()), None)
        if found is None:
            missing.append(f'{label}: {value} (tried: {", ".join(str(p.resolve()) for p in candidates)})')
            return candidates[0].resolve()
        register(found)
        return found

    register(config_path)
    if fixture_path is not None:
        register(fixture_path)
    root_value = kin.get('asset_root_path')
    asset_root = None
    if isinstance(root_value, str):
        roots = (config_path.parent, assets_dir)
        asset_root = next((p.resolve() for p in (r / root_value for r in roots) if p.is_dir()), None)
        if asset_root is None:
            missing.append(f'asset_root_path: {root_value} (tried: {", ".join(str((r / root_value).resolve()) for r in roots)})')

    urdfs = []
    urdf_value = kin.get('urdf_path')
    if not isinstance(urdf_value, str):
        missing.append('kinematics.urdf_path is missing')
    else:
        urdfs.append(resolve_file(urdf_value, (config_path.parent, assets_dir), 'urdf_path'))
    exact = document.get('service', {}).get('exact_geometry')
    if exact:
        exact_urdf = exact.get('urdf_path')
        if not isinstance(exact_urdf, str):
            missing.append('service.exact_geometry.urdf_path is missing')
        else:
            # geometry.py opens this path directly, without the planner's search roots.
            path = Path(exact_urdf).resolve()
            register(path)
            urdfs.append(path)
    spheres = kin.get('collision_spheres')
    if isinstance(spheres, str):
        resolve_file(spheres, (config_path.parent, assets_dir), 'collision_spheres')

    for urdf in set(urdfs):
        if not urdf.is_file():
            continue
        xml = ET.parse(urdf).getroot()
        collision_meshes = set(xml.findall('.//collision//mesh'))
        for mesh in xml.findall('.//mesh'):
            name = mesh.get('filename')
            if not name:
                missing.append(f'{urdf}: mesh has no filename')
                continue
            # geometry.py hashes collision meshes at this exact URDF-relative path.
            path = urdf.parent / name
            candidates = (path, assets_dir / name) if asset_root is None else (path, assets_dir / name, asset_root / name)
            found = next((p.resolve() for p in candidates if p.is_file()), None)
            if found is None:
                missing.append(f'{urdf}: mesh {name} (tried: {", ".join(str(p.resolve()) for p in candidates)})')
            else:
                register(found)
                if mesh in collision_meshes and not path.is_file():
                    missing.append(f'{urdf}: collision mesh {name} must exist at {path.resolve()} for geometry.py')

    if missing:
        raise FileNotFoundError('Diagnostic inputs missing:\n' + '\n'.join(sorted(set(missing))))
    return files


def request_inputs(core, fixture: dict, side: str):
    """Rebuild SapienV2Planner.body() from a saved service fixture."""
    source = fixture.get('response')
    if source is not None and source.get('trajectory'):
        q0 = core.named(source['trajectory'][0], source['joint_names'])
    else:
        q0 = core.named(fixture['q0'], fixture['joint_names'])
    recorded = fixture.get('locked_joints', {})
    # A continuous certificate records the merged model locks. The request
    # contains only the configured gripper/base locks from lock_state().
    missing = set(core._locks) - set(recorded)
    if missing:
        raise ValueError(f'Fixture lacks configured measured locks: {sorted(missing)}')
    locks = {name: recorded[name] for name in core._locks}
    tool = f'openarm_{side}_hand_tcp'
    active = [f'openarm_{side}_joint{i}' for i in range(1, 8)]
    for name in set(recorded) & (set(core.joint_names) - set(active)):
        if abs(recorded[name] - q0[core.joint_names.index(name)]) > 1e-5:
            raise ValueError(f'Fixture inactive joint lock differs from current_q: {name}')
    world = fixture['world']
    if isinstance(world, dict):
        world = world['cuboids']
    if not isinstance(world, list):
        raise ValueError('Fixture world must be the complete robot-base-frame cuboid list')
    body = dict(env_id='kitchen', current_q=q0.tolist(), joint_names=list(core.joint_names),
                locked_joints=locks, active_joints=active)
    return body, world, fixture.get('attachment'), tool


def precheck(core, body: dict, world: list, attachment, tool: str, targets=None,
             ik_candidates=(1,)):
    """Run planner_core's CPU validations for every benchmark call shape."""
    q0 = core.named(body['current_q'], body['joint_names'])
    if targets is None:
        core.set_collision_world(world)
        core.set_attachment(attachment)
    active, locks = body['active_joints'], body['locked_joints']
    # /warmup and both planning routes select only the requested TCP.
    core.check_selection(q0, active, [tool], locks)
    # /fk selects every configured TCP and clears the attachment.
    core.check_selection(q0, active, None, locks)
    shape_target = {tool: {'position': [0., 0., 0.],
                           'quaternion_wxyz': [1., 0., 0., 0.]}} if targets is None else targets
    for count in ik_candidates:
        core.check_ik_path_request(shape_target, active, ik_candidates=count)
    if targets is not None:
        core.check_targets(targets)
    return q0


def print_summary(rows):
    fields = [('IK solve s', 'ik_seconds'), ('select+check s', 'ik_selection_seconds'),
              ('trajopt s', 'trajopt_seconds'), ('path s', 'trajectory_generation_seconds'),
              ('max dq rad', 'max_joint_change_rad'), ('cert n', 'certificate_checks'),
              ('cert s', 'certification_seconds'), ('total', 'total_seconds'),
              ('pos m', 'position_error_m'), ('rot rad', 'orientation_error_rad')]
    header = ['case', 'mode', 'k', 'result', *[label for label, _ in fields]]
    print('\n| ' + ' | '.join(header) + ' |')
    print('|' + '|'.join(['---'] * len(header)) + '|')
    for row in rows:
        result = 'PASS' if row['passed'] else 'FAIL: ' + str(row.get('reason') or 'unknown')
        values = [row['case'], row['mode'], str(row.get('ik_candidates', '—')), result]
        for _, key in fields:
            value = row.get(key)
            values.append('—' if value is None else str(value) if isinstance(value, int) else f'{value:.6f}')
        print('| ' + ' | '.join(values) + ' |')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--preflight-only', action='store_true',
                        help='Validate saved request inputs with planner_core on CPU; stop before GPU warmup')
    parser.add_argument('--robot-config', type=Path)
    parser.add_argument('--assets-dir', type=Path)
    parser.add_argument('--fixture', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'tests/fixtures/ik_path_world.json',
                        help='Saved continuous-*.json or bundled minimal world/start fixture')
    parser.add_argument('--side', choices=('left', 'right'), default='left')
    parser.add_argument('--ik-candidates', type=parse_ik_candidates, default=(1, 4, 8),
                        help='Comma-separated candidate counts (default: 1,4,8)')
    args = parser.parse_args()
    if args.dry_run:
        print(json.dumps({'cases': CASES, 'ik_candidates': args.ik_candidates}, indent=2))
        return
    if not all((args.robot_config, args.assets_dir, args.fixture)):
        parser.error('real runs require --robot-config, --assets-dir and --fixture')

    # Diagnostic process only: CuroboPlanner's service import guard needs a
    # context. This read-only context cannot register a deployment or serve HTTP.
    import curobo_v2.identity as identity
    import yaml
    import numpy as np
    config_path = args.robot_config.resolve()
    document = yaml.safe_load(config_path.read_text())
    assets = args.assets_dir.resolve()
    files = diagnostic_files(config_path, assets, document, args.fixture)
    import curobo
    library_root = Path(curobo.__file__).resolve().parent.parent
    identity._service_context = ({'assets_root': str(assets), 'files': files,
                                  'library_root': str(library_root),
                                  'diagnostic_mode': 'diagnostic-read-only'}, str(config_path))
    from curobo_v2.service.curobo_service.planner import PlannerAdapter
    from curobo_v2.service.curobo_service.world import WorldStore
    import curobo_v2.sweep as sweep

    fixture = json.loads(args.fixture.read_text())
    adapter = PlannerAdapter('cuda:0', WorldStore(max_envs=8))
    core = adapter._planner
    body, world, attachment, tool = request_inputs(core, fixture, args.side)
    q0 = precheck(core, body, world, attachment, tool, ik_candidates=args.ik_candidates)
    if args.preflight_only:
        print(json.dumps({'preflight': 'ok', 'diagnostic_identity': 'diagnostic-read-only',
                          'registered_files': len(files), 'geometry_counts':
                          None if core.geometry is None else core.geometry.geometry_counts,
                          'env_id': body['env_id'], 'configured_locks': list(body['locked_joints']),
                          'active_joints': body['active_joints'], 'world_cuboids': len(world),
                          'ik_candidates': args.ik_candidates}))
        return
    adapter.push_world(body['env_id'], world)
    # SapienV2Planner.prepare first warms the complete tool set, checks FK,
    # then warms the requested arm's TCP separately.
    adapter.warmup(**{key: value for key, value in body.items() if key != 'env_id'})
    start_pose = adapter.fk(body['env_id'], body['current_q'], body['joint_names'],
                            body['locked_joints'], body['active_joints'])['poses'][tool]
    if [tool] != core.tool_frames:
        adapter.warmup(**{key: value for key, value in body.items() if key != 'env_id'},
                       tool_frames=[tool])
    solver_times = {'ik': [], 'trajopt': []}

    def instrument(solver, stage):
        original = solver.solve_pose
        def timed(*a, **kw):
            began = time.monotonic()
            try:
                return original(*a, **kw)
            finally:
                solver_times[stage].append(time.monotonic() - began)
        solver.solve_pose = timed

    original_certify = sweep.certify_trajectory
    certificate_times = []

    def timed_certify(*a, **kw):
        begin = time.monotonic()
        try:
            return original_certify(*a, **kw)
        finally:
            certificate_times.append(time.monotonic() - begin)

    sweep.certify_trajectory = timed_certify
    rows = []
    for name, delta in CASES.items():
        target = {tool: {'position': (np.asarray(start_pose['position']) + delta).tolist(),
                         'quaternion_wxyz': start_pose['quaternion_wxyz']}}
        precheck(core, body, world, attachment, tool, target, args.ik_candidates)
        for mode, count in [('plan_pose', None), *(('ik_path', k) for k in args.ik_candidates)]:
            certificate_times.clear()
            solver_times['ik'].clear()
            solver_times['trajopt'].clear()
            # Native _plan uploads the same base-frame world and prepares the
            # selected measured model with the current attachment each request.
            adapter.push_world(body['env_id'], world)
            adapter.warmup(**body, tool_frames=[tool], attachment=attachment)
            selected, _ = core._select(q0, body['active_joints'], [tool], body['locked_joints'])
            originals = []
            if mode == 'plan_pose':
                for solver, stage in ((selected.ik_solver, 'ik'), (selected.trajopt_solver, 'trajopt')):
                    originals.append((solver, solver.solve_pose))
                    instrument(solver, stage)
            began = time.monotonic()
            try:
                if mode == 'plan_pose':
                    response = adapter.plan_pose(**body, targets=target, attachment=attachment)
                else:
                    response = adapter.plan_ik_path(**body, targets=target, attachment=attachment,
                                                    ik_candidates=count)
                elapsed = time.monotonic() - began
                passed = response['status'] == 'success'
                trajectory = np.asarray(response['trajectory']) if passed else None
                endpoint = (adapter.fk(body['env_id'], trajectory[-1].tolist(), body['joint_names'],
                            body['locked_joints'], body['active_joints'])['poses'][tool] if passed else None)
                position_error = float(np.linalg.norm(np.asarray(endpoint['position']) - target[tool]['position'])) if passed else None
                if passed:
                    from scipy.spatial.transform import Rotation
                    a = Rotation.from_quat(np.asarray(endpoint['quaternion_wxyz'])[[1, 2, 3, 0]])
                    b = Rotation.from_quat(np.asarray(target[tool]['quaternion_wxyz'])[[1, 2, 3, 0]])
                    rotation_error = float((a * b.inv()).magnitude())
                else:
                    rotation_error = None
                metrics = core.last_ik_path_metrics if mode == 'ik_path' else {}
                row = dict(case=name, mode=mode, ik_candidates=count, passed=passed, reason=response['reason'],
                    ik_seconds=sum(solver_times['ik']) if solver_times['ik'] else metrics.get('ik_seconds'),
                    ik_selection_seconds=metrics.get('selection_seconds'),
                    trajopt_seconds=sum(solver_times['trajopt']) if solver_times['trajopt'] else None,
                    trajectory_generation_seconds=metrics.get('trajectory_generation_seconds'),
                    certification_seconds=sum(certificate_times) if certificate_times else metrics.get('certification_seconds'),
                    certificate_checks=(((response.get('validation') or {}).get('continuous_geometry') or {}).get('queries')
                                        or metrics.get('certificate_checks')),
                    total_seconds=elapsed, position_error_m=position_error,
                    orientation_error_rad=rotation_error,
                    max_joint_change_rad=float(np.max(np.abs(trajectory - q0))) if passed else None,
                    ik_selected_joint_distance_rad=response.get('ik_selected_joint_distance_rad'),
                    ik_candidate_distances=response.get('ik_candidate_distances'),
                    ik_candidate_reasons=response.get('ik_candidate_reasons'))
            except Exception as exc:
                row = dict(case=name, mode=mode, ik_candidates=count, passed=False,
                           reason=f'{type(exc).__name__}: {exc}', total_seconds=time.monotonic()-began,
                           ik_seconds=sum(solver_times['ik']) if solver_times['ik'] else None,
                           ik_selection_seconds=core.last_ik_path_metrics.get('selection_seconds') if mode == 'ik_path' else None,
                           trajopt_seconds=sum(solver_times['trajopt']) if solver_times['trajopt'] else None,
                           trajectory_generation_seconds=None,
                           certification_seconds=sum(certificate_times) if certificate_times else None,
                           certificate_checks=None, position_error_m=None,
                           orientation_error_rad=None, max_joint_change_rad=None)
            finally:
                for solver, original in originals:
                    solver.solve_pose = original
            print(json.dumps(row, ensure_ascii=False))
            rows.append(row)
    print_summary(rows)


if __name__ == '__main__':
    main()
