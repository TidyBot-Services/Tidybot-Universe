"""Dev-only initial-state fixture, applied after the real reset is validated."""
import math
import numpy as np

SETTLE_STEPS = 5


def apply_test_fixture(env, spec):
    from .dev_config import validate_test_fixture
    validate_test_fixture(spec)
    sim = env.sim
    robot = env.robots[0]
    objects = env.objects.values() if isinstance(env.objects, dict) else env.objects  # robosuite keeps a name -> object dict
    names = {obj.name: obj for obj in objects}
    unknown = set(spec.get('object_poses', {})) - set(names)
    if unknown:
        raise ValueError('test_fixture unknown object names: ' + ', '.join(sorted(unknown)))
    base_indices = robot._ref_base_joint_pos_indexes
    base_before = np.asarray(sim.data.qpos[base_indices], dtype=float).copy()
    base_target = base_before.copy()
    base_target[0] += spec.get('base_delta', {}).get('forward', 0)
    base_target[1] += spec.get('base_delta', {}).get('side', 0)
    sim.data.qpos[base_indices] = base_target
    base_vel_indices = getattr(robot, '_ref_base_joint_vel_indexes', None)
    if base_vel_indices is None:  # WheeledRobot keeps only qpos indexes; map each base joint's qpos address to its dof
        model = sim.model._model if hasattr(sim.model, '_model') else sim.model
        base_vel_indices = [int(model.jnt_dofadr[[j for j in range(model.njnt) if model.jnt_qposadr[j] == i][0]]) for i in base_indices]
    sim.data.qvel[base_vel_indices] = 0
    base_ctrl = robot.composite_controller.part_controllers['base']
    if base_ctrl.name != 'JOINT_VELOCITY':
        raise ValueError('test_fixture requires JOINT_VELOCITY base controller')
    base_ctrl.set_goal(np.zeros(base_ctrl.control_dim))
    object_before = {}
    for name, pose in spec.get('object_poses', {}).items():
        joints = names[name].joints
        if len(joints) != 1:
            raise ValueError('test_fixture object must have one free joint: ' + name)
        joint = joints[0]
        qaddr = sim.model.get_joint_qpos_addr(joint)
        vaddr = sim.model.get_joint_qvel_addr(joint)
        if not isinstance(qaddr, (tuple, list)) or qaddr[1]-qaddr[0] != 7 or not isinstance(vaddr, (tuple, list)) or vaddr[1]-vaddr[0] != 6:
            raise ValueError('test_fixture object must have a free joint: ' + name)
        object_before[name] = np.asarray(sim.data.qpos[qaddr[0]:qaddr[1]], dtype=float).tolist()
        sim.data.qpos[qaddr[0]:qaddr[1]] = pose
        sim.data.qvel[vaddr[0]:vaddr[1]] = 0
    sim.forward()
    action = np.zeros(len(env.action_spec[0]))
    parts = robot.composite_controller._action_split_indexes
    for side in ('left', 'right'):
        ctrl = robot.composite_controller.part_controllers[side]
        ctrl.set_goal(np.asarray(sim.data.qpos[ctrl.qpos_index], dtype=float).copy())
        start, end = parts[side]
        action[start:end] = sim.data.qpos[ctrl.qpos_index]
    for _ in range(SETTLE_STEPS):
        env.step(action)
    sim.forward()  # mj_step leaves body poses one integration behind qpos; the planner's FK alignment reads both
    base_after = np.asarray(sim.data.qpos[base_indices], dtype=float).copy()
    base_drift = np.abs(base_after-base_target)
    objects = {}
    failures = []
    for name, pose in spec.get('object_poses', {}).items():
        qaddr = sim.model.get_joint_qpos_addr(names[name].joints[0])
        after = np.asarray(sim.data.qpos[qaddr[0]:qaddr[1]], dtype=float).copy()
        target = np.asarray(pose, dtype=float)
        position_error = float(np.linalg.norm(after[:3]-target[:3]))
        norm=float(np.linalg.norm(after[3:]))
        angle_error = 180. if not math.isfinite(norm) or norm<1e-12 else math.degrees(2*math.acos(min(1., abs(float(np.dot(after[3:], target[3:])))/norm)))
        if not math.isfinite(position_error):position_error=float('inf')
        objects[name] = dict(requested=pose, before=object_before[name], after=after.tolist(), position_error_m=position_error, angle_error_deg=angle_error)
        if position_error > .01 or angle_error > 10:failures.append(f'{name} position_error_m={position_error:.9g} angle_error_deg={angle_error:.9g}')
    receipt = dict(requested=spec, base_qpos=dict(before=base_before.tolist(), requested=base_target.tolist(), after=base_after.tolist(), drift_m=base_drift.tolist()), object_poses=objects, settle_steps=SETTLE_STEPS)
    if not np.isfinite(base_drift).all() or np.any(base_drift[:2] > .002):failures.insert(0, 'base drift_m=' + repr(base_drift.tolist()))
    if failures:raise RuntimeError('test_fixture settle failed: ' + '; '.join(failures))
    return receipt
