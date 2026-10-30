import hashlib,json
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pytest
from curobo_v2.client import PlanningRejected
from openarm365_dev import executor as ex
from openarm365_dev_v2 import dev_config
from benchmarks.attention_harness.dev_tasks import dev_sdk_prompt


def fake_executor(monkeypatch):
    obj=ex.NativeExecutor.__new__(ex.NativeExecutor)
    obj.bimanual_collaboration='c1';obj.held={'left':None,'right':None}
    obj.held_transform={'left':None,'right':None};obj.held_trace_enabled=False
    obj.held_reference={side:dict(configured='fixed',source='config',mode='fixed') for side in ('left','right')}
    obj.grasp_counts={'left':{4:12},'right':{5:12}};obj.grips={'left':0.,'right':0.}
    obj.epoch='fake';obj.sequence=0;obj.physics_steps=0;obj.contact_event_count=0;obj.contact_event_overflow=0
    obj.planner=NS(ledger=NS(invalidate=lambda reason:None),_attachment_side=None)
    obj.env=NS(sim=NS(data=NS(contact=[])))
    obj.journal=Path('journal');obj.events=[];obj.log=lambda path,row:obj.events.append(row)
    obj.check_live=lambda:None
    obj.control_step=lambda:setattr(obj,'sequence',obj.sequence+1)
    monkeypatch.setattr(ex,'tcp_pose',lambda env,side:np.eye(4))
    from openarm365_dev import attachment
    monkeypatch.setattr(attachment,'object_pose',lambda env,bid:np.eye(4))
    return obj


def test_c1_two_grasps_attachment_and_release(monkeypatch):
    obj=fake_executor(monkeypatch)
    left=ex.NativeExecutor.gripper(obj,'left',1.,request_id='left-close')
    right=ex.NativeExecutor.gripper(obj,'right',1.,request_id='right-close')
    assert obj.held=={'left':{'holder':'left','target_id':4},'right':{'holder':'right','target_id':5}}
    assert left['held_by_arm']['right'] is None and right['held_by_arm']==obj.held
    obj.planner._attachment_side='right'
    assert ex.NativeExecutor.planner_attachment(obj)=={'holder':'right','target_id':5}
    obj.planner._attachment_side='left'
    assert ex.NativeExecutor.planner_attachment(obj)=={'holder':'left','target_id':4}
    obj.execution_settings=None;obj.planner.plan_pose=lambda *args,side,**kwargs: (
        setattr(obj.planner,'_attachment_side',side) or ex.NativeExecutor.planner_attachment(obj))
    obj.apply=lambda response,request_id:response
    assert ex.NativeExecutor.move_delta(obj,'right',0,0,0,request_id='right-move')['target_id']==5
    assert ex.NativeExecutor.move_delta(obj,'left',0,0,0,request_id='left-move')['target_id']==4
    ex.NativeExecutor.gripper(obj,'left',-1.,request_id='left-open')
    assert obj.held['left'] is None and obj.held['right']['target_id']==5
    assert obj.held_transform['left'] is None


def test_c1_dual_motion_and_handoff_rejected_before_motion(monkeypatch):
    obj=fake_executor(monkeypatch)
    obj.held={'left':{'holder':'left','target_id':4},'right':{'holder':'right','target_id':5}}
    obj.planner._attachment_side='both'
    with pytest.raises(PlanningRejected,match='Simultaneous motion of two held objects'):
        ex.NativeExecutor.planner_attachment(obj)
    obj.execution_settings=None;obj.planner.plan_joints=lambda *args,**kwargs:pytest.fail('planner called')
    with pytest.raises(PlanningRejected,match='Simultaneous motion of two held objects'):
        ex.NativeExecutor.move_joints(obj,np.zeros(14),side='both',request_id='dual')
    obj.held['right']=None
    monkeypatch.setattr(ex.NativeExecutor,'touching_other_held',lambda self,side:True)
    with pytest.raises(PlanningRejected,match='Handoff is not enabled'):
        ex.NativeExecutor.gripper(obj,'right',1.,request_id='handoff')
    assert obj.sequence==0 and obj.held['right'] is None


def test_c1_monitor_checks_static_held_and_cross_arm_contact(monkeypatch):
    import sys
    from openarm365_dev_v2.tests.test_contact_exemption import contact_executor
    from openarm365_dev import attachment
    if 'mujoco' not in sys.modules:
        monkeypatch.setitem(sys.modules,'mujoco',NS(mj_contactForce=lambda model,data,index,force:None))
    obj,rows=contact_executor(monkeypatch,0,2,'movable_objects')
    obj.bimanual_collaboration='c1';obj.held={'left':{'holder':'left','target_id':4},'right':{'holder':'right','target_id':6}}
    obj.held_transform={'left':np.eye(4),'right':np.eye(4)}
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{4},'right':{6}})
    monkeypatch.setattr(attachment,'object_pose',lambda env,bid:np.eye(4))
    obj.monitor()
    obj.env.sim.model.geoms=obj.env.sim.model.geoms.copy()
    obj.env.sim.model.geoms[0]='openarm_right_finger_collision0'
    with pytest.raises(RuntimeError,match='Unexpected physical robot/world contact'):obj.monitor()
    obj.env.sim.data.contact=[]
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{4},'right':set()})
    with pytest.raises(RuntimeError,match='bilateral grasp contact lost'):obj.monitor()
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':set(),'right':{6}})
    with pytest.raises(RuntimeError,match='bilateral grasp contact lost'):obj.monitor()
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{4},'right':{6}})
    monkeypatch.setattr(attachment,'object_pose',lambda env,bid:np.diag([1.,1.,1.,1.])+
        (np.array([[0,0,0,.006],[0,0,0,0],[0,0,0,0],[0,0,0,0]]) if bid==6 else 0))
    with pytest.raises(RuntimeError,match='4mm/0.03rad bound'):obj.monitor()


def test_c1_motion_contact_gate_is_arm_specific(monkeypatch):
    obj=fake_executor(monkeypatch)
    obj.held={'left':{'holder':'left','target_id':4},'right':{'holder':'right','target_id':5}}
    model=NS(geom_bodyid=[2,5,4],geom_id2name=lambda gid:['openarm_right_finger','object_b','object_a'][gid])
    obj.env.sim.model=model;obj.env.obj_body_id={'a':4,'b':5}
    obj.env.sim.data.contact=[NS(geom1=0,geom2=1,dist=-.001)]
    monkeypatch.setattr(ex,'is_descendant',lambda model,body,root:body==root)
    assert not ex.NativeExecutor.unheld_object_contact(obj,'left')
    assert not ex.NativeExecutor.unheld_object_contact(obj,'right')
    obj.env.sim.data.contact=[NS(geom1=0,geom2=2,dist=-.001)]
    assert ex.NativeExecutor.unheld_object_contact(obj,'right')
    assert ex.NativeExecutor.touching_other_held(obj,'right')
    with pytest.raises(PlanningRejected,match='Handoff is not enabled'):
        ex.NativeExecutor.gripper(obj,'right',1.,request_id='handoff')
    assert obj.sequence==0


def test_c1_config_prompt_and_none_golden(monkeypatch):
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs:None)
    args=('pick_place_counter_to_sink',102,{'path':'fixture','sha256':'fixture'})
    base=dev_config._make_dev_config(*args,budget_tier='legacy')
    none=dev_config._make_dev_config(*args,budget_tier='legacy',bimanual_collaboration='none')
    assert json.dumps(base,sort_keys=True)==json.dumps(none,sort_keys=True)
    assert hashlib.sha256(json.dumps(base,sort_keys=True).encode()).digest()==hashlib.sha256(json.dumps(none,sort_keys=True).encode()).digest()
    assert dev_sdk_prompt(base)==dev_sdk_prompt(none)
    for profile in ('r3-20261025','r4-20261027'):
        old=dict(base,execution_settings={'profile':profile})
        assert dev_sdk_prompt(old)==dev_sdk_prompt(dict(old,bimanual_collaboration='none'))
    c1=dev_config._make_dev_config(*args,budget_tier='legacy',bimanual_collaboration='c1')
    assert c1['bimanual_collaboration']=='c1'
    assert 'Each arm may hold its own object' in dev_sdk_prompt(c1)
    assert dev_config.validate_dev_config(c1)==c1


def test_c1_agent_run_config_creation(monkeypatch):
    from dataclasses import replace
    from curobo_v2 import identity,dev_local,execution_settings as es
    from benchmarks.attention_harness.dev_experiment import validate_selection_fields
    validate_selection_fields({'bimanual_collaboration':'c1'})
    pin={'lock_path':'fixture-planner','sha256':'a'*64}
    monkeypatch.setattr(es,'_deployment_pin',lambda:pin)
    settings=replace(es.R3_20261025,planner_deployment=pin)
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs:None)
    monkeypatch.setattr(dev_config,'_planner_source',lambda value,*args:value)
    monkeypatch.setattr(identity,'load_lock',lambda *args,**kwargs:({},pin['sha256']))
    monkeypatch.setattr(dev_local,'is_dev_local',lambda value:True)
    c=dev_config.make_dev_config('pick_place_counter_to_sink',102,
        {'path':'fixture','sha256':'fixture'},execution_settings=settings,bimanual_collaboration='c1')
    c['dev_mode']='agent'
    assert dev_config.validate_dev_config(c)==c
    assert c['execution_settings']['profile']=='r3-20261025'
    assert c['dev_sdk_prompt_sha256']==hashlib.sha256(dev_sdk_prompt(c).encode()).hexdigest()


def test_c1_journal_and_none_golden(tmp_path):
    def row(mode):
        obj=ex.NativeExecutor.__new__(ex.NativeExecutor)
        if mode is not None:obj.bimanual_collaboration=mode
        obj.execution_settings=None;obj._execution_receipt={}
        obj.held={'left':{'holder':'left','target_id':4},'right':None} if mode=='c1' else None
        obj.journal=tmp_path/('journal-'+str(mode));obj.physics=tmp_path/('physics-'+str(mode))
        ex.NativeExecutor.log(obj,obj.journal,dict(event='receipt',request_id='r'))
        return obj.journal.read_bytes()
    assert row(None)==row('none')
    c1=json.loads(row('c1'))
    assert c1['bimanual_collaboration']=='c1' and c1['held_by_arm']['left']['target_id']==4


def test_none_planner_request_golden():
    from openarm365_dev.planner import MujocoV2Planner
    def request(mode):
        planner=MujocoV2Planner.__new__(MujocoV2Planner)
        planner.names=[f'openarm_{side}_joint{i}' for side in ('left','right') for i in range(1,8)]
        executor=ex.NativeExecutor.__new__(ex.NativeExecutor)
        if mode is not None:executor.bimanual_collaboration=mode
        executor.held={'holder':'left','target_id':4};executor.planner=planner
        planner.attachment_provider=lambda:ex.NativeExecutor.planner_attachment(executor)
        planner.plan=lambda path,extra,active,epoch:(path,extra,active,epoch,planner.attachment_provider())
        return MujocoV2Planner.plan_joints(planner,np.zeros(14),side='left',epoch='same')
    assert json.dumps(request(None),sort_keys=True)==json.dumps(request('none'),sort_keys=True)
