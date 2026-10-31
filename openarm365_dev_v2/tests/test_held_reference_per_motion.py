"""Offline per-motion held reference and unchanged transport guards."""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from curobo_v2.client import PlanningRejected
from curobo_v2.execution_settings import R3_20261025
from openarm365_dev import attachment, executor as ex
from openarm365_dev_v2.tests.test_contact_exemption import contact_executor
from openarm365_dev_v2.tests.test_handoff_collaboration import handoff_executor


def transport(monkeypatch, *, per_motion, collaboration='c1'):
    obj, rows = contact_executor(monkeypatch, 0, 2, 'movable_objects')
    obj.execution_settings = R3_20261025  # unchanged 10 mm / 0.1 rad envelope
    obj.bimanual_collaboration = collaboration
    obj.held = ({'left':{'holder':'left','target_id':4},'right':None}
                if collaboration == 'c1' else {'holder':'left','target_id':4})
    obj.held_transform = ({'left':np.eye(4),'right':None} if collaboration == 'c1' else np.eye(4))
    obj.held_reference_per_motion = per_motion
    obj.held_trace_enabled = False
    obj.failed = False
    obj.epoch = 'fake'
    obj.moving = False
    obj.gripping = False
    obj.handoff = None
    obj.sequence = 0
    obj.check_live = lambda:None
    obj.any_object_contact = lambda:True
    obj.unheld_object_contact = lambda side:False
    obj.planner.ledger = NS(invalidate=lambda reason:None)
    obj.planner.plan_joints = lambda *a, **kw:{'attachment':pose[0].copy()}
    obj.planner.plan_pose = lambda *a, **kw:{'attachment':pose[0].copy()}
    pose=[np.eye(4)]
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{4},'right':set()})
    monkeypatch.setattr(attachment,'object_pose',lambda env,bid:pose[0].copy())
    obj.apply=lambda response,request_id: (obj.monitor() or response)
    return obj,rows,pose


@pytest.mark.parametrize('collaboration', ['c1','none'])
def test_three_six_mm_motions_rebase_and_upload(monkeypatch, collaboration):
    obj,rows,pose=transport(monkeypatch,per_motion=True,collaboration=collaboration)
    for i in range(3):
        response=ex.NativeExecutor.move_joints(obj,np.zeros(1),side='left',request_id=f'm{i}')
        assert response['attachment'][0,3]==pytest.approx(i*.006)
        pose[0][0,3]+=.006
        obj.monitor()
    rebases=[row for path,row in rows if path=='journal' and row['event']=='held_reference_rebase']
    assert len(rebases)==3
    assert [row['side'] for row in rebases]==['left']*3
    assert [row['displacement_mm'] for row in rebases]==pytest.approx([0,6,6])
    assert [row['rotation_deg'] for row in rebases]==pytest.approx([0,0,0])


def test_jump_inside_motion_and_contact_loss_still_fatal(monkeypatch):
    obj,_,pose=transport(monkeypatch,per_motion=True)
    ex.NativeExecutor.move_joints(obj,np.zeros(1),side='left',request_id='m0')
    pose[0][0,3]=.015
    with pytest.raises(RuntimeError,match=r'configured 10mm/0.1rad bound'):
        obj.monitor()
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':set(),'right':set()})
    with pytest.raises(RuntimeError,match='Measured bilateral grasp contact lost during transport'):
        ex.NativeExecutor.move_joints(obj,np.zeros(1),side='left',request_id='m1')


def test_without_switch_cumulative_drift_still_fails(monkeypatch):
    obj,rows,pose=transport(monkeypatch,per_motion=False)
    for i in range(2):
        ex.NativeExecutor.move_joints(obj,np.zeros(1),side='left',request_id=f'm{i}')
        pose[0][0,3]+=.006
        if i==0:obj.monitor()
    with pytest.raises(RuntimeError,match=r'configured 10mm/0.1rad bound'):
        obj.monitor()
    assert not any(row.get('event')=='held_reference_rebase' for _,row in rows)


def test_receipt_label_only_when_enabled(monkeypatch):
    obj,_,_=transport(monkeypatch,per_motion=True)
    ex.refresh_execution_receipt(obj)
    assert obj._execution_receipt['held_reference']=='per_motion'
    obj.held_reference_per_motion=False
    ex.refresh_execution_receipt(obj)
    assert 'held_reference' not in obj._execution_receipt


def test_pose_and_base_requests_rebase_before_planning(monkeypatch):
    obj,rows,pose=transport(monkeypatch,per_motion=True)
    pose[0][0,3]=.006
    ex.NativeExecutor.move_delta(obj,'left',0,0,0,request_id='pose')
    assert obj.held_transform['left'][0,3]==pytest.approx(.006)
    pose[0][0,3]=.012
    monkeypatch.setattr(ex,'validate_delta',lambda *a:np.zeros(3))
    monkeypatch.setattr(ex.NativeExecutor,'handoff_motion_gate',lambda self:None)
    from openarm365_dev import world
    monkeypatch.setattr(world,'snapshot',lambda *a,**kw:{'world_sha256':'fake'})
    def stop(*a,**kw):
        assert obj.held_transform['left'][0,3]==pytest.approx(.012)
        raise PlanningRejected('precheck stop')
    monkeypatch.setattr(ex,'check_exact_base_path',stop)
    obj.env.sim.data.qpos[:3]=0
    obj.base_initial=np.zeros(3)
    with pytest.raises(PlanningRejected,match='precheck stop'):
        ex.NativeExecutor.base_move(obj,0,0,0,request_id='base')
    assert [row['event'] for path,row in rows if path=='journal' and row['event']=='held_reference_rebase']==['held_reference_rebase']*2


def test_handoff_begin_uses_rebased_donor_reference(monkeypatch):
    obj=handoff_executor(monkeypatch)
    obj.held_reference_per_motion=True
    pose=np.eye(4)
    monkeypatch.setattr(attachment,'object_pose',lambda env,bid:pose.copy())
    ex.NativeExecutor.gripper(obj,'left',1.,request_id='close')
    pose[0,3]=.012
    with pytest.raises(PlanningRejected,match='Handoff donor is not stably holding the object'):
        ex.NativeExecutor.gripper(obj,'right',1.,request_id='unrebased')
    obj.planner.plan_joints=lambda *a,**kw:{'ok':True}
    obj.apply=lambda response,request_id:response
    obj.unheld_object_contact=lambda side:False
    ex.NativeExecutor.move_joints(obj,np.zeros(1),side='left',request_id='move')
    assert obj.held_transform['left'][0,3]==pytest.approx(.012)
    receipt=ex.NativeExecutor.gripper(obj,'right',1.,request_id='handoff')
    assert receipt['handoff']['phase']=='shared'
