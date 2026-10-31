"""Offline D6-D9 boundary checks; geometry and executor state are fakes."""
import json
from pathlib import Path
from types import SimpleNamespace as NS
import sys
import numpy as np
import pytest
from curobo_v2 import fixture_interaction as fi,fixture_telemetry as ft,execution_settings as es
from curobo_v2.client import PlanningRejected
from curobo_v2.fixture_distance import point_geom_distance
from openarm365_dev import world as wm,executor as em
from openarm365_dev.base_motion import guard_base_and_arms
from openarm365_dev_v2.agent_executor import AgentExecutor
from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
from openarm365_dev_v2.tests.test_fixture_interaction import (
    fixture_env,monitor_fixture,fixture_types,settings,r4_deployment,base,fake_mujoco_if_unavailable)


@pytest.mark.parametrize('distance,removed',[(.009,True),(.01,True),(.011,False)])
def test_world_neighbourhood_exact_distance_and_audit(monkeypatch,fixture_types,settings,distance,removed):
    import mujoco
    env=fixture_env(monkeypatch,fixture_types);calls=[]
    env.sim.model.geom_size=np.array(env.sim.model.geom_size,copy=True);env.sim.model.geom_size[3]=.005
    env.sim.data.geom_xpos[3]=env.sim.data.geom_xpos[4]+[.055+distance,0,0]
    def exact(model,data,g1,g2,maximum,segment):
        calls.append((g1,g2));return distance
    monkeypatch.setattr(mujoco,'mj_geomDistance',exact)
    result=wm.snapshot(env,execution_settings=settings)
    assert ((3,4) in calls) and all(g1==3 for g1,g2 in calls)
    audit=result['fixture_contact_exemption']
    assert bool(audit['neighbourhood']) is removed
    assert (3 not in [r['geom_id'] for r in result['inventory']]) is removed
    if removed:
        row=audit['neighbourhood'][0]
        assert row['part_geom_id']==4 and row['part_name']=='cab_left_door_handle_g0'
        assert row['distance_m']==distance and row['fixture']=='cab'
    full=wm.snapshot(env,exempt_body_ids=set(),execution_settings=settings)
    assert len(full['unexempted_cuboids'])==4
    env.sim.model.geom_bodyid=np.array(env.sim.model.geom_bodyid,copy=True)
    env.sim.model.geom_bodyid[3]=2
    calls.clear();assert not wm.snapshot(env,execution_settings=settings)['fixture_contact_exemption']['neighbourhood']
    assert not calls


def neighbourhood_executor(monkeypatch,fixture_types,settings,kind='finger',point=.058):
    import mujoco
    e,rows=monitor_fixture(monkeypatch,fixture_types,settings,kind)
    m=e.env.sim.model;d=e.env.sim.data
    m.geoms[3]='panel';m.geom_bodyid=np.array(m.geom_bodyid,copy=True);m.geom_bodyid[3]=4
    m.geom_type=np.full(m.ngeom,6);m.geom_size=np.full((m.ngeom,3),.05)
    d.geom_xpos=np.zeros((m.ngeom,3));d.geom_xmat=np.tile(np.eye(3).reshape(1,9),(m.ngeom,1))
    d.contact=[NS(geom1=0,geom2=3,dist=-.0001,pos=np.array([point,0,0]))]
    monkeypatch.setattr(mujoco,'mj_geomDistance',lambda *args:.0004,raising=False)
    return e,rows


@pytest.mark.parametrize('kind,point,allowed',[('finger',.058,True),('hand',.058,True),
    ('finger',.061,False),('arm',.058,False)])
def test_neighbourhood_monitor_contact_point_and_link(monkeypatch,fixture_types,settings,kind,point,allowed):
    e,rows=neighbourhood_executor(monkeypatch,fixture_types,settings,kind,point)
    if allowed:
        e.monitor();receipt=ft.receipt(e)['fixture_pilot']
        assert receipt['part_contact_count']==0 and receipt['neighbourhood_contact_count']==1
        assert receipt['parts']==[] and receipt['neighbourhood'][0]['geom_id']==3
        sample=next(row for _,row in rows if row.get('event')=='fixture_contact_sample')
        assert sample['part_contacts']==[] and len(sample['neighbourhood_contacts'])==1
        assert sample['contacts'][0]['contact_point_distance_m']==pytest.approx(.008)
    else:
        with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()


def test_same_pair_second_contact_outside_neighbourhood_is_fatal(monkeypatch,fixture_types,settings):
    e,rows=neighbourhood_executor(monkeypatch,fixture_types,settings)
    e.env.sim.data.contact+=[NS(geom1=0,geom2=3,dist=-.001,pos=np.array([.2,0,0]))]
    with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()
    assert e._fixture_motion['neighbourhood_contact_count']==1


@pytest.mark.parametrize('kind,size,point,expected',[(6,[.05,.1,.2],[.06,.12,0],.0005**.5),
    (2,[.05,0,0],[.06,0,0],.01),(3,[.05,.1,0],[0,0,.16],.01),
    (5,[.05,.1,0],[.06,0,.12],.0005**.5),(4,[.05,.1,.2],[.06,0,0],.01)])
def test_exact_primitive_contact_point_distance(kind,size,point,expected):
    m=NS(geom_type=[kind],geom_size=[size]);d=NS(geom_xmat=[np.eye(3).ravel()],geom_xpos=[np.zeros(3)])
    assert point_geom_distance(m,d,0,point)==pytest.approx(expected)


def test_mesh_uses_surface_not_aabb_and_rotated_frame():
    # Tetrahedron: (.6,.6,.6) is inside its AABB, well outside its solid.
    vertices=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],float)
    m=NS(geom_type=[7],geom_size=[[1,1,1]],geom_dataid=[0],mesh_vertadr=[0],mesh_vertnum=[4],mesh_vert=vertices)
    r=np.array([[0,-1,0],[1,0,0],[0,0,1.]])
    d=NS(geom_xmat=[r.ravel()],geom_xpos=[np.array([2,3,4])])
    assert point_geom_distance(m,d,0,r@np.array([.6,.6,.6])+d.geom_xpos[0])==pytest.approx(.8/3**.5)
    assert point_geom_distance(m,d,0,r@np.array([.1,.1,.1])+d.geom_xpos[0])==0


@pytest.mark.parametrize('contact',[True,False])
@pytest.mark.parametrize('guard',['drift','mimic'])
def test_drift_and_mimic_record_only_during_current_action(monkeypatch,fixture_types,settings,contact,guard):
    e,rows=monitor_fixture(monkeypatch,fixture_types,settings)
    if not contact:e.env.sim.data.contact=[]
    if guard=='drift':e.env.sim.data.qpos[:3]=[.00302,0,.0031]
    else:e.env.sim.data.qpos[3]=.02
    if not contact:
        with pytest.raises(RuntimeError,match='base drift|mimic'):e.monitor()
        return
    e.monitor()
    e.env.sim.data.contact=[];e.monitor()
    receipt=ft.receipt(e)['fixture_pilot']
    assert receipt['guard_record_only_steps']==2
    assert receipt['maximum_base_drift_translation_m']==pytest.approx(.00302 if guard=='drift' else 0)
    assert receipt['maximum_gripper_mimic_error_m']['left']==pytest.approx(.02 if guard=='mimic' else 0)
    assert any(row.get('event')=='fixture_guard_sample' for _,row in rows)
    ft.begin(e)
    with pytest.raises(RuntimeError,match='base drift|mimic'):e.monitor()


@pytest.mark.parametrize('profile',[None,'r3-20261025','r4-20261027'])
def test_legacy_base_guard_cannot_be_relaxed(profile):
    settings=None if profile is None else NS(profile=profile,base_hold_drift_m=.003,base_hold_drift_rad=.003)
    with pytest.raises(RuntimeError,match='base drift'):
        guard_base_and_arms([.004,0,0],[0,0,0],None,execution_settings=settings,fixture_contact=True)


def test_fixture_contact_does_not_relax_arm_hold(settings):
    with pytest.raises(RuntimeError,match='Arm joint drift'):
        guard_base_and_arms([0,0,0],[0,0,0],[.005],[0],execution_settings=settings,fixture_contact=True)


@pytest.mark.parametrize('profile',[None,'r3-20261025','r4-20261027',fi.PROFILE])
def test_empty_close_gate_and_no_attachment(monkeypatch,fixture_types,settings,profile):
    e,_=monitor_fixture(monkeypatch,fixture_types,settings)
    e.execution_settings=None if profile is None else NS(profile=profile,attach_after_grasp=True)
    e.env.sim.data.contact=[];e.check_live=lambda:None;e.sequence=0;e.grips={'left':-1.,'right':-1.}
    e.held_trace_enabled=False;e.held_reference={'mode':'fixed'};e.epoch='pilot';e.failed=False
    invalid=[];e.planner.ledger=NS(invalidate=invalid.append)
    monkeypatch.setattr(em,'receipt_fields',lambda settings:{})
    monkeypatch.setattr(em,'held_envelope_receipt',lambda settings:{})
    e.control_step=lambda:setattr(e,'sequence',e.sequence+1)
    if profile in ('r3-20261025','r4-20261027'):
        with pytest.raises(PlanningRejected,match='bilateral'):em.NativeExecutor.gripper.__wrapped__(e,'left',1.,request_id='close')
    else:
        receipt=em.NativeExecutor.gripper.__wrapped__(e,'left',1.,request_id='close')
        assert receipt.get('empty_close') is (True if profile==fi.PROFILE else None)
    assert e.held is None and not any(reason=='grasp_acquired' for reason in invalid)


@pytest.mark.parametrize('profile',[None,'r3-20261025','r4-20261027',fi.PROFILE])
@pytest.mark.parametrize('delta,trajectory,rejected',[([.001,0,0],[[0],[0]],True),
    ([0,0,0],[[0],[0]],False),([.01,0,0],[[0],[.0002],[0]],False)])
def test_zero_motion_plan_profile_gate(monkeypatch,profile,delta,trajectory,rejected):
    e=em.NativeExecutor.__new__(em.NativeExecutor);calls=[]
    e.execution_settings=None if profile is None else NS(profile=profile)
    e.check_live=lambda:None;e.any_object_contact=lambda:False;e.env=NS();e.held=None;e.epoch='test'
    e.planner=NS(state=lambda:np.zeros(1),plan_pose=lambda *a,**k:{'trajectory':trajectory,'plan_id':'unused'},
        ledger=NS(invalidate=calls.append))
    e.apply=lambda *a,**k:calls.append('executed') or {'status':'completed'}
    monkeypatch.setattr(em,'tcp_pose',lambda *args:np.eye(4))
    if profile==fi.PROFILE and rejected:
        with pytest.raises(PlanningRejected,match='motion_below_planner_tolerance'):
            em.NativeExecutor.move_delta.__wrapped__(e,'left',*delta,request_id='tiny')
        assert calls==['motion_below_planner_tolerance']
    else:
        em.NativeExecutor.move_delta.__wrapped__(e,'left',*delta,request_id='move')
        assert calls==['executed']


def test_rotation_only_tiny_plan_is_recoverable_before_physics(monkeypatch):
    e=AgentExecutor.__new__(AgentExecutor);calls=[]
    e.execution_settings=NS(profile=fi.PROFILE,base_hold_drift_m=.003,base_hold_drift_rad=.003)
    e.check_live=lambda:None;e.any_object_contact=lambda:False
    e.env=NS(sim=NS(data=NS(qpos=np.zeros(3))),robots=[NS(_ref_base_joint_pos_indexes=[0,1,2])])
    e.base_hold=np.zeros(3);e.base_moving=False;e.journal='journal';e.log=lambda *args:None
    e.held=None;e.epoch='test';e.failed=False;e.sequence=0;e.physics_steps=0
    e.planner=NS(state=lambda:np.zeros(1),plan_pose=lambda *a,**k:{'trajectory':[[0],[.00001]]},
        ledger=NS(invalidate=calls.append),client=NS(attest=lambda:calls.append('attested')))
    monkeypatch.setattr(em,'tcp_pose',lambda *args:np.eye(4))
    monkeypatch.setattr(em,'receipt_fields',lambda *args:{})
    with pytest.raises(RecoverablePlanRejection) as exc:
        e.move_delta('left',0,0,0,rotation_delta=[0,0,.01],request_id='tiny')
    assert exc.value.public()['reason']=='motion_below_planner_tolerance'
    assert exc.value.public()['ok'] is False and not e.failed and e.sequence==e.physics_steps==0
    assert 'attested' in calls


@pytest.mark.parametrize('candidate,object_contact',[(True,True),(False,True)])
def test_object_close_keeps_attachment_requirement(monkeypatch,fixture_types,settings,candidate,object_contact):
    from openarm365_dev import attachment
    e,_=monitor_fixture(monkeypatch,fixture_types,settings)
    e.env.sim.data.contact=[];e.check_live=lambda:None;e.sequence=0;e.grips={'left':-1.,'right':-1.}
    e.held_trace_enabled=False;e.held_reference={'mode':'fixed'};e.epoch='pilot';e.failed=False
    e.planner.ledger=NS(invalidate=lambda *args:None)
    e.any_object_contact=lambda:object_contact
    e.grasp_counts['left']={7:12} if candidate else {}
    monkeypatch.setattr(em,'receipt_fields',lambda settings:{})
    monkeypatch.setattr(em,'held_envelope_receipt',lambda settings:{})
    monkeypatch.setattr(attachment,'object_pose',lambda *args:np.eye(4))
    e.control_step=lambda:setattr(e,'sequence',e.sequence+1)
    if candidate:
        receipt=em.NativeExecutor.gripper.__wrapped__(e,'left',1.,request_id='close')
        assert receipt['attached_object']=={'holder':'left','target_id':7} and not receipt['empty_close']
    else:
        with pytest.raises(PlanningRejected,match='bilateral'):em.NativeExecutor.gripper.__wrapped__(e,'left',1.,request_id='close')
        assert e.held is None


@pytest.mark.parametrize('task',fi.DEFAULT_PILOT_TASKS)
@pytest.mark.parametrize('agent',[False,True])
def test_pilot_custom_policy_and_seed_in_native_and_agent_modes(monkeypatch,tmp_path,settings,task,agent):
    from types import ModuleType
    from openarm365_dev_v2 import dev_config as dc,fixture_pilot
    monkeypatch.setattr(dc,'new_execution_settings',lambda **kwargs:settings)
    monkeypatch.setattr(dc,'make_lock',lambda path,**kwargs:{'path':'fake','sha256':'fake'})
    monkeypatch.setattr(dc,'verify_lock',lambda *args,**kwargs:None)
    module=ModuleType('openarm365_dev_v2.runner');captured=[]
    class Runner:
        def __init__(self,**kwargs):captured.append(kwargs)
        def execute(self,request):
            captured.append(request);return {'status':'completed'}
    module.OpenArm365DevRunnerV2=Runner;monkeypatch.setitem(sys.modules,module.__name__,module)
    policy=tmp_path/'custom.py';policy.write_text('from robot_sdk import sensors\nprint(sensors.find_fixtures())\n')
    out=tmp_path/'pilot'
    argv=['--task',task,'--seed','102','--policy',str(policy),'--out',str(out)]
    if agent:argv+=['--agent-mode']
    assert fixture_pilot.main(argv)==0
    assert captured[0]['agent_mode'] is agent and captured[1].seed==102
    config=json.loads((out/'config.json').read_text())
    assert config['seed']==config['scene_reset_seed']==102
    assert (out/'policy.py').read_bytes()==policy.read_bytes()


@pytest.mark.parametrize('task',fi.DEFAULT_PILOT_TASKS)
@pytest.mark.parametrize('near',[0,1])
def test_scripted_pilots_choose_near_arm_rotate_and_avoid_tiny_steps(monkeypatch,task,near):
    from types import ModuleType
    from scipy.spatial.transform import Rotation
    sdk=ModuleType('robot_sdk');calls=[]
    positions=[np.array([.4,-.1,0.]),np.array([.4,.1,0.])]
    positions[near]=np.array([.2,0,0.]);quats=[np.array([0.,0.,0.,1.]) for _ in range(2)]
    cls={'turn_on_microwave':'Microwave','turn_off_stove':'Stove','open_drawer':'Drawer','open_cabinet':'HingeCabinet'}[task]
    name={'turn_on_microwave':'start_button','turn_off_stove':'knob_front_left_main','open_drawer':'door_handle','open_cabinet':'left_door_handle'}[task]
    axes=[] if task=='turn_on_microwave' else [dict(joint='joint',axis_world=[1,0,0] if task!='open_cabinet' else [0,0,1],
        origin_world=[0,0,0],position=0,range=[0,1])]
    fixtures=[dict(name='fixture',fixture_class=cls,interaction_parts=[dict(name=name,center=[0,0,0],axes=axes)],
        state=dict(turned_on=False,knob_angles_rad={'front_left':0.},door_open_fraction={'left_door':1.,'door':1.}),
        task_controls={'selected_knob':'front_left'})]
    def observation():
        return {key:value.tolist() for side,pos,quat in zip(('left','right'),positions,quats)
            for key,value in [(f'robot0_{side}_eef_pos',pos),(f'robot0_{side}_eef_quat',quat)]}
    def move(side,dx,dy,dz,rotation_delta=None):
        delta=np.array([dx,dy,dz]);rotation=np.array(rotation_delta if rotation_delta is not None else [0,0,0])
        assert np.linalg.norm(delta)>=.006-1e-9 or np.linalg.norm(rotation)>.03
        calls.append((side,delta.copy(),rotation.copy()))
        positions[side]+=delta
        quats[side]=(Rotation.from_rotvec(rotation)*Rotation.from_quat(quats[side])).as_quat()
    sdk.sensors=NS(get_observation=observation,find_fixtures=lambda:fixtures)
    for side,index in [('left',0),('right',1)]:
        setattr(sdk,side+'_arm',NS(move_delta=lambda dx,dy,dz,rotation_delta=None,index=index:move(index,dx,dy,dz,rotation_delta)))
        setattr(sdk,side+'_gripper',NS(open=lambda:None,close=lambda:None))
    monkeypatch.setitem(sys.modules,'robot_sdk',sdk)
    path=Path('openarm365_dev_v2/policies')/('fixture_open_cabinet.py' if task=='open_cabinet' else task+'.py')
    exec(compile(path.read_text(),str(path),'exec'),{})
    assert calls and {row[0] for row in calls}=={near}
    assert any(np.linalg.norm(row[2])>.03 for row in calls)


def test_r5_prompt_states_actual_tolerances_and_new_rules():
    from benchmarks.attention_harness.dev_tasks import dev_sdk_prompt,dev_sdk
    config=dict(execution_settings={'profile':fi.PROFILE},sdk_call_limit=320,horizon=9600,attempt_wall_seconds=1920)
    prompt=dev_sdk_prompt(config)
    for token in ('4 mm','0.03 rad','5 mm','combined','motion_below_planner_tolerance','empty_close=true','1 cm'):
        assert token in prompt
    assert dev_sdk(config)['fixture_interaction']['neighbourhood_radius_m']==.01
