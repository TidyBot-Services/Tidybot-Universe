"""Offline coverage for the fixture-only receiver finger exemption."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from curobo_v2.attachment import SingleArmAttachment
from curobo_v2.client import PlanningRejected
from curobo_v2.service.curobo_service.attachment_config import configure_carried_body
from curobo_v2.service.curobo_service.geometry import ExactGeometryValidator
from openarm365_dev import attachment as measured_attachment
from openarm365_dev import executor as ex
from openarm365_dev_v2 import dev_config, formal_config
from openarm365_dev_v2.tests.test_handoff_collaboration import handoff_executor
from openarm365_dev_v2.tests.test_contact_exemption import contact_executor
from openarm365_dev_v2.tests.test_test_fixture import SPEC
from benchmarks.attention_harness.tests.test_curobo_v2_attachment import measured
from benchmarks.attention_harness.dev_experiment import validate_selection_fields
from benchmarks.attention_harness.memory_fixture_guard import fixture_reason, REASON


FINGERS=['openarm_left_left_finger','openarm_left_right_finger',
         'openarm_right_left_finger','openarm_right_right_finger']


def test_receipt_flag_is_only_present_when_configured():
    obj=NS(execution_settings=None,held_contact_only=False,held_reference_per_motion=False,
           handoff_receiver_fingers=False)
    ex.refresh_execution_receipt(obj)
    assert 'handoff_receiver_fingers' not in obj._execution_receipt
    obj.handoff_receiver_fingers=True
    ex.refresh_execution_receipt(obj)
    assert obj._execution_receipt['handoff_receiver_fingers'] is True


def test_flag_only_inside_fixture_and_true(monkeypatch):
    spec=dict(SPEC,handoff_receiver_fingers=True)
    assert dev_config.validate_test_fixture(spec) is spec
    for value in (False,None,1,'true',{},[]):
        with pytest.raises(ValueError,match='handoff_receiver_fingers'):
            dev_config.validate_test_fixture(dict(SPEC,handoff_receiver_fingers=value))
    with pytest.raises(ValueError):
        validate_selection_fields({'handoff_receiver_fingers':True})
    with pytest.raises(ValueError,match='test_fixture'):
        validate_selection_fields({'test_fixture':spec})
    assert fixture_reason(config={'test_fixture':spec})==REASON
    monkeypatch.setattr(dev_config,'verify_lock',lambda *a,**kw:None)
    cfg=dev_config._make_dev_config('open_cabinet',104,{'path':'fixture','sha256':'fixture'},budget_tier='legacy')
    with pytest.raises(ValueError,match='Dev configuration mismatch'):
        dev_config.validate_dev_config(dict(cfg,handoff_receiver_fingers=True))
    with pytest.raises(ValueError,match='formal boundary rejects test_fixture'):
        formal_config.validate_formal_config(dict(cfg,test_fixture=spec,
            schema_version=formal_config.SCHEMA,runner_boundary='formal'))


def test_attachment_context_and_service_model():
    context,world=measured()
    original=SingleArmAttachment(context,world,['openarm_left_hand_tcp'])
    assert original.artifact()['allowed_fingers']==FINGERS[:2]
    extra=dict(context,handoff_receiver_fingers=FINGERS[2:])
    attached=SingleArmAttachment(extra,world,['openarm_left_hand_tcp'])
    assert attached.artifact()['allowed_fingers']==FINGERS
    robot={'kinematics':{'collision_link_names':[],'collision_spheres':{}}}
    assert configure_carried_body(robot,original)['kinematics']['self_collision_ignore'][original.link]==FINGERS[:2]
    assert configure_carried_body(robot,attached)['kinematics']['self_collision_ignore'][attached.link]==FINGERS
    assert 'self_collision_ignore' not in robot['kinematics']
    for invalid in (None,[],FINGERS[:2],list(reversed(FINGERS[2:])),FINGERS[2:]+['openarm_right_hand']):
        with pytest.raises(ValueError,match='Handoff receiver fingers'):
            SingleArmAttachment(dict(context,handoff_receiver_fingers=invalid),world,['openarm_left_hand_tcp'])


def test_exact_geometry_only_receiver_fingers():
    context,world=measured()
    def check(other,enabled):
        c=dict(context,**({'handoff_receiver_fingers':FINGERS[2:]} if enabled else {}))
        attachment=SingleArmAttachment(c,world,['openarm_left_hand_tcp'])
        contact=NS(bodies=[NS(entity=NS(name='carried:147')),NS(entity=NS(name=other))],
                   points=[NS(separation=-.001,position=np.zeros(3))])
        actor=NS(name='carried:147',find_component_by_type=lambda cls:NS())
        fake=NS(attachment=attachment,joint_names=['joint'],names=['joint'],indices={'joint':0},joints={},
                clearance=.005,fixed_groups={},robot=NS(set_qpos=lambda q:None,set_qvel=lambda q:None,set_qf=lambda q:None),
                sapien=NS(Pose=lambda p:p,physx=NS(PhysxRigidDynamicComponent=object)),carried_actor=actor,
                scene=NS(step=lambda:None,get_contacts=lambda:[contact]))
        attachment.pose=lambda robot:attachment.initial_pose
        return ExactGeometryValidator.validate_single_attachment(fake,np.zeros((1,1)),{},measured_start=False)
    assert not check('openarm_right_left_finger',False)['collision_free']
    assert check('openarm_right_left_finger',True)['collision_free']
    assert not check('openarm_right_hand',True)['collision_free']
    assert not check('world:other',True)['collision_free']


def test_receiver_motion_audit_and_other_phases(monkeypatch):
    obj=handoff_executor(monkeypatch)
    obj.handoff_receiver_fingers=True
    obj.held['left']={'holder':'left','target_id':4}
    obj.held_transform['left']=np.eye(4)
    obj.env.obj_body_id={}
    def plan(target,side,epoch):
        obj.planner._attachment_side=side
        return ex.NativeExecutor.planner_attachment(obj)
    obj.planner.plan_joints=plan
    obj.apply=lambda response,request_id:response
    response=ex.NativeExecutor.move_joints(obj,np.zeros(14),side='right',request_id='receiver-joint')
    assert response==dict(holder='left',target_id=4,handoff_receiver_fingers=FINGERS[2:])
    assert [row for row in obj.events if row.get('event')=='handoff_receiver_finger_exemption']==[
        dict(event='handoff_receiver_finger_exemption',request_id='receiver-joint',receiver='right',
             finger_names=FINGERS[2:],target_id=4,phase='receiver_approach')]
    assert obj._receiver_exemption is None
    obj.planner.plan_pose=lambda *args,side,**kw:(setattr(obj.planner,'_attachment_side',side) or
        ex.NativeExecutor.planner_attachment(obj))
    assert ex.NativeExecutor.move_delta(obj,'right',0,0,0,request_id='receiver-pose')==response
    assert ex.NativeExecutor.move_joints(obj,np.zeros(14),side='left',request_id='donor')=={'holder':'left','target_id':4}
    obj.held['left']=None
    assert ex.NativeExecutor.move_joints(obj,np.zeros(14),side='right',request_id='empty') is None
    obj.held['left']={'holder':'left','target_id':4};obj.held['right']={'holder':'right','target_id':4}
    obj.handoff=dict(id='x',phase='shared',donor='left',receiver='right',target_id=4,begin_request_id='b')
    with pytest.raises(PlanningRejected,match='Arm and base motion are not allowed'):
        ex.NativeExecutor.move_joints(obj,np.zeros(14),side='right',request_id='shared')


def test_receiver_close_audit(monkeypatch):
    obj=handoff_executor(monkeypatch)
    obj.handoff_receiver_fingers=True
    obj.held['left']={'holder':'left','target_id':4}
    obj.held_transform['left']=np.eye(4)
    result=ex.NativeExecutor.gripper(obj,'right',1.,request_id='receiver-close')
    assert result['handoff']['phase']=='shared'
    assert [row['phase'] for row in obj.events if row.get('event')=='handoff_receiver_finger_exemption']==['receiver_close']
    assert obj._receiver_exemption is None


@pytest.mark.parametrize('mode',['none','movable_objects'])
@pytest.mark.parametrize('name,flag,allowed',[
    ('openarm_right_left_finger_collision0',True,True),
    ('openarm_right_palm_collision0',True,False),
    ('openarm_right_left_finger_collision0',False,False),
])
def test_monitor_receiver_contact(monkeypatch,name,flag,allowed,mode):
    obj,rows=contact_executor(monkeypatch,0,2,mode)
    obj.env.sim.model.geoms=obj.env.sim.model.geoms.copy();obj.env.sim.model.geoms[0]=name
    obj.bimanual_collaboration='handoff';obj.handoff=None;obj.handoff_receiver_fingers=flag
    obj.held={'left':{'holder':'left','target_id':4},'right':None}
    obj.held_transform={'left':np.eye(4),'right':None}
    monkeypatch.setattr(measured_attachment,'bilateral_contacts',lambda env:{'left':{4},'right':set()})
    monkeypatch.setattr(measured_attachment,'object_pose',lambda env,bid:np.eye(4))
    if flag:ex.NativeExecutor.begin_receiver_exemption(obj,'right','req','receiver_approach')
    if allowed:
        obj.monitor();obj.monitor()
        events=[row['event'] for path,row in rows if path=='journal']
        assert events.count('handoff_receiver_finger_contact')==1
    else:
        with pytest.raises(RuntimeError,match='Unexpected physical robot/world contact'):obj.monitor()


def test_full_vector_move_of_only_the_receiver_arm_is_receiver_approach(monkeypatch):
    # bimanual.move_joints always sends side='both'; the receiver phase is the one where the donor arm's target is its
    # measured joints. Moving both arms, or the flag off, keeps the donor-only attachment.
    obj=handoff_executor(monkeypatch)
    obj.held['left']={'holder':'left','target_id':4}
    obj.held_transform['left']=np.eye(4)
    obj.env.obj_body_id={}
    current=np.linspace(-.5,.5,14)
    obj.planner.state=lambda:current.copy()
    def plan(target,side,epoch):
        obj.planner._attachment_side=side
        return ex.NativeExecutor.planner_attachment(obj)
    obj.planner.plan_joints=plan
    obj.apply=lambda response,request_id:response
    receiver_only=current.copy();receiver_only[7:]+=.3
    both=current+.3
    assert ex.NativeExecutor.move_joints(obj,receiver_only,side='both',request_id='a')=={'holder':'left','target_id':4}
    obj.handoff_receiver_fingers=True
    assert ex.NativeExecutor.move_joints(obj,receiver_only,side='both',request_id='b')==dict(
        holder='left',target_id=4,handoff_receiver_fingers=FINGERS[2:])
    assert ex.NativeExecutor.move_joints(obj,both,side='both',request_id='c')=={'holder':'left','target_id':4}
    donor_only=current.copy();donor_only[:7]+=.3
    assert ex.NativeExecutor.move_joints(obj,donor_only,side='both',request_id='d')=={'holder':'left','target_id':4}
    assert [row['request_id'] for row in obj.events if row.get('event')=='handoff_receiver_finger_exemption']==['b']
    assert obj._receiver_exemption is None


def test_after_the_release_only_the_donor_retreat_is_exempt(monkeypatch):
    # pilot_a110r 23:04: the released donor's open fingers sat in the receiver-held object's proxy. User 11-01 (in person):
    # exempt what the handoff needs. Only the former donor's fingers, only vs that object; the receiver's lift stays holder-only.
    obj=handoff_executor(monkeypatch)
    obj.handoff_receiver_fingers=True
    obj.held['left']=None;obj.held['right']={'holder':'right','target_id':4}
    obj.held_transform['right']=np.eye(4)
    obj.env.obj_body_id={}
    obj.planner.plan_pose=lambda *args,side,**kw:(setattr(obj.planner,'_attachment_side',side) or
        ex.NativeExecutor.planner_attachment(obj))
    obj.apply=lambda response,request_id:response
    obj.handoff=dict(id='x',phase='completed',donor='left',receiver='right',target_id=4,begin_request_id='b')
    assert ex.NativeExecutor.move_delta(obj,'left',.05,0,.05,request_id='retreat')==dict(
        holder='right',target_id=4,handoff_receiver_fingers=FINGERS[:2])
    assert [row['phase'] for row in obj.events if row.get('event')=='handoff_receiver_finger_exemption']==['donor_retreat']
    assert ex.NativeExecutor.move_delta(obj,'right',0,0,.03,request_id='lift')=={'holder':'right','target_id':4}
    obj.handoff=dict(obj.handoff,donor='right',receiver='left')  # a completed handoff in the other direction exempts nothing here
    assert ex.NativeExecutor.receiver_phase(obj,'left') is None
    obj.handoff=dict(obj.handoff,donor='left',receiver='right');obj.handoff_receiver_fingers=False
    assert ex.NativeExecutor.move_delta(obj,'left',.05,0,.05,request_id='flag-off') is None
