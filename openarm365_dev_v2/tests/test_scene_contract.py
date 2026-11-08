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

TASK='pick_place_counter_to_sink'
QIFEI_SHA256='149932e26b0a6c8d4651418eac60b8fdf1c4a5c348d9b22a37056c0c40af9676'

def validate_fingerprint(monkeypatch,digest,seed=102,task=TASK,ref=None):
 import openarm365_dev_v2.scene_contract as sc
 # Exercise the real pinned registrations without constructing a native scene.
 monkeypatch.setattr(sc,'fingerprint',lambda state,rounded=False:digest if rounded else 'a'*64)
 return sc.validate(sc.registered_contract_ref() if ref is None else ref,task,{},seed)

@pytest.mark.parametrize('task',['pick_place_counter_to_sink','open_cabinet'])
@pytest.mark.parametrize('seed',[101,102,103])
def test_registered_reference_fingerprint_verified(monkeypatch,task,seed):
 from openarm365_dev_v2 import scene_contract as sc
 ref=sc.registered_contract_ref();sha=sc.seed_entry(sc.load_contract(ref),task,seed)['normalized_sha256']
 result=validate_fingerprint(monkeypatch,sha,seed,task)
 assert result['matched'] and result['status']=='verified' and result['matched_variant']=='reference'
 assert result['accepted'][0]=={'variant':'reference','normalized_sha256':sha}
 assert result['differences']==[] and result['contract']==ref

def test_registered_qifei_fingerprint_verified(monkeypatch):
 result=validate_fingerprint(monkeypatch,QIFEI_SHA256)
 assert result['matched'] and result['status']=='verified'
 assert result['matched_variant']=='zen5-glibc239-20261109'
 assert result['accepted'][1]=={'variant':result['matched_variant'],'normalized_sha256':QIFEI_SHA256}
 assert result['differences']==[]

@pytest.mark.parametrize('task,seed',[(TASK,101),(TASK,103),('open_cabinet',102)])
def test_host_variant_is_scoped_to_task_and_seed(monkeypatch,task,seed):
 result=validate_fingerprint(monkeypatch,QIFEI_SHA256,seed,task)
 assert not result['matched'] and result['status']=='scene_contract_mismatch'
 assert result['matched_variant'] is None and len(result['accepted'])==1

def test_unknown_fingerprint_reports_all_accepted_and_base_reference(monkeypatch):
 from openarm365_dev_v2 import scene_contract as sc
 expected=sc.seed_entry(sc.load_contract(sc.registered_contract_ref()),TASK,102)['normalized_sha256']
 result=validate_fingerprint(monkeypatch,'0'*64)
 assert not result['matched'] and result['status']=='scene_contract_mismatch' and result['matched_variant'] is None
 assert result['accepted']==[{'variant':'reference','normalized_sha256':expected},
                            {'variant':'zen5-glibc239-20261109','normalized_sha256':QIFEI_SHA256}]
 assert result['differences']==[{'field':'state','reason':'fingerprint_mismatch','reference':expected,
                                'actual':'0'*64,'accepted':result['accepted']}]

def test_host_variants_digest_drift_fails_closed(tmp_path,monkeypatch):
 from openarm365_dev_v2 import scene_contract as sc
 path=tmp_path/'variants.json';path.write_bytes(sc.REGISTERED_HOST_VARIANTS.read_bytes()+b' ')
 monkeypatch.setattr(sc,'REGISTERED_HOST_VARIANTS',path)
 with pytest.raises(ValueError,match='host variants digest drift'):
  validate_fingerprint(monkeypatch,QIFEI_SHA256)

@pytest.mark.parametrize('seed',[101,102])
def test_host_variants_base_mismatch_fails_closed(tmp_path,monkeypatch,seed):
 import hashlib
 from openarm365_dev_v2 import scene_contract as sc
 variants=json.loads(sc.REGISTERED_HOST_VARIANTS.read_bytes());variants['base_contract']['sha256']='0'*64
 path=tmp_path/'variants.json';path.write_text(json.dumps(variants))
 monkeypatch.setattr(sc,'REGISTERED_HOST_VARIANTS',path)
 monkeypatch.setattr(sc,'REGISTERED_HOST_VARIANTS_SHA256',hashlib.sha256(path.read_bytes()).hexdigest())
 ref=sc.registered_contract_ref()
 assert ref['sha256']==sc.REGISTERED_SHA256
 with pytest.raises(ValueError,match='base contract mismatch'):
  validate_fingerprint(monkeypatch,QIFEI_SHA256,seed,ref=ref)

def test_synthetic_registered_contract_has_only_reference_without_reading_variants(tmp_path,monkeypatch):
 import hashlib
 from openarm365_dev_v2 import scene_contract as sc
 path=tmp_path/'synthetic-contract.json';own='1'*64
 path.write_text(json.dumps({'schema':sc.SCHEMA_V2,'tolerance':1e-6,
                             'tasks':{TASK:{'seeds':{'102':{'normalized_sha256':own}}}}}))
 ref={'uri':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 assert ref['sha256']!=sc.REGISTERED_SHA256
 monkeypatch.setattr(sc,'registered_contract_ref',lambda:ref)
 monkeypatch.setattr(sc,'REGISTERED_HOST_VARIANTS',tmp_path/'absent.json')
 assert sc.accepted_fingerprints(ref,TASK,102)==[('reference',own)]
 result=validate_fingerprint(monkeypatch,own,ref=ref)
 assert result['matched'] and result['accepted']==[{'variant':'reference','normalized_sha256':own}]
 result=validate_fingerprint(monkeypatch,QIFEI_SHA256,ref=ref)
 assert not result['matched'] and result['matched_variant'] is None

@pytest.mark.parametrize('mode',['success','error','no_condition'])
def test_worker_host_info_is_diagnostic_only(mode):
 import ast
 from openarm365_dev_v2 import worker
 # Execute the actual diagnostic branch without starting the worker or a simulator.
 tree=ast.parse(Path(worker.__file__).read_text())
 branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=='condition is not None')
 calls=[];error=RuntimeError('host metadata unavailable');host={'cpu_model':'test'}
 def host_info():
  calls.append('host_info')
  if mode=='error':raise error
  return host
 condition=None if mode=='no_condition' else {'matched':True,'status':'verified'}
 scope=dict(condition=condition,host_info=host_info)
 exec(compile(ast.Module(body=[branch],type_ignores=[]),worker.__file__,'exec'),scope)
 if condition is None:assert not calls
 else:
  assert calls==['host_info'] and condition['matched'] and condition['status']=='verified'
  assert condition['host']==({'error':repr(error)} if mode=='error' else host)

@pytest.mark.parametrize('copied_base',[False,True])
def test_other_v2_contract_has_only_its_own_fingerprint(tmp_path,monkeypatch,copied_base):
 import hashlib
 from openarm365_dev_v2 import scene_contract as sc
 path=tmp_path/'contract.json'
 if copied_base:path.write_bytes(sc.REGISTERED_CONTRACT.read_bytes())
 else:path.write_text(json.dumps({'schema':sc.SCHEMA_V2,'tolerance':1e-6,
                                'tasks':{TASK:{'seeds':{'102':{'normalized_sha256':'1'*64}}}}}))
 ref={'uri':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
 own=sc.seed_entry(sc.load_contract(ref),TASK,102)['normalized_sha256']
 # A nonregistered URI must not even read the host-variant file, including when its base digest matches.
 monkeypatch.setattr(sc,'REGISTERED_HOST_VARIANTS',tmp_path/'absent.json')
 assert sc.accepted_fingerprints(ref,TASK,102)==[('reference',own)]
 result=validate_fingerprint(monkeypatch,own,ref=ref)
 assert result['matched'] and result['matched_variant']=='reference' and len(result['accepted'])==1
 result=validate_fingerprint(monkeypatch,QIFEI_SHA256,ref=ref)
 assert not result['matched'] and result['matched_variant'] is None

def test_registered_contract_ref_compatibility_is_pinned():
 from openarm365_dev_v2 import scene_contract as sc
 expected=Path(__file__).resolve().parents[1]/'scene_contracts'/'dev-seeds-101-103-v2-20261019-kp1500.json'
 assert sc.registered_contract_ref()=={'uri':str(expected),'sha256':'bb28cd3eb1334bf5baf7dd428741ed8640adfd62ecb31e6cf0379d2d1670c716'}

@pytest.mark.parametrize('mismatch',[False,True])
def test_scene_check_all_seeds_in_fresh_processes(monkeypatch,capsys,mismatch):
 from scripts.attentionbench import scene_fingerprint_check as check
 from openarm365_dev_v2 import scene_contract as sc
 calls=[]
 def probe(task,seed,resets,render=False):
  calls.append((task,seed,resets,render))
  sha=sc.seed_entry(sc.load_contract(sc.registered_contract_ref()),task,seed)['normalized_sha256']
  if seed==102:sha='0'*64 if mismatch else QIFEI_SHA256
  return dict(normalized_sha256=[sha],host={'cpu_model':'test'},frame_rendered=render)
 monkeypatch.setattr(check,'fresh_probe',probe)
 assert check.check_registered() is (not mismatch)
 assert calls==[(TASK,101,1,True),(TASK,102,1,False),(TASK,103,1,False)]
 output=capsys.readouterr().out
 assert f'matches_registered: {not mismatch}' in output and 'frame_rendered: True' in output
 if mismatch:assert 'host:' in output and 'register_host_variant.py' in output and 'matches_registered: True' not in output
 else:assert 'matched_variant: zen5-glibc239-20261109' in output

def test_host_variant_collector_prints_only_snippet_and_omits_reference_seeds(monkeypatch,capsys):
 from scripts.attentionbench import register_host_variant as register
 from openarm365_dev_v2 import scene_contract as sc
 before=sc.REGISTERED_HOST_VARIANTS.read_bytes();calls=[]
 def probe(task,seed,resets):
  calls.append((task,seed,resets))
  sha=sc.seed_entry(sc.load_contract(sc.registered_contract_ref()),task,seed)['normalized_sha256']
  return dict(seed=seed,normalized_sha256=[QIFEI_SHA256 if seed==102 else sha]*resets,host={'cpu_model':'test'})
 monkeypatch.setattr(register,'fresh_probe',probe)
 assert register.main(['--task',TASK,'--seeds','101','102','103','--id','test-host'])==0
 snippet=json.loads(capsys.readouterr().out)
 assert snippet['id']=='test-host' and snippet['tasks']=={TASK:{'seeds':{'102':{'normalized_sha256':QIFEI_SHA256}}}}
 assert snippet['evidence']['fresh_processes']==5 and snippet['evidence']['resets_per_process']==4
 assert calls==[(TASK,seed,4) for seed in (101,102,103) for _ in range(5)]
 assert sc.REGISTERED_HOST_VARIANTS.read_bytes()==before

@pytest.mark.parametrize('between_processes',[False,True])
def test_host_variant_collector_refuses_unstable_evidence(monkeypatch,capsys,between_processes):
 from scripts.attentionbench import register_host_variant as register
 calls=[]
 def probe(task,seed,resets):
  calls.append(seed)
  values=[QIFEI_SHA256]*resets
  if between_processes and len(calls)>1:values=['0'*64]*resets
  elif not between_processes:values[-1]='0'*64
  return dict(seed=seed,normalized_sha256=values,host={'cpu_model':'test'})
 monkeypatch.setattr(register,'fresh_probe',probe)
 assert register.main(['--task',TASK,'--seeds','102','--processes','2','--resets','2','--id','test'])==1
 output=capsys.readouterr()
 assert output.out=='' and 'unstable normalized fingerprints' in output.err

def test_reset_probe_repeats_seeded_reset_and_closes_without_planner(monkeypatch):
 from scripts.attentionbench import scene_fingerprint_check as check
 from openarm365_dev_v2 import environment
 calls=[]
 env=SimpleNamespace(reset=lambda:calls.append('reset'),close=lambda:calls.append('close'))
 def create(name,**kwargs):
  assert name=='PickPlaceCounterToSink' and kwargs['seed']==kwargs['scene_reset_seed']==102
  return env
 monkeypatch.setattr(environment,'create_env',create)
 monkeypatch.setattr(check,'capture_state',lambda env:{'x':[1.]})
 monkeypatch.setattr(check,'host_info',lambda:{'cpu_model':'test'})
 row=check.probe_scene(TASK,102,4)
 assert calls==['reset']*4+['close'] and len(set(row['normalized_sha256']))==1
 assert row['frame_rendered'] is None
