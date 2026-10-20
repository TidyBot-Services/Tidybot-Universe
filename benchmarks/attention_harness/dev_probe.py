"""Pre-reserved real preflight in the matrix goal, excluded from comparison cells."""
import copy,hashlib,json,time
from pathlib import Path
from .dev_ledger import RESOURCES,encoded,GOAL
from .dev_experiment import snapshot,verify_manifest,cell_key,acquire
from .dev_loop import process_identity
from .dev_v2_loop import provider_call,execute,immutable,job_for
from .core.dev_store import DevConflict
from openarm365_dev_v2.diag_budget import task_limits
def probe_budget(budget_tier='dev',model_reserve_seconds=None):
 limits=task_limits(budget_tier)
 return dict(eval_calls=0,dev_calls=1,advisor_calls=0,provider_tokens=10000,input_tokens=2000,output_tokens=256,executions=2,sdk_calls=2*limits['sdk_calls'],control_steps=2*limits['control_steps'],help_credits=0,help_count=0,help_wait_seconds=0,model_seconds=model_reserve_seconds if model_reserve_seconds is not None else (360 if budget_tier=='dev' else 120),execution_seconds=2*__import__('math').ceil(540*limits['attempt_seconds']/480))
PROBE_BUDGET=probe_budget()
SOURCE='from robot_sdk import sensors\nobservation = sensors.get_observation()\n'
def create_probe(store,parent):
 e=snapshot(store,parent);m=verify_manifest(e)
 if 'goal_id' not in m:raise ValueError('probe requires isolated configurable goal')
 eid='devexp-'+hashlib.sha256(('probe|'+parent).encode()).hexdigest()[:24]
 with store.tx() as db:
  row=db.execute('SELECT payload FROM v2_experiments WHERE id=?',(eid,)).fetchone()
 if row:return snapshot(store,eid)
 directory=Path(e['artifact_dir']).parent/eid;directory.mkdir(parents=True,exist_ok=True)
 tasks=m['selection']['tasks'];seed=m['selection']['seeds'][0]
 tier=m.get('budget_tier','legacy');reserve=probe_budget(tier,m.get('model_reserve_seconds',120));limits=task_limits(tier)
 if len(tasks)!=2:raise ValueError('probe preflight uses two registered tasks')
 cells=[dict(key=cell_key(eid,t,seed,'autonomous'),task_id=t,seed=seed,policy_id='autonomous') for t in tasks]
 pm=copy.deepcopy(m);pm.update(experiment_id=eid,cells=cells,schedule=[],report_scope='probe only, excluded from comparison')
 pm['selection'].update(mode='probe',policies=['autonomous'],seeds=[seed]);pm['selection']['request_key']='probe-'+m['selection']['request_key']
 ref=immutable(directory/'manifest.json',pm)
 pe=dict(experiment_id=eid,goal_id=m['goal_id'],selection=pm['selection'],manifest=ref,artifact_dir=str(directory),state='planned',cancelled=False,owner=None,created_at=time.time(),formal_eligible=False)
 with store.tx() as db:
  total={k:0 for k in RESOURCES}
  for cp,ep in db.execute('SELECT c.payload,e.payload FROM v2_cells c JOIN v2_experiments e ON c.exp=e.id'):
   if json.loads(ep).get('goal_id',GOAL)==m['goal_id']:
    for k,v in json.loads(cp)['budget'].items():total[k]+=v
  if any(total[k]+reserve.get(k,0)>m['goal_limits'].get(k,0) for k in RESOURCES):raise ValueError('probe allocation exceeds goal limits')
  store.put(db,'experiments',eid,pe)
  for i,row in enumerate(cells):
   b={k:0 for k in RESOURCES};b.update(executions=1,sdk_calls=limits['sdk_calls'],control_steps=limits['control_steps'],execution_seconds=reserve['execution_seconds']//2)
   if i==0:b.update(dev_calls=1,provider_tokens=10000,input_tokens=2000,output_tokens=256,model_seconds=reserve['model_seconds'])
   payload=dict(row,experiment_id=eid,state='planned',owner=None,mode='probe',mixed_policy=False,experiment_eligible=False,policy_segments=['autonomous'],budget=b,used={k:0 for k in RESOURCES},created_at=None,formal_eligible=False,probe_max_tokens=256,runtime={k:m[k] for k in ['model_reserve_seconds','startup_cleanup_seconds','max_input_bytes_per_request','source_size_limit_bytes','public_failure_rule']})
   db.execute('INSERT INTO v2_cells VALUES (?,?,?)',(row['key'],eid,encoded(payload)))
 return snapshot(store,eid)
def run_probe(store,parent,client=None,runner_factory=None):
 if snapshot(store,parent)['selection']['mode']=='diagnostic':
  from .dev_scene_diag import probe
  return probe(store,parent,client,runner_factory)
 from openarm365_dev.policy_validation import validate_generated_policy
 validate_generated_policy(SOURCE)
 pe=create_probe(store,parent);m=verify_manifest(pe);owner=process_identity();acquire(store,pe['experiment_id'],owner)
 try:
  for i,c in enumerate(pe['cells']):
   if c['state']=='completed':continue
   key=c['key'];root=Path(pe['artifact_dir'])/'cells'/key;root.mkdir(parents=True,exist_ok=True)
   config=m['configs'][f"{c['task_id']}:{c['seed']}"]
   store.update_cell(key,state='running',owner=owner,created_at=time.time(),artifact_dir=str(root))
   immutable(root/'config.json',config);d=root/'v1';d.mkdir(exist_ok=True)
   if i==0:provider_call(store,key,1,'model',[{'role':'system','content':'Return OK. This is a minimal connectivity probe.'},{'role':'user','content':'{"request":"Reply OK"}'}],owner,d,client)
   record={'source':immutable(d/'policy.py',SOURCE)}
   existing=job_for(store.cell(key),1,'execute')
   if not existing:execute(store,key,1,record,config,owner,d,runner_factory)
   elif existing['state']!='settled':raise DevConflict('unresolved probe execution')
   settled=job_for(store.cell(key),1,'execute')
   if settled['receipt']['execution_status']!='completed':raise DevConflict('probe failed with known public execution status')
   store.update_cell(key,state='completed',owner=None)
  with store.tx() as db:
   e=store.get(db,'experiments',pe['experiment_id']);e.update(state='completed',owner=None);store.put(db,'experiments',pe['experiment_id'],e)
 except BaseException as exc:
  with store.tx() as db:
   e=store.get(db,'experiments',pe['experiment_id']);e.update(state='needs_review',owner=None,reason=f'{type(exc).__name__}: {exc}');store.put(db,'experiments',pe['experiment_id'],e)
  raise
 return snapshot(store,pe['experiment_id'])
