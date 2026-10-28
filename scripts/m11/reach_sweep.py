#!/usr/bin/env python3
"""Diagnostic cabinet reachability; no physics stepping or network service."""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
import itertools
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.m11_cabinet_probe import TASK, check_environment, write_json

STAGES = ('base_path', 'pregrasp', 'grasp', 'lift', 'carry', 'place')
TUCK_LEFT = [-1.30, -2.78, -.76, 2.14, .94, -.31, -.30]
# Reflected physical chain: shoulder joint1/2 and wrist joint5/6 change sign.
# This is only a proposed goal; the ordinary joint planner must certify it.
TUCK_RIGHT = [1.30, 2.78, -.76, 2.14, -.94, .31, -.30]


def validate_probe(probe):
    if (probe.get('task_id') != TASK or probe.get('seed') not in range(101, 126)
            or probe.get('same_normalized_public_state') is not True):
        raise ValueError('stable development cabinet probe required')


def base_steps(offset):
    dx, dy, yaw = offset
    count = math.ceil(math.hypot(dx, dy)/.19)
    turns = math.ceil(abs(yaw)/.29)
    return ([[dx/count, dy/count, 0.] for _ in range(count)] +
            [[0., 0., yaw/turns] for _ in range(turns)])


def grasp_orientation(forward, pitch, yaw):
    """Pitch below horizontal; local Z approaches, local Y closes.

    Closing yaw is rotation about the approach axis from the transverse basis.
    At top-down this is the horizontal closing-axis yaw (relative to forward).
    """
    import numpy as np
    from scipy.spatial.transform import Rotation
    p, y = math.radians(pitch), math.radians(yaw)
    z = np.array([math.cos(p)*forward[0], math.cos(p)*forward[1], -math.sin(p)])
    transverse = np.array([-forward[1], forward[0], 0.])
    closing = math.cos(y)*transverse + math.sin(y)*np.cross(z, transverse)
    x = np.cross(closing, z)
    return Rotation.from_matrix(np.column_stack((x, closing, z))).as_quat().tolist(), z.tolist()


def waypoints(scene, approach_axis):
    import numpy as np
    lo, hi = np.array(scene['target']['aabb_world_m'])
    grasp = (lo+hi)/2 + [0., 0., .005]
    regions = scene['cabinet']['interior_regions']
    region = regions.get('level0') or min(regions.values(), key=lambda r: r['shelf_front_world_m'][2])
    p0, px, py, pz = np.array(region['sites_world_m'])
    inward = np.array(region['inward_world'])
    height = hi[2]-lo[2]
    z = p0[2]+height+.03
    if z+height/2 >= pz[2] or region['depth_m'] <= .15:
        raise ValueError('target does not fit level0 with diagnostic clearance')
    # Choose one of three interior lanes farthest from the cabinet distractor.
    # Actual carried geometry still checks distr_cab in the planner world below.
    obstacle = np.array(scene['distractors']['distr_cab'])
    lanes = [p0+f*(px-p0) for f in (.25, .5, .75)]
    shelf = max(lanes, key=lambda p: np.linalg.norm((p+.15*inward-obstacle)[:2]))
    front, place = shelf-.10*inward, shelf+.15*inward
    front[2] = place[2] = z
    lift = grasp.copy(); lift[2] = max(grasp[2]+.10, z)
    return dict(pregrasp=(grasp-.10*np.array(approach_axis)).tolist(), grasp=grasp.tolist(),
                lift=lift.tolist(), carry=front.tolist(), place=place.tolist())


def base_offsets(arm, grid):
    sign = 1 if arm == 'right' else -1
    if grid == 'none':
        return [(0., 0., 0.)]
    if grid == 'small':
        # Eight offsets per physical arm; retain both stage orders/tuck choices.
        return [(dx, sign*dy, yaw) for dx, dy, yaw in (
            (0., 0., 0.), (0., .15, 0.), (0., .27, 0.), (-.20, 0., 0.),
            (.10, 0., 0.), (0., 0., -.30), (0., 0., .30), (-.20, .15, 0.))]
    if grid == 'full':
        return list(itertools.product((0., -.20, .10),
            tuple(sign*y for y in (0., .15, .27, .30)), (0., -.30, .30, -.60, .60)))
    raise ValueError('unknown base grid')


def candidates(probe, arms=('right', 'left'), *, base_grid='full',
               pitches=(90, 60, 45, 30), yaw_step_deg=30):
    validate_probe(probe)
    scene = probe['resets'][0]['scene']
    mount = scene['mount_pose_world']
    initial_yaw = math.atan2(mount[1][0], mount[0][0])
    serial = 0
    for arm in arms:
        # Inboard relative to the physical hand: +base Y for right, -Y for left.
        for offset, order, tuck in itertools.product(base_offsets(arm, base_grid),
                ('base_first', 'grasp_first'), (False, True)):
            dx, dy, yaw = offset
            angle = initial_yaw + (yaw if order == 'base_first' else 0.)
            for pitch, closing in itertools.product(pitches, range(0, 360, yaw_step_deg)):
                quat, axis = grasp_orientation((math.cos(angle), math.sin(angle)), pitch, closing)
                serial += 1
                other = 'left' if arm == 'right' else 'right'
                yield dict(id=serial, arm=arm, order=order, base_offset=[dx, dy, yaw],
                    base_moves=base_steps((dx, dy, yaw)), tuck_other_arm=tuck,
                    tuck_arm=other, tuck_q=(TUCK_LEFT if other == 'left' else TUCK_RIGHT) if tuck else None,
                    pitch_deg=pitch, closing_yaw_deg=closing, approach_axis_world=axis,
                    grasp_quat_xyzw=quat, waypoints=waypoints(scene, axis))


def evaluate(candidate, planner):
    """Planner implements begin, tuck, base_path, waypoint, attach (no execution).

    Failed paths stop immediately. A deferred held-base check cannot be counted
    as passed until grasp and lift have passed. Tuck is a prerequisite, not rank.
    """
    row = deepcopy(candidate)
    row['stages'] = {}
    row['stage_order'] = (['base_path', 'pregrasp', 'grasp', 'lift', 'carry', 'place']
        if row['order'] == 'base_first' else ['pregrasp', 'grasp', 'lift', 'base_path', 'carry', 'place'])
    planner.begin(row)
    if row['tuck_other_arm']:
        row['tuck_result'] = planner.tuck(row)
        if not row['tuck_result']['success']:
            row['failure_stage'] = 'tuck'
            row['failure_reason'] = row['tuck_result']['failure_reason']
    for stage in row['stage_order']:
        if row.get('failure_stage'):
            break
        result = planner.base_path(row) if stage == 'base_path' else planner.waypoint(stage, row)
        row['stages'][stage] = result
        if not result['success']:
            row['failure_stage'], row['failure_reason'] = stage, result['failure_reason']
            break
        if stage == 'grasp':
            planner.attach(row)  # Hypothetical grasp geometry, never a stable-grasp claim.
    margins = [r['min_joint_margin_rad'] for r in row['stages'].values()
               if r.get('min_joint_margin_rad') is not None]
    if row.get('tuck_result', {}).get('min_joint_margin_rad') is not None:
        margins.append(row['tuck_result']['min_joint_margin_rad'])
    row['min_joint_margin_rad'] = min(margins, default=None)
    row['furthest_stage'] = next((s for s in reversed(STAGES) if row['stages'].get(s, {}).get('success')), None)
    row['stage_score'] = STAGES.index(row['furthest_stage'])+1 if row['furthest_stage'] else 0
    row['complete'] = all(row['stages'].get(s, {}).get('success') for s in STAGES)
    for stage in STAGES:
        row['stages'].setdefault(stage, dict(success=False, skipped=True,
            failure_reason='prior_stage_failed', min_joint_margin_rad=None, min_clearance_m=None))
    return row


def rank_key(row):
    return (row['stage_score'], row['min_joint_margin_rad'] if row['min_joint_margin_rad'] is not None else -math.inf,
            -row['id'])


def sweep(probe, planner, arms, limit, progress=None, *, base_grid='full',
          pitches=(90, 60, 45, 30), yaw_step_deg=30, max_candidates=None, time_limit_s=None):
    top, counts, failures, total = [], Counter(), Counter(), 0
    untucked_base = {}
    by_stage = {s: Counter() for s in ('tuck', *STAGES)}
    started = time.monotonic()
    stop_reason = None
    for candidate in candidates(probe, arms, base_grid=base_grid, pitches=pitches, yaw_step_deg=yaw_step_deg):
        if max_candidates is not None and total >= max_candidates:
            stop_reason = 'max_candidates'
            break
        if time_limit_s is not None and time.monotonic()-started >= time_limit_s:
            stop_reason = 'time_limit_s'
            break
        candidate_started = time.monotonic()
        row = evaluate(candidate, planner)
        row['elapsed_seconds'] = time.monotonic()-candidate_started
        pair = (row['arm'], tuple(row['base_offset']), row['order'], row['pitch_deg'], row['closing_yaw_deg'])
        base = row['stages']['base_path']
        if not row['tuck_other_arm']:
            untucked_base[pair] = None if base.get('skipped') else base['success']
        baseline = untucked_base.get(pair)
        row['untucked_base_path_passed'] = baseline
        row['tuck_required_for_base_path'] = (None if baseline is None or base.get('skipped')
            else bool(row['tuck_other_arm'] and base['success'] and not baseline))
        total += 1
        counts.update(s for s, r in row['stages'].items() if r['success'])
        if row.get('failure_reason'):
            failures[row['failure_reason']] += 1
            by_stage[row['failure_stage']][row['failure_reason']] += 1
        top.append(row); top.sort(key=rank_key, reverse=True); del top[limit:]
        if progress:
            progress(total, counts, time.monotonic()-started, row['elapsed_seconds'])
    return dict(schema='attentionbench.m11-reach-sweep.v1', diagnostic_only=True,
        task_id=TASK, seed=probe['seed'], probe_normalized_sha256=probe['resets'][0]['normalized_sha256'],
        total_candidates=total, elapsed_seconds=time.monotonic()-started,
        partial=stop_reason is not None, stop_reason=stop_reason,
        grid=dict(arms=list(arms), base_grid=base_grid, pitches=list(pitches), yaw_step_deg=yaw_step_deg),
        bounds=dict(max_candidates=max_candidates, time_limit_s=time_limit_s,
            time_limit_scope='evaluation only; checked between candidates, in-flight candidate finishes'),
        pass_counts={s: counts[s] for s in STAGES}, blocking_reasons=dict(failures),
        stage_blocking_reasons={s: dict(c) for s, c in by_stage.items()}, candidates=top,
        assumptions=['Hypothetical rigid grasp; no bilateral-contact or stable-grasp verification.',
            'Active fingers use the configured 0.043 m open target, retained during hypothetical carry; close/contact dynamics are untested.',
            'No physics stepping after the canonical reset; no drift or native success claim.',
            'Primary arm world matches executor exemptions; supplemental continuous carried sweep includes all distractors.'])


def summary(report):
    lines = ['# Offline cabinet reachability (diagnostic only)', '',
        f"Seed {report['seed']}; {report['total_candidates']} candidates; {report['elapsed_seconds']:.1f} s.", '',
        ('PARTIAL: stopped at '+report['stop_reason']+'.' if report.get('partial')
            else 'Complete: evaluated the selected grid.'), '',
        '| Stage | Passed | Dominant first blocking reason (count) |', '| --- | ---: | --- |']
    for stage in ('tuck', *STAGES):
        reasons = report['stage_blocking_reasons'][stage]
        reason, count = max(reasons.items(), key=lambda x: x[1], default=('none', 0))
        lines.append(f"| {stage} | {report['pass_counts'].get(stage, 'prerequisite')} | {reason} ({count}) |")
    lines += ['', 'Counts cover evaluated candidates only; --limit retains only the top N evaluated rows. Rank is furthest passed stage, then minimum measured joint margin.',
              'Null clearance means the planner did not report a measured minimum; certificate thresholds are separately labelled.', '', *report['assumptions']]
    return '\n'.join(lines)+'\n'


def refuse_keys_env():
    import os
    def audit(event, args):
        if (event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike))
                and os.path.basename(os.fsdecode(args[0])) == 'keys.env'):
            raise PermissionError('T1b never reads keys.env')
    sys.addaudithook(audit)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--planner-source', type=Path, default=ROOT)
    parser.add_argument('--arms', default='right,left')
    parser.add_argument('--limit', type=int, default=20)
    parser.add_argument('--base-grid', choices=('none', 'small', 'full'), default='full',
                        help='1 stationary / 8 / 60 offsets per arm; both orders and tuck choices retained')
    parser.add_argument('--pitches', default='90,60,45,30', help='comma-separated pitches below horizontal, in degrees')
    parser.add_argument('--yaw-step-deg', type=int, default=30, help='closing-axis yaw spacing (integer 1..360)')
    parser.add_argument('--max-candidates', type=int, help='stop after evaluating N candidates; independent of --limit')
    parser.add_argument('--time-limit-s', type=float,
                        help='evaluation time budget; checked between candidates (excludes startup)')
    args = parser.parse_args(argv)
    arms = args.arms.split(',')
    if not arms or len(set(arms)) != len(arms) or set(arms)-{'right', 'left'} or args.limit < 1:
        parser.error('unique --arms right,left and positive --limit required')
    try:
        pitches = tuple(float(p) for p in args.pitches.split(','))
    except ValueError:
        parser.error('--pitches must be comma-separated numbers')
    if (not pitches or len(set(pitches)) != len(pitches)
            or any(not math.isfinite(p) or not 0 <= p <= 90 for p in pitches)):
        parser.error('--pitches must be unique finite degrees in [0, 90]')
    if not 1 <= args.yaw_step_deg <= 360:
        parser.error('--yaw-step-deg must be in [1, 360]')
    if args.max_candidates is not None and args.max_candidates < 1:
        parser.error('--max-candidates must be positive')
    if args.time_limit_s is not None and (not math.isfinite(args.time_limit_s) or args.time_limit_s <= 0):
        parser.error('--time-limit-s must be finite and positive')
    check_environment()
    probe = json.loads(args.probe.read_text()); validate_probe(probe)
    args.out.mkdir(parents=True, exist_ok=False)
    from scripts.m11.reach_sweep_native import NativeSweep
    with NativeSweep(probe, args.probe, args.out, args.planner_source) as planner:
        report = sweep(probe, planner, arms, args.limit,
            lambda n, c, t, dt: print(f'{n} candidates, elapsed={t:.1f}s, candidate={dt:.2f}s, passes={dict(c)}', flush=True),
            base_grid=args.base_grid, pitches=pitches, yaw_step_deg=args.yaw_step_deg,
            max_candidates=args.max_candidates, time_limit_s=args.time_limit_s)
        report['runtime'] = planner.receipt
    write_json(args.out/'candidates.json', report)
    (args.out/'summary.md').write_text(summary(report))
    return 0


if __name__ == '__main__':
    refuse_keys_env()
    raise SystemExit(main())
