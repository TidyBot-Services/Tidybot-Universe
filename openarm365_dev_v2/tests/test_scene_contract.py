"""Simulated condition and metering tests; no native process or provider request."""
import json,copy
from pathlib import Path
from types import SimpleNamespace
import pytest
import numpy as np
from openarm365_dev_v2.tests.test_iteration import setup,selection
from openarm365_dev_v2.tests.fakes import Client,Runner
from openarm365_dev_v2.scene_contract import compare,fingerprint,validate
from openarm365_dev_v2.environment import rng_epoch
from benchmarks.attention_harness.dev_experiment import run,verify_manifest
from benchmarks.attention_harness.dev_scene_diag import reset_group
from benchmarks.attention_harness.dev_configuration import derive_cell_budget,normalize_configuration
from benchmarks.attention_harness.dev_report import report
from benchmarks.attention_harness.dev_loop import immutable

def test_raw_tolerance_and_quaternion():
 a={'robot':{'quat':[0,0,0,1],'pos':[1.,2.,3.]}}
 b={'robot':{'quat':[0,0,0,-1],'pos':[1.0000009,2.,3.]}}
 assert compare(a,b)['matched'] and fingerprint(a,True)!=fingerprint(b,True)
 b['robot']['pos'][0]=1.0000011;assert not compare(a,b)['matched']
 b['robot']['pos'][0]=float('nan')
 with pytest.raises(ValueError):compare(a,b)

def test_rng_epoch_all_sources_and_reset_order():
 import random
 original=np.random.default_rng;env=SimpleNamespace(rng=original(9));rows=[]
 for _ in range(2):
  # Irrelevant earlier draws must not perturb this reset's source sequence.
  env.rng.random(100);np.random.random(20);random.random()
  with rng_epoch(101,env):rows.append([env.rng.random(),np.random.random(),random.random(),np.random.default_rng().random(),np.random.default_rng(22).random()])
 assert rows[0]==rows[1] and np.random.default_rng is original
 assert rows[0][0]==rows[0][3] and rows[0][4]==original(22).random()

def test_contract_config_roundtrip_and_digest(tmp_path):
 state={'robot':{'x':[1.]},'objects':{},'fixtures':{}}
 ref=immutable(tmp_path/'contract.json',{'schema':'attentionbench.public-scene-contract.v1','tolerance':1e-6,'tasks':{'open_cabinet':{'state':state}}})
 s,e,b=setup(tmp_path,{**selection(),'reproducible_reset':True,'scene_contract':ref});m=verify_manifest(e)
 from openarm365_dev_v2.dev_config import validate_dev_config
 assert validate_dev_config(m['configs']['open_cabinet:101'])['scene_contract']==ref
 Path(ref['uri']).chmod(0o644);Path(ref['uri']).write_text('{}')
 with pytest.raises(ValueError,match='digest'):validate(ref,'open_cabinet',state)

def test_condition_mismatch_no_policy_or_revision(tmp_path,monkeypatch):
 import openarm365_dev_v2.runner as rr
 class Owner:
  def __init__(self,**kw):self.ledger=[];self.unresolved=set()
  def start(self):return {'initial_observation':{'robot':{},'perception':{'values':{}}},'scene_state':{'robot':{},'objects':{},'fixtures':{}},'scene_condition':{'matched':False,'status':'scene_contract_mismatch'},'capabilities':{}}
  def stop(self,*args):return {'leader_reaped':True,'process_group_gone':True,'planner_process_groups_gone':True}
 def forbidden(**kw):pytest.fail('condition mismatch dispatched policy')
 monkeypatch.setattr(rr,'NativeOwner',Owner);monkeypatch.setattr(rr,'execute_formal_policy',forbidden);monkeypatch.setattr(rr,'probe_sandbox',lambda:{'simulated':True})
 s,e,b=setup(tmp_path,{**selection(),'tasks':['pick_place_counter_to_sink','open_cabinet']});cl=Client()
 f=run(s,e['experiment_id'],max_cells=2,budget_file=b,client=cl,runner_factory=rr.OpenArm365DevRunnerV2)
 assert f['state']=='condition_invalid' and len(cl.inputs)==1
 first=f['cells'][0];assert first['state']=='condition_invalid' and first['used']['executions']==1 and first['used']['sdk_calls']==0
 assert f['cells'][1]['state']=='not_started' and not f['cells'][1]['jobs']
 r=report(s,e['experiment_id']);assert r['planned']==2
 assert not r['cells'][0]['eligible'] and not r['cells'][0]['evaluated'] and not r['cells'][0]['unknown']
 assert r['cells'][0]['versions'][0]['failure_classification']['category']=='scene_condition_invalid'
 assert r['cells'][0]['physical_initial_conditions']=='mismatch' and not r['cells'][0]['fair_conditions_qualified']
 assert not r['same_generation_depth'][0]['cells'][0]['fair_conditions_qualified']
 group=next(g for g in r['groups'] if g['task_id']==first['task_id'])
 assert group['excluded_condition_invalid']==1 and group['all_planned_resources']['executions']==1
 assert not r['same_effective_execution_subset']

def diagnostic(tmp_path):
 sel={**selection(),'mode':'diagnostic','help_budget':0,'max_versions':1,'target_depth':1,'target_valid_executions':1,'coverage':'budget_limited'}
 cb=derive_cell_budget(normalize_configuration(sel));cb.update(executions=9,execution_seconds=4860,sdk_calls=720,control_steps=21600);sel['cell_budget']=cb
 return setup(tmp_path,sel)
class FakeOwner:
 calls=[]
 def __init__(self,**kwargs):self.process=None;self.deadline=0
 def start(self):self.calls.append('start');return {'initial_observation':{'robot':{'x':[1.]},'perception':{'values':{}}},'scene_state':{'robot':{'x':[1.]},'objects':{},'fixtures':{}}}
 def rpc(self,*args,**kwargs):self.calls.append('reset');return self.start()
 def stop(self,*args):return {'simulated':True}

def test_diag_separate_reset_reservations_and_tenth_refused(tmp_path):
 s,e,b=diagnostic(tmp_path);key=e['cells'][0]['key'];cfg=verify_manifest(e)['configs']['open_cabinet:101']
 refs=reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',list(range(1,9)),FakeOwner)
 assert len(refs)==8 and s.cell(key)['used']['executions']==8 and s.export_ledger(e['goal_id'])['recompute_matches']
 n=len(s.cell(key)['jobs'])
 with pytest.raises(ValueError,match='versions'):reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'tenth',[10],FakeOwner)
 assert len(s.cell(key)['jobs'])==n
 with pytest.raises(Exception,match='budget'):s.reserve_check(key,{'executions':2})

def test_diag_unknown_holds_and_stops_goal(tmp_path):
 s,e,b=diagnostic(tmp_path);key=e['cells'][0]['key'];cfg=verify_manifest(e)['configs']['open_cabinet:101']
 class Lost(FakeOwner):
  def start(self):raise RuntimeError('injected response lost')
 with pytest.raises(RuntimeError):reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',[1],Lost)
 assert s.goal(e['goal_id'])['reserved']['executions']==1 and s.cell(key)['jobs'][0]['state']=='needs_review'
 cl=Client()
 with pytest.raises(Exception,match='diagnostic'):run(s,e['experiment_id'],max_cells=1,budget_file=b,client=cl,runner_factory=Runner)
 assert not cl.inputs

def test_diag_first_recovered_claim_stops_before_owner(tmp_path):
 from openarm365_dev_v2.tests.test_recovery_matrix import dead_claim
 s,e,b=diagnostic(tmp_path);key=e['cells'][0]['key'];cfg=verify_manifest(e)['configs']['open_cabinet:101'];dead_claim(s,e,key,'execute')
 before=len(FakeOwner.calls)
 with pytest.raises(Exception,match='quarantined'):reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',[2],FakeOwner)
 assert len(FakeOwner.calls)==before and s.goal(e['goal_id'])['reserved']['executions']==1

def test_real_owner_constructor_signature_without_start(tmp_path):
 from openarm365_dev_v2.owner import NativeOwnerV2
 # Real class construction, no process/start/RPC or model consumption.
 obj=NativeOwnerV2(python=Path('/unused/python'),config=tmp_path/'config.json',evidence=tmp_path,deadline=10,cancel_check=lambda:False,source_root=Path.cwd())
 assert obj.process is None and obj.source_root==Path.cwd() and obj.cancel_check() is False

def test_diag_wires_real_owner_required_keywords(tmp_path):
 s,e,b=diagnostic(tmp_path);key=e['cells'][0]['key'];cfg=verify_manifest(e)['configs']['open_cabinet:101']
 class Strict(FakeOwner):
  def __init__(self,*,python,config,evidence,deadline,cancel_check,source_root):
   assert callable(cancel_check) and cancel_check() is False
   super().__init__()
 refs=reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',[1],Strict)
 assert len(refs)==1 and s.cell(key)['used']['executions']==1

def test_diag_constructor_error_is_before_claim(tmp_path):
 s,e,b=diagnostic(tmp_path);key=e['cells'][0]['key'];cfg=verify_manifest(e)['configs']['open_cabinet:101']
 class Broken(FakeOwner):
  def __init__(self,**kw):raise TypeError('local construction fails before dispatch')
 with pytest.raises(TypeError):reset_group(s,e['experiment_id'],key,cfg,Path.cwd(),tmp_path/'reset',[1],Broken)
 assert not s.cell(key)['jobs'] and not any(s.goal(e['goal_id'])['reserved'].values())

def test_fixture_world_pose_covers_floor_without_rot():
 from openarm365_dev_v2.scene_contract import fixture_state
 # Native Floor is a BoxObject with root_body/get_quat, no yaw rot property.
 floor=SimpleNamespace(root_body='floor_main',pos=[99,99,99])
 cabinet=SimpleNamespace(root_body='cabinet_main',rot=0.,pos=[88,88,88])
 model=SimpleNamespace(body_name2id=lambda name:{'floor_main':0,'cabinet_main':1}[name])
 data=SimpleNamespace(body_xpos=np.array([[1.,2.,3.],[4.,5.,6.]]),body_xquat=np.array([[.707106781,0.,0.,.707106781],[.5,.5,.5,.5]]))
 env=SimpleNamespace(fixtures={'floor':floor,'cabinet':cabinet},sim=SimpleNamespace(model=model,data=data))
 state=fixture_state(env)
 assert state['floor_pos']==[1.,2.,3.] and state['floor_quat']==[0.,0.,.707106781,.707106781]
 assert state['cabinet_pos']==[4.,5.,6.] and state['cabinet_quat']==[.5,.5,.5,.5]
 assert list(state)==['cabinet_pos','cabinet_quat','floor_pos','floor_quat']

def test_missing_fixture_body_fails_instead_of_fabricating_pose():
 from openarm365_dev_v2.scene_contract import fixture_state
 def missing(name):raise ValueError('body absent')
 env=SimpleNamespace(fixtures={'floor':SimpleNamespace(root_body='missing')},sim=SimpleNamespace(model=SimpleNamespace(body_name2id=missing)))
 with pytest.raises(ValueError,match='body absent'):fixture_state(env)
