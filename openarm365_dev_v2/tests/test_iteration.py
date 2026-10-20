import json,copy,ast
from pathlib import Path
import pytest
from benchmarks.attention_harness.dev_ledger import DevV2Store,RESOURCES,GOAL
from benchmarks.attention_harness.dev_experiment import create,run,verify_manifest,DevConflict,MODEL
from benchmarks.attention_harness.dev_configuration import derive_cell_budget,normalize_configuration
from benchmarks.attention_harness.dev_v2_loop import provider_call,process_identity,feedback_for
from openarm365_dev_v2.tests.fakes import Client,Runner,EvalRunner,FailedEvalClient

def selection(key='iteration-test',**kw):return dict(request_key=key,tasks=['open_cabinet'],seeds=[101],policies=['autonomous'],target_depth=3,target_valid_executions=3,max_versions=5,help_budget=2,coverage='strict_full_depth',**kw)
def setup(tmp_path,sel=None):
 s=DevV2Store(tmp_path/'s.sqlite');e=create(s,tmp_path/'a',sel or selection());m=verify_manifest(e)
 b=tmp_path/'budget.json';b.write_text(json.dumps(dict(goal_id=m['goal_id'],ledger_path=str(s.path.resolve()),experiment_id=e['experiment_id'],manifest_sha256=e['manifest']['sha256'],limits=m['goal_limits'],wall_limit=m['wall_limit'])))
 return s,e,b
class HintClient(FailedEvalClient):pass
def test_empty_constructor_and_multigoal(tmp_path):
 s=DevV2Store(tmp_path/'s.sqlite')
 with s.tx() as db:assert db.execute('SELECT COUNT(*) FROM v2_goals').fetchone()[0]==0
 a=create(s,tmp_path/'a',selection('separate-goal-a'));b=create(s,tmp_path/'a',selection('separate-goal-b'))
 assert a['goal_id']!=b['goal_id'];assert len(s.export_ledger()['goals'])==2
 assert a['resources']['used']['dev_calls']==0
 assert create(s,tmp_path/'a',selection('separate-goal-a'))['manifest']==a['manifest']
 with pytest.raises(DevConflict):create(s,tmp_path/'a',{**selection('separate-goal-a'),'target_depth':4})
def test_formula():
 p=normalize_configuration(selection());b=derive_cell_budget(p,80,2400)
 assert b['provider_tokens']==253008 and b['input_tokens']==210000 and b['execution_seconds']==5940
 assert b['model_seconds']==2040 and b['output_tokens']==43008 and b['control_steps']==7200
 assert b['advisor_calls']==p['help_budget']==2
 assert 255056-b['provider_tokens']==p['help_budget']*1024
 assert 45056-b['output_tokens']==p['help_budget']*1024
@pytest.mark.parametrize('seconds,expected_model_seconds',[(120,840),(240,1440)])
def test_frozen_dev_model_reserve_survives_manifest_reload(tmp_path,seconds,expected_model_seconds):
 s,e,_=setup(tmp_path,selection(model_reserve_seconds=seconds))
 before=Path(e['manifest']['uri']).read_bytes()
 manifest=verify_manifest(e)
 assert manifest['model_reserve_seconds']==seconds
 assert manifest['cell_budget']['model_seconds']==expected_model_seconds
 assert s.cell(e['cells'][0]['key'])['runtime']['model_reserve_seconds']==seconds
 assert Path(e['manifest']['uri']).read_bytes()==before
@pytest.mark.parametrize('depth',[1,3,4])
def test_depth_and_trigger_reachability(tmp_path,depth):
 s,e,b=setup(tmp_path,{**selection(),'target_depth':depth,'target_valid_executions':depth})
 m=verify_manifest(e);assert m['target_depth']==depth and m['k_trigger_reachable']==(depth>=3)
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=Client(),runner_factory=Runner)
 assert len(f['cells'][0]['versions'])==depth
 assert f['cells'][0]['state']=='completed'
def test_strict_insufficient_zero_records(tmp_path):
 s=DevV2Store(tmp_path/'s.sqlite')
 with pytest.raises(ValueError):create(s,tmp_path/'a',{**selection(),'cell_budget':{k:1 for k in RESOURCES}})
 with s.tx() as db:assert db.execute('SELECT COUNT(*) FROM v2_cells').fetchone()[0]==0 and db.execute('SELECT COUNT(*) FROM v2_goals').fetchone()[0]==0
@pytest.mark.parametrize('policy',['autonomous','demo_first','reactive_help','retry_k_then_ask','budget_matched_random_escalation','trace_aware_hint_only','full_trace_aware_attention_planner'])
def test_seven_behavior_input_chain(tmp_path,policy):
 s,e,b=setup(tmp_path,{**selection(),'policies':[policy],'eval_agent':{'enabled':True}});cl=HintClient()
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=EvalRunner);c=f['cells'][0]
 assert len(c['versions'])==3 and c['used']['executions']==3
 assert all(j['reservation']['model_seconds']==360 for j in c['jobs'] if j['kind']=='model')
 assert all(j['reservation']['model_seconds']==120 for j in c['jobs'] if j['kind'] in {'eval','advisor'})
 assert c['used']['eval_calls']==3
 helps=[j for j in c['jobs'] if j['kind'] in {'demo','advisor'}]
 if policy=='autonomous':assert not helps
 else:assert helps
 if policy=='retry_k_then_ask':
  assert helps[0]['kind']=='advisor'
  assert helps[0]['reservation']['help_count']==helps[0]['reservation']['help_credits']==1
  assert c['used']['help_count']==c['used']['help_credits']==1
 if policy=='retry_k_then_ask':assert [j['version'] for j in helps]==[3]
 for v in c['versions']:
  if v['help']:
   h=json.loads(Path(v['help']['uri']).read_text());inp=json.loads(Path(v['model_input']['uri']).read_text());p=json.loads(inp['messages'][1]['content'])
   assert h['content']==p['attention_control']['help']['content']
def test_validation_compensation_and_restart(tmp_path):
 s,e,b=setup(tmp_path);cl=Client(True)
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 c=f['cells'][0];assert len(c['versions'])==4 and c['used']['executions']==3 and not c['versions'][0]['valid']
 n=len(cl.inputs);run(DevV2Store(s.path),e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner);assert len(cl.inputs)==n
 # Simulate the settled-model/version crash window, preserving external receipts.
 with s.tx() as db:
  db.execute('DELETE FROM v2_versions WHERE cell=? AND version=4',(c['key'],));cc=s.get(db,'cells',c['key']);cc['state']='planned';s.put(db,'cells',c['key'],cc)
 run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 assert len(cl.inputs)==n and len(s.cell(c['key'])['versions'])==4

def test_input_bound_rejected_no_reserve(tmp_path):
 s,e,b=setup(tmp_path);c=e['cells'][0];owner=process_identity();s.update_cell(c['key'],state='running',owner=owner)
 cl=Client()
 with pytest.raises(Exception,match='input_bound_exceeded'):provider_call(s,c['key'],1,'model',[dict(role='user',content=json.dumps({'source':'x'*31000}))],owner,tmp_path,cl)
 assert not cl.inputs and not s.cell(c['key'])['jobs']
def test_native_dataflow_invariance(tmp_path):
 packets=[]
 for value in [False,True]:
  root=tmp_path/str(value);s,e,b=setup(root)
  class R(Runner):
   native_success=value
   def execute(self,r):
    result=super().execute(r);ref=result['artifacts']['native_result'];native=json.loads(Path(ref['uri']).read_text());native['reward']=99 if value else -1
    from benchmarks.attention_harness.dev_loop import immutable
    result['artifacts']['native_result']=immutable(Path(ref['uri']).with_name('with-reward.json'),native)
    return result
  cl=HintClient();f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=R)
  packets.append([[(m['role'],m['content']) for m in i['messages']] for i in cl.inputs])
 assert packets[0]==packets[1]
def test_online_ast_boundary():
 for name in ['dev_v2_loop','dev_help','dev_attention']:
  p=Path('benchmarks/attention_harness')/(name+'.py');tree=ast.parse(p.read_text())
  for n in ast.walk(tree):
   if isinstance(n,ast.ImportFrom):assert n.module not in {'dev_report','dev_recompute'}
   if isinstance(n,ast.Subscript) and isinstance(n.slice,ast.Constant) and n.slice.value in {'native_success','reward','native_result'}:
    if n.slice.value=='native_result':assert name=='dev_v2_loop'
    else:pytest.fail('private decision read')
