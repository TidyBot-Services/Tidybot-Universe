# Vendored verbatim from ~/下载/success-teleport-20261110/scripts/cases.py (2026-11-10 success check); see REPORT.md there.
"""Per-task teleport cases.  Each case: (id, kind, expect, description, fn(env)->dict).

kind:  pos = goal state (expect True)
       neg = near miss (expect False)
       sub = composite sub-goal only (expect False)
       arm = the same goal reached with the other OpenArm gripper (physically
             should count; expect True) -- tests the 'native right gripper only' wiring
       info = diagnostic, no expectation
"""
import numpy as np
from .teleport_lib import *  # noqa


# ======================= A: open/close fixtures =======================
def _cab_set(env, fx, frac_each):
    names = list(fx.door_joint_names)
    for n, f in zip(names, frac_each):
        fx.set_joint_state(env=env, min=f, max=f, joint_names=[n])
    forward(env)
    return names


def opencab_pos(env):
    fx = env.fxtr
    names = _cab_set(env, fx, [1.0] * len(fx.door_joint_names))
    settle(env)
    return dict(doors=joint_fracs(env, fx, names), n_doors=len(names),
                ranges_deg={n: [round(float(np.degrees(x)), 1) for x in fx._joint_infos[n]['range']] for n in names})


def opencab_085(env):
    fx = env.fxtr
    names = _cab_set(env, fx, [0.85] * len(fx.door_joint_names))
    settle(env)
    return dict(doors=joint_fracs(env, fx, names))


def opencab_one(env):
    fx = env.fxtr
    n = len(fx.door_joint_names)
    if n < 2:
        return dict(skipped='single door')
    names = _cab_set(env, fx, [1.0] + [0.0] * (n - 1))
    settle(env)
    return dict(doors=joint_fracs(env, fx, names))


def opencab_092(env):
    fx = env.fxtr
    names = _cab_set(env, fx, [0.92] * len(fx.door_joint_names))
    settle(env)
    return dict(doors=joint_fracs(env, fx, names))


def _lid(env):
    bl = env.blender
    body = f'{bl.blender_lid.name}_main'
    bid = env.sim.model.body_name2id(body)
    m = env.sim.model
    jid = m.body_jntadr[bid]
    return bl, body, m.joint_id2name(jid)


def _set_lid(env, pos, quat=(1, 0, 0, 0)):
    bl, body, j = _lid(env)
    env.sim.data.set_joint_qpos(j, np.concatenate([pos, quat]))
    env.sim.data.set_joint_qvel(j, np.zeros(6))
    forward(env)


def lid_pos(env):
    bl, body, j = _lid(env)
    cp = np.array(bl.get_lid_closed_pos(env))
    _set_lid(env, cp + [0, 0, 0.005])
    settle(env)
    cur = env.sim.data.get_body_xpos(body)
    return dict(lid_err_m=round(float(np.linalg.norm(cur - cp)), 4), state=bl.get_state(),
                grip=grip_dists(env, cur), thresh=bl._BLENDER_LID_POS_THRESH)


def lid_offset(env):
    bl, body, j = _lid(env)
    cp = np.array(bl.get_lid_closed_pos(env))
    _set_lid(env, cp + [0.05, 0, 0.02])  # lid sitting crooked on the rim
    settle(env)
    cur = env.sim.data.get_body_xpos(body)
    return dict(lid_err_m=round(float(np.linalg.norm(cur - cp)), 4), state=bl.get_state())


def lid_upside(env):
    bl, body, j = _lid(env)
    cp = np.array(bl.get_lid_closed_pos(env))
    _set_lid(env, cp + [0, 0, 0.01], (0, 1, 0, 0))
    settle(env)
    cur = env.sim.data.get_body_xpos(body)
    return dict(lid_err_m=round(float(np.linalg.norm(cur - cp)), 4), state=bl.get_state())


def _lid_then_hold(env, native):
    lid_pos(env)
    bl, body, j = _lid(env)
    e = ik_any(env, native, env.sim.data.get_body_xpos(body))
    env.update_state()
    return dict(arm=f'native_{native}', ik_err=round(e, 4), grip=grip_dists(env, env.sim.data.get_body_xpos(body)))


def lid_hold_r(env):
    return _lid_then_hold(env, 'right')


def lid_hold_l(env):
    return _lid_then_hold(env, 'left')


def lid_lifted(env):
    """Lid hovering 4 cm above its seat (not seated) -> False expected."""
    bl, body, j = _lid(env)
    cp = np.array(bl.get_lid_closed_pos(env))
    _set_lid(env, cp + [0, 0, 0.035])
    env.update_state()
    return dict(state=bl.get_state())


def _drawer(env, f):
    dr = env.drawer
    dr.set_door_state(min=f, max=f, env=env)
    forward(env)
    settle(env)
    return dict(state={k: round(float(v), 4) for k, v in dr.get_door_state(env).items()},
                full_travel_m=round(float(dr.size[1] * 0.55), 3))


def drawer_pos(env):
    return _drawer(env, 1.0)


def drawer_090(env):
    return _drawer(env, 0.90)


def drawer_050(env):
    return _drawer(env, 0.50)


# ======================= B: buttons / knobs =======================
def mw_press(env, native='right', button='start_button', release=True):
    mw = env.microwave
    g = f'{mw.naming_prefix}{button}'
    other = f"{mw.naming_prefix}{'stop_button' if button == 'start_button' else 'start_button'}"
    info = press(env, native, g, release=release, avoid=(other,))
    settle(env, 10)
    info.update(state=mw.get_state(), door_open=mw.is_open(env))
    return info


def mw_pos(env):
    return mw_press(env, 'right')


def mw_other_arm(env):
    return mw_press(env, 'left')


def mw_stop_btn(env):
    return mw_press(env, 'right', 'stop_button')


def mw_no_release(env):
    mw = env.microwave
    info = press(env, 'right', f'{mw.naming_prefix}start_button', release=False, avoid=(f'{mw.naming_prefix}stop_button',))
    info.update(state=mw.get_state())
    return info


def mw_door_open(env):
    mw = env.microwave
    mw.open_door(env)
    forward(env)
    return mw_press(env, 'right')


def stove_set(env, knob, val):
    st = env.stove
    env.sim.data.set_joint_qpos(f'{st.naming_prefix}knob_{knob}_joint', val)
    forward(env)


def stove_info(env):
    st = env.stove
    return dict(target=getattr(env, 'knob', None), knobs={k: round(float(v), 3) for k, v in st.get_knobs_state(env).items()},
                on={k: st.is_burner_on(env, k) for k in st.get_knobs_state(env)})


def stove_off_pos(env):
    stove_set(env, env.knob, 0.0); settle(env)
    return stove_info(env)


def stove_still_on(env):
    stove_set(env, env.knob, 0.5); settle(env)
    return stove_info(env)


def stove_wrong_knob(env):
    others = [k for k in env.stove.get_knobs_state(env) if k != env.knob]
    for k in others:
        stove_set(env, k, 0.0)
    settle(env)
    d = stove_info(env); d['turned_off'] = others
    return d


def stove_small(env):
    """Knob turned back to 0.30 rad (17 deg) -- counts as off by the 0.35 rad threshold."""
    stove_set(env, env.knob, 0.30); settle(env)
    return stove_info(env)


def _mixer_joint(env, key):
    sm = env.stand_mixer
    return sm._joint_names[key]


def mixer_pos(env):
    sm = env.stand_mixer
    jn = _mixer_joint(env, 'head')
    sm.set_joint_state(env=env, min=1.0, max=1.0, joint_names=[jn])
    forward(env); settle(env)
    return dict(state=sm.get_state(env), head_range=[round(float(x), 3) for x in sm._joint_infos[jn]['range']])


def mixer_095(env):
    sm = env.stand_mixer
    jn = _mixer_joint(env, 'head')
    sm.set_joint_state(env=env, min=0.95, max=0.95, joint_names=[jn])
    forward(env); settle(env)
    return dict(state=sm.get_state(env))


def mixer_button_only(env):
    """Press the head-lock button fully (joint to 1.0), do not touch the head."""
    sm = env.stand_mixer
    jn = _mixer_joint(env, 'button_head_lock')
    sm.set_joint_state(env=env, min=1.0, max=1.0, joint_names=[jn])
    forward(env); settle(env, 60)
    return dict(state=sm.get_state(env), button_flag=sm._button_head_lock)


def mixer_hold_check(env):
    """Head opened to 1.0 then 3 s of physics: does it stay open without the lock?"""
    sm = env.stand_mixer
    jn = _mixer_joint(env, 'head')
    sm.set_joint_state(env=env, min=1.0, max=1.0, joint_names=[jn])
    forward(env); settle(env, 60)
    return dict(state=sm.get_state(env), raw=round(float(sm.get_joint_state(env, [jn])[jn]), 4))


# ======================= C: put on appliance then start =======================
def _pour_site(env):
    cm = env.coffee_machine
    sid = env.sim.model.site_name2id(f'{cm.naming_prefix}receptacle_place_site')
    return np.array(env.sim.data.site_xpos[sid]).copy()


def _mug_at_site(env, dxy=(0, 0), tipped=False):
    site = _pour_site(env)
    q = tipped_quat(env, 'obj') if tipped else None
    rest_obj(env, 'obj', site[:2] + np.array(dxy), site[2], quat=q)
    settle(env)
    p = obj_pos(env, 'obj')
    return dict(site=site.round(3).tolist(), mug=p.round(3).tolist(),
                xy_err=round(float(np.linalg.norm(p[:2] - site[:2])), 4), z_err=round(float(p[2] - site[2]), 4),
                upright=bool(__import__('robocasa.utils.object_utils', fromlist=['x']).check_obj_upright(env, 'obj')),
                grip=grip_dists(env, p))


def mug_pos(env):
    return _mug_at_site(env)


def mug_off5(env):
    return _mug_at_site(env, (0.05, 0.0))


def mug_tipped(env):
    return _mug_at_site(env, tipped=True)


def mug_held_r(env):
    d = _mug_at_site(env)
    d['hold'] = hold_obj(env, 'right', 'obj')
    return d


def mug_held_l(env):
    d = _mug_at_site(env)
    d['hold'] = hold_obj(env, 'left', 'obj')
    return d


def _coffee_press(env, native='right'):
    cm = env.coffee_machine
    g = f'{cm.naming_prefix}{cm._start_button_names[0]}'
    info = press(env, native, g)
    settle(env, 10)
    info['turned_on'] = cm._turned_on
    return info


def pc_pos(env):
    d = _mug_at_site(env)
    d['press'] = _coffee_press(env)
    return d


def pc_mug_only(env):
    return _mug_at_site(env)


def pc_button_only(env):
    return dict(press=_coffee_press(env), mug=obj_pos(env, 'obj').round(3).tolist())


def pc_press_first(env):
    """Order reversed: press start with no mug, then put the mug under the outlet."""
    p = _coffee_press(env)
    d = _mug_at_site(env)
    d['press_before_mug'] = p
    return d


def pc_other_arm(env):
    d = _mug_at_site(env)
    d['press'] = _coffee_press(env, 'left')
    return d


def _burners(env):
    st = env.stove
    out = {}
    knobs = st.get_knobs_state(env)
    for loc, site in st.burner_sites.items():
        if site is None or loc not in knobs:
            continue
        out[loc] = np.array(env.sim.data.get_site_xpos(site.get('name'))).copy()
    return out


def _free_burner(env):
    """Burner farthest from the distractor pan/pot."""
    b = _burners(env)
    dp = obj_pos(env, 'stove_distr')
    return max(b, key=lambda k: np.linalg.norm(b[k][:2] - dp[:2])), b


def _kettle_on(env, loc, b, dxy=(0, 0)):
    p = b[loc]
    rest_obj(env, 'obj', p[:2] + np.array(dxy), p[2])
    settle(env)
    k = obj_pos(env, 'obj')
    from robocasa.utils import object_utils as OU
    return dict(burner=loc, kettle_xy_err=round(float(np.linalg.norm(k[:2] - p[:2])), 3),
                stove_contact=bool(OU.check_obj_fixture_contact(env, 'obj', env.stove)), grip=grip_dists(env, k))


def kb_pos(env):
    loc, b = _free_burner(env)
    d = _kettle_on(env, loc, b)
    stove_set(env, loc, 1.0); settle(env, 5)
    d['knobs'] = stove_info(env)['on']
    return d


def kb_kettle_only(env):
    loc, b = _free_burner(env)
    return _kettle_on(env, loc, b)


def kb_knob_only(env):
    loc, b = _free_burner(env)
    stove_set(env, loc, 1.0); settle(env, 5)
    return dict(burner=loc, knobs=stove_info(env)['on'])


def kb_wrong_burner(env):
    loc, b = _free_burner(env)
    d = _kettle_on(env, loc, b)
    other = [k for k in b if k != loc]
    far = max(other, key=lambda k: np.linalg.norm(b[k][:2] - b[loc][:2]))
    stove_set(env, far, 1.0); settle(env, 5)
    d.update(lit=far, lit_dist=round(float(np.linalg.norm(b[far][:2] - b[loc][:2])), 3), knobs=stove_info(env)['on'])
    return d


def kb_any_lit_near(env):
    """Kettle on its burner; light the NEAREST other burner (may be < 0.15 m from the kettle)."""
    loc, b = _free_burner(env)
    d = _kettle_on(env, loc, b)
    other = [k for k in b if k != loc]
    near = min(other, key=lambda k: np.linalg.norm(b[k][:2] - b[loc][:2]))
    stove_set(env, near, 1.0); settle(env, 5)
    d.update(lit=near, lit_dist=round(float(np.linalg.norm(b[near][:2] - b[loc][:2])), 3))
    return d


def kb_held_l(env):
    d = kb_pos(env)
    d['hold'] = hold_obj(env, 'left', 'obj')
    return d


def kb_held_r(env):
    d = kb_pos(env)
    d['hold'] = hold_obj(env, 'right', 'obj')
    return d


def _toaster_rack(env):
    to = env.toaster_oven
    pre = to._get_joint_prefix()
    for kind in ('rack', 'tray'):
        n = f'{pre}{kind}0'
        try:
            bid = env.sim.model.body_name2id(n)
            return to, n, np.array(env.sim.data.body_xpos[bid]).copy()
        except Exception:
            continue
    raise RuntimeError('no rack0/tray0 body')


def _food_in(env, which=('baguette', 'kebab'), need_after_close=True):
    """Lay the food on rack 0; try several layouts and keep the first where every item touches the rack
    after the door is shut and 1 s of physics (the oven door can knock food off)."""
    to, rack, p = _toaster_rack(env)
    m = env.sim.model
    bid = m.body_name2id(rack)
    tops = [env.sim.data.geom_xpos[g][2] + m.geom_size[g][2] for g in range(m.ngeom)
            if m.geom_bodyid[g] == bid and m.geom_contype[g] and m.geom_size[g][0] > 0.05]
    z = max(tops) if tops else p[2]
    R = np.array(env.sim.data.body_xmat[bid]).reshape(3, 3)
    ax, ay = R[:, 0], R[:, 1]
    base_yaw = np.arctan2(ax[1], ax[0])
    saved = env.sim.get_state().flatten().copy()
    tried = []
    for yaw_off in (0.0, np.pi / 2):
        for sep in (0.035, 0.05, 0.025):
            for along in (ay, ax):
                env.sim.set_state_from_flattened(saved); forward(env)
                to.open_door(env); forward(env)
                yaw = base_yaw + yaw_off
                q = np.array([np.cos(yaw / 2), 0, 0, np.sin(yaw / 2)])
                offs = {'baguette': -sep, 'kebab': sep} if len(which) > 1 else {which[0]: 0.0}
                for name in which:
                    rest_obj(env, name, p[:2] + offs[name] * along[:2], z, quat=q, gap=0.004)
                settle(env, 20)
                c1 = all(to.check_rack_contact(env, n, 0) for n in which)
                ok_after = c1
                if c1 and need_after_close:
                    s2 = env.sim.get_state().flatten().copy()
                    to.set_joint_state(env=env, min=0.0, max=0.0, joint_names=[to._joint_names['door']]); forward(env)
                    settle(env, 20)
                    ok_after = all(to.check_rack_contact(env, n, 0) for n in which)
                    env.sim.set_state_from_flattened(s2); forward(env)
                tried.append([round(float(yaw_off), 2), sep, 'y' if along is ay else 'x', bool(c1), bool(ok_after)])
                if ok_after:
                    return to, dict(rack=rack, rack_top_z=round(float(z), 3), layouts_tried=tried,
                                    food={n: obj_pos(env, n).round(3).tolist() for n in which},
                                    contact={n: bool(to.check_rack_contact(env, n, 0)) for n in which})
    return to, dict(rack=rack, layouts_tried=tried, placement_failed=True,
                    contact={n: bool(to.check_rack_contact(env, n, 0)) for n in which})


def _close_set_time(env, to, door=0.0, time=0.5):
    to.set_joint_state(env=env, min=door, max=door, joint_names=[to._joint_names['door']]); forward(env)
    if time is not None:
        to.set_time(env, time)
    settle(env, 20)
    st = to.get_state()
    return dict(door=round(float(st['door']), 3) if st.get('door') is not None else None, time=st.get('time'),
                is_open=bool(to.is_open(env)))


def hk_pos(env):
    to, d = _food_in(env)
    d.update(_close_set_time(env, to))
    d['contact_after'] = {n: bool(to.check_rack_contact(env, n, 0)) for n in ('baguette', 'kebab')}
    return d


def hk_one_food(env):
    to, d = _food_in(env, ('baguette',))
    d.update(_close_set_time(env, to))
    return d


def hk_no_timer(env):
    to, d = _food_in(env)
    d.update(_close_set_time(env, to, time=None))
    return d


def hk_door_half(env):
    to, d = _food_in(env)
    d.update(_close_set_time(env, to, door=0.5))
    return d


def hk_door_open_timer(env):
    to, d = _food_in(env)
    d.update(_close_set_time(env, to, door=1.0))
    return d


def hk_food_held(env):
    """Goal state, then the right-native gripper put at the kebab (inside the oven)."""
    d = hk_pos(env)
    d['hold'] = hold_obj(env, 'right', 'kebab')
    return d


# ======================= D: cabinet pick/place =======================
def _in_cab(env, name, fx, fu=0.5, fv=0.35, quat=None, region=None):
    rname, reg = int_region(fx, region)
    p = region_point(reg, fu, fv)
    rest_obj(env, name, p[:2], p[2], quat=quat)
    return rname


def pp_pos(env):
    r = _in_cab(env, 'obj', env.cab)
    settle(env)
    from robocasa.utils import object_utils as OU
    return dict(region=r, inside=bool(OU.obj_inside_of(env, 'obj', env.cab)), grip=grip_dists(env, obj_pos(env, 'obj')))


def pp_front_edge(env):
    """Object sitting on the cabinet floor but sticking out of the opening (centre at the front edge)."""
    r = _in_cab(env, 'obj', env.cab, fv=-0.05)
    settle(env)
    from robocasa.utils import object_utils as OU
    return dict(region=r, inside_full=bool(OU.obj_inside_of(env, 'obj', env.cab)),
                inside_centre=bool(OU.obj_inside_of(env, 'obj', env.cab, partial_check=True)))


def pp_on_top(env):
    fx = env.cab
    rname, reg = int_region(fx)
    regs = fx.get_int_sites(relative=False)
    top = max(np.asarray(v[3])[2] for v in regs.values())
    p = region_point(reg, 0.5, 0.5)
    rest_obj(env, 'obj', p[:2], top + 0.03)
    settle(env)
    return dict(obj=obj_pos(env, 'obj').round(3).tolist())


def pp_outside_held_l(env):
    """Object held 8 cm OUTSIDE the cabinet opening (every bbox corner outside) by native-left; robot not stepped."""
    from robocasa.utils import object_utils as OU
    fx = env.cab
    rname, reg = int_region(fx)
    p0, px, py, pz = reg
    v = (py - p0) / np.linalg.norm(py - p0)
    half = max(env.objects['obj'].size) / 2
    out = {}
    for gap in (0.02, 0.05, 0.08, 0.12):
        c = region_point(reg, 0.5, 0.0, 0.4) - (half + gap) * v
        set_obj(env, 'obj', c, upright_quat(env, 'obj'))
        out[f'gap_{gap}'] = bool(OU.obj_inside_of(env, 'obj', fx))
    c = region_point(reg, 0.5, 0.0, 0.4) - (half + 0.08) * v
    set_obj(env, 'obj', c, upright_quat(env, 'obj'))
    h = hold_obj(env, 'left', 'obj')
    return dict(inside_by_gap=out, hold=h, grip=grip_dists(env, obj_pos(env, 'obj')))


def pp_held_r(env):
    d = pp_pos(env)
    d['hold'] = hold_obj(env, 'right', 'obj')
    return d


def pp_held_l(env):
    d = pp_pos(env)
    d['hold'] = hold_obj(env, 'left', 'obj')
    return d


def _region_of(env, fx, ref):
    z = obj_pos(env, ref)[2] - bottom_dz(env, ref)
    for k, v in fx.get_int_sites(relative=False).items():
        v = [np.asarray(x, float) for x in v]
        if v[0][2] - 0.03 <= z <= v[3][2]:
            return k, v
    return int_region(fx)


def _next_to(env, name, ref, dx=0.08):
    """Put `name` on the same shelf as `ref`, 7-13 cm away, at the free spot farthest from other objects."""
    fx = env.cab
    rname, reg = _region_of(env, fx, ref)
    p0, px, py, pz = reg
    lu, lv = np.linalg.norm(px - p0), np.linalg.norm(py - p0)
    u, v = (px - p0) / lu, (py - p0) / lv
    rp = obj_pos(env, ref)
    ref_bottom = rp[2] - bottom_dz(env, ref)
    others = [n for n in env.objects if n not in (name, ref)]
    rad = lambda n: max(env.objects[n].size[:2]) / 2
    best, score = None, -9
    for d in (0.07, 0.09, 0.11, 0.13):
        for a in np.radians(np.arange(0, 360, 30)):
            q = rp + d * (np.cos(a) * u + np.sin(a) * v)
            fu, fv = np.dot(q - p0, u) / lu, np.dot(q - p0, v) / lv
            if not (0.08 < fu < 0.92 and 0.2 < fv < 0.85):
                continue
            sc = min([np.linalg.norm(q[:2] - obj_pos(env, n)[:2]) - rad(n) - rad(name) for n in others] + [
                     np.linalg.norm(q[:2] - rp[:2]) - rad(ref) - rad(name)])
            if sc > score:
                best, score = q, sc
    if best is None:
        best = rp + dx * u
    rest_obj(env, name, best[:2], ref_bottom)
    return best


def cc_pos(env):
    _next_to(env, 'obj1', 'cab_obj1'); settle(env, 10)
    _next_to(env, 'obj2', 'cab_obj2'); settle(env)
    return _cc_info(env)


def _cc_info(env):
    from robocasa.utils import object_utils as OU
    return dict(in_cab={n: bool(OU.obj_inside_of(env, n, env.cab)) for n in ('obj1', 'obj2')},
                d1=round(float(env._xy_dist('obj1', 'cab_obj1')), 3), d2=round(float(env._xy_dist('obj2', 'cab_obj2')), 3),
                cab_pair_dist=round(float(env._xy_dist('cab_obj1', 'cab_obj2')), 3))


def cc_one(env):
    _next_to(env, 'obj1', 'cab_obj1'); settle(env)
    return _cc_info(env)


def cc_swapped(env):
    """Bottle next to the shaker, shaker next to the bottle (wrong categories)."""
    _next_to(env, 'obj1', 'cab_obj2'); settle(env, 10)
    _next_to(env, 'obj2', 'cab_obj1'); settle(env)
    return _cc_info(env)


def cc_held_l(env):
    d = cc_pos(env)
    d['hold'] = hold_obj(env, 'left', 'obj1')
    return d


def _scale_put(env, tipped=False):
    sp = obj_pos(env, 'digital_scale')
    top = sp[2] + top_dz(env, 'digital_scale')
    q = tipped_quat(env, 'obj') if tipped else None
    rest_obj(env, 'obj', sp[:2], top, quat=q)


def _close_cab(env, frac=0.0):
    fx = env.cab
    fx.set_joint_state(env=env, min=frac, max=frac, joint_names=list(fx.door_joint_names)); forward(env)


def _wi_info(env):
    from robocasa.utils import object_utils as OU
    return dict(on_scale=bool(OU.check_obj_in_receptacle(env, 'obj', 'digital_scale')),
                upright=bool(OU.check_obj_upright(env, 'obj')), cab_closed=bool(env.cab.is_closed(env)),
                doors=joint_fracs(env, env.cab, list(env.cab.door_joint_names)), grip=grip_dists(env, obj_pos(env, 'obj')))


def wi_pos(env):
    _scale_put(env); _close_cab(env); settle(env)
    return _wi_info(env)


def wi_doors_open(env):
    _scale_put(env); settle(env)
    return _wi_info(env)


def wi_doors_only(env):
    _close_cab(env); settle(env)
    return _wi_info(env)


def wi_tipped(env):
    _scale_put(env, tipped=True); _close_cab(env); settle(env)
    return _wi_info(env)


def wi_door_1pct(env):
    _scale_put(env); _close_cab(env, 0.02); settle(env)
    return _wi_info(env)


def wi_distractor(env):
    """Wrong object (the distractor food) on the scale, doors closed."""
    sp = obj_pos(env, 'digital_scale')
    rest_obj(env, 'obj_distractor', sp[:2], sp[2] + top_dz(env, 'digital_scale'))
    _close_cab(env); settle(env)
    return _wi_info(env)


def wi_held_l(env):
    d = wi_pos(env)
    d['hold'] = hold_obj(env, 'left', 'obj')
    return d


def wi_held_r(env):
    d = wi_pos(env)
    d['hold'] = hold_obj(env, 'right', 'obj')
    return d


def _tray_put(env, names):
    tp = obj_pos(env, 'container')
    top = tp[2] + top_dz(env, 'container') - 0.01
    R = env.objects['container'].horizontal_radius
    offs = {'obj2': np.array([-0.3 * R, 0]), 'obj': np.array([0.35 * R, 0])}
    for n in names:
        rest_obj(env, n, tp[:2] + offs[n], top)
        settle(env, 10)
    return dict(tray_radius=round(float(R), 3))


def at_mug_held_l(env):
    d = at_pos(env)
    d['hold'] = hold_obj(env, 'left', 'obj')
    return d


def _at_info(env):
    from robocasa.utils import object_utils as OU
    return dict(mug_on_tray=bool(OU.check_obj_in_receptacle(env, 'obj', 'container')),
                kettle_on_tray=bool(OU.check_obj_in_receptacle(env, 'obj2', 'container')),
                cab_closed=bool(env.cab.is_closed(env)), grip_mug=grip_dists(env, obj_pos(env, 'obj')),
                grip_kettle=grip_dists(env, obj_pos(env, 'obj2')))


def at_pos(env):
    d = _tray_put(env, ['obj2', 'obj']); _close_cab(env); settle(env)
    d.update(_at_info(env)); return d


def at_kettle_only(env):
    d = _tray_put(env, ['obj2']); _close_cab(env); settle(env)
    d.update(_at_info(env)); return d


def at_doors_open(env):
    d = _tray_put(env, ['obj2', 'obj']); settle(env)
    d.update(_at_info(env)); return d


def at_kettle_held(env):
    """Goal state, then the right-native gripper put on the KETTLE (only the mug is checked for release)."""
    d = at_pos(env)
    d['hold'] = hold_obj(env, 'right', 'obj2')
    d['grip_after'] = dict(mug=grip_dists(env, obj_pos(env, 'obj')), kettle=grip_dists(env, obj_pos(env, 'obj2')))
    return d


CASES = {
    'OpenCabinet': [
        ('pos', 'pos', True, '所有门开到 100%', opencab_pos),
        ('open92', 'info', None, '所有门开到 92%（刚过 90% 门槛）', opencab_092),
        ('open85', 'neg', False, '所有门开到 85%', opencab_085),
        ('one_door', 'neg', False, '双门柜只开一扇', opencab_one),
    ],
    'CloseBlenderLid': [
        ('pos', 'pos', True, '盖子放回盖座、正放', lid_pos),
        ('crooked', 'neg', False, '盖子偏 5 cm 斜搭在杯口', lid_offset),
        ('upside', 'neg', False, '盖子倒扣在盖座位置', lid_upside),
        ('hover', 'neg', False, '盖子悬在盖座上方 3.5 cm（没放下）', lid_lifted),
        ('held_r', 'neg', False, '盖好但 native-right 夹爪还在盖子上', lid_hold_r),
        ('held_l', 'neg', False, '盖好但 native-left 夹爪还在盖子上', lid_hold_l),
    ],
    'OpenDrawer': [
        ('pos', 'pos', True, '抽屉拉到底', drawer_pos),
        ('open90', 'neg', False, '抽屉拉开 90%', drawer_090),
        ('open50', 'neg', False, '抽屉拉开一半', drawer_050),
    ],
    'TurnOnMicrowave': [
        ('pos', 'pos', True, 'native-right 夹爪按启动键后移开', mw_pos),
        ('other_arm', 'arm', True, '换 native-left 夹爪按启动键后移开', mw_other_arm),
        ('stop_btn', 'neg', False, '按的是停止键', mw_stop_btn),
        ('no_release', 'neg', False, '按住启动键不松开', mw_no_release),
        ('door_open', 'neg', False, '门开着时按启动键', mw_door_open),
    ],
    'TurnOffStove': [
        ('pos', 'pos', True, '目标旋钮转回 0', stove_off_pos),
        ('still_on', 'neg', False, '目标旋钮还在 0.5 rad（开着）', stove_still_on),
        ('wrong_knob', 'neg', False, '关掉其它所有旋钮，目标旋钮不动', stove_wrong_knob),
        ('small', 'info', None, '目标旋钮停在 0.30 rad（约 17°，没完全关）', stove_small),
    ],
    'OpenStandMixerHead': [
        ('pos', 'pos', True, '机头抬到 100%', mixer_pos),
        ('open95', 'neg', False, '机头抬到 95%', mixer_095),
        ('button_only', 'info', None, '只把锁头按钮按到底，不碰机头', mixer_button_only),
        ('stays', 'info', None, '机头抬到 100% 后放 3 秒，看会不会落下', mixer_hold_check),
    ],
    'CoffeeSetupMug': [
        ('pos', 'pos', True, '杯子正放在出口下', mug_pos),
        ('off5', 'neg', False, '杯子偏 5 cm', mug_off5),
        ('tipped', 'neg', False, '杯子侧躺在出口下', mug_tipped),
        ('held_r', 'neg', False, '杯子到位但 native-right 夹爪还握着', mug_held_r),
        ('held_l', 'neg', False, '杯子到位但 native-left 夹爪还握着', mug_held_l),
    ],
    'PrepareCoffee': [
        ('pos', 'pos', True, '杯子放好 + native-right 按启动键', pc_pos),
        ('mug_only', 'sub', False, '只放杯子不按键', pc_mug_only),
        ('button_only', 'sub', False, '只按键不放杯子', pc_button_only),
        ('press_first', 'neg', False, '先按键（没杯子），再把杯子放上', pc_press_first),
        ('other_arm', 'arm', True, '杯子放好 + native-left 按启动键', pc_other_arm),
    ],
    'KettleBoiling': [
        ('pos', 'pos', True, '水壶放在空灶头上 + 该灶头打开', kb_pos),
        ('kettle_only', 'sub', False, '只放水壶不开火', kb_kettle_only),
        ('knob_only', 'sub', False, '只开火不放水壶', kb_knob_only),
        ('wrong_burner', 'neg', False, '水壶在灶头 X，开的是最远的灶头 Y', kb_wrong_burner),
        ('near_burner', 'info', None, '水壶在灶头 X，开的是最近的另一个灶头', kb_any_lit_near),
        ('held_r', 'neg', False, '到位但 native-right 夹爪还握着水壶', kb_held_r),
        ('held_l', 'neg', False, '到位但 native-left 夹爪还握着水壶', kb_held_l),
    ],
    'HeatKebabSandwich': [
        ('pos', 'pos', True, '两样食物在架上 + 关门 + 定时 0.5', hk_pos),
        ('one_food', 'sub', False, '只放面包', hk_one_food),
        ('no_timer', 'sub', False, '两样都放、关门、不拧定时', hk_no_timer),
        ('door_half', 'neg', False, '门只关到一半 + 定时', hk_door_half),
        ('door_open', 'neg', False, '门全开 + 定时', hk_door_open_timer),
        ('held', 'info', None, '到位后 native-right 夹爪还在烤串上', hk_food_held),
    ],
    'PickPlaceCounterToCabinet': [
        ('pos', 'pos', True, '物体放进柜子中间', pp_pos),
        ('front_edge', 'neg', False, '物体卡在柜口，一半在外', pp_front_edge),
        ('on_top', 'neg', False, '物体放在吊柜顶板上面（柜子外面）', pp_on_top),
        ('outside_held', 'neg', False, '物体被 native-left 夹爪举在柜口外 8 cm（整个物体在柜外）', pp_outside_held_l),
        ('held_r', 'neg', False, '放进去但 native-right 夹爪还握着', pp_held_r),
        ('held_l', 'neg', False, '放进去但 native-left 夹爪还握着', pp_held_l),
    ],
    'CategorizeCondiments': [
        ('pos', 'pos', True, '两样调料各放到柜里同类旁边 8 cm', cc_pos),
        ('one', 'sub', False, '只放了瓶子', cc_one),
        ('swapped', 'neg', False, '放反：瓶子挨着调料罐，调料罐挨着瓶子', cc_swapped),
        ('held_l', 'neg', False, '到位但 native-left 夹爪还握着瓶子', cc_held_l),
    ],
    'WeighIngredients': [
        ('pos', 'pos', True, '食品正放在秤上 + 关柜门', wi_pos),
        ('doors_open', 'sub', False, '食品在秤上，柜门没关', wi_doors_open),
        ('doors_only', 'sub', False, '只关柜门，食品还在柜里', wi_doors_only),
        ('tipped', 'neg', False, '食品侧躺在秤上 + 关门', wi_tipped),
        ('door2pct', 'info', None, '食品在秤上，柜门留 2% 缝', wi_door_1pct),
        ('distractor', 'neg', False, '把另一包（干扰物）放上秤 + 关门', wi_distractor),
        ('held_r', 'neg', False, '到位但 native-right 夹爪还握着食品', wi_held_r),
        ('held_l', 'neg', False, '到位但 native-left 夹爪还握着食品', wi_held_l),
    ],
    'ArrangeTea': [
        ('pos', 'pos', True, '水壶 + 杯子都在托盘上 + 关柜门', at_pos),
        ('kettle_only', 'sub', False, '只放水壶 + 关门', at_kettle_only),
        ('doors_open', 'sub', False, '两样都在托盘上，柜门没关', at_doors_open),
        ('kettle_held', 'neg', False, '到位后 native-right 夹爪握着水壶', at_kettle_held),
        ('mug_held_l', 'neg', False, '到位后 native-left 夹爪还握着杯子', at_mug_held_l),
    ],
}

SEEDS = {
    'OpenCabinet': [101, 102, 103], 'CloseBlenderLid': [101, 102, 103], 'OpenDrawer': [101, 102, 103],
    'TurnOnMicrowave': [101, 102, 103], 'TurnOffStove': [101, 102, 103], 'OpenStandMixerHead': [101, 102, 103],
    'CoffeeSetupMug': [101, 103, 104], 'PrepareCoffee': [102, 103, 104], 'KettleBoiling': [101, 102, 103],
    'HeatKebabSandwich': [103, 105, 106], 'PickPlaceCounterToCabinet': [101, 102, 107],
    'CategorizeCondiments': [102, 103, 105], 'WeighIngredients': [101, 102, 103], 'ArrangeTea': [104, 105, 106],
}
