import numpy as np
import pytest
from curobo_v2.sapien_monitor import pose_changes
from curobo_v2.client import PlanningRejected


def test_door_or_object_motion_identifies_changed_entity():
    before=np.array([[0,0,0,1,0,0,0],[1,2,3,1,0,0,0]],float)
    after=before.copy();after[1,0]+=.001
    assert pose_changes(before,after)==[1]
    after=before.copy();angle=.001;after[0,3:]=[np.cos(angle/2),0,0,np.sin(angle/2)]
    assert pose_changes(before,after)==[0]


def test_sign_equivalence_and_small_solver_noise():
    before=np.array([[0,0,0,1,0,0,0]],float);after=before.copy();after[0,0]=1e-6;after[0,3]=-1
    assert pose_changes(before,after)==[]


def test_float32_quaternion_norm_rounding_does_not_invent_rotation():
    q=np.array([.70710677,0,0,.70710677],dtype=np.float32)
    pose=np.r_[np.zeros(3),q][None,:]
    assert pose_changes(pose,pose.copy())==[]


@pytest.mark.parametrize('bad',[[[float('nan'),0,0,1,0,0,0]],[[0,0,0,2,0,0,0]],[[0,0,0]]])
def test_nonfinite_or_replaced_inventory_stops(bad):
    with pytest.raises(PlanningRejected):pose_changes([[0,0,0,1,0,0,0]],bad)


def test_bilateral_grasp_loss_and_slip_fail_even_when_object_is_still_in_contact():
    from curobo_v2.sapien_monitor import KitchenWorldMonitor,GraspLost,AttachmentShifted
    monitor=object.__new__(KitchenWorldMonitor)
    monitor.attachment={'holder':'left','target_id':147};monitor.attachment_transform=np.eye(4)
    monitor.attachment_provider=lambda:None;monitor._attachment_transform=lambda:np.eye(4)
    with pytest.raises(GraspLost):monitor._check_attachment()
    monitor.attachment_provider=lambda:dict(monitor.attachment)
    monitor._check_attachment()
    shifted=np.eye(4);shifted[0,3]=.0041;monitor._attachment_transform=lambda:shifted
    with pytest.raises(AttachmentShifted):monitor._check_attachment()
    shifted=np.eye(4);angle=.031;shifted[:2,:2]=[[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]]
    monitor._attachment_transform=lambda:shifted
    with pytest.raises(AttachmentShifted):monitor._check_attachment()
