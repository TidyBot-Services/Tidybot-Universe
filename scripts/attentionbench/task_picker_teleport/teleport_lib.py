# Vendored verbatim from ~/下载/success-teleport-20261110/scripts/teleport_lib.py (2026-11-10 success check),
# except: the screening make_env import is removed (task_picker_pictures builds the scene with create_env, as runs do).
"""Shared helpers for the teleport ("瞬移法") check of RoboCasa365 native success criteria.

CPU only, no rendering, no planner, no agent. Each case builds a fresh env
(same make_env as the 10-29 screening: OpenArm365, layout/style (11,34)),
sets simulator state directly, settles with zero (hold) actions, and calls
env._check_success().
"""
import sys, math
import numpy as np


PHYSICAL_OF_NATIVE = {'right': 'left', 'left': 'right'}  # openarm365_dev_v2.environment.PHYSICAL_TO_NATIVE inverted


def mj(env):
    return env.sim.model._model, env.sim.data._data


def forward(env):
    env.sim.forward()


def settle(env, n=30):
    a = np.zeros(env.action_dim)
    for _ in range(n):
        env.step(a)


def ok(env):
    return bool(env._check_success())


# ---------------- objects ----------------
def obj_joint(env, name):
    return env.objects[name].joints[0]


def obj_pos(env, name):
    return np.array(env.sim.data.body_xpos[env.obj_body_id[name]]).copy()


def obj_quat(env, name):
    return np.array(env.sim.data.body_xquat[env.obj_body_id[name]]).copy()


def set_obj(env, name, pos, quat=None):
    if quat is None:
        quat = obj_quat(env, name)
    j = obj_joint(env, name)
    env.sim.data.set_joint_qpos(j, np.concatenate([np.asarray(pos, float), np.asarray(quat, float)]))
    env.sim.data.set_joint_qvel(j, np.zeros(6))
    forward(env)


def bottom_dz(env, name):
    """Distance from body origin down to the object's bottom (positive)."""
    return -float(np.asarray(env.objects[name].bottom_offset)[2])


def top_dz(env, name):
    return float(np.asarray(env.objects[name].top_offset)[2])


def rest_obj(env, name, xy, surface_z, quat=None, gap=0.01):
    if quat is None:
        quat = upright_quat(env, name)
    set_obj(env, name, [xy[0], xy[1], surface_z + bottom_dz(env, name) + gap], quat)


def upright_quat(env, name):
    """Keep yaw, zero roll/pitch (wxyz)."""
    w, x, y, z = obj_quat(env, name)
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    return np.array([math.cos(yaw / 2), 0, 0, math.sin(yaw / 2)])


def tipped_quat(env, name, deg=90):
    """Lying on its side: rotate about world x by deg (wxyz)."""
    a = math.radians(deg) / 2
    q = np.array([math.cos(a), math.sin(a), 0, 0])
    return quat_mul(q, upright_quat(env, name))


def quat_mul(a, b):
    w1, x1, y1, z1 = a; w2, x2, y2, z2 = b
    return np.array([w1*w2 - x1*x2 - y1*y2 - z1*z2, w1*x2 + x1*w2 + y1*z2 - z1*y2,
                     w1*y2 - x1*z2 + y1*w2 + z1*x2, w1*z2 + x1*y2 - y1*x2 + z1*w2])


# ---------------- robot ----------------
def eef(env, native):
    return np.array(env.sim.data.site_xpos[env.robots[0].eef_site_id[native]]).copy()


def chain_joints(env, native):
    """Hinge/slide joints between the eef site and the world (arm only, no base)."""
    m, d = mj(env)
    sid = env.robots[0].eef_site_id[native]
    b = m.site_bodyid[sid]
    out = []
    while b > 0:
        for j in range(m.body_jntadr[b], m.body_jntadr[b] + m.body_jntnum[b]):
            if j >= 0:
                out.append(j)
        b = m.body_parentid[b]
    names = [env.sim.model.joint_id2name(j) for j in out]
    arm = [j for j, n in zip(out, names) if 'openarm' in n]
    base = [j for j, n in zip(out, names) if 'mobile' in n]
    return arm, base


def ik(env, native, target, use_base=False, iters=600, tol=0.008):
    import mujoco
    m, d = mj(env)
    sid = env.robots[0].eef_site_id[native]
    arm, base = chain_joints(env, native)
    js = arm + (base if use_base else [])
    dofs = [m.jnt_dofadr[j] for j in js]
    qadr = [m.jnt_qposadr[j] for j in js]
    target = np.asarray(target, float)
    err = None
    for _ in range(iters):
        mujoco.mj_kinematics(m, d); mujoco.mj_comPos(m, d)
        err = target - d.site_xpos[sid]
        if np.linalg.norm(err) < tol:
            break
        jacp = np.zeros((3, m.nv)); mujoco.mj_jacSite(m, d, jacp, None, sid)
        J = jacp[:, dofs]
        dq = J.T @ np.linalg.solve(J @ J.T + 1e-3 * np.eye(3), err)
        dq = np.clip(dq, -0.08, 0.08)
        for k, (j, a) in enumerate(zip(js, qadr)):
            v = d.qpos[a] + dq[k]
            if m.jnt_limited[j]:
                v = np.clip(v, *m.jnt_range[j])
            d.qpos[a] = v
    d.qvel[:] = 0
    forward(env)
    return float(np.linalg.norm(target - d.site_xpos[sid]))


def ik_any(env, native, target):
    e = ik(env, native, target)
    if e > 0.02:
        e = ik(env, native, target, use_base=True)
    return e


def robot_qpos(env):
    m, d = mj(env)
    idx = []
    for native in ('left', 'right'):
        arm, base = chain_joints(env, native)
        idx += [m.jnt_qposadr[j] for j in arm + base]
    idx = sorted(set(idx))
    return idx, d.qpos[idx].copy()


def restore_robot(env, saved):
    idx, q = saved
    env.sim.data._data.qpos[idx] = q
    env.sim.data._data.qvel[:] = 0
    forward(env)


def grip_dists(env, point):
    return {f'native_{n}(physical_{PHYSICAL_OF_NATIVE[n]})': round(float(np.linalg.norm(eef(env, n) - point)), 3)
            for n in ('right', 'left')}


def touch_geom(env, native, geom_name, avoid=()):
    """Move one gripper onto a geom until the gripper contacts it (and none of `avoid`); returns (contact, ik_err)."""
    gid = env.sim.model.geom_name2id(geom_name)
    p = np.array(env.sim.data.geom_xpos[gid]).copy()
    grip = env.robots[0].gripper[native]
    best = (False, None)
    offsets = [np.zeros(3)] + [np.array(v) * s for s in (0.01, 0.02) for v in
                               [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]]
    for off in offsets:
        e = ik_any(env, native, p + off)
        c = bool(env.check_contact(grip, geom_name)) and not any(env.check_contact(grip, a) for a in avoid)
        best = (c, round(e, 4))
        if c:
            return best
    return best


def press(env, native, geom_name, release=True, updates=2, avoid=()):
    """Touch a button with one gripper, let fixtures see the contact, then move the arm back."""
    saved = robot_qpos(env)
    contact, err = touch_geom(env, native, geom_name, avoid)
    for _ in range(updates):
        env.update_state()
    info = dict(arm=f'native_{native}(physical_{PHYSICAL_OF_NATIVE[native]})', contact=contact, ik_err=err)
    if release:
        restore_robot(env, saved)
        env.update_state()
        env.update_state()
    return info


def hold_obj(env, native, name):
    """Put one gripper at the object's centre (as if still holding it); no physics step."""
    e = ik_any(env, native, obj_pos(env, name))
    return dict(arm=f'native_{native}(physical_{PHYSICAL_OF_NATIVE[native]})', ik_err=round(e, 4))


def back_off(env, d=0.6):
    """Teleport the mobile base straight back (robot walked away)."""
    m, dd = mj(env)
    for n in ('mobilebase0_joint_mobile_forward',):
        a = env.sim.model.get_joint_qpos_addr(n)
        dd.qpos[a] -= d
    dd.qvel[:] = 0
    forward(env)


# ---------------- fixtures ----------------
def int_region(fx, which=None):
    """Return (p0, px, py, pz) of an interior region (lowest by default)."""
    regs = fx.get_int_sites(relative=False)
    items = list(regs.items())
    if which is not None:
        items = [(k, v) for k, v in items if k == which]
    items.sort(key=lambda kv: kv[1][0][2])
    return items[0][0], [np.asarray(x, float) for x in items[0][1]]


def region_point(reg, fu, fv, fw=0.0):
    p0, px, py, pz = reg
    return p0 + fu * (px - p0) + fv * (py - p0) + fw * (pz - p0)


def joint_fracs(env, fx, names):
    return {k: round(float(v), 4) for k, v in fx.get_joint_state(env, names).items()}
