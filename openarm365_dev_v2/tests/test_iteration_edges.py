import copy,json,time
from pathlib import Path
import pytest
from openarm365_dev_v2.tests.test_iteration import setup,selection,HintClient
from openarm365_dev_v2.tests.fakes import Client,Runner
from benchmarks.attention_harness.dev_experiment import *
from benchmarks.attention_harness.dev_v2_loop import *
from benchmarks.attention_harness.dev_probe import run_probe

def test_probe_and_goal_allocation(tmp_path):
 sel={**selection(),'tasks':['pick_place_counter_to_sink','open_cabinet']}
 # Explicit room for probe, per matrix cell worst-case plus probe allocation.
 from benchmarks.attention_harness.dev_configuration import normalize_configuration,derive_cell_budget
 from benchmarks.attention_harness.dev_probe import PROBE_BUDGET
 cb=derive_cell_budget(normalize_configuration(sel));sel['goal_limits']={k:2*v+PROBE_BUDGET.get(k,0) for k,v in cb.items()}
 class ProbeRunner(Runner):
  def execute(self,r):
   result=super().execute(r);ep=Path(result['artifact_dir']);result['status']='completed'
   native=json.loads(Path(result['artifacts']['native_result']['uri']).read_text());native['status']='completed';result['native_evaluator']=native;result['artifacts']['native_result']=immutable(ep/'normal-native.json',native)
   trace=json.loads(Path(result['artifacts']['trace']['uri']).read_text());trace.update(status='completed',sdk_events=[],error=None);result['artifacts']['trace']=immutable(ep/'normal-trace.json',trace)
   return result
 s,e,b=setup(tmp_path,sel);p=run_probe(s,e['experiment_id'],Client(),ProbeRunner)
 assert p['state']=='completed' and not any(c['experiment_eligible'] for c in p['cells'])
 assert s.goal(e['goal_id'])['used']['executions']==2
 n=len(s.export_ledger(e['goal_id'])['jobs']);run_probe(s,e['experiment_id'],Client(),ProbeRunner)
 assert len(s.export_ledger(e['goal_id'])['jobs'])==n
 assert s.export_ledger(e['goal_id'])['recompute_matches']
def test_budget_limited_exhaustion(tmp_path):
 from benchmarks.attention_harness.dev_configuration import derive_cell_budget,normalize_configuration
 sel=selection();p=normalize_configuration(sel);p.update(max_versions=1,target_valid_executions=1,help_budget=0)
 cb=derive_cell_budget(p)
 s,e,b=setup(tmp_path,{**sel,'cell_budget':cb,'coverage':'budget_limited'})
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=Client(),runner_factory=Runner)
 assert f['cells'][0]['state']=='failed' and len(f['cells'][0]['versions'])==1
 assert f['cells'][0]['used']['dev_calls']==1
def test_max_version_failures(tmp_path):
 class Bad(Client):
  def chat(self,**kw):
   self.inputs.append(kw);return ParccResponse('simulated','invalid',None,.01,1,dict(total_tokens=100,prompt_tokens=40,completion_tokens=60))
 s,e,b=setup(tmp_path);c=Bad();f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=c,runner_factory=Runner)
 assert len(f['cells'][0]['versions'])==5 and f['cells'][0]['state']=='failed' and f['cells'][0]['used']['executions']==0
def test_slots_outer_deadline_and_binding(tmp_path):
 s,e,b=setup(tmp_path)
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,max_slots=1,client=Client(),runner_factory=Runner)
 assert len(f['cells'][0]['versions'])==3
 with pytest.raises(ValueError):run(s,e['experiment_id'],max_cells=2,budget_file=b,client=Client(),runner_factory=Runner)
 sel=selection('deadline-test');ss,ee,bb=setup(tmp_path/'deadline',sel)
 final=run(ss,ee['experiment_id'],max_cells=1,budget_file=bb,outer_deadline_epoch=time.time()+1,client=Client(),runner_factory=Runner)
 assert final['cells'][0]['state']=='not_started' and not final['cells'][0]['jobs']
def test_separate_goal_unknown_and_settlement(tmp_path):
 s,e,b=setup(tmp_path)
 e2=create(s,tmp_path/'a',selection('another-goal'))
 owner=process_identity();key=e['cells'][0]['key'];s.update_cell(key,state='running',owner=owner)
 j=s.reserve(key,1,'model',{'dev_calls':1});s.claim(j['job_id'],owner);s.unknown(j['job_id'],'unknown')
 assert s.goal(e['goal_id'])['reserved']['dev_calls']==1 and s.goal(e2['goal_id'])['reserved']['dev_calls']==0
 assert len(s.export_ledger()['goals'])==2
 with pytest.raises(DevConflict):acquire(s,e['experiment_id'],owner)
 with pytest.raises(DevConflict):s.ensure_goal(e['goal_id'],{**verify_manifest(e)['goal_limits'],'dev_calls':99},verify_manifest(e)['wall_limit'])
def test_help_crash_reuse_and_execute_crash(tmp_path,monkeypatch):
 s,e,b=setup(tmp_path,{**selection(),'policies':['demo_first']});cl=HintClient();original=s.version
 def crash(key,record):raise RuntimeError('crash before version insert')
 monkeypatch.setattr(s,'version',crash)
 with pytest.raises(RuntimeError):run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 monkeypatch.setattr(s,'version',original)
 # No claims are unknown; cleared worker lost local bookkeeping only.
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 assert len(cl.inputs)==3 and f['cells'][0]['used']['help_count']==1 and f['cells'][0]['used']['executions']==3

def test_old_manifest_exact_bytes(tmp_path):
 import importlib.util
 original=Path(__file__).with_name('fixtures')/'dev_experiment_v2.py.txt'
 from importlib.machinery import SourceFileLoader
 spec=importlib.util.spec_from_loader('benchmarks.attention_harness.baseline_experiment',SourceFileLoader('benchmarks.attention_harness.baseline_experiment',str(original)));baseline=importlib.util.module_from_spec(spec);spec.loader.exec_module(baseline)
 # Both use identical runtime paths and resources, isolating product conditions.
 for name in ['make_lock','make_dev_config','verify_lock','get_task','DemoProvider']:setattr(baseline,name,globals().get(name) or __import__('benchmarks.attention_harness.dev_experiment',fromlist=[name]).__dict__[name])
 old=baseline.create(DevV2Store(tmp_path/'old.sqlite'),tmp_path/'a',{'request_key':'legacy-byte-canary'})
 before=Path(old['manifest']['uri']).read_bytes()
 new=create(DevV2Store(tmp_path/'new.sqlite'),tmp_path/'a',{'request_key':'legacy-byte-canary'})
 assert before==Path(new['manifest']['uri']).read_bytes() and old['selection']==new['selection']

@pytest.mark.parametrize('depth,execution_target',[(4,3),(5,1)])
def test_generation_and_execution_targets_distinct(tmp_path,depth,execution_target):
 s,e,b=setup(tmp_path,{**selection(),'target_depth':depth,'target_valid_executions':execution_target})
 f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=Client(),runner_factory=Runner)
 assert len(f['cells'][0]['versions'])==depth and f['cells'][0]['used']['executions']==execution_target and f['cells'][0]['state']=='completed'
def test_copied_ledger_cannot_dispatch(tmp_path):
 s,e,b=setup(tmp_path)
 import shutil
 target=tmp_path/'copy.sqlite';shutil.copyfile(s.path,target);cl=Client()
 with pytest.raises(ValueError,match='ledger binding'):run(DevV2Store(target),e['experiment_id'],max_cells=1,client=cl,runner_factory=Runner)
 assert not cl.inputs

def test_recovered_claim_quarantines_other_cells(tmp_path):
 s,e,b=setup(tmp_path,{**selection(),'tasks':['pick_place_counter_to_sink','open_cabinet']});owner=process_identity();key=e['cells'][0]['key']
 s.update_cell(key,state='running',owner=owner);j=s.reserve(key,1,'model',{'dev_calls':1});s.claim(j['job_id'],owner)
 dead={'pid':999999999,'start_ticks':'0','boot_id':'absent'}
 with s.tx() as db:
  ee=s.get(db,'experiments',e['experiment_id']);ee['owner']=dead;s.put(db,'experiments',e['experiment_id'],ee);cc=s.get(db,'cells',key);cc['owner']=dead;s.put(db,'cells',key,cc)
 cl=Client();f=run(s,e['experiment_id'],max_cells=2,budget_file=b,client=cl,runner_factory=Runner)
 assert f['state']=='needs_review' and not cl.inputs
 assert s.goal(e['goal_id'])['reserved']['dev_calls']==1
 assert not f['cells'][1]['jobs'] and not f['cells'][1]['versions']
