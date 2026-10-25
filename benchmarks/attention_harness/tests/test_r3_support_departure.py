"""Monotone support departure: keep, never deepen, a measured start overlap with a declared support."""
import json
from types import SimpleNamespace as NS
import numpy as np
import pytest
from curobo_v2 import execution_settings as settings
from curobo_v2 import identity
from curobo_v2.service.curobo_service.geometry import ExactGeometryValidator
from curobo_v2.sweep import SweepValidator

SUPPORT, OTHER, CARRIED = 'counter', 'cabinet', 'carried:7'
NAMES = ['openarm_left_joint1']


def validate(monkeypatch, mode, separations, *, z_offsets=None, other=SUPPORT, measured_start=True):
    """One contact against `other` per sample, with the given separation (m)."""
    initial = np.eye(4)
    z_offsets = z_offsets or [0.]*len(separations)
    poses = iter([_shift(initial, z) for z in z_offsets])
    contacts = iter(separations)
    current = {}

    def pose(robot):
        current['pose'] = next(poses)
        return current['pose']

    def get_contacts():
        s = next(contacts)
        return [NS(bodies=[NS(entity=NS(name=CARRIED)), NS(entity=NS(name='world:'+other))],
                   points=[NS(separation=s, position=np.zeros(3))])]
    attachment = NS(initial_pose=initial, pose=pose, supports={SUPPORT}, allowed_fingers=set(),
                    artifact=lambda: {})
    fake = NS(execution_settings=settings.R3_20261025 if mode else None, attachment=attachment, joint_names=NAMES, names=NAMES, indices={NAMES[0]: 0}, joints={},
              clearance=.005, fixed_groups={},
              robot=NS(set_qpos=lambda q: None, set_qvel=lambda q: None, set_qf=lambda q: None),
              sapien=NS(Pose=lambda p: p, physx=NS(PhysxRigidDynamicComponent=object)),
              carried_actor=NS(name=CARRIED, find_component_by_type=lambda cls: NS()),
              scene=NS(step=lambda: None, get_contacts=get_contacts))
    samples = np.zeros((len(separations), 1))
    return ExactGeometryValidator.validate_single_attachment(fake, samples, {}, measured_start=measured_start)


def _shift(pose, z):
    moved = pose.copy()
    moved[2, 3] += z
    return moved


def test_floor():
    assert settings.departure_floor() == -.0001
    assert settings.departure_floor(-.004) == pytest.approx(-.0041)
    assert settings.departure_floor(.002) == -.0001  # A clear start never relaxes the historical floor.


def test_default_unchanged_start_overlap_rejected(monkeypatch):
    result = validate(monkeypatch, None, [-.004, -.004, -.003])
    assert result['collision_free'] is False and result['sample'] == 0
    assert 'support_departure' not in result


def test_monotone_keeps_start_overlap(monkeypatch):
    result = validate(monkeypatch, 'monotone', [-.004, -.004, -.002, -.0005], z_offsets=[0., .01, .02, .03])
    assert result['collision_free'] is True
    assert result['support_departure']['start_separation_m'] == {'world:'+SUPPORT: -.004}


def test_monotone_rejects_deeper_than_start(monkeypatch):
    result = validate(monkeypatch, 'monotone', [-.004, -.0043], z_offsets=[0., .01])
    assert result['collision_free'] is False and result['sample'] == 1


def test_monotone_rejects_moving_down(monkeypatch):
    result = validate(monkeypatch, 'monotone', [-.004, -.004], z_offsets=[0., -.001])
    assert result['collision_free'] is False and result['sample'] == 1


def test_monotone_never_covers_other_bodies(monkeypatch):
    result = validate(monkeypatch, 'monotone', [-.004, -.004], other=OTHER)
    assert result['collision_free'] is False and result['sample'] == 0


def test_monotone_needs_measured_start(monkeypatch):
    result = validate(monkeypatch, 'monotone', [-.004], measured_start=False)
    assert result['collision_free'] is False


def test_sweep_floor_from_measured_start(monkeypatch):
    fake = NS(initial=np.eye(4), local_shapes=[(np.eye(4), np.array([.02, .02, .02]))],
              static_corners={'world:'+SUPPORT: np.array([[0., 0., -.1], [0., 0., -.016]])},
              departure_start_gaps={})
    up = np.array([0., 0., 1.])
    fake.execution_settings=None
    fake.measured_start=True
    fake.supports={SUPPORT}
    assert SweepValidator.departure_floor(fake, 'world:'+SUPPORT, up) == -.0001
    fake.execution_settings=settings.R3_20261025
    # Object bottom at -0.020, support top at -0.016: 4 mm measured overlap.
    assert SweepValidator.departure_floor(fake, 'world:'+SUPPORT, up) == pytest.approx(-.0041)
    assert fake.departure_start_gaps['world:'+SUPPORT] == [pytest.approx(-.004)]



def test_clear_start_does_not_relax(monkeypatch):
    result=validate(monkeypatch,'monotone',[.001,-.0003])
    assert result['collision_free'] is False and result['sample']==1


def test_held_geometry_uses_same_envelope(monkeypatch):
    # 6 mm is inside the r3 sphere inflation and geometry consistency envelope.
    assert validate(monkeypatch,'monotone',[.001],z_offsets=[.006])['collision_free']
    assert not validate(monkeypatch,None,[.001],z_offsets=[.006])['collision_free']
    assert not validate(monkeypatch,'monotone',[.001],z_offsets=[.011])['collision_free']


def test_sweep_nonstart_and_nonsupport_remain_strict():
    fake=NS(execution_settings=settings.R3_20261025,measured_start=False,supports={SUPPORT})
    assert SweepValidator.departure_floor(fake,'world:'+SUPPORT,np.array([0.,0.,1.]))==-.0001
    fake.measured_start=True
    assert SweepValidator.departure_floor(fake,'world:'+OTHER,np.array([0.,0.,1.]))==-.0001


@pytest.mark.parametrize('enabled,measured_start,expected', [(True,True,[[True]*6]),(False,True,[]),(True,False,[])])
def test_sweep_locks_query_body_without_changing_contacts(tmp_path,monkeypatch,enabled,measured_start,expected):
    import sys
    import types
    urdf=tmp_path/'fake.urdf'
    urdf.write_text('''<robot name="fake"><link name="base"/><link name="openarm_left_hand_tcp"/>
    <joint name="lift" type="prismatic"><parent link="base"/><child link="openarm_left_hand_tcp"/>
    <axis xyz="0 0 1"/><limit lower="-1" upper="1"/></joint></robot>''')
    locks=[]
    class Shape:
        collision_groups=[1,1,0,0]
        def set_collision_groups(self,v):self.collision_groups=v
    class Builder:
        def __init__(self):self.shapes=[]
        def add_box_collision(self,**kwargs):self.shapes.append(Shape())
        def build(self,name):
            body=NS(collision_shapes=self.shapes,set_locked_motion_axes=lambda axes:locks.append(axes))
            return NS(name=name,find_component_by_type=lambda cls:body)
        build_static=build
    robot=NS(links=[],get_active_joints=lambda:[NS(name='lift')])
    loader=NS(ignore_pairs=[],load_file_as_articulation_builder=lambda path:NS(link_builders=[],build=lambda **kw:robot))
    scene=NS(set_timestep=lambda x:None,physx_system=NS(config=NS()),
             create_urdf_loader=lambda:loader,create_actor_builder=Builder)
    sapien=types.ModuleType('sapien');sapien.Scene=lambda x:scene;sapien.Pose=lambda *a:None
    sapien.physx=NS(PhysxCpuSystem=lambda:None,PhysxRigidDynamicComponent=object,PhysxRigidBaseComponent=object)
    monkeypatch.setitem(sys.modules,'sapien',sapien)
    world=[dict(name='held',center=[0.,0.,0.],half_size=[.02]*3),dict(name=SUPPORT,center=[0.,0.,-.04],half_size=[.02]*3)]
    pose=np.eye(4).tolist()
    context=dict(holder='left',tool_frame='openarm_left_hand_tcp',object_pose=pose,tcp_object_transform=pose,
                 shape_names=['held'],support_names=[SUPPORT])
    gate=SweepValidator(urdf,['lift'],world,{},context,execution_settings=settings.R3_20261025 if enabled else None,
                        measured_start=measured_start)
    assert locks==expected
    assert gate.carried_body.collision_shapes[0].collision_groups==[1,1,0,0]


def test_cooperative_held_consistency_does_not_relax_closure():
    pose=np.eye(4)
    attachment=NS(poses=lambda robot:{'left':pose,'right':pose},initial_pose=pose,
                  supports=set(),allowed_fingers=set(),name='held')
    fake=NS(attachment=attachment,execution_settings=settings.R3_20261025,joint_names=NAMES,names=NAMES,
            indices={NAMES[0]:0},joints={},clearance=.005,fixed_groups={},fixed_components=[],
            robot=NS(set_qpos=lambda q:None,set_qvel=lambda q:None,set_qf=lambda q:None),
            sapien=NS(Pose=lambda p:p,physx=NS(PhysxRigidDynamicComponent=object)),
            carried_actor=NS(find_component_by_type=lambda cls:NS()),scene=NS(step=lambda:None,get_contacts=lambda:[]))
    errors=iter([dict(translation_error_m=0.,rotation_error_rad=0.),
                 dict(translation_error_m=.006,rotation_error_rad=.05),
                 dict(translation_error_m=.006,rotation_error_rad=.05)])
    attachment.error=lambda a,b:next(errors)
    assert ExactGeometryValidator.validate(fake,np.zeros((1,1)),{})['collision_free']
    attachment.error=lambda a,b:dict(translation_error_m=.005,rotation_error_rad=0.)
    result=ExactGeometryValidator.validate(fake,np.zeros((1,1)),{})
    assert not result['collision_free'] and result['reason']=='closed_chain_constraint'
