#!/usr/bin/env python3
"""Generate a bounded diagnostic reference; robot feasibility remains unproven."""
import argparse
import json
import math
from pathlib import Path
from string import Template
import sys
ROOT=Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.m11.cabinet_reference_template import SOURCE
from scripts.m11_cabinet_probe import TASK


def generate(probe, *, arm='left', base_dx=0., base_dy=0., base_dyaw=0.,
             approach_height=.10, entry_depth=.15, clearance=.03, front_offset=.10, region=None):
    if probe['task_id']!=TASK or probe['seed'] not in range(101,126):
        raise ValueError('development cabinet probe required')
    if probe.get('same_normalized_public_state') is not True:raise ValueError('unstable probe resets')
    if arm not in ('left','right'):raise ValueError('physical arm must be left/right')
    numbers=[base_dx,base_dy,base_dyaw,approach_height,entry_depth,clearance,front_offset]
    if not all(math.isfinite(v) for v in numbers):raise ValueError('finite parameters required')
    if min(approach_height,entry_depth,clearance,front_offset)<=0:raise ValueError('positive clearances/depth required')
    scene=probe['resets'][0]['scene']; target=scene['target']; regions=scene['cabinet']['interior_regions']
    region=region or min(regions,key=lambda n:regions[n]['aabb_world_m'][0][2])
    selected=regions[region]
    if entry_depth>=selected['depth_m']:raise ValueError('entry depth must stay inside native region')
    lo,hi=target['aabb_world_m']; center=[(a+b)/2 for a,b in zip(lo,hi)]
    grasp=[center[0],center[1],center[2]+.005]
    approach=[center[0],center[1],hi[2]+approach_height]
    shelf=selected['shelf_front_world_m']; inward=selected['inward_world']
    z=shelf[2]+(hi[2]-lo[2])/2+.005+clearance
    if z+(hi[2]-lo[2])/2 >= selected['aabb_world_m'][1][2]:
        raise ValueError('object plus clearance does not fit selected shelf height')
    front=[shelf[0]-inward[0]*front_offset,shelf[1]-inward[1]*front_offset,z]
    place=[shelf[0]+inward[0]*entry_depth,shelf[1]+inward[1]*entry_depth,z]
    safe_z=max(approach[2],z+approach_height)
    lift=[grasp[0],grasp[1],safe_z];safe_front=[front[0],front[1],safe_z]
    # Downward TCP; physical finger closing axis is local Y.
    yaw=target['grasp_axis_world_xy_rad']-math.pi/2
    quat=[math.cos(yaw/2),math.sin(yaw/2),0.,0.]
    steps=[]
    count=math.ceil(math.hypot(base_dx,base_dy)/.19)
    if count:steps.extend([[base_dx/count,base_dy/count,0.] for _ in range(count)])
    turns=math.ceil(abs(base_dyaw)/.29)
    if turns:steps.extend([[0.,0.,base_dyaw/turns] for _ in range(turns)])
    if len(steps)>40:raise ValueError('too many base moves for diagnostic template')
    values=dict(object_name=repr(target['public_name']),grasp=grasp,approach=approach,lift=lift,
        front=front,place=place,safe_front=safe_front,grasp_quat=quat,base_steps=steps)
    # Explicit names keep policy imports/method calls within the Dev validator.
    source=Template(SOURCE).substitute(arm=arm,arm_arm=arm+'_arm',arm_gripper=arm+'_gripper',
        **{key:str(value) for key,value in values.items()})
    from openarm365_dev.policy_validation import validate_generated_policy
    validate_generated_policy(source)
    return source


def generate_candidate(report, rank=1, probe=None):
    """Ranks are one-based; failed candidates remain explicitly diagnostic."""
    from scripts.m11.cabinet_candidate_template import SOURCE as CANDIDATE_SOURCE
    if (report.get('schema') != 'attentionbench.m11-reach-sweep.v1'
            or report.get('diagnostic_only') is not True or report.get('task_id') != TASK
            or report.get('seed') not in range(101, 126)):
        raise ValueError('T1b development candidate report required')
    if type(rank) is not int or not 1 <= rank <= len(report['candidates']):
        raise ValueError('rank is one-based and must exist in candidates.json')
    if probe is not None:
        from scripts.m11.reach_sweep import validate_probe
        validate_probe(probe)
        if (probe['seed'] != report['seed'] or probe['resets'][0]['normalized_sha256'] != report['probe_normalized_sha256']):
            raise ValueError('candidate/probe seed or fingerprint mismatch')
    row = report['candidates'][rank-1]
    arm = row['arm']
    if arm not in ('left', 'right') or row['order'] not in ('base_first', 'grasp_first'):
        raise ValueError('invalid candidate arm/order')
    def vector(value, length):
        if (not isinstance(value, list) or len(value) != length or
                any(type(v) not in (int, float) or not math.isfinite(v) for v in value)):
            raise ValueError('finite candidate vector required')
        return value
    quat = vector(row['grasp_quat_xyzw'], 4)
    if abs(sum(v*v for v in quat)-1.) > 1e-6:
        raise ValueError('unit grasp quaternion required')
    points = {k: vector(row['waypoints'][k], 3) for k in ('pregrasp', 'grasp', 'lift', 'carry', 'place')}
    if points['grasp'][2] >= points['pregrasp'][2] or points['lift'][2] < points['grasp'][2]+.10-1e-9:
        raise ValueError('candidate requires descent and at least 0.10 m lift')
    moves = [vector(v, 3) for v in row['base_moves']]
    if len(moves) > 40 or any(math.hypot(*v[:2]) > .19+1e-10 or abs(v[2]) > .29+1e-10 for v in moves):
        raise ValueError('candidate base step exceeds diagnostic bound')
    other = 'right' if arm == 'left' else 'left'
    if type(row['tuck_other_arm']) is not bool or row['tuck_arm'] != other:
        raise ValueError('candidate must tuck the other physical arm')
    tuck_q = vector(row['tuck_q'], 7) if row['tuck_other_arm'] else [0.]*7
    values = dict(candidate_id=int(row['id']), seed=report['seed'], complete=bool(row['complete']),
        arm=arm, arm_arm=arm+'_arm', arm_gripper=arm+'_gripper', grasp_quat=quat,
        tuck_enabled=row['tuck_other_arm'], tuck_index=0 if other == 'left' else 7, tuck_q=tuck_q,
        base_before=moves if row['order'] == 'base_first' else [],
        base_after=moves if row['order'] == 'grasp_first' else [], **points)
    source = Template(CANDIDATE_SOURCE).substitute(**{k: str(v) for k, v in values.items()})
    from openarm365_dev.policy_validation import validate_generated_policy
    validate_generated_policy(source)
    return source


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--probe',type=Path,help='required unless --candidate is supplied')
    p.add_argument('--candidate',type=Path)
    p.add_argument('--rank',type=int,default=1,help='one-based candidate rank (default 1)')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--arm',choices=('left','right'),default='left')
    for key,default in [('base-dx',0.),('base-dy',0.),('base-dyaw',0.),('approach-height',.10),
                        ('entry-depth',.15),('clearance',.03),('front-offset',.10)]:
        p.add_argument('--'+key,type=float,default=default)
    p.add_argument('--region')
    args=p.parse_args(argv)
    if args.candidate:
        source=generate_candidate(json.loads(args.candidate.read_text()),args.rank,
            json.loads(args.probe.read_text()) if args.probe else None)
    else:
        if args.probe is None:p.error('--probe or --candidate required')
        if args.rank != 1:p.error('--rank requires --candidate')
        params=vars(args).copy()
        for key in ('probe','out','candidate','rank'):params.pop(key)
        source=generate(json.loads(args.probe.read_text()),**params)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(source)
    return 0


if __name__=='__main__':raise SystemExit(main())
