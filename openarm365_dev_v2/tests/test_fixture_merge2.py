"""Offline R5 contact composition with the implicit handoff state machine."""
from copy import deepcopy
import ast,json,os,time,traceback
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pytest
from curobo_v2.client import PlanningRejected
from curobo_v2.fixture_gripper import GripperWidthRejected
from curobo_v2 import fixture_telemetry as ft
from openarm365_dev import attachment, executor as ex
from openarm365_dev_v2.tests.test_fixture_phase1g import (
    width_executor, fixture_types, settings, r4_deployment, base,
    fake_mujoco_if_unavailable)
from openarm365_dev_v2.tests.test_fixture_phase1c import contact_action


def handoff(e, monkeypatch, phase=None):
    e.bimanual_collaboration='handoff'
    e.held={'left':dict(holder='left',target_id=6),'right':None}
    e.held_transform={'left':np.eye(4),'right':None}
    e.held_reference={side:dict(mode='fixed') for side in ('left','right')}
    e.handoff=None
    if phase:
        e.handoff=dict(id='transfer',donor='left',receiver='right',target_id=6,
            phase=phase,begin_request_id='begin')
        if phase=='shared':
            e.held['right']=dict(holder='right',target_id=6)
            e.held_transform['right']=np.eye(4)
        elif phase=='completed':
            e.held={'left':None,'right':dict(holder='right',target_id=6)}
            e.held_transform={'left':None,'right':np.eye(4)}
            e.handoff.update(release_request_id='release',independent_steps=12)
    monkeypatch.setattr(attachment,'object_pose',lambda env,bid:np.eye(4))
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{6},'right':{6}})
    monkeypatch.setattr(ex.NativeExecutor,'touching_other_held',lambda self,side:side=='right')
    monkeypatch.setattr(ex.NativeExecutor,'handoff_donor_released',lambda self,side,bid:True)


@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('action',['begin','release','cancel'])
@pytest.mark.parametrize('displacement',[.011,.06])
def test_contact_rehold_preserves_handoff_ownership(monkeypatch,fixture_types,settings,agent,action,displacement):
    e,rows,invalid,_=contact_action(monkeypatch,fixture_types,settings,[displacement,0,0],agent=agent)
    handoff(e,monkeypatch,None if action=='begin' else 'shared')
    side,command=('right',1.) if action=='begin' else ('left',-1.) if action=='release' else ('right',-1.)
    phase={'begin':'shared','release':'completed','cancel':'cancelled'}[action]
    if displacement>.05:
        with pytest.raises(ft.FixtureContactBaseDisplacementRejected):
            e.gripper(side,command,request_id='contact')
        assert invalid[-1]=='fixture_contact_base_displacement_excessive'
        np.testing.assert_array_equal(e.base_hold,[0,0,0])
    else:
        result=e.gripper(side,command,request_id='contact')
        assert result['base_rehold']['translation_m']==displacement
        assert result['handoff']['phase']==phase and result['held_by_arm']==e.held
        np.testing.assert_array_equal(e.base_hold,[displacement,0,0])
        assert invalid[-1]=='fixture_contact_rehold'
        # Re-hold is idempotent and never changes the TCP-local grasp reference.
        ft.rehold(e)
        assert sum(r.get('event')=='fixture_base_rehold' for _,r in rows)==1
    assert e.handoff['phase']==phase and not e.failed and not e.planner.prepared
    if action=='begin':
        assert all(slot['target_id']==6 for slot in e.held.values())
        with pytest.raises(PlanningRejected,match='Arm and base motion are not allowed'):
            e.move_joints([.02],side='left',request_id='blocked')
    else:
        survivor='right' if action=='release' else 'left'
        assert e.held[survivor]['target_id']==6 and e.held[side] is None
        np.testing.assert_array_equal(e.held_transform[survivor],np.eye(4))
        if action=='release':assert e.handoff['independent_steps']==12
    assert not e.gripping and not e.moving and e.active_request is None


@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('displacement',[.011,.06])
def test_unconfirmed_receiver_reholds_before_recoverable_rejection(monkeypatch,fixture_types,settings,agent,displacement):
    e,rows,invalid,_=contact_action(monkeypatch,fixture_types,settings,[displacement,0,0],agent=agent)
    handoff(e,monkeypatch)
    # Even an R5-authorized receiver/fixture contact cannot replace a handoff grasp.
    e.env.sim.model.geoms[0]='robot0_openarm_right_left_finger_collision0'
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{6},'right':set()})
    before=deepcopy(e.held)
    error=ft.FixtureContactBaseDisplacementRejected if displacement>.05 else PlanningRejected
    message='fixture_contact_base_displacement_excessive' if displacement>.05 else 'Handoff receiver did not confirm a bilateral grasp'
    with pytest.raises(error,match=message):e.gripper('right',1.,request_id='unconfirmed')
    assert e.held==before and e.handoff is None and not e.failed
    assert not e.planner.prepared and e.active_request is None and not e.gripping
    if displacement>.05:
        np.testing.assert_array_equal(e.base_hold,[0,0,0])
        assert invalid[-1]=='fixture_contact_base_displacement_excessive'
    else:
        np.testing.assert_array_equal(e.base_hold,[displacement,0,0])
        assert invalid[-1]=='fixture_contact_rehold'
        # A following ordinary open has no fixture contact and uses the new base hold.
        result=e.gripper('right',-1.,request_id='following')
        assert result['base_drift']['max_translation_m']==0 and 'base_rehold' not in result
        assert result['held_by_arm']==before and result['fixture_pilot']['contact_count']==0


@pytest.mark.parametrize('phase',[None,'completed'])
@pytest.mark.parametrize('guard',['ok','lost_contact','transform'])
def test_width_empty_arm_keeps_other_holder_monitored(monkeypatch,fixture_types,settings,phase,guard):
    e,_,invalid,_=width_executor(monkeypatch,fixture_types,settings)
    handoff(e,monkeypatch,phase)
    side='right' if phase is None else 'left';holder='left' if phase is None else 'right'
    before=deepcopy(e.held);record=deepcopy(e.handoff)
    if guard=='lost_contact':
        monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':set(),'right':set()})
    elif guard=='transform':e.held_transform[holder][0,3]=.02
    if guard=='ok':
        result=e.gripper_move_to(side,.006,request_id='width')
        assert result['held_by_arm']==before and result['handoff']==record
        assert result['settle_steps']==20 and not e.failed
        assert invalid==['gripper_width_changed']
    else:
        with pytest.raises(RuntimeError,match='bilateral grasp contact lost|transform exceeds'):
            e.gripper_move_to(side,.006,request_id='width')
        assert e.failed and invalid[-1]=='gripper_failure'
    assert e.held==before and e.handoff==record


@pytest.mark.parametrize('side',['left','right'])
def test_width_shared_holder_rejected_without_execution(monkeypatch,fixture_types,settings,side):
    e,_,invalid,_=width_executor(monkeypatch,fixture_types,settings)
    handoff(e,monkeypatch,'shared');before=deepcopy(e.held)
    with pytest.raises(GripperWidthRejected,match='gripper_width_while_holding'):
        e.gripper_move_to(side,.006,request_id='shared-width')
    assert e.sequence==0 and e.physics_steps==0 and not e.failed and not invalid
    assert e.held==before and e.handoff['phase']=='shared'


@pytest.mark.parametrize('side',['left','right'])
def test_handoff_empty_close_with_empty_slots(monkeypatch,fixture_types,settings,side):
    e,_,invalid,_=width_executor(monkeypatch,fixture_types,settings)
    handoff(e,monkeypatch);e.held={'left':None,'right':None};e.held_transform={'left':None,'right':None}
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':set(),'right':set()})
    monkeypatch.setattr(ex.NativeExecutor,'touching_other_held',lambda self,side:False)
    result=e.gripper(side,1.,request_id='empty')
    assert result['empty_close'] is True and result['attached_object'] is None
    assert e.held=={'left':None,'right':None} and e.handoff is None and not e.failed
    assert 'grasp_acquired' not in invalid


@pytest.mark.parametrize('phase',[None,'shared','completed'])
def test_handoff_empty_close_cannot_bypass_existing_holds(monkeypatch,fixture_types,settings,phase):
    e,_,invalid,_=width_executor(monkeypatch,fixture_types,settings)
    handoff(e,monkeypatch,phase);before=deepcopy(e.held)
    side='left' if phase=='completed' else 'right'
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{6} if phase!='completed' else set(),'right':{6} if phase else set()})
    monkeypatch.setattr(ex.NativeExecutor,'touching_other_held',lambda self,side:False)
    message='Handoff state is inconsistent' if phase=='shared' else 'Close did not confirm bilateral grasp'
    with pytest.raises(PlanningRejected,match=message):e.gripper(side,1.,request_id='empty-with-holder')
    assert e.held==before
    if phase=='shared':assert e.sequence==0 and not e.failed
    else:assert e.sequence==20 and e.failed


@pytest.mark.parametrize('side',['left','right'])
@pytest.mark.parametrize('guard',['lost_contact','transform'])
def test_fixture_record_only_keeps_shared_grasp_guards(monkeypatch,fixture_types,settings,side,guard):
    e,_,_,_=contact_action(monkeypatch,fixture_types,settings,[.011,0,0])
    handoff(e,monkeypatch,'shared')
    if guard=='lost_contact':
        monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{arm:set() if arm==side else {6} for arm in ('left','right')})
    else:e.held_transform[side][0,3]=.02
    ft.begin(e);e.env.sim.data.qpos[0]=.011
    with pytest.raises(RuntimeError,match='bilateral grasp contact lost|transform exceeds'):e.monitor()
    assert e._fixture_motion['guard_record_only_steps']==1
    assert e.handoff['phase']=='shared'


@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('reject',[False,True])
def test_handoff_worker_post_execution_rehold_and_replay(monkeypatch,tmp_path,fixture_types,settings,agent,reject):
    from openarm365_dev_v2 import worker,agent_probe
    from curobo_v2.fixture_interaction import enabled
    from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
    e,_,_,_=contact_action(monkeypatch,fixture_types,settings,[.06 if reject else .011,0,0],agent=agent)
    handoff(e,monkeypatch,'shared')
    monkeypatch.setattr(agent_probe,'state_fingerprint',lambda env,executor:(executor.sequence,tuple(executor.base_hold)))
    main=next(n for n in ast.parse(Path(worker.__file__).read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
    loop=next(n for n in ast.walk(main) if isinstance(n,ast.For) and ast.unparse(n.iter)=='sys.stdin')
    request=dict(request_id='release',group='left_gripper',operation='open',args=[],kwargs={})
    sent=[]
    scope=dict(__name__=worker.__name__,__package__=worker.__package__,c=dict(bimanual_collaboration='handoff'),
        json=json,os=os,time=time,traceback=traceback,sys=NS(stdin=[json.dumps(request)]*2),
        stop=NS(is_set=lambda:False),args=NS(agent_mode=agent,deadline=e.deadline,evidence=tmp_path),
        receipt_cache={},executor=e,env=e.env,execution_settings=settings,fixture_enabled=enabled,
        allowed_operations={'left_gripper':['open']},send=sent.append,override=None,tier=None,
        receipt_fields=lambda settings:{},RecoverablePlanRejection=RecoverablePlanRejection,
        FixtureContactBaseDisplacementRejected=ft.FixtureContactBaseDisplacementRejected)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[loop],type_ignores=[])),str(worker.__file__),'exec'),scope)
    assert len(sent)==2 and sent[0]==sent[1] and sent[0]['ok'] and not e.failed
    result=sent[0]
    assert result['bimanual_collaboration']=='handoff' and result['handoff']['phase']=='completed'
    assert result['handoff']['independent_steps']==12
    assert result['held_by_arm']=={'left':None,'right':dict(holder='right',target_id=6)}
    assert result['fixture_pilot']['contact_count']==1
    if reject:
        assert result['value']['reason']=='fixture_contact_base_displacement_excessive'
        assert result['value']['rejected'] and 'base_rehold' not in result
    else:assert result['base_rehold']['translation_m']==.011
    assert len((tmp_path/'rpc-receipts.jsonl').read_text().splitlines())==1


@pytest.mark.parametrize('mode',['c1','handoff'])
def test_r5_collaboration_config_keeps_fixture_initial_joints_and_seals(monkeypatch,settings,mode):
    from openarm365_dev_v2 import dev_config as dc
    from openarm365_dev_v2.environment import initial_joints
    from benchmarks.attention_harness.dev_tasks import dev_sdk_seal
    monkeypatch.setattr(dc,'verify_lock',lambda *a,**k:None)
    c=dc.make_dev_config('open_cabinet',101,{'path':'fixture','sha256':'fixture'},
        execution_settings=settings,bimanual_collaboration=mode)
    assert dc.validate_dev_config(c) is c
    assert c['bimanual_collaboration']==mode
    assert c['initial_joint_targets']==initial_joints('open_cabinet',fixture=True)
    assert c['fixture_scene_contract'] and c['fixture_success_condition']
    assert all(c[key]==value for key,value in dev_sdk_seal(c).items())
