"""Freeze the public container success condition for one scene from an oracle-reset fixture.

Usage: python -m benchmarks.attention_harness.diagnostics.derive_success_condition <fixture.json> <task_id> <seed>
The fixture's diagnostic_truth.sink_int_sites_world_m are the fixture.get_int_sites(relative=False)
points that robocasa.utils.object_utils.obj_inside_of iterates; nothing native is copied.
"""
import hashlib
import json
from pathlib import Path
import sys
from ..dev_agent_success import ROOT, SCHEMA, digest, ranges


def derive(fixture_path, task_id, seed):
    raw = Path(fixture_path).read_bytes()
    fixture = json.loads(raw)
    truth = fixture['diagnostic_truth']
    if fixture['seed'] != seed: raise ValueError('seed mismatch')
    sites = truth['sink_int_sites_world_m']
    if len(sites) != 1: raise ValueError('expected one interior region')
    (name, points), = sites.items()
    p0, px, py, pz = points
    axes = [[b-a for a, b in zip(p0, p)] for p in (px, py, pz)]
    # Axis-aligned check: each edge has one dominant world component.
    aligned = all(sorted(abs(c) for c in axis)[1] < 1e-6 for axis in axes)
    box = None
    if aligned:
        hi = [p0[i]+sum(a[i] for a in axes) for i in range(3)]   # corner opposite p0
        box = {k: [min(p0[i], hi[i]), max(p0[i], hi[i])] for i, k in enumerate('xyz')}
    body = dict(
        schema=SCHEMA, task_id=task_id, seed=seed,
        task_rule='The object counts as placed in the sink when its body center is inside the sink basin '
                  'interior box and both grippers are released and at least 0.25 m away from it.',
        container=dict(fixture=truth['sink_fixture_name'], interior_region=name,
                       public_scene_check=dict(field='perception.values.sink_pos', value=truth['sink_pos_world_m'])),
        region=dict(frame='world', units='m', interior_sites_world_m=points, projection_ranges=ranges(points),
                    axis_aligned_box_world_m=box,
                    description='p0 plus the three edge points px, py, pz. A point o is inside when, for each edge '
                                'point p in (px, py, pz), dot(p-p0, p0) <= dot(p-p0, o) <= dot(p-p0, p); '
                                'projection_ranges lists those [low, high] pairs. When axis_aligned_box_world_m is '
                                'present it is the same box written as x/y/z ranges.'),
        rule=dict(object_reference='object body center (obj_pos), not its bounding box', tolerance_m=0.0,
                  gripper_far_m=0.25,
                  gripper_reference='robot.*_eef_pos (public end-effector position; the upstream check uses the '
                                    'gripper site, so treat distances within 2 cm of 0.25 m as uncertain)'),
        public_fields=dict(final_object_position='final_observation.json perception.values.obj_pos',
                           final_gripper_positions='final_observation.json robot.robot0_left_eef_pos / robot0_right_eef_pos',
                           trajectory='state_log.json'),
        derivation=dict(source='fixture diagnostic_truth.sink_int_sites_world_m (fixture.get_int_sites(relative=False))',
                        fixture_sha256=hashlib.sha256(raw).hexdigest(),
                        upstream='robocasa.utils.object_utils.obj_inside_of(partial_check=True): object center, th=0',
                        robocasa_commit=fixture.get('identity', {}).get('robocasa', {}).get('commit')))
    return dict(body, sha256=digest(body))


if __name__ == '__main__':
    value = derive(sys.argv[1], sys.argv[2], int(sys.argv[3]))
    out = ROOT/f'{value["task_id"]}-seed{value["seed"]}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(value, indent=2, sort_keys=True)+'\n')
    print(out, value['sha256'])
