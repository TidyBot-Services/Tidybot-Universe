"""Public reset condition comparison, never task scoring or Safety overrides."""
import hashlib,json,math
from pathlib import Path

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
def fingerprint(state,rounded=False):
 def convert(x):
  if isinstance(x,dict):return {k:convert(v) for k,v in sorted(x.items())}
  if isinstance(x,list):return [convert(v) for v in x]
  if isinstance(x,(float,int)) and type(x) is not bool:
   if not math.isfinite(x):raise ValueError('nonfinite public condition')
   return round(x,6) if rounded else x
  raise ValueError('numeric public state required')
 return hashlib.sha256(canonical(convert(state)).encode()).hexdigest()
def observation_state(observation):
 return {'robot':observation['robot'],'objects':observation['perception']['values']}
def compare(reference,actual,tolerance=1e-6):
 differences=[]
 def walk(a,b,path):
  if isinstance(a,dict):
   if not isinstance(b,dict) or set(a)!=set(b):differences.append({'field':path,'reason':'field_set_mismatch'});return
   for k in sorted(a):walk(a[k],b[k],path+'.'+k)
  elif isinstance(a,list):
   if not isinstance(b,list) or len(a)!=len(b):differences.append({'field':path,'reason':'shape_mismatch'});return
   if 'quat' in path and len(a)==4 and sum(x*y for x,y in zip(a,b))<0:b=[-x for x in b]
   for i,(x,y) in enumerate(zip(a,b)):walk(x,y,path+f'[{i}]')
  elif type(a) in (int,float) and type(b) in (int,float):
   error=abs(a-b)
   if not math.isfinite(error) or error>tolerance:differences.append({'field':path,'reference':a,'actual':b,'absolute_error':error})
  else:differences.append({'field':path,'reason':'nonnumeric_value'})
 walk(reference,actual,'state')
 return {'matched':not differences,'tolerance':tolerance,'differences':differences,'raw_sha256':fingerprint(actual),'normalized_sha256':fingerprint(actual,True)}
SCHEMA_V1='attentionbench.public-scene-contract.v1'
SCHEMA_V2='attentionbench.public-scene-contract.v2'
def load_contract(ref):
 p=Path(ref['uri'])
 if hashlib.sha256(p.read_bytes()).hexdigest()!=ref['sha256']:raise ValueError('scene contract digest drift')
 contract=json.loads(p.read_bytes())
 if contract['schema'] not in (SCHEMA_V1,SCHEMA_V2) or contract['tolerance']!=1e-6:raise ValueError('unregistered scene tolerance')
 return contract
def seed_entry(contract,task,seed):
 """v2 contract: one registered public-state fingerprint per (task, seed); a missing seed is refused, never skipped."""
 if contract['schema']!=SCHEMA_V2:raise ValueError('seed-keyed scene contract required')
 try:return contract['tasks'][task]['seeds'][str(seed)]
 except KeyError:raise ValueError(f'scene contract has no reference for {task} seed {seed}') from None
def validate(ref,task,state,seed=None):
 contract=load_contract(ref)
 if contract['schema']==SCHEMA_V2:
  if type(seed) is not int:raise ValueError('seed-keyed scene contract requires the configuration seed')
  expected=seed_entry(contract,task,seed)['normalized_sha256']
  actual=fingerprint(state,True);matched=actual==expected
  return {'matched':matched,'tolerance':contract['tolerance'],'comparison':'normalized_sha256',
          'differences':[] if matched else [{'field':'state','reason':'fingerprint_mismatch','reference':expected,'actual':actual}],
          'raw_sha256':fingerprint(state),'normalized_sha256':actual,'contract':ref,'task_id':task,'seed':seed,
          'status':'verified' if matched else 'scene_contract_mismatch'}
 result=compare(contract['tasks'][task]['state'],state,contract['tolerance'])
 return {**result,'contract':ref,'task_id':task,'status':'verified' if result['matched'] else 'scene_contract_mismatch'}
def fixture_state(env):
    """Actual world body poses, including Floor/Wall without a yaw property."""
    import numpy as np
    fixtures={}
    for name,fixture in sorted(env.fixtures.items()):
        body=env.sim.model.body_name2id(fixture.root_body)
        fixtures[name+'_pos']=np.asarray(env.sim.data.body_xpos[body],float).tolist()
        quaternion=env.sim.data.body_xquat[body]  # MuJoCo wxyz -> public xyzw.
        fixtures[name+'_quat']=[float(v) for v in [*quaternion[1:],quaternion[0]]]
    return fixtures

def capture_state(env):
    import numpy as np
    from scipy.spatial.transform import Rotation
    from .environment import tcp_pose,joint_address,PHYSICAL_TO_NATIVE
    robot={};r=env.robots[0]
    for side,native in PHYSICAL_TO_NATIVE.items():
        t=tcp_pose(env,side)
        robot[f'robot0_{side}_eef_pos']=t[:3,3].tolist()
        robot[f'robot0_{side}_eef_quat']=Rotation.from_matrix(t[:3,:3]).as_quat().tolist()
        robot[f'robot0_{side}_joint_pos']=[float(env.sim.data.qpos[joint_address(env.sim,f'openarm_{side}_joint{i}')]) for i in range(1,8)]
        robot[f'robot0_{side}_gripper_qpos']=env.sim.data.qpos[r._ref_gripper_joint_pos_indexes[native]].tolist()
    objects={}
    for name,bid in sorted(env.obj_body_id.items()):
        objects[name+'_pos']=env.sim.data.body_xpos[bid].tolist()
        q=env.sim.data.body_xquat[bid];objects[name+'_quat']=[float(x) for x in [*q[1:],q[0]]]
    fixtures=fixture_state(env)
    return {'robot':robot,'objects':objects,'fixtures':fixtures}
