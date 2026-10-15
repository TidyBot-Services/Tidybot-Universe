"""Meter native public resets; prepare local worker contracts before claims."""
import json,math,time,hashlib,tempfile
from pathlib import Path
from .dev_experiment import snapshot,verify_manifest,acquire
from .dev_loop import process_identity
from .dev_v2_loop import immutable,job_for,provider_call,execute
from .core.dev_store import DevConflict,DevBudgetExhausted
from openarm365_dev_v2.owner import NativeOwnerV2
from openarm365_dev_v2.scene_contract import observation_state,fingerprint
from openarm365_dev_v2.dev_config import SIM_PYTHON,validate_dev_config
RESET_RESERVATION={'executions':1,'execution_seconds':540,'sdk_calls':80,'control_steps':2400}

def deadline_check(store,goal_id,outer_deadline_epoch,required_seconds=600):
 if outer_deadline_epoch is not None and (type(outer_deadline_epoch) not in (int,float) or not math.isfinite(outer_deadline_epoch)):
  raise ValueError('finite absolute deadline required')
 available=store.goal(goal_id)['wall_remaining']
 if outer_deadline_epoch is not None:available=min(available,outer_deadline_epoch-time.time())
 if available<required_seconds:raise DevBudgetExhausted('absolute/goal deadline insufficient before diagnostic dispatch')
 return available

def validate_recording_context(context):
 from .dev_help import public_screen
 if not isinstance(context,dict):raise ValueError('recording context required')
 for name in ['run_id','attempt_id','camera_name','origin','dev_version']:
  if not isinstance(context.get(name),str) or not context[name]:raise ValueError('invalid recording context: '+name)
 if context['camera_name']!='robot0_agentview_left':raise ValueError('unregistered diagnostic camera')
 value=context.get('started_monotonic')
 if type(value) not in (int,float) or not math.isfinite(value) or value<=0:raise ValueError('invalid recording clock')
 public_screen(context)
 return context

def prepare_worker_inputs(directory,config,*,run_id,attempt_id,version,deadline,source_root,cancel_check,owner_factory=None,recording_context=None):
 """Pure local preparation; no reservation, start, Popen, RPC or environment."""
 validate_dev_config(config,expected_source_root=source_root)
 if type(deadline) not in (int,float) or not math.isfinite(deadline) or deadline<=time.monotonic():raise ValueError('invalid worker deadline')
 context=recording_context if recording_context is not None else dict(run_id=run_id,attempt_id=attempt_id,camera_name='robot0_agentview_left',started_monotonic=time.monotonic(),origin='native_pipe://owned-openarm365-v2-diagnostic',dev_version=str(version))
 validate_recording_context(context)
 from .robocasa365.recording import StepRecorder
 with tempfile.TemporaryDirectory(prefix='attentionbench-contract-') as temporary:StepRecorder(Path(temporary),context)
 directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
 cfg=immutable(directory/'config.json',config)
 recording=immutable(directory/'recording_context.json',context)
 native=(owner_factory or NativeOwnerV2)(python=SIM_PYTHON,config=Path(cfg['uri']),evidence=directory,deadline=deadline,source_root=source_root,cancel_check=cancel_check)
 return cfg,recording,native

def file_ref(path):
 path=Path(path)
 return {'uri':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def reset_rpc_receipt(directory,job,payload,config_ref,version,*,simulated=False):
 journal=Path(directory)/'rpc-receipts.jsonl';matches=[]
 if journal.exists():
  for raw in journal.read_bytes().splitlines(keepends=True):
   row=json.loads(raw)
   if row['request']['request_id']==job['job_id']:matches.append((raw,row))
 if simulated and not matches:
  row={'request':{'request_id':job['job_id']},'response':{'request_id':job['job_id'],'ok':True,'sequence':version,'start_sequence':version-1,'value':payload}}
  raw=json.dumps(row,sort_keys=True).encode();matches=[(raw,row)]
 if len(matches)!=1:raise DevConflict('independent durable reset RPC receipt missing/ambiguous')
 raw,row=matches[0];response=row['response']
 if response.get('ok') is not True or response.get('value')!=payload:raise DevConflict('reset RPC response differs from durable receipt')
 return immutable(Path(directory)/f'reset-rpc-{version}.json',dict(schema='attentionbench.reset-rpc.v1',simulated=simulated,job_id=job['job_id'],request_id=response['request_id'],sequence=response['sequence'],start_sequence=response['start_sequence'],scene_state=payload['scene_state'],observation=payload['initial_observation'],rng_events=payload.get('rng_events',[]),source_line_sha256=hashlib.sha256(raw).hexdigest(),config_sha256=config_ref['sha256'],journal=file_ref(journal) if journal.exists() else None))

def reset_group(store,experiment,key,config,source_root,directory,versions,owner_factory=None,*,outer_deadline_epoch=None):
 """First version starts a fresh worker; later versions reset that SAME worker."""
 e=snapshot(store,experiment);m=verify_manifest(e)
 if e['selection']['mode']!='diagnostic':raise ValueError('registered diagnostic experiment required')
 if not versions or len(set(versions))!=len(versions) or any(type(v) is not int or not 1<=v<=8 for v in versions):raise ValueError('diagnostic reset versions 1–8 required; v9 reserved for probe')
 deadline_check(store,m['goal_id'],outer_deadline_epoch)
 c=store.cell(key)
 if any(j['version'] in versions for j in c['jobs']):raise DevConflict('diagnostic identity used; no replay')
 # Capacity of the whole group is checked before constructing or claiming.
 store.reserve_check(key,{k:v*len(versions) for k,v in RESET_RESERVATION.items()})
 owner_id=process_identity();directory=Path(directory);native=None;rows=[];acquired=None
 lifecycle_deadline=time.monotonic()+480*len(versions)
 operation_deadline=[time.monotonic()+480]
 def cancelled():
  return time.monotonic()>=operation_deadline[0] or store.goal(m['goal_id'])['wall_remaining']<=0 or (outer_deadline_epoch is not None and time.time()>=outer_deadline_epoch) or snapshot(store,experiment)['cancelled']
 try:
  # Acquiring first quarantines old claims before any owner is constructed.
  acquired=acquire(store,experiment,owner_id)
  if acquired['state']=='needs_review':raise DevConflict('diagnostic goal quarantined')
  cfg,recording,native=prepare_worker_inputs(directory,config,run_id='diagnostic:'+key,attempt_id=f'reset:{key}:{versions[0]}',version=versions[0],deadline=lifecycle_deadline,source_root=source_root,cancel_check=cancelled,owner_factory=owner_factory)
  for i,v in enumerate(versions):
   deadline_check(store,m['goal_id'],outer_deadline_epoch)
   operation_deadline[0]=time.monotonic()+480
   if i:native.deadline=min(lifecycle_deadline,operation_deadline[0])
   store.update_cell(key,state='running',owner=owner_id)
   store.reserve_check(key,RESET_RESERVATION)
   j=store.reserve(key,v,'execute',RESET_RESERVATION);store.claim(j['job_id'],owner_id);begun=time.monotonic();payload=None;authority=None
   try:
    if i==0:
     ready=native.start();payload=ready
     authority=immutable(directory/f'ready-{v}.json',ready)
    else:
     payload=native.rpc('owner','reset',request_id=j['job_id'])
     authority=reset_rpc_receipt(directory,j,payload,cfg,v,simulated=owner_factory is not None)
    obs=payload['initial_observation'];state=payload.get('scene_state') or observation_state(obs)
    owner_identity=process_identity(native.process.pid) if native.process and native.process.poll() is None else None
    cleanup=native.stop() if i==len(versions)-1 else None
    startup=file_ref(directory/'reset_receipt.json') if i==0 and (directory/'reset_receipt.json').exists() else None
    row=dict(schema='attentionbench.real-reset.v2',real=owner_factory is None,job_id=j['job_id'],version=v,source_root=str(source_root),config=cfg,recording_context=recording,owner_identity=owner_identity,observation=obs,state=state,raw_sha256=fingerprint(state),normalized_sha256=fingerprint(state,True),cleanup=cleanup,worker_launch=file_ref(directory/'worker-launch.json') if (directory/'worker-launch.json').exists() else None,startup_receipt=startup,reset_rpc_receipt=authority if i else None,ready_receipt=authority if not i else None,rng_events=payload.get('rng_events',json.loads((directory/'reset_receipt.json').read_bytes()).get('rng_events',[]) if startup else []),seed=config['seed'],scene=[config['layout_id'],config['style_id']],elapsed_seconds=time.monotonic()-begun)
    ref=immutable(directory/f'reset-{v}.json',row)
    store.settle(j['job_id'],{'executions':1,'execution_seconds':row['elapsed_seconds'],'sdk_calls':0,'control_steps':0},{'diagnostic_reset':ref,'source_epoch':str(source_root),'config':cfg})
    rows.append(ref)
   except BaseException as exc:
    current=job_for(store.cell(key),v,'execute')
    if current['state']!='settled':
     # Fatal startup and rejected reset RPCs are returned authoritative failures.
     # A late local write error also cannot erase a response already received.
     if payload is None:
      response=getattr(native,'startup_response',None) if not i else next((r for r in getattr(native,'ledger',[]) if r.get('request_id')==j['job_id']),None)
      if response is not None:payload=response
     if payload is not None and authority is None:
      authority=immutable(directory/f'returned-response-{v}.json',payload)
     # A received, durable response is known even if later local evidence/cleanup fails.
     if payload is not None and authority is not None:
      row=dict(schema='attentionbench.real-reset.v2',real=owner_factory is None,job_id=j['job_id'],version=v,state=payload.get('scene_state'),observation=payload.get('initial_observation'),elapsed_seconds=time.monotonic()-begun,local_error=f'{type(exc).__name__}: {exc}',authoritative_response=authority)
      ref=immutable(directory/f'reset-{v}-local-error.json',row)
      store.settle(j['job_id'],{'executions':1,'execution_seconds':row['elapsed_seconds'],'sdk_calls':0,'control_steps':0},{'diagnostic_reset':ref,'source_epoch':str(source_root),'config':cfg})
      store.update_cell(key,state='failed',reason='known_reset_local_evidence_failure')
     else:store.unknown(j['job_id'],f'{type(exc).__name__}: {exc}',{'config':cfg})
    raise
  store.update_cell(key,state='planned',owner=None)
  return rows
 finally:
  if native:native.stop()
  if acquired:
   store.update_cell(key,owner=None)
   with store.tx() as db:
    current=store.get(db,'experiments',experiment);current['owner']=None
    current['state']='needs_review' if any(s=='needs_review' for s,j in store.goal_jobs(db,m['goal_id'])) else 'planned';store.put(db,'experiments',experiment,current)

def probe(store,parent,client=None,runner_factory=None,*,outer_deadline_epoch=None,configs=None):
 """Reuse diagnostic allocations: one model connectivity call and one v9 execute per task."""
 from .dev_probe import SOURCE
 e=snapshot(store,parent);m=verify_manifest(e)
 if e['selection']['mode']!='diagnostic':raise ValueError('registered diagnostic probe required')
 if configs is not None:
  if set(configs)!=set(m['configs']):raise ValueError('diagnostic epoch config denominator differs')
  for name,cfg in configs.items():
   validate_dev_config(cfg)
   if {k:v for k,v in cfg.items() if k!='dev_lock'}!={k:v for k,v in m['configs'][name].items() if k!='dev_lock'}:raise ValueError('diagnostic epoch conditions differ')
 deadline_check(store,m['goal_id'],outer_deadline_epoch,720);owner=process_identity();got=acquire(store,parent,owner)
 if got['state']=='needs_review':raise DevConflict('diagnostic goal quarantined')
 try:
  for i,c in enumerate(e['cells']):
   if c['state']=='completed':continue
   key=c['key'];root=Path(e['artifact_dir'])/'cells'/key;root.mkdir(parents=True,exist_ok=True)
   cfg=(configs or m['configs'])[f"{c['task_id']}:{c['seed']}"]
   store.update_cell(key,state='running',owner=owner,created_at=time.time(),artifact_dir=str(root))
   immutable(root/'config.json',cfg);d=root/'v9';d.mkdir(exist_ok=True)
   store.update_cell(key,probe_max_tokens=256)
   deadline_check(store,m['goal_id'],outer_deadline_epoch,720)
   provider_call(store,key,9,'model',[{'role':'system','content':'Return OK. This is a minimal connectivity probe.'},{'role':'user','content':'{"request":"Reply OK"}'}],owner,d,client)
   if job_for(store.cell(key),9,'execute'):raise DevConflict('probe already dispatched; no repeat')
   deadline_check(store,m['goal_id'],outer_deadline_epoch)
   execute(store,key,9,{'source':immutable(d/'policy.py',SOURCE)},cfg,owner,d,runner_factory)
   j=job_for(store.cell(key),9,'execute')
   if j['state']!='settled' or j['receipt']['execution_status']!='completed':raise DevConflict('probe failed; no retry')
   store.update_cell(key,state='completed',owner=None)
  with store.tx() as db:
   x=store.get(db,'experiments',parent);x.update(state='completed',owner=None);store.put(db,'experiments',parent,x)
 except BaseException:
  with store.tx() as db:
   x=store.get(db,'experiments',parent);x.update(state='needs_review',owner=None);store.put(db,'experiments',parent,x)
  raise
 return snapshot(store,parent)
