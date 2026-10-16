"""Simulated fault windows; zero real provider/simulation consumption."""
import json,time,copy
from pathlib import Path
import pytest
from openarm365_dev_v2.tests.test_iteration import setup,selection,HintClient
from openarm365_dev_v2.tests.fakes import Client,Runner,EvalRunner
from benchmarks.attention_harness.dev_experiment import create,run,acquire,verify_manifest,DevConflict
from benchmarks.attention_harness.dev_configuration import normalize_configuration,derive_cell_budget,worst_slot
from benchmarks.attention_harness.dev_ledger import RESOURCES,DevV2Store
from benchmarks.attention_harness.dev_loop import process_identity
import benchmarks.attention_harness.dev_v2_loop as loop
DEAD={'pid':999999999,'start_ticks':'0','boot_id':'absent'}

def dead_claim(s,e,key,kind,version=1):
 owner=process_identity();s.update_cell(key,state='running',owner=owner)
 r={'dev_calls':1} if kind=='model' else {'advisor_calls':1,'help_count':1} if kind=='advisor' else {'executions':1,'execution_seconds':540}
 j=s.reserve(key,version,kind,r);s.claim(j['job_id'],owner)
 with s.tx() as db:
  x=s.get(db,'experiments',e['experiment_id']);x['owner']=DEAD;s.put(db,'experiments',e['experiment_id'],x)
  c=s.get(db,'cells',key);c['owner']=DEAD;s.put(db,'cells',key,c)
 return j

@pytest.mark.parametrize('kind',['model','advisor','execute'])
@pytest.mark.parametrize('index',[0,1,3])
def test_R1_R2_R3_claim_loss(tmp_path,kind,index):
 s,e,b=setup(tmp_path,{**selection(),'tasks':['pick_place_counter_to_sink','open_cabinet'],'policies':['autonomous','reactive_help']})
 key=e['cells'][index]['key'];j=dead_claim(s,e,key,kind);cl=HintClient()
 f=run(s,e['experiment_id'],max_cells=4,budget_file=b,client=cl,runner_factory=Runner)
 assert f['state']=='needs_review' and not cl.inputs
 assert s.cell(key)['jobs'][0]['state']=='needs_review'
 assert all(s.goal(e['goal_id'])['reserved'][k]==v for k,v in j['reservation'].items())
 assert all(not c['jobs'] and c['state']=='not_started' for c in f['cells'] if c['key']!=key)

def test_R4_two_claims(tmp_path):
 s,e,b=setup(tmp_path,{**selection(),'tasks':['pick_place_counter_to_sink','open_cabinet'],'policies':['autonomous','reactive_help']})
 for c in e['cells'][:2]:dead_claim(s,e,c['key'],'model')
 cl=Client();f=run(s,e['experiment_id'],max_cells=4,budget_file=b,client=cl,runner_factory=Runner)
 assert not cl.inputs and s.goal(e['goal_id'])['reserved']['dev_calls']==2
 assert all(c['jobs'][0]['state']=='needs_review' for c in f['cells'][:2])
 assert all(c['state']=='not_started' and not c['jobs'] for c in f['cells'][2:])

def shared(tmp_path):
 sel=selection('shared-one',goal_key='shared-goal-test')
 budget=derive_cell_budget(normalize_configuration(sel));sel['goal_limits']={k:2*v for k,v in budget.items()};sel['wall_limit']=10000
 s,e,b=setup(tmp_path,sel);other=create(s,tmp_path/'a',{**sel,'request_key':'shared-two'})
 return s,e,b,other

def test_R4b_cross_experiment(tmp_path):
 s,e,b,other=shared(tmp_path);dead_claim(s,other,other['cells'][0]['key'],'advisor')
 cl=Client();f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 assert f['state']=='needs_review' and not cl.inputs
 assert s.cell(other['cells'][0]['key'])['state']=='needs_review'
 assert s.goal(e['goal_id'])['reserved']['advisor_calls']==1

def test_R12_atomic_allocations(tmp_path):
 s,e,b,other=shared(tmp_path);assert e['goal_id']==other['goal_id'] and e['manifest']!=other['manifest']
 with s.tx() as db:before=[db.execute('SELECT count(*) FROM v2_'+t).fetchone()[0] for t in ['goals','experiments','cells']]
 with pytest.raises(ValueError,match='allocation'):create(s,tmp_path/'a',{**e['selection'],'request_key':'shared-three'})
 with s.tx() as db:assert before==[db.execute('SELECT count(*) FROM v2_'+t).fetchone()[0] for t in ['goals','experiments','cells']]
 with pytest.raises(DevConflict):s.ensure_goal(e['goal_id'],{**e['resources']['limits'],'dev_calls':99},10000)

@pytest.mark.parametrize('after',[False,True])
@pytest.mark.parametrize('policy,help_version',[('reactive_help',2),('retry_k_then_ask',3)])
def test_R5_R9_paid_help(tmp_path,monkeypatch,after,policy,help_version):
 s,e,b=setup(tmp_path,{**selection(),'policies':[policy],'help_budget':1,'eval_agent':{'enabled':True}});cl=HintClient();original=loop.immutable;hit=[]
 def injected(path,value):
  path=Path(path)
  if path.name=='help.json' and path.parent.name=='v'+str(help_version) and not hit:
   hit.append(1)
   if after:original(path,value)
   raise RuntimeError('paid_help_local_crash')
  return original(path,value)
 monkeypatch.setattr(loop,'immutable',injected)
 with pytest.raises(RuntimeError,match='paid_help'):run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=EvalRunner)
 paid=s.cell(e['cells'][0]['key'])['used']['advisor_calls'];assert paid==1
 count=len(cl.inputs);monkeypatch.setattr(loop,'immutable',original)
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=EvalRunner);c=f['cells'][0]
 assert c['used']['advisor_calls']==paid and len(cl.inputs)-count==4-help_version
 v=c['versions'][help_version-1];h=json.loads(Path(v['help']['uri']).read_text());inp=json.loads(Path(v['model_input']['uri']).read_text())
 assert json.loads(inp['messages'][1]['content'])['attention_control']['help']['content']==h['content']
 if help_version==3:assert not any(v['help'] for v in c['versions'][:2])

@pytest.mark.parametrize('window',['policy','version'])
def test_R6_paid_model(tmp_path,monkeypatch,window):
 s,e,b=setup(tmp_path,{**selection(),'target_depth':1,'target_valid_executions':1});cl=Client();old=loop.immutable;insert=s.version;hit=[]
 def injected(path,value):
  if Path(path).name=='policy.py' and not hit:hit.append(1);raise RuntimeError('paid_model_local_crash')
  return old(path,value)
 def fail_insert(*args):raise RuntimeError('paid_model_local_crash')
 if window=='policy':monkeypatch.setattr(loop,'immutable',injected)
 else:monkeypatch.setattr(s,'version',fail_insert)
 with pytest.raises(RuntimeError,match='paid_model'):run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 assert len(cl.inputs)==1 and s.cell(e['cells'][0]['key'])['used']['dev_calls']==1
 monkeypatch.setattr(loop,'immutable',old);monkeypatch.setattr(s,'version',insert)
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 assert len(cl.inputs)==1 and len(f['cells'][0]['versions'])==1 and f['cells'][0]['used']['executions']==1

def test_R7_settled_execute(tmp_path,monkeypatch):
 s,e,b=setup(tmp_path);cl=Client();old=s.settle;hit=[];calls=[]
 class CountRunner(Runner):
  def execute(self,r):calls.append(r.attempt_id);return super().execute(r)
 def injected(jid,actual,receipt):
  old(jid,actual,receipt)
  if jid.endswith(':execute') and not hit:hit.append(1);raise RuntimeError('settled_execute_local_crash')
 monkeypatch.setattr(s,'settle',injected)
 with pytest.raises(RuntimeError,match='settled_execute'):run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=CountRunner)
 assert s.cell(e['cells'][0]['key'])['jobs'][-1]['state']=='settled'
 monkeypatch.setattr(s,'settle',old);f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=CountRunner)
 assert len(calls)==len(set(calls))==3
 assert json.loads(cl.inputs[1]['messages'][1]['content'])['public_feedback']['public_error']=='IK planning rejected'

def test_R8_validation_v3(tmp_path):
 class BadThird(Client):
  def chat(self,**kw):
   out=super().chat(**kw)
   if len(self.inputs)==3:
    from benchmarks.attention_harness.parcc_client import ParccResponse
    return ParccResponse('simulated','bad JSON',None,.01,1,out.usage)
   return out
 s,e,b=setup(tmp_path);f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=BadThird(),runner_factory=Runner);c=f['cells'][0]
 assert [v['version'] for v in c['versions']]==[1,2,3,4] and not c['versions'][2]['valid']
 assert [j['version'] for j in c['jobs'] if j['kind']=='execute']==[1,2,4] and c['used']['dev_calls']==4

@pytest.mark.parametrize('resource',RESOURCES)
def test_R10a_strict_resource_boundaries(tmp_path,resource):
 sel=selection('budget-boundary');cb=derive_cell_budget(normalize_configuration(sel))
 s=DevV2Store(tmp_path/'s.sqlite');bad={**cb,resource:cb[resource]-1}
 with pytest.raises(ValueError):create(s,tmp_path/'a',{**sel,'cell_budget':bad})
 with s.tx() as db:assert db.execute('SELECT count(*) FROM v2_goals').fetchone()[0]==0 and db.execute('SELECT count(*) FROM v2_jobs').fetchone()[0]==0
 good=create(s,tmp_path/'a',{**sel,'cell_budget':cb});assert not any(good['resources']['used'].values())

def test_R10b_minimal_wall(tmp_path):
 sel={**selection('minimal-boundary'),'target_depth':1,'target_valid_executions':1,'max_versions':1,'help_budget':0,'coverage':'budget_limited','wall_limit':780}
 s,e,b=setup(tmp_path,sel);assert worst_slot(verify_manifest(e))==780
 with pytest.raises(ValueError,match='zero slot'):create(s,tmp_path/'a',{**sel,'request_key':'minimal-too-short','wall_limit':779})
 cl=Client();f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner);assert f['cells'][0]['state']=='completed'

def test_start_threshold_no_dispatch(tmp_path):
 s,e,b=setup(tmp_path);cl=Client()
 with pytest.raises(Exception,match='threshold'):run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner,outer_deadline_epoch=time.time()+100,min_start_wall_seconds=101)
 assert not cl.inputs and not s.cell(e['cells'][0]['key'])['jobs']

def test_diag_minimum_preserved(tmp_path):
 cb=derive_cell_budget(normalize_configuration({**selection(),'max_versions':1,'target_depth':1,'target_valid_executions':1,'help_budget':0}))
 s=DevV2Store(tmp_path/'s.sqlite')
 with pytest.raises(ValueError,match='zero capacity'):create(s,tmp_path/'a',{**selection(),'mode':'diagnostic','coverage':'budget_limited','cell_budget':{**cb,'input_tokens':2000}})

def test_paid_provider_settle_exception_preserves_known(tmp_path,monkeypatch):
 s,e,b=setup(tmp_path,{**selection(),'target_depth':1,'target_valid_executions':1});cl=Client();old=s.settle;hit=[]
 def injected(jid,actual,receipt):
  old(jid,actual,receipt)
  if jid.endswith(':model') and not hit:hit.append(1);raise RuntimeError('after_paid_model_settle')
 monkeypatch.setattr(s,'settle',injected)
 with pytest.raises(RuntimeError,match='after_paid'):run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 assert s.cell(e['cells'][0]['key'])['jobs'][0]['state']=='settled' and not any(s.goal(e['goal_id'])['reserved'].values())
 monkeypatch.setattr(s,'settle',old);f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 assert len(cl.inputs)==1 and f['cells'][0]['used']['executions']==1
