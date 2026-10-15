"""SAPIEN 3.0.3 feedback adapter, preserving native target contact identity.

Contact forces are estimates: sum |J dot unit_normal| / dt per finger, N.
No tangential impulse is counted. Raw records are saved; alpha=.25 EMA is
only for control, never masks missing bilateral contact in the safety gate.
qf is caller-applied passive compensation; incoming joint forces are constraint
reactions. Neither is exposed as measured drive force.
"""
from dataclasses import asdict
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path
import numpy as np
from .grasp import Feedback, Limits, SIDES
from .contact_policy import bilateral_target_contacts, target_finger_contacts


def normal_force(points, dt):
    if not np.isfinite(dt) or dt <= 0: raise ValueError('Invalid physics timestep')
    total = 0.
    for p in points:
        n, j = np.asarray(p['normal'], float), np.asarray(p['impulse_Ns'], float)
        if n.shape != (3,) or j.shape != (3,) or not np.isfinite([n, j]).all() or abs(np.linalg.norm(n) - 1) > 1e-3:
            raise ValueError('Invalid contact impulse or normal')
        total += abs(float(np.dot(j, n / np.linalg.norm(n)))) / dt
    return total


def loaded_limits(rig):
    """Gap is minimum spacing of opposing collision mesh planes along jaw axis.

    Native fingers are mirrors with parallel prismatic axes. Extract inner-plane
    offset from the actual loaded meshes and URDF origins, never guess 2*q.
    This is a geometric bounding-plane gap, not a sensor measurement.
    """
    import trimesh
    result = {}; provenance = {}
    for side in SIDES:
        pair = [rig.jxml[f'openarm_{side}_finger_joint{i}'] for i in (1, 2)]
        a, b = pair
        mimic = b.find('mimic')
        if mimic is None or mimic.get('joint') != a.get('name') or float(mimic.get('multiplier', 1)) != 1 or float(mimic.get('offset', 0)) != 0:
            raise ValueError('Grasp v2 requires the frozen native equal-displacement mimic')
        planes = []; meshes = []
        for index, joint in enumerate(pair):
            if joint.get('type') != 'prismatic': raise ValueError('Grasp v2 requires prismatic fingers')
            axis = np.fromstring(joint.find('axis').get('xyz'), sep=' ')
            if not np.allclose(axis, [0, -1 if index == 0 else 1, 0]): raise ValueError('Unexpected jaw axis')
            if not np.allclose(np.fromstring(joint.find('origin').get('rpy'), sep=' '), 0): raise ValueError('Unexpected finger rotation')
            link = rig.xml.find(f"link[@name='{joint.find('child').get('link')}']")
            c = link.find('collision'); mesh = c.find('geometry/mesh'); origin = c.find('origin')
            if not np.allclose(np.fromstring(origin.get('rpy'), sep=' '), 0): raise ValueError('Unexpected collision rotation')
            file = Path(mesh.get('filename'))
            vertices = trimesh.load(file, force='mesh').vertices * np.fromstring(mesh.get('scale', '1 1 1'), sep=' ')
            vertices += np.fromstring(origin.get('xyz'), sep=' ') + np.fromstring(joint.find('origin').get('xyz'), sep=' ')
            planes.append(float(vertices[:, 1].max() if index == 0 else vertices[:, 1].min()))
            meshes.append(dict(path=str(file), sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
        indices = [rig.idx[j.get('name')] for j in pair]
        if not np.allclose(rig.qlimits[indices[0]], rig.qlimits[indices[1]]): raise ValueError('Unexpected finger limits')
        cap = min(float(j.find('limit').get('effort')) for j in pair)
        if any(float(rig.joints[i].force_limit) > cap + 1e-6 for i in indices): raise ValueError('Loaded drive cap exceeds URDF')
        lo, hi = map(float, rig.qlimits[indices[0]])
        result[side] = Limits(lo, hi, cap, planes[1] - planes[0])
        provenance[side] = dict(limits=asdict(result[side]), meshes=meshes,
                                jaw_gap_reference='opposing collision bounding planes in URDF joint frame')
    return result, provenance


def force_contact_points(scene, *, side, target_id):
    """All solver impulse points, including positive separation within margin.

    This does not alter native contact recognition: bilateral proof still uses
    the unchanged separation<=0 predicate. Dropping loaded margin points would
    undercount force and failed the static weight calibration.
    """
    names={f'openarm_{side}_{name}_finger' for name in ('left','right')};rows={}
    for contact in scene.get_contacts():
        a,b=[body.entity for body in contact.bodies]
        for finger,other in ((a,b),(b,a)):
            if finger.name in names and other.per_scene_id==target_id:
                rows.setdefault(finger.name,[]).extend(dict(normal=p.normal.tolist(),impulse_Ns=p.impulse.tolist(),
                    separation_m=float(p.separation),position=p.position.tolist()) for p in contact.points)
    return rows


def sample_feedback(rig, *, target, epoch, step, dt, identity_valid=True):
    q, v = np.array(rig.robot.qpos), np.array(rig.robot.qvel)
    target_id = int(target.per_scene_id)
    result = {}
    error=abs(q-rig.goal)
    full_group_ok=bool(np.max(error[rig.arm])<=.03 and np.max(error[rig.finger])<=.001 and
        np.max(abs(v[rig.arm]))<=.05 and np.max(abs(v[rig.finger]))<=.005)
    for side in SIDES:
        indices = [rig.idx[f'openarm_{side}_finger_joint{i}'] for i in (1, 2)]
        contacts = target_finger_contacts(rig.scene, side=side, target_id=target_id)
        # Joint1 is right_finger, joint2 is left_finger. Never swap them in logs.
        force_points = force_contact_points(rig.scene,side=side,target_id=target_id)
        forces = [normal_force(force_points.get(f'openarm_{side}_{name}_finger', []), dt) for name in ('right', 'left')]
        relative = np.linalg.inv(rig.robot.find_link_by_name(f'openarm_{side}_hand_tcp').entity_pose.to_transformation_matrix()) @ target.pose.to_transformation_matrix()
        result[side] = Feedback(step, epoch, q[indices].tolist(), v[indices].tolist(),
                               rig.command[indices].tolist(), target_id,
                               bool(bilateral_target_contacts(rig.scene, side=side, target_id=target_id)),
                               forces, relative.tolist(), identity_valid=identity_valid, raw_contacts=contacts, force_contact_points=force_points,full_group_completion=full_group_ok)
    return result


def apply_controller(rig, coordinator, *, stiffness=6000., damping=500.):
    """Only gripper drive properties/targets; native mimic/tendon is untouched."""
    for side, target in coordinator.targets().items():
        f = coordinator.fingers[side]
        driver, follower = [rig.joints[rig.idx[f'openarm_{side}_finger_joint{i}']] for i in (1, 2)]
        driver.set_drive_properties(stiffness, damping, f.drive_force_limit_n, mode='force')
        follower.set_drive_properties(0, 0, f.drive_force_limit_n, mode='force')
        rig.goal = rig.make_q({side + '_gripper': target}, rig.goal)
