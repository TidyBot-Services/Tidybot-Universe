"""Real PhysX checks for the continuous gate's allowed carried contacts."""
import numpy as np
import pytest

from curobo_v2.sweep import PADDING, SweepValidator


def _scene(tmp_path, *, palm_x, finger_x):
    pytest.importorskip('sapien', reason='requires the official SAPIEN environment')
    urdf = tmp_path / 'contact.urdf'
    urdf.write_text(f'''<robot name="contact">
      <link name="base"/>
      <link name="openarm_left_palm">
        <collision><origin xyz="{palm_x} 0 0"/><geometry><box size="0.04 0.04 0.04"/></geometry></collision>
      </link>
      <link name="openarm_left_left_finger">
        <collision><geometry><box size="0.04 0.04 0.04"/></geometry></collision>
      </link>
      <link name="openarm_left_hand_tcp"/>
      <joint name="palm_fixed" type="fixed"><parent link="base"/><child link="openarm_left_palm"/></joint>
      <joint name="finger_slide" type="prismatic">
        <parent link="openarm_left_palm"/><child link="openarm_left_left_finger"/>
        <origin xyz="{finger_x} 0 0"/><axis xyz="1 0 0"/>
        <limit lower="-0.5" upper="0.5" effort="10" velocity="1"/>
      </joint>
      <joint name="tcp_fixed" type="fixed"><parent link="openarm_left_palm"/><child link="openarm_left_hand_tcp"/></joint>
    </robot>''')
    pose = np.eye(4).tolist()
    world = [dict(name='held', center=[0., 0., 0.], half_size=[.03, .03, .03], quat_wxyz=[1., 0., 0., 0.])]
    attachment = dict(holder='left', tool_frame='openarm_left_hand_tcp', object_pose=pose,
                      tcp_object_transform=pose, shape_names=['held'], support_names=[])
    response = dict(status='success', joint_names=['finger_slide'], trajectory=[[0.], [0.]],
                    time_from_start_seconds=[0., 1.],
                    trajectory_derivatives=dict(velocity=[[0.], [0.]], acceleration=[[0.], [0.]]))
    return SweepValidator(urdf, response['joint_names'], world, {}, attachment), response


def _can_collide(a, b):
    a, b = a.collision_groups, b.collision_groups
    return bool((a[0] & b[1] or a[1] & b[0])
                and not (a[2] & b[2] and (a[3] & 0xffff) == (b[3] & 0xffff)))


def test_allowed_finger_overlap_does_not_move_certificate_geometry(tmp_path):
    gate, response = _scene(tmp_path, palm_x=.2, finger_x=0.)
    carried = gate.carried_body.collision_shapes[0]
    finger = gate.robot.find_link_by_name('openarm_left_left_finger').collision_shapes[0]
    palm = gate.robot.find_link_by_name('openarm_left_palm').collision_shapes[0]
    assert not _can_collide(finger, carried)
    assert _can_collide(palm, carried)
    receipt = gate.certify(response)
    assert receipt['result'] == 'PASS', receipt['failure']
    assert receipt['queries'] == 1
    assert receipt['maximum_query_step_point_displacement_m'] <= PADDING / 4


def test_carried_palm_overlap_remains_a_violation(tmp_path):
    gate, response = _scene(tmp_path, palm_x=0., finger_x=.2)
    carried = gate.carried_body.collision_shapes[0]
    palm = gate.robot.find_link_by_name('openarm_left_palm').collision_shapes[0]
    assert _can_collide(palm, carried)
    receipt = gate.certify(response)
    assert receipt['result'] != 'PASS'
    assert receipt['failure']['reason'] == 'numerical_query_geometry_moved'
    assert any({body.entity.name for body in contact.bodies}
               == {'openarm_left_palm', gate.carried_name}
               and any(point.separation < 0 for point in contact.points)
               for contact in gate.scene.get_contacts())
