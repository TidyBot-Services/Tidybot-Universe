"""AttentionBench success rule for R5 fixture runs (rule `attentionbench-fixture-success-v2`).

RoboCasa's own `_check_success()` and fixture state machines read only
`robots[0].gripper["right"]` / `eef_site_id["right"]`.  On OpenArm365 that is the
physical LEFT arm, so upstream (a) ignores a button pressed by the physical right
gripper and (b) checks "released" only for the left gripper.  This module fixes both
in our layer, never in the upstream sources (identity-checked, kept clean):

* either arm presses: `env.check_contact(<native-right gripper>, x)` also counts the
  native-left gripper, so every fixture state machine (microwave, coffee machine,
  blender button, ...) registers a press from either hand;
* both arms released: the upstream check is evaluated twice, once per gripper site,
  and both must pass, so either hand still holding/touching at the goal is a failure.

Task fixes found by the 2026-11-10 teleport check (~/下载/success-teleport-20261110):
* HeatKebabSandwich: the door must be closed (every door joint <= 0.01 of its range,
  the oven's own snap-shut threshold), and the timer must have last been turned while
  the door was closed with both foods on the rack.  Upstream accepted a door 17–62 % open.
* PrepareCoffee: the mug must already be under the outlet (upstream pour-site rule)
  when the machine turns on.  Upstream accepted pressing first and adding the mug later.
* PrepareCoffee / CoffeeSetupMug: the mug must be upright (roll/pitch < 15 deg, the
  upstream `check_obj_upright` default).  Upstream accepted a mug lying on its side.
* OpenStandMixerHead: the head must be open (> 0.99, upstream) with neither gripper
  touching the head, i.e. it stays open after release.  The lock button has no effect
  upstream; on seed 101 the head falls shut when released, so that seed cannot pass.

Only R5 fixture runs install this; legacy/formal runs keep the upstream verdict.
"""
import numpy as np

RULE = 'attentionbench-fixture-success-v2'
KEBAB_DOOR_CLOSED = 0.01
UPRIGHT_DEG = 15


def _grippers(env):
    g = env.robots[0].gripper
    return g['right'], g['left']


def _install_contact(env):
    original = env.check_contact

    def check_contact(geoms_1, geoms_2=None):
        right, left = _grippers(env)
        if geoms_1 is right:
            return bool(original(right, geoms_2)) or bool(original(left, geoms_2))
        if geoms_2 is right:
            return bool(original(geoms_1, right)) or bool(original(geoms_1, left))
        return original(geoms_1, geoms_2)

    env.check_contact = check_contact
    env._attentionbench_upstream_check_contact = original


# ---------------------------------------------------------------- task monitors
def _door_fraction(env, oven):
    jn = oven._joint_names['door']
    return float(oven.get_joint_state(env, [jn])[jn])


def _kebab_foods_in(env, oven):
    def on(name):
        return any(oven.check_rack_contact(env, name, level) for level in (0, 1))
    return on('baguette') and on('kebab')


def _time_value(env, oven):
    jn = oven._joint_names['time']
    return float(oven.get_joint_state(env, [jn])[jn])


def _mug_at_outlet(env):
    return bool(env.coffee_machine.check_receptacle_placement_for_pouring(env, 'obj'))


def _monitor_step(env, before):
    m = env._attentionbench_monitor
    name = type(env).__name__
    if name == 'PrepareCoffee':
        on = bool(env.coffee_machine._turned_on)
        if on and not before.get('coffee_on', False):
            m['coffee_started_with_mug'] = _mug_at_outlet(env)
            m['coffee_started_upright'] = _upright(env, 'obj')
    elif name == 'HeatKebabSandwich':
        oven = env.toaster_oven
        t = _time_value(env, oven)
        prev = m.get('kebab_last_time')
        if prev is not None and t > prev + 1e-6:
            m['kebab_timer_ok'] = (_door_fraction(env, oven) <= KEBAB_DOOR_CLOSED and _kebab_foods_in(env, oven))
        m['kebab_last_time'] = t


def _snapshot(env):
    if type(env).__name__ == 'PrepareCoffee':
        return {'coffee_on': bool(env.coffee_machine._turned_on)}
    return {}


def _install_monitor(env):
    original_update = env.update_state
    original_reset = env.reset

    def update_state():
        before = _snapshot(env)
        original_update()
        _monitor_step(env, before)

    def reset(*a, **k):
        out = original_reset(*a, **k)
        clear()
        return out

    def clear():
        env._attentionbench_monitor = {}
        if type(env).__name__ == 'HeatKebabSandwich':
            env._attentionbench_monitor['kebab_last_time'] = _time_value(env, env.toaster_oven)

    env.update_state = update_state
    env.reset = reset
    clear()


def install(env):
    """Idempotent; call once after create_env (before reset is fine too)."""
    if getattr(env, '_attentionbench_success', None) == RULE:
        return env
    _install_contact(env)
    _install_monitor(env)
    env._attentionbench_success = RULE
    return env


# ---------------------------------------------------------------- verdict
def _upright(env, name):
    from robocasa.utils import object_utils as OU
    return bool(OU.check_obj_upright(env, name, th=UPRIGHT_DEG))


def _upstream_with_site(env, native_site):
    ids = env.robots[0].eef_site_id
    saved = dict(ids)
    try:
        ids['right'] = saved[native_site]
        return bool(env._check_success())
    finally:
        ids.clear(); ids.update(saved)


def _mixer_head_geoms(env):
    sm = env.stand_mixer
    m = env.sim.model
    jid = m.joint_name2id(sm._joint_names['head'])
    root = m.jnt_bodyid[jid]
    bodies = {root}
    changed = True
    while changed:
        changed = False
        for b in range(m.nbody):
            if b not in bodies and m.body_parentid[b] in bodies:
                bodies.add(b); changed = True
    return [m.geom_id2name(g) for g in range(m.ngeom) if m.geom_bodyid[g] in bodies and m.geom_id2name(g)]


def evaluate(env):
    """Return the AttentionBench verdict plus every part, for owner-only evidence."""
    upstream = bool(env._check_success())
    left_site = _upstream_with_site(env, 'left')   # same rule, measured at the physical right gripper
    parts = {'upstream_native_success': upstream, 'upstream_with_physical_right_gripper': left_site}
    ok = upstream and left_site
    name = type(env).__name__
    mon = getattr(env, '_attentionbench_monitor', {})
    if name in ('PrepareCoffee', 'CoffeeSetupMug'):
        parts['mug_upright'] = _upright(env, 'obj'); ok = ok and parts['mug_upright']
    if name == 'PrepareCoffee':
        parts['started_with_mug_at_outlet'] = bool(mon.get('coffee_started_with_mug', False))
        ok = ok and parts['started_with_mug_at_outlet']
    if name == 'HeatKebabSandwich':
        door = _door_fraction(env, env.toaster_oven)
        parts['door_fraction'] = round(door, 4)
        parts['door_closed'] = door <= KEBAB_DOOR_CLOSED
        parts['timer_set_with_door_closed_and_food_in'] = bool(mon.get('kebab_timer_ok', False))
        ok = ok and parts['door_closed'] and parts['timer_set_with_door_closed_and_food_in']
    if name == 'OpenStandMixerHead':
        geoms = _mixer_head_geoms(env)
        right, left = _grippers(env)
        raw = env._attentionbench_upstream_check_contact
        touching = bool(raw(right, geoms)) or bool(raw(left, geoms))
        parts['gripper_touching_head'] = touching
        ok = ok and not touching
    return dict(native_success=bool(ok), success_rule=RULE, **parts)
