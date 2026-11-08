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
REGISTERED_CONTRACT=Path(__file__).resolve().parent/'scene_contracts'/'dev-seeds-101-103-v2-20261019-kp1500.json'
REGISTERED_SHA256="bb28cd3eb1334bf5baf7dd428741ed8640adfd62ecb31e6cf0379d2d1670c716"
REGISTERED_HOST_VARIANTS=REGISTERED_CONTRACT.parent/'host-variants-20261109.json'
REGISTERED_HOST_VARIANTS_SHA256='9fa774f30b523fac866e74628dd081244d3de06a004c97faffbfac2ecbe9fb65'
def registered_contract_ref():
 """Reference to the contract registered for development experiments. Pinned by digest in code: editing the file without
 re-registering it here fails closed instead of silently changing what counts as the same scene."""
 digest=hashlib.sha256(REGISTERED_CONTRACT.read_bytes()).hexdigest()
 if digest!=REGISTERED_SHA256:raise ValueError('registered scene contract changed without re-registration')
 return {'uri':str(REGISTERED_CONTRACT),'sha256':digest}
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
def accepted_fingerprints(ref,task,seed)->list[tuple[str,str]]:
 accepted=[('reference',seed_entry(load_contract(ref),task,seed)['normalized_sha256'])]
 if ref!=registered_contract_ref() or ref['sha256']!=REGISTERED_SHA256:return accepted
 data=REGISTERED_HOST_VARIANTS.read_bytes()
 if hashlib.sha256(data).hexdigest()!=REGISTERED_HOST_VARIANTS_SHA256:raise ValueError('registered host variants digest drift')
 variants=json.loads(data)
 if variants['schema']!='attentionbench.public-scene-contract-host-variants.v1':raise ValueError('unregistered host variants schema')
 if variants['base_contract']['sha256']!=REGISTERED_SHA256:raise ValueError('host variants base contract mismatch')
 for variant in variants['variants']:
  entry=variant['tasks'].get(task,{}).get('seeds',{}).get(str(seed))
  if entry is not None:accepted.append((variant['id'],entry['normalized_sha256']))
 return accepted
def host_info():
 """Diagnostic metadata only; host identity never selects an accepted fingerprint."""
 import platform
 from importlib.metadata import version
 cpuinfo=Path('/proc/cpuinfo').read_text()
 cpu_model=next((line.split(':',1)[1].strip() for line in cpuinfo.splitlines() if line.startswith('model name')),platform.processor())
 return {'cpu_model':cpu_model,'glibc':platform.libc_ver()[1],'python':platform.python_version(),
         'mujoco':version('mujoco'),'numpy':version('numpy')}
def validate(ref,task,state,seed=None):
 contract=load_contract(ref)
 if contract['schema']==SCHEMA_V2:
  if type(seed) is not int:raise ValueError('seed-keyed scene contract requires the configuration seed')
  accepted=accepted_fingerprints(ref,task,seed);expected=accepted[0][1]
  actual=fingerprint(state,True);matched=actual in {sha for _,sha in accepted}
  matched_variant=next((variant for variant,sha in accepted if sha==actual),None)
  accepted=[{'variant':variant,'normalized_sha256':sha} for variant,sha in accepted]
  return {'matched':matched,'tolerance':contract['tolerance'],'comparison':'normalized_sha256',
          'differences':[] if matched else [{'field':'state','reason':'fingerprint_mismatch','reference':expected,'actual':actual,'accepted':accepted}],
          'raw_sha256':fingerprint(state),'normalized_sha256':actual,'contract':ref,'task_id':task,'seed':seed,
          'matched_variant':matched_variant,'accepted':accepted,
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
