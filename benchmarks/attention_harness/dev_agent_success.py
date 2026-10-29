"""Public container success condition for the agent Eval only (user option A, 2026-10-29).

The region is the target fixture's public interior box, the same four interior
sites robocasa.utils.object_utils.obj_inside_of(partial_check=True) uses. The
native verdict itself (native_success, object_in_sink) never enters this file.
Dev never receives it: it is frozen into the private manifest at creation and
copied only into the per-version Eval workspace.
"""
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]/'openarm365_dev_v2'/'success_conditions'
FILE = 'success_condition.json'
SCHEMA = 'attentionbench.success-condition.v1'

EVAL_RULE = '''

## Container success condition
success_condition.json gives the public interior region of the target container and
the rule this task uses. For this task `passed` may be true only if ALL hold:
(a) you read the settled final object position from final_observation.json
    (perception.values.obj_pos) and show with numbers that each projection lies in
    its range, as the file's rule describes;
(b) at the end every gripper is at least gripper_far_m from the object
    (final_observation.json robot.*_eef_pos);
(c) no frame contradicts this.
If any check fails, `passed` must be false and the reason must cite the object
position and which check failed. In reason, advice and evidence claims state the
object position and whether it is inside or outside, but never quote the region's
bounds or corner coordinates.
'''


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _sub(a, b): return [x-y for x, y in zip(a, b)]
def _dot(a, b): return sum(x*y for x, y in zip(a, b))


def ranges(points):
    """Per-axis [low, high] projections, exactly obj_inside_of's checks with th=0."""
    p0, px, py, pz = points
    return [[_dot(_sub(p, p0), p0), _dot(_sub(p, p0), p)] for p in (px, py, pz)]


def check(condition, final_observation):
    """Offline rule (tests and proofs). Returns numbers, never a native field."""
    values = final_observation['perception']['values']
    obj = values['obj_pos']
    p0, px, py, pz = condition['region']['interior_sites_world_m']
    rows = []
    for (low, high), p in zip(ranges(condition['region']['interior_sites_world_m']), (px, py, pz)):
        t = _dot(_sub(p, p0), obj)
        rows.append(dict(value=t, low=low, high=high, ok=low <= t <= high))
    robot = final_observation.get('robot', {})
    far = condition['rule']['gripper_far_m']
    grippers = {k: sum((a-b)**2 for a, b in zip(v, obj))**.5 for k, v in robot.items() if k.endswith('_eef_pos')}
    inside = all(r['ok'] for r in rows)
    return dict(inside=inside, projections=rows, gripper_distance_m=grippers,
                grippers_far=all(d >= far for d in grippers.values()),
                passed=inside and all(d >= far for d in grippers.values()))


def load(task_id, seed):
    """Frozen condition for one scene, or None (task without a container rule)."""
    path = ROOT/f'{task_id}-seed{seed}.json'
    if not path.is_file(): return None
    value = json.loads(path.read_bytes())
    if value.get('schema') != SCHEMA or value['task_id'] != task_id or value['seed'] != seed:
        raise ValueError('success_condition_mismatch')
    body = {k: v for k, v in value.items() if k != 'sha256'}
    if value.get('sha256') != digest(body): raise ValueError('success_condition_digest')
    if [[round(a, 9), round(b, 9)] for a, b in ranges(value['region']['interior_sites_world_m'])] != \
            [[round(a, 9), round(b, 9)] for a, b in value['region']['projection_ranges']]:
        raise ValueError('success_condition_ranges')
    return value


def for_configs(configs):
    """Creation-time freeze: {'task:seed': condition} for every config that has one."""
    result = {}
    for key, config in configs.items():
        value = load(config['task_id'], config['seed'])
        if value: result[key] = value
    return result


def eval_view(condition):
    """What the Eval workspace receives: rule, region, reference, public fields."""
    return {k: condition[k] for k in ('schema', 'task_id', 'seed', 'task_rule', 'container', 'region', 'rule',
                                      'public_fields', 'sha256')}


NUMBER = re.compile(r'-?\d+\.\d+')


def redact_bounds(text, condition, tol=.005):
    """Dev sees Eval's verdict text: hide any number that matches a region bound or corner."""
    bounds = [v for p in condition['region']['interior_sites_world_m'] for v in p]
    bounds += [v for r in condition['region']['projection_ranges'] for v in r]
    bounds += [v for r in condition['region'].get('axis_aligned_box_world_m', {}).values() for v in r]
    def sub(m):
        x = float(m.group())
        return '[region]' if any(abs(x-b) <= tol for b in bounds) else m.group()
    return NUMBER.sub(sub, text)
