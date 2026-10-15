"""Simulated/pre-dispatch contracts, zero provider or native environment calls."""
import copy,json,time,hashlib
from pathlib import Path
from unittest.mock import patch
import pytest
from openarm365_dev_v2.tests.test_scene_contract import diagnostic,FakeOwner
from benchmarks.attention_harness.dev_experiment import verify_manifest
from benchmarks.attention_harness.dev_scene_diag import prepare_worker_inputs,reset_group,validate_recording_context

def inputs(tmp_path):
 s,e,b=diagnostic(tmp_path);cfg=verify_manifest(e)['configs']['open_cabinet:101']
 return s,e,cfg

def test_actual_constructor_and_worker_files(tmp_path):
 s,e,cfg=inputs(tmp_path)
 with patch('subprocess.Popen') as popen:
  ref,rec,owner=prepare_worker_inputs(tmp_path/'evidence',cfg,run_id='diag:real-class',attempt_id='reset:1',version=1,deadline=time.monotonic()+960,source_root=Path.cwd(),cancel_check=lambda:False)
  assert not popen.called and owner.process is None
 assert json.loads(Path(ref['uri']).read_text())==cfg
 context=json.loads(Path(rec['uri']).read_text());assert validate_recording_context(context)==context
 assert owner.deadline-time.monotonic()>959
 assert not s.cell(e['cells'][0]['key'])['jobs']

@pytest.mark.parametrize('field',['run_id','attempt_id','camera_name','origin','dev_version','started_monotonic'])
def test_missing_context_no_dispatch(tmp_path,field):
 s,e,cfg=inputs(tmp_path)
 context=dict(run_id='run:x',attempt_id='reset:x',camera_name='robot0_agentview_left',origin='native_pipe://owned-openarm365-v2-diagnostic',dev_version='1',started_monotonic=time.monotonic());del context[field]
 with patch('subprocess.Popen') as popen,pytest.raises(ValueError):
  prepare_worker_inputs(tmp_path/'invalid',cfg,run_id='x',attempt_id='y',version=1,deadline=time.monotonic()+480,source_root=Path.cwd(),cancel_check=lambda:False,recording_context=context)
 assert not popen.called and not s.cell(e['cells'][0]['key'])['jobs']
 assert not (tmp_path/'invalid').exists()

@pytest.mark.parametrize('deadline',[float('nan'),float('inf'),0,-1,'invalid'])
def test_invalid_deadline_zero_claim(tmp_path,deadline):
 s,e,cfg=inputs(tmp_path)
 with patch('subprocess.Popen') as popen,pytest.raises(ValueError):
  prepare_worker_inputs(tmp_path/'invalid',cfg,run_id='x',attempt_id='y',version=1,deadline=deadline,source_root=Path.cwd(),cancel_check=lambda:False)
 assert not popen.called and not s.cell(e['cells'][0]['key'])['jobs']

def test_missing_config_zero_claim(tmp_path):
 s,e,cfg=inputs(tmp_path)
 with patch('subprocess.Popen') as popen,pytest.raises(ValueError):
  prepare_worker_inputs(tmp_path/'invalid',None,run_id='x',attempt_id='y',version=1,deadline=time.monotonic()+480,source_root=Path.cwd(),cancel_check=lambda:False)
 assert not popen.called and not s.cell(e['cells'][0]['key'])['jobs']

def test_absolute_deadline_zero_reservation(tmp_path):
 s,e,cfg=inputs(tmp_path)
 with patch('subprocess.Popen') as popen,pytest.raises(Exception,match='deadline'):
  reset_group(s,e['experiment_id'],e['cells'][0]['key'],cfg,Path.cwd(),tmp_path/'reset',[1],outer_deadline_epoch=time.time()+599)
 assert not popen.called and not s.cell(e['cells'][0]['key'])['jobs'] and not any(s.goal(e['goal_id'])['reserved'].values())

def test_same_worker_lifetime_independent_durable_rpc(tmp_path):
 s,e,cfg=inputs(tmp_path);key=e['cells'][0]['key'];calls=[]
 class Owner(FakeOwner):
  def __init__(self,**kw):
   super().__init__();self.evidence=kw['evidence'];self.deadline=kw['deadline'];calls.append(('construct',self.deadline-time.monotonic()))
  def start(self):
   calls.append(('start',None));return super().start()
  def rpc(self,*args,request_id=None,**kwargs):
   payload={'initial_observation':{'robot':{'x':[1.]},'perception':{'values':{}}},'scene_state':{'robot':{'x':[1.]},'objects':{},'fixtures':{}},'rng_events':[{'entropy_replaced':True}]}
   row={'request':{'request_id':request_id},'response':{'request_id':request_id,'ok':True,'sequence':0,'start_sequence':0,'value':payload}}
   with (self.evidence/'rpc-receipts.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
   return payload
 refs=reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',[6,7],Owner)
 a,b=[json.loads(Path(r['uri']).read_text()) for r in refs]
 assert calls[0][1]>959 and len([x for x in calls if x[0]=='construct'])==1
 assert a['reset_rpc_receipt'] is None and b['startup_receipt'] is None
 receipt=json.loads(Path(b['reset_rpc_receipt']['uri']).read_text())
 assert receipt['request_id']==key+':7:execute' and receipt['rng_events']
 assert a['job_id']!=b['job_id'] and refs[0]['sha256']!=refs[1]['sha256']
 assert s.cell(key)['used']['executions']==2 and s.export_ledger(e['goal_id'])['recompute_matches']

def test_eight_execute_allocation_ninth_refused(tmp_path):
 s,e,cfg=inputs(tmp_path);key=e['cells'][0]['key']
 with s.tx() as db:
  c=s.get(db,'cells',key);c['budget'].update(executions=8,execution_seconds=4320,sdk_calls=640,control_steps=19200);s.put(db,'cells',key,c)
 reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',list(range(1,9)),FakeOwner)
 with pytest.raises(Exception,match='budget'):s.reserve_check(key,{'executions':1})
 assert len(s.cell(key)['jobs'])==8

def test_known_ready_cleanup_error_settles(tmp_path):
 s,e,cfg=inputs(tmp_path);key=e['cells'][0]['key']
 class Owner(FakeOwner):
  count=0
  def stop(self,*args):
   self.count+=1
   if self.count==1:raise RuntimeError('known local cleanup fault')
   return super().stop()
 with pytest.raises(RuntimeError,match='cleanup'):
  reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',[1],Owner)
 assert s.cell(key)['jobs'][0]['state']=='settled' and not any(s.goal(e['goal_id'])['reserved'].values())

def test_known_fatal_startup_is_not_unknown(tmp_path):
 s,e,cfg=inputs(tmp_path);key=e['cells'][0]['key']
 class Owner(FakeOwner):
  def start(self):
   self.startup_response={'kind':'fatal','error':'simulated known startup failure'}
   raise RuntimeError('returned fatal startup')
 with pytest.raises(RuntimeError,match='fatal'):
  reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',[1],Owner)
 job=s.cell(key)['jobs'][0];assert job['state']=='settled' and not any(s.goal(e['goal_id'])['reserved'].values())
 row=json.loads(Path(job['receipt']['diagnostic_reset']['uri']).read_text());assert row['local_error']



def test_each_task_probe_model_and_execute_once(tmp_path):
 from benchmarks.attention_harness.dev_scene_diag import probe
 from openarm365_dev_v2.tests.test_iteration import setup,selection
 from benchmarks.attention_harness.dev_configuration import derive_cell_budget,normalize_configuration
 from openarm365_dev_v2.tests.fakes import Client,Runner
 sel={**selection(),'tasks':['pick_place_counter_to_sink','open_cabinet'],'mode':'diagnostic','help_budget':0,'max_versions':1,'target_depth':1,'target_valid_executions':1,'coverage':'budget_limited'}
 cb=derive_cell_budget(normalize_configuration(sel));cb.update(executions=8,execution_seconds=4320,sdk_calls=640,control_steps=19200);sel['cell_budget']=cb
 s,e,b=setup(tmp_path,sel);client=Client()
 class ProbeRunner(Runner):
  def execute(self,r):
   from benchmarks.attention_harness.dev_v2_loop import immutable
   result=super().execute(r);result['status']='completed';ep=Path(result['artifact_dir'])
   for name in ['trace','native_result']:
    row=json.loads(Path(result['artifacts'][name]['uri']).read_text());row['status']='completed'
    if name=='trace':row.update(sdk_events=[],error=None)
    else:result['native_evaluator']=row
    result['artifacts'][name]=immutable(ep/(name+'-completed.json'),row)
   return result
 result=probe(s,e['experiment_id'],client,ProbeRunner,outer_deadline_epoch=time.time()+1800)
 assert result['state']=='completed' and len(client.inputs)==2
 assert all(c['used']['dev_calls']==1 and c['used']['executions']==1 for c in result['cells'])
 assert s.export_ledger(e['goal_id'])['recompute_matches']
