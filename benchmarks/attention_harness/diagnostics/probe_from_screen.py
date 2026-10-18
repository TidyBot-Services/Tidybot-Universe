"""Turn a successful development scene screen into a runnable grasp-chain command.

This tool only reads JSON and prints shell text. It never resets or steps a scene.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shlex

from .scene_screen import theta_from_world_quaternion

ENVIRONMENT = {
    'ATTN_PLANNER_MODE': 'ik_path',
    'ATTN_IK_CANDIDATES': '8',
    'ATTN_IK_MAX_JOINT_JUMP': '1.0',
    'ATTN_DIAG_HELD_TRACE': '1',
}
REQUIRED_IK = ('grasp_quaternion_world_xyzw', 'grasp_theta_deg',
               'object_height_m', 'target_z_range_world_m', 'selected_candidate_number')


def finite_vector(value, size, name):
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise ValueError(f'{name} requires {size} finite numbers')
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{name} requires {size} finite numbers') from exc
    if not all(math.isfinite(item) for item in result):
        raise ValueError(f'{name} requires {size} finite numbers')
    return result


def clearance_for_height(object_height_m):
    """TCP above rim: half object height plus 4 cm bottom/geometry margin."""
    height = float(object_height_m)
    if not math.isfinite(height) or height <= 0:
        raise ValueError('object_height_m must be positive and finite')
    clearance = max(0.08, height/2+0.04)
    if clearance > 0.20:
        raise ValueError(f'clearance {clearance:.6g} m exceeds 0.20 m; inspect tall-object geometry and rerun screening')
    return clearance


def assemble_parameters(reset, ik, *, seed, out, budget_scale=4.,
                        lift_height_m=.06, lift_step_m=.025, grasp_z_offset_mm=5.):
    if reset.get('seed') != seed or ik.get('seed') != seed:
        raise ValueError('reset.json and ik.json seed do not match --seed')
    if not ik.get('grasp_ik_feasible') or not ik.get('carry_ik_feasible'):
        raise ValueError(f'seed {seed} has no grasp-and-carry endpoint IK result')
    missing = [name for name in REQUIRED_IK if name not in ik]
    if missing:
        raise ValueError('old ik.json lacks ' + ', '.join(missing) + '; rerun scene_screen --stage ik for this seed')
    grasp = finite_vector(ik['grasp_base_shift'], 3, 'grasp_base_shift')
    cell = finite_vector(ik['carry_base_cell'], 2, 'carry_base_cell')
    if abs(grasp[2]) > 1e-9:
        raise ValueError('nonzero grasp base yaw requires a new mount-frame carry conversion; rerun screening with --dyaw-list 0')
    quaternion = finite_vector(ik['grasp_quaternion_world_xyzw'], 4, 'grasp_quaternion_world_xyzw')
    theta = theta_from_world_quaternion(quaternion)
    recorded_theta = ik['grasp_theta_deg']
    if (theta is None) != (recorded_theta is None) or (theta is not None and
            abs((theta-float(recorded_theta)+180.) % 360.-180.) > 1e-5):
        raise ValueError('grasp_theta_deg disagrees with grasp_quaternion_world_xyzw; rerun screening')
    z_range = finite_vector(ik['target_z_range_world_m'], 2, 'target_z_range_world_m')
    reset_z = finite_vector(reset['target_z_range_world_m'], 2, 'reset target_z_range_world_m')
    if z_range[1] <= z_range[0] or any(abs(a-b) > 1e-6 for a, b in zip(z_range, reset_z)):
        raise ValueError('IK target z range differs from reset; rerun screening')
    height = float(ik['object_height_m'])
    if abs(height-(z_range[1]-z_range[0])) > 1e-6:
        raise ValueError('object_height_m disagrees with target z range; rerun screening')
    clearance = clearance_for_height(height)
    release = finite_vector(ik['release_point_world_m'], 3, 'release_point_world_m')
    sink = reset['sink']
    region = next((row for row in sink['regions'] if row['name'] == sink['chosen_region']), None)
    if region is None:
        raise ValueError('reset sink chosen_region is missing')
    bounds = region['xy_aabb_world_m']
    if any(not float(bounds[i][0]) <= release[i] <= float(bounds[i][1]) for i in (0, 1)):
        raise ValueError('release XY lies outside the native sink interior; rerun screening')
    rim = float(sink['rim_top_world_m'])
    if not math.isfinite(rim) or not .5 <= rim <= 1.5 or not sink.get('rim_source_collision_geom'):
        raise ValueError('reset lacks a valid basin-wall rim top; rerun reset screening')
    if not math.isfinite(budget_scale) or not 1 <= budget_scale <= 8:
        raise ValueError('--budget-scale must be finite and in [1, 8]')
    if not all(math.isfinite(v) for v in (lift_height_m, lift_step_m, grasp_z_offset_mm)) or not 0 < lift_height_m <= .5 or not 0 < lift_step_m <= .05:
        raise ValueError('invalid lift or grasp offset override')
    return {'seed': seed, 'out': str(out), 'budget_scale': budget_scale,
            'candidate_number': ik['selected_candidate_number'],
            'base_shift': grasp, 'grasp_quaternion_world_xyzw': quaternion,
            'grasp_theta_deg': theta, 'object_height_m': height,
            'target_z_range_world_m': z_range, 'release_point_world_m': release,
            'native_interior_xy_world_m': bounds,
            'carry_sink_xy': release[:2], 'carry_rim_top': rim,
            'carry_base_shift': (cell[0]-grasp[0], cell[1]-grasp[1], 0.),
            'clearance_m': clearance, 'lift_height_m': lift_height_m,
            'lift_step_m': lift_step_m, 'grasp_z_offset_mm': grasp_z_offset_mm}


def command_tokens(params):
    number = lambda value: format(value, '.16g')
    csv = lambda values: ','.join(number(value) for value in values)
    return ['python', '-m', 'benchmarks.attention_harness.diagnostics.grasp_chain_probe',
            '--seed', str(params['seed']), '--base-shift', csv(params['base_shift']),
            '--grasp-quat', csv(params['grasp_quaternion_world_xyzw']),
            '--oracle-from-reset', '--sequence', 'translate-first', '--rotation-step', '0.15',
            '--exempt', 'movable_objects', '--grasp-z-offset-mm', number(params['grasp_z_offset_mm']),
            '--lift-step-m', number(params['lift_step_m']), '--lift-height-m', number(params['lift_height_m']),
            '--carry', '--carry-sink-xy', csv(params['carry_sink_xy']),
            '--carry-rim-top', number(params['carry_rim_top']),
            '--carry-base-shift', csv(params['carry_base_shift']),
            '--carry-clearance-m', number(params['clearance_m']),
            '--release-clearance-m', number(params['clearance_m']),
            '--retreat-m', '0.025', '--retreat-base=-0.30,0,0',
            '--budget-scale', number(params['budget_scale']), '--out', params['out']]


def shell_command(params):
    return ' '.join(f'{key}={shlex.quote(value)}' for key, value in ENVIRONMENT.items()) + ' ' + shlex.join(command_tokens(params))


def selected_row(path, seed):
    data = json.loads(path.read_text())
    rows = [row for row in data['rows'] if row['seed'] == seed]
    if len(rows) != 1:
        raise ValueError(f'{path}: expected exactly one row for seed {seed}, found {len(rows)}')
    return rows[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--screen-dir', required=True, type=Path)
    parser.add_argument('--seed', required=True, type=int, choices=range(101, 126))
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--budget-scale', type=float, default=4.)
    parser.add_argument('--lift-height-m', type=float, default=.06)
    parser.add_argument('--lift-step-m', type=float, default=.025)
    parser.add_argument('--grasp-z-offset-mm', type=float, default=5.)
    display = parser.add_mutually_exclusive_group()
    display.add_argument('--dry-run', action='store_true', help='print summary and command (default)')
    display.add_argument('--print-command', action='store_true', help='print only the shell command')
    args = parser.parse_args(argv)
    try:
        reset = selected_row(args.screen_dir/'reset.json', args.seed)
        ik = selected_row(args.screen_dir/'ik.json', args.seed)
        params = assemble_parameters(reset, ik, seed=args.seed, out=args.out,
            budget_scale=args.budget_scale, lift_height_m=args.lift_height_m,
            lift_step_m=args.lift_step_m, grasp_z_offset_mm=args.grasp_z_offset_mm)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    if not args.print_command:
        summary = {key: params[key] for key in ('seed', 'candidate_number', 'base_shift',
            'grasp_quaternion_world_xyzw', 'grasp_theta_deg', 'object_height_m',
            'target_z_range_world_m', 'carry_sink_xy', 'carry_rim_top',
            'carry_base_shift', 'clearance_m')}
        print(json.dumps({'dry_run': True, 'parameters': summary,
            'clearance_formula': 'max(0.08, object_height_m/2 + 0.04); TCP above basin-wall rim'},
            ensure_ascii=False))
    print(shell_command(params))


if __name__ == '__main__':
    main()
