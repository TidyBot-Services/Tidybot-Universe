"""Per-task goal teleports, marks and plain-words text for the New run task picker.

Goal states reuse the 2026-11-10 native success check (``task_picker_teleport``, vendored from
~/下载/success-teleport-20261110, REPORT.md there). Counter -> Sink was not part of that check; its goal uses
the same method (rest the object on the sink's interior region, settle, native check).

Marks are derived, not placed by hand: objects the teleport moved get an orange circle at their start pose and a
dashed blue target (the fixture's interior region, or the object's settled goal box); fixture parts whose pose
changed get an orange circle (handles preferred) and a dashed blue outline of their goal pose; pressed buttons
(recorded from the press helper) get an orange circle. Several steps are numbered.
"""
from __future__ import annotations

import math
import re

from task_picker_teleport import cases as C
from task_picker_teleport import teleport_lib as T

MOVE_M = 0.03       # object moved by the teleport
PART_M = 0.015      # fixture geom moved
PART_RAD = math.radians(8)

FAMILIES = {
    'A': 'Open & close',
    'B': 'Buttons & knobs',
    'C': 'Put on an appliance',
    'D': 'Cabinet & sink',
}

# Words in [o:...] are orange (the thing to move / press), [b:...] blue (where it goes). {name} = object category.
TEXT = {
    'pick_place_counter_to_sink': dict(
        family='D', title='Counter → Sink',
        sentence='Pick up the [o:{obj}] and put it in the [b:sink].',
        rule='Done when the {obj} is inside the sink basin and the robot has let go of it.'),
    'turn_on_microwave': dict(
        family='B', title='Turn on the microwave',
        sentence='Press the [o:start button] on the microwave.',
        rule='Done when the microwave is running: start pressed with the door closed, then the hand moves away.',
        note='Only a press by the robot’s left arm counts; the check does not see a right-arm press. '
             'Feasibility with the left arm is proven on seed 101 only.'),
    'pick_place_counter_to_cabinet': dict(
        family='D', title='Counter → Cabinet',
        sentence='Pick up the [o:{obj}] and put it in the [b:cabinet].',
        rule='Done when the {obj} is inside the cabinet and the robot’s left hand has let go.',
        note='The check’s “inside” margin is loose (even the cabinet top counts), and a right hand still '
             'holding the item also counts as done.'),
    'open_cabinet': dict(
        family='A', title='Open the cabinet',
        sentence='Open the [o:cabinet doors] all the way.',
        rule='Done when every door is open at least 90% (about 81°).'),
    'close_blender_lid': dict(
        family='A', title='Close the blender lid',
        sentence='Put the [o:lid] back on the [b:blender].',
        rule='Done when the lid sits on the blender (within 4 cm, nearly level) and the left hand has let go.',
        note='Holding the lid 3.5 cm above the blender also counts.'),
    'open_drawer': dict(
        family='A', title='Open the drawer',
        sentence='Pull the [o:drawer] all the way open.',
        rule='Done when the drawer is fully open (90% open is not enough).'),
    'turn_off_stove': dict(
        family='B', title='Turn off the burner',
        sentence='Turn the [o:knob] of the lit burner back to off.',
        rule='Done when that burner’s knob is below about 20°.'),
    'open_stand_mixer_head': dict(
        family='B', title='Open the mixer head',
        sentence='Lift the [o:mixer head] all the way up.',
        rule='Done when the head is raised past 99% (about 89°).',
        defect='Check is defective: the head-lock button does nothing, the screening checked the wrong action, '
               'and on seed 101 the head falls back when released.'),
    'coffee_setup_mug': dict(
        family='C', title='Mug under the coffee machine',
        sentence='Put the [o:{obj}] under the [b:coffee outlet].',
        rule='Done when the mug’s centre is within 4 cm of the outlet and the left hand has let go.',
        note='On seed 104 a mug lying on its side also counts.'),
    'prepare_coffee': dict(
        family='C', title='Prepare coffee',
        sentence='① Put the [o:{obj}] under the [b:coffee outlet], ② press the [o:start button].',
        rule='Done when the mug is under the outlet, the machine is on and the hand has let go.',
        defect='Check is defective: pressing start before the mug is in place also counts. Only a left-arm press counts.'),
    'kettle_boiling': dict(
        family='C', title='Boil the kettle',
        sentence='① Put the [o:{obj}] on a free [b:burner], ② turn that burner’s [o:knob] on.',
        rule='Done when the kettle stands on a burner and that same burner is on.'),
    'heat_kebab_sandwich': dict(
        family='C', title='Heat the kebab sandwich',
        sentence='Put the [o:{kebab}] and [o:{baguette}] in the [b:toaster oven], close the door and set the timer.',
        rule='Done when both foods are on the rack, the door is not open past 90% and the timer is set.',
        defect='Check is defective: a door left 17–62% open with the timer set also counts, and the hand need not let go.'),
    'categorize_condiments': dict(
        family='D', title='Sort the condiments',
        sentence='Put the [o:{obj1}] and the [o:{obj2}] next to their matches in the [b:cabinet].',
        rule='Done when each item stands within 15 cm of its matching item in the cabinet and the hand has let go.'),
    'weigh_ingredients': dict(
        family='D', title='Weigh an ingredient',
        sentence='① Put the [o:{obj}] on the [b:scale], ② close the [o:cabinet].',
        rule='Done when the item stands upright on the scale and the cabinet door is shut (at most 0.5% open).'),
    'arrange_tea': dict(
        family='D', title='Arrange tea',
        sentence='Put the [o:{obj2}] and the [o:{obj}] on the [b:tray], then close the cabinet doors.',
        rule='Done when the kettle and the mug are on the tray, the cabinet is shut and the hand has let go of the mug.'),
}


def sink_goal(env):
    """Same method as cases.pp_pos, into the sink basin (not part of the 11-10 check)."""
    rname, reg = T.int_region(env.sink)
    p = T.region_point(reg, 0.5, 0.5)
    T.rest_obj(env, 'obj', p[:2], p[2])
    T.settle(env)
    from robocasa.utils import object_utils as OU
    return dict(region=rname, inside=bool(OU.obj_inside_of(env, 'obj', env.sink, partial_check=True)))


class Spec:
    def __init__(self, goal, target=None, order=None):
        self.goal, self.target, self.order = goal, target, order


SPECS = {
    'pick_place_counter_to_sink': Spec(sink_goal, target='sink'),
    'turn_on_microwave': Spec(C.mw_pos),
    'pick_place_counter_to_cabinet': Spec(C.pp_pos, target='cab'),
    'open_cabinet': Spec(C.opencab_pos),
    'close_blender_lid': Spec(C.lid_pos),
    'open_drawer': Spec(C.drawer_pos),
    'turn_off_stove': Spec(C.stove_off_pos),
    'open_stand_mixer_head': Spec(C.mixer_pos),
    'coffee_setup_mug': Spec(C.mug_pos),
    'prepare_coffee': Spec(C.pc_pos),
    'kettle_boiling': Spec(C.kb_pos),
    'heat_kebab_sandwich': Spec(C.hk_pos, order=['kebab', 'baguette']),
    'categorize_condiments': Spec(C.cc_pos, target='cab', order=['obj1', 'obj2']),
    'weigh_ingredients': Spec(C.wi_pos),
    'arrange_tea': Spec(C.at_pos, order=['obj2', 'obj']),
}


# ------------------------------------------------------------------ recording what the teleport did

class Recorder:
    """Snapshot before the goal; record pressed buttons during it; derive marks after it."""

    def __init__(self, env):
        import numpy as np
        self.env, self.np = env, np
        self.pressed = []
        self.placed = []     # objects the teleport put somewhere (set_obj), in call order
        m = env.sim.model
        robot_words = ('robot0', 'gripper0', 'mobilebase', 'openarm')
        object_roots = {int(m.body_rootid[b]) for b in env.obj_body_id.values()}
        self.fixture_geoms = []
        for gid in range(m.ngeom):
            root = int(m.body_rootid[m.geom_bodyid[gid]])
            body = m.body_id2name(int(m.geom_bodyid[gid])) or ''
            if root in object_roots or any(w in body for w in robot_words):
                continue
            if m.geom_rgba[gid][3] <= 0:
                continue
            self.fixture_geoms.append(gid)
        self.obj_start = {n: self.obj_pose(n) for n in env.objects}
        self.obj_corners = {}
        self.geom_start = self.geom_poses()
        self._press = C.press

    def obj_pose(self, name):
        d = self.env.sim.data
        return self.np.array(d.body_xpos[self.env.obj_body_id[name]], float).copy()

    def geom_poses(self):
        d = self.env.sim.data
        return {g: (self.np.array(d.geom_xpos[g]).copy(), self.np.array(d.geom_xmat[g]).reshape(3, 3).copy())
                for g in self.fixture_geoms}

    def __enter__(self):
        recorder = self

        def press(env, native, geom_name, *a, **k):
            recorder.pressed.append(geom_name)
            return recorder._press(env, native, geom_name, *a, **k)
        C.press = press
        self._set_obj = T.set_obj

        def set_obj(env, name, *a, **k):
            if name in env.objects and name not in recorder.placed:
                recorder.placed.append(name)
            return recorder._set_obj(env, name, *a, **k)
        T.set_obj = C.set_obj = set_obj
        return self

    def __exit__(self, *exc):
        C.press = self._press
        T.set_obj = C.set_obj = self._set_obj


def start_shapes(env, camera, pictures):
    """Everything needed from the start state, before teleporting."""
    rec = Recorder(env)
    rec.start_corners = {n: pictures.object_corners(env, n) for n in env.objects}
    rec.start_geom_corners = {g: pictures.geom_corners(env, g) for g in rec.fixture_geoms}
    return rec


def _angle(a, b):
    c = (float((a.T @ b).trace()) - 1) / 2
    return math.acos(max(-1.0, min(1.0, c)))


def derive_marks(env, camera, pictures, rec, spec, pressed):
    np = rec.np
    steps = []
    # 1. objects the teleport moved
    moved = [n for n in rec.placed if np.linalg.norm(rec.obj_pose(n) - rec.obj_start[n]) > MOVE_M]
    if spec.order:
        moved = [n for n in spec.order if n in moved] + [n for n in moved if n not in spec.order]
    for name in moved:
        circle = pictures.circle_of(camera, rec.start_corners[name])
        if spec.target:
            region = pictures.region_of(camera, pictures.interior_corners(getattr(env, spec.target)))
        else:
            corners = pictures.object_corners(env, name)
            center = np.mean(corners, axis=0)
            corners = [center + (c - center) * 1.6 + np.sign(c - center) * 0.02 for c in corners]
            region = pictures.region_of(camera, corners)
        steps.append(dict(kind='move', object=name, circle=circle, region=region))
    # 2. fixture parts whose pose changed (doors, drawers, knobs, lids, heads)
    now = rec.geom_poses()
    m = env.sim.model
    changed = [g for g in rec.fixture_geoms
               if np.linalg.norm(now[g][0] - rec.geom_start[g][0]) > PART_M or _angle(now[g][1], rec.geom_start[g][1]) > PART_RAD]
    def moving_body(g):
        b = int(m.geom_bodyid[g])
        while b > 0 and m.body_jntnum[b] == 0:
            b = int(m.body_parentid[b])
        return b
    def label(g):
        return ((m.geom_id2name(g) or '') + ' ' + (m.body_id2name(int(m.geom_bodyid[g])) or '')).lower()
    groups = {}
    for g in changed:
        groups.setdefault(moving_body(g), []).append(g)
    for body, geoms in groups.items():
        everything = [g for g in rec.fixture_geoms if moving_body(g) == body]
        handles = [g for g in everything if 'handle' in label(g)]
        focus = handles or everything
        start = [c for g in focus for c in rec.start_geom_corners[g]]
        circle = pictures.circle_of(camera, start, minimum=16)
        step = dict(kind='part', part=m.body_id2name(body), circle=circle)
        moved_m = float(np.linalg.norm(np.mean([now[g][0] for g in everything], 0) - np.mean([rec.geom_start[g][0] for g in everything], 0)))
        if moved_m > 0.03:  # a door / drawer / lid that travels: outline where it ends up
            step['region'] = pictures.region_of(camera, [c for g in focus for c in pictures.geom_corners(env, g)])
        steps.append(step)
    # 3. pressed buttons
    for name in dict.fromkeys(pressed):
        gid = m.geom_name2id(name)
        steps.append(dict(kind='press', part=name, circle=pictures.circle_of(camera, pictures.geom_corners(env, gid), minimum=18)))
    if len(steps) > 1:
        for i, s in enumerate(steps, 1):
            s['n'] = i
    for s in steps:
        if s.get('region') and s.get('circle'):
            pts = s['region']['points']
            cx = sum(p[0] for p in pts) / len(pts); cy = sum(p[1] for p in pts) / len(pts)
            s['arrow'] = [s['circle']['cx'], s['circle']['cy'], round(cx, 1), round(cy, 1)]
    return dict(steps=steps, objects=len(moved))


# ------------------------------------------------------------------ text

def object_words(env):
    """Object name -> readable category from the episode metadata ("bell_pepper" -> "bell pepper")."""
    out = {}
    for cfg in env.get_ep_meta().get('object_cfgs', []):
        cat = (cfg.get('info') or {}).get('cat')
        if cfg.get('name') and cat:
            out[cfg['name']] = cat.replace('_', ' ')
    return out


def segments(template: str, words: dict):
    text = template.format_map(_Default(words))
    out = []
    for part in re.split(r'(\[[ob]:[^\]]+\])', text):
        if not part:
            continue
        m = re.fullmatch(r'\[([ob]):([^\]]+)\]', part)
        out.append(dict(text=m.group(2), mark=m.group(1)) if m else dict(text=part))
    return out


class _Default(dict):
    def __missing__(self, key):
        return 'item' if key.startswith('obj') else key


def plain(template: str, words: dict):
    return ''.join(s['text'] for s in segments(template, words))


def manifest(rows, schema):
    """One entry per task (in family order), one per seed with pictures, marks and text."""
    from benchmarks.attention_harness.dev_fixture_tasks import FAMILIES as SOURCE
    order = ['pick_place_counter_to_sink'] + [t for _, (_, tasks) in sorted(SOURCE.items()) for t in tasks]
    tasks = []
    for task in order:
        text = TEXT[task]
        seeds = []
        for row in sorted((r for r in rows if r['task'] == task), key=lambda r: r['seed']):
            ok = 'error' not in row and row.get('done_success') and not row.get('start_success') and bool(row.get('start'))
            entry = dict(seed=row['seed'], pictures=bool(ok))
            if ok:
                words = row.get('words', {})
                entry.update(start=row['start'], done=row['done'], view=row.get('view', 'side'), marks=row['marks']['steps'],
                             objects=row['marks']['objects'], steps=max(1, len(row['marks']['steps'])),
                             sentence=segments(text['sentence'], words), rule=plain(text['rule'], words),
                             alt_start=f"Start: {row['lang']}", alt_done=f"Done: {plain(text['rule'], words)}")
            else:
                # Last line only: a traceback carries host paths, and this file is public on Remote.
                last = [l for l in row.get('error', '').splitlines() if l.strip()][-1:] or ['']
                entry['reason'] = ('rendering failed: ' + last[0].strip()[:160]) if last[0] else (
                    'the recorded cameras only see a wall in this scene' if row.get('blank') else 
                    'already successful at reset' if row.get('start_success') else 'goal state is not a native success')
            seeds.append(entry)
        tasks.append(dict(task_id=task, family=text['family'], family_label=FAMILIES[text['family']],
                          title=text['title'], sentence=segments(text['sentence'], {}), rule=plain(text['rule'], {}),
                          note=text.get('note'), defect=text.get('defect'), seeds=seeds))
    return dict(schema=schema, families=FAMILIES, tasks=tasks,
                source='scripts/attentionbench/task_picker_pictures.py; goals from the 2026-11-10 native success check')
