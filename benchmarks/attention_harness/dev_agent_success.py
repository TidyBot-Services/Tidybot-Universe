"""Public container success condition for the agent run (user option A, then step 6, 2026-10-29).

The region is the target fixture's public interior box, the same four interior
sites robocasa.utils.object_utils.obj_inside_of(partial_check=True) uses. The
native verdict itself (native_success, object_in_sink) never enters this file.
It is frozen into the private manifest at creation and copied into the Eval
workspace. Manifests with success_condition_dev_visible also publish it to Dev
as docs/success_condition.json; earlier ones keep it Eval-only and redact
Eval's Dev-facing text.
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


DEV_RULE = '''docs/success_condition.json is the task's public success rule: the final settled obj_pos
(from find_objects or observe) must be inside its region, and both grippers released and at least
gripper_far_m from the object. It is the rule, not a verdict.
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
    from . import dev_fixture_success as fixture
    if condition.get('schema') == fixture.SCHEMA:return fixture.check(condition, final_observation)
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
        from curobo_v2.fixture_interaction import config_enabled
        from . import dev_fixture_success as fixture
        value = (fixture.load if config_enabled(config) else load)(config['task_id'], config['seed'])
        if value: result[key] = value
    return result


def eval_view(condition):
    """What the Eval workspace receives: rule, region, reference, public fields."""
    from . import dev_fixture_success as fixture
    if condition.get('schema') == fixture.SCHEMA:return fixture.eval_view(condition)
    return {k: condition[k] for k in ('schema', 'task_id', 'seed', 'task_rule', 'container', 'region', 'rule',
                                      'public_fields', 'sha256')}


NUMBER = re.compile(r'-?\d+\.\d+')
FILE_REF = re.compile(r'success_condition\.json(?::[^;|\n]*)?')
FIELD_REF = re.compile(r'\b(?:region|rule)\.[A-Za-z_][A-Za-z_.]*')


def hidden_numbers(condition, final_observation=None):
    """Every number Dev must not learn: region corners and bounds, the clearance
    threshold, and this execution's projections onto the region axes."""
    region = condition['region']
    values = [v for p in region['interior_sites_world_m'] for v in p]
    values += [v for r in region['projection_ranges'] for v in r]
    values += [v for r in (region.get('axis_aligned_box_world_m') or {}).values() for v in r]
    values.append(condition['rule']['gripper_far_m'])
    if final_observation:
        try:values += [row['value'] for row in check(condition, final_observation)['projections']]
        except (KeyError, TypeError, ValueError):pass
    return values


def redact(text, condition, final_observation=None, tol=.005):
    """Dev sees Eval's verdict text: keep the conclusion and the object position, hide
    the rule's numbers, its file and its field names."""
    hidden = hidden_numbers(condition, final_observation)
    text = FIELD_REF.sub('the success rule', FILE_REF.sub('Eval-only success rule', text))
    def sub(m):
        x = float(m.group())
        return '[region]' if any(abs(x-b) <= tol for b in hidden) else m.group()
    return NUMBER.sub(sub, text)


def dev_view(verdict, condition, final_observation=None):
    hide = lambda text: redact(text, condition, final_observation)
    return dict(verdict, reason=hide(verdict['reason']), advice=hide(verdict['advice']),
                evidence=[dict(r, claim=hide(r['claim']), source=hide(r['source'])) for r in verdict['evidence']])
