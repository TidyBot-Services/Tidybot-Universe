"""Creation-frozen public R5 predicates; no scene construction or scoring IO."""
import copy
import fnmatch
import hashlib
import json
import math
import operator
from pathlib import Path

SCHEMA='attentionbench.success-condition.fixture.v1'
RESOURCE=Path(__file__).resolve().parents[2]/'openarm365_dev_v2/success_conditions/fixture-public-v2.json'
RESOURCE_SHA256='690d038b3e7fdc584101bd46f4c93a0c81de7d0d8a646c5557d4f3d80d51fe8d'
DEV_RULE='''docs/success_condition.json gives this fixture task's public predicates, exact comparison
operators and thresholds. Apply every predicate to the settled final observation; distances
use the listed physical gripper(s), WORLD metres and exact part position, not part extent.
The file is the rule, not a verdict. Checks marked observable:false remain requirements;
the reason explains missing evidence. Do not substitute an object centre for bounding-box
containment, infer contact from proximity, or invent missing geometry.
'''
EVAL_RULE='''

## Fixture success condition
success_condition.json gives the public fixture rule, not a verdict. For this task
`passed` may be true only when ALL predicates are established by settled final public
evidence and no frame contradicts them. Read final_observation.json (or the supplied
final_observation), its robot, perception.values (objects in one-call) and fixtures, and cite numeric values,
exact operators and thresholds. Gripper names are physical: gripper-distance rules list
both left and right, and every listed gripper must satisfy them (either hand still holding
or touching fails); a press by either gripper counts. Part position is the geometry origin in WORLD
metres, not its bounding-box centre. Missing fields or checks marked observable:false
must be identified as unavailable, never silently counted as satisfied. Use false if
the evidence cannot establish every requirement; state the missing check and reason.
Use only the supplied public observations and frames, never an internal verdict.
'''


def load(task_id,seed):
    from .dev_agent_success import digest
    raw=RESOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=RESOURCE_SHA256:raise ValueError('fixture_success_resource_digest')
    resource=json.loads(raw)
    if resource['schema']!=SCHEMA:raise ValueError('fixture_success_schema')
    value=copy.deepcopy(resource['tasks'][task_id])
    value.update(schema=SCHEMA,task_id=task_id,seed=seed,combination='all',
        public_fields=['robot.robot0_left_eef_pos','robot.robot0_right_eef_pos','perception.values.*_pos','fixtures[].state',
                       'fixtures[].task_controls','fixtures[].interaction_parts[].position'])
    value['sha256']=digest(value)
    return value


def eval_view(condition):
    return {k:v for k,v in condition.items() if k!='derivation'}


def check(condition,observation):
    """Fail closed on unavailable evidence, with individual public measurements."""
    comparisons={'==':operator.eq,'>':operator.gt,'>=':operator.ge,'<':operator.lt,'<=':operator.le}
    fixtures=observation.get('fixtures',[])
    def fixture(ref):return next(f for f in fixtures if f['task_reference']==ref)
    def obj(name):return observation['perception']['values'][name+'_pos']
    def finite(value):
        if isinstance(value,bool):return value
        if not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('nonfinite')
        return value
    def vector(value):
        if not isinstance(value,(list,tuple)) or len(value)!=3:raise ValueError('position_shape')
        return [finite(v) for v in value]
    def distance(a,b,axes=3):return math.dist(vector(a)[:axes],vector(b)[:axes])
    rows=[]
    for p in condition['predicates']:
        row=dict(id=p['id'],ok=None)
        if not p['observable']:
            rows.append(dict(row,reason=p['reason']));continue
        try:
            kind=p['kind']
            if kind in ('fixture_state','not_all_joints'):
                value=fixture(p['fixture'])['state'][p['field']]
                values=list(value.values()) if isinstance(value,dict) else [value]
                if not values or any(v is None for v in values):raise ValueError('missing_state')
                if type(p['value']) is bool and any(type(v) is not bool for v in values):raise ValueError('state_type')
                ok=all(comparisons[p['op']](finite(v),p['value']) for v in values)
                if kind=='not_all_joints':ok=not ok
            elif kind=='knob_off':
                f=fixture(p['fixture']);selected=f['task_controls'][p['selection']]
                value=finite(f['state'][p['field']][selected])%(2*math.pi)
                ok=not (p['on_interval_rad'][0]<=abs(value)<=2*math.pi-.35)
            elif kind=='xy_distance':
                value=distance(*(obj(n) for n in p['objects']),axes=2)
                ok=comparisons[p['op']](value,p['threshold_m'])
            elif kind=='gripper_distance':
                target=p['target'];name=target['name']
                if target['kind']=='object':targets=[obj(name)]
                else:
                    ref,pattern=name.split(':',1) if ':' in name else (p['fixture'],name)
                    f=fixture(ref)
                    targets=[part['position'] for part in f['interaction_parts']
                             if fnmatch.fnmatchcase(part['name'],f['name']+'_'+pattern)]
                if not targets:raise ValueError('missing_target')
                value={side:[distance(observation['robot']['robot0_'+side+'_eef_pos'],v) for v in targets]
                       for side in p['grippers']}
                ok=all(comparisons[p['op']](d,p['threshold_m']) for ds in value.values() for d in ds)
            else:raise ValueError('unsupported_predicate')
            row.update(ok=ok,value=value)
        except (KeyError,TypeError,ValueError,StopIteration,OverflowError):row['reason']='required public field unavailable or invalid'
        rows.append(row)
    return dict(predicates=rows,complete=all(r['ok'] is not None for r in rows),
                passed=bool(rows) and all(r['ok'] is True for r in rows))
