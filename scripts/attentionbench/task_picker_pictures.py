"""Start / done pictures and overlay marks for the New run task picker.

For every offered (task, seed) this builds the scene exactly as a run does (``create_env`` with the run's
fields, ``scene_reset_seed=seed``, the R5 fixture profile for fixture tasks), renders the start picture from
the camera that runs record as ``recording_views/side/000000.jpg`` (free camera, azimuth 90, elevation -30,
distance 2.6, 960x540, aimed like ``ExtraViews``), teleports the scene into its goal state with the method of
the 2026-11-10 success check (``task_picker_goals``), settles with hold actions, checks the verdict runs report as
native_success (R5 success rule v2 for fixture tasks, upstream ``_check_success()`` otherwise) and renders the done picture from the same camera. Object and target positions are
projected into the image to give the overlay marks.

Display only: no planner, no policy, no agent, no sim.lock, no real run. Each pair runs in a fresh process.

    nice -n 19 env MUJOCO_GL=egl PYTHONPATH=$PWD python scripts/attentionbench/task_picker_pictures.py --all
    ... --pair turn_on_microwave 101         # one pair, prints JSON

Writes benchmarks/attention_harness/ui/tasks/<task>-<seed>-start.jpg / -done.jpg and manifest.json.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'benchmarks/attention_harness/ui/tasks'
WIDTH, HEIGHT = 960, 540
THUMB = (640, 360)
QUALITY = 80
SIDE = dict(azimuth=90.0, elevation=-30.0, distance=2.6)  # extra_views.VIEWS['side']
SCHEMA = 'attentionbench.task-picker.v1'
FIXTURE_PROFILE = 'r5-fixture-20261031'


# ------------------------------------------------------------------ scene

def build(task: str, seed: int):
    """The worker's create_env call (openarm365_dev_v2/worker.py) with a run's config fields."""
    from benchmarks.attention_harness.dev_tasks import FIXTURE_TASKS, get_task
    from openarm365_dev_v2.environment import create_env
    fixture = task in FIXTURE_TASKS and task != 'pick_place_counter_to_sink'
    official = (FIXTURE_TASKS[task] if fixture else get_task(task)).official_env
    env = create_env(official, robots='OpenArm365', seed=seed, obj_instance_split='pretrain',
                     **({'fixture_profile': FIXTURE_PROFILE} if fixture else {}),
                     layout_and_style_ids=[(11, 34)], camera_widths=128, camera_heights=128,
                     randomize_cameras=False, generative_textures=None, control_freq=20, horizon=9600,
                     scene_reset_seed=seed)
    env.reset()
    if fixture:
        # As the worker does for R5 fixture runs: success rule v2 (either arm presses, both arms released,
        # task fixes; openarm365_dev_v2/fixture_success.py). Pictures are checked against that verdict.
        from openarm365_dev_v2.fixture_success import install
        install(env)
    # Pictures only: runs drive absolute joint targets, where a zero action pulls every joint toward 0 and the
    # arms swing down into the scene while it settles. The success check held the arms with delta input (zero =
    # hold); do the same here so the goal picture shows the arms where they started.
    for native in ('left', 'right'):
        env.robots[0].composite_controller.part_controllers[native].input_type = 'delta'
    return env, fixture


def lookat(env, task: str, fixture: bool):
    """Where the worker aims the free cameras (ExtraViews lookat)."""
    if fixture:
        from openarm365_dev_v2.fixture_contract import camera_target
        return camera_target(env, {'task_id': task})
    from openarm365_dev_v2.extra_views import look_target
    return look_target(env)


class Camera:
    def __init__(self, env, target):
        import mujoco
        import numpy as np
        self.env, self.mujoco, self.np = env, mujoco, np
        self.cam = env.sim._render_context_offscreen.cam
        self.target = [float(v) for v in target]
        self.aim()
        # Exact projection: let MuJoCo place the free camera, then read its eye and frustum.
        model, data = env.sim.model._model, env.sim.data._data
        scene = mujoco.MjvScene(model, maxgeom=10)
        cam = mujoco.MjvCamera()
        cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        cam.lookat[:] = self.target
        cam.azimuth, cam.elevation, cam.distance = SIDE['azimuth'], SIDE['elevation'], SIDE['distance']
        mujoco.mjv_updateScene(model, data, mujoco.MjvOption(), None, cam, mujoco.mjtCatBit.mjCAT_ALL, scene)
        eye = scene.camera[0]
        self.pos = np.array(eye.pos, float)
        self.forward = np.array(eye.forward, float)
        self.up = np.array(eye.up, float)
        self.right = np.cross(self.forward, self.up)
        self.near = float(eye.frustum_near)
        self.bottom, self.top = float(eye.frustum_bottom), float(eye.frustum_top)
        self.center = float(eye.frustum_center)
        half = float(getattr(eye, 'frustum_width', 0.0) or 0.0)
        self.half_width = half if half > 0 else (self.top - self.bottom) / 2 * WIDTH / HEIGHT

    def aim(self):
        self.cam.type = self.mujoco.mjtCamera.mjCAMERA_FREE
        self.cam.lookat[:] = self.target
        self.cam.azimuth, self.cam.elevation, self.cam.distance = SIDE['azimuth'], SIDE['elevation'], SIDE['distance']

    def render(self) -> bytes:
        from PIL import Image
        self.aim()
        raw = self.np.asarray(self.env.sim.render(WIDTH, HEIGHT))
        if raw.shape != (HEIGHT, WIDTH, 3):
            raise ValueError(f'unexpected render shape {raw.shape}')
        full = Image.fromarray(raw[::-1].copy())  # MuJoCo rows are bottom-up.
        out = io.BytesIO()
        full.resize(THUMB, Image.LANCZOS).save(out, format='JPEG', quality=QUALITY, optimize=True, progressive=True)
        return out.getvalue()

    def project(self, point):
        """World point -> (x, y) in the 960x540 picture."""
        rel = self.np.asarray(point, float) - self.pos
        z = float(rel @ self.forward)
        if z <= 1e-6:
            raise ValueError('point behind the camera')
        xn = float(rel @ self.right) * self.near / z
        yn = float(rel @ self.up) * self.near / z
        u = (xn - (self.center - self.half_width)) / (2 * self.half_width)
        v = (yn - self.bottom) / (self.top - self.bottom)
        return [round(u * WIDTH, 1), round((1 - v) * HEIGHT, 1)]


class OverviewCamera:
    """The run's main recording camera (robot0_agentview_left, aimed as the worker aims it), used only when the
    side camera sees nothing but a wall."""
    name = 'robot0_agentview_left'

    def __init__(self, env, task, fixture):
        import numpy as np
        self.env, self.np = env, np
        if fixture:
            from openarm365_dev_v2.fixture_contract import configure_camera
            configure_camera(env, {'task_id': task})
        else:
            from openarm365.environment import configure_display_camera
            configure_display_camera(env)
        cid = env.sim.model.camera_name2id(self.name)
        self.pos = np.array(env.sim.data.cam_xpos[cid], float)
        self.rot = np.array(env.sim.data.cam_xmat[cid], float).reshape(3, 3)
        self.focal = (HEIGHT / 2) / math.tan(math.radians(float(env.sim.model.cam_fovy[cid])) / 2)

    def render(self) -> bytes:
        from PIL import Image
        raw = self.np.asarray(self.env.sim.render(WIDTH, HEIGHT, camera_name=self.name, depth=False))
        out = io.BytesIO()
        Image.fromarray(raw[::-1].copy()).resize(THUMB, Image.LANCZOS).save(out, format='JPEG', quality=QUALITY,
                                                                          optimize=True, progressive=True)
        return out.getvalue()

    def project(self, point):
        rel = self.rot.T @ (self.np.asarray(point, float) - self.pos)   # camera looks along its -z, y up
        z = -float(rel[2])
        if z <= 1e-6:
            raise ValueError('point behind the camera')
        return [round(WIDTH / 2 + self.focal * float(rel[0]) / z, 1), round(HEIGHT / 2 - self.focal * float(rel[1]) / z, 1)]


# ------------------------------------------------------------------ shapes

def box_corners(center, half, rotation=None):
    import itertools
    import numpy as np
    rotation = np.eye(3) if rotation is None else np.asarray(rotation, float).reshape(3, 3)
    return [np.asarray(center, float) + rotation @ (np.array(s) * np.asarray(half, float))
            for s in itertools.product((-1, 1), repeat=3)]


def hull(points):
    pts = sorted(set((round(x, 1), round(y, 1)) for x, y in points))
    if len(pts) < 3:
        return [list(p) for p in pts]
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [list(p) for p in lower[:-1] + upper[:-1]]


def circle_of(camera, corners, minimum=14.0, pad=8.0):
    pts = [camera.project(c) for c in corners]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    r = max(math.hypot(x - cx, y - cy) for x, y in pts)
    return dict(cx=round(cx, 1), cy=round(cy, 1), r=round(max(minimum, r * 0.8 + pad), 1))


def region_of(camera, corners):
    return dict(points=hull([camera.project(c) for c in corners]))


def object_corners(env, name):
    import numpy as np
    obj = env.objects[name]
    bid = env.obj_body_id[name]
    center = np.array(env.sim.data.body_xpos[bid], float)
    rotation = np.array(env.sim.data.body_xmat[bid], float).reshape(3, 3)
    return box_corners(center, np.array(obj.size, float) / 2, rotation)


def geom_corners(env, gid):
    import numpy as np
    from openarm365_dev.world import shape_box
    m, d = env.sim.model, env.sim.data
    center, half = shape_box(m, gid)
    rotation = np.asarray(d.geom_xmat[gid]).reshape(3, 3)
    return box_corners(rotation @ np.asarray(center) + d.geom_xpos[gid], half, rotation)


def interior_corners(fixture):
    """Corners of a fixture's interior region (p0, px, py, pz parallelepiped), lowest region."""
    import numpy as np
    regions = sorted(fixture.get_int_sites(relative=False).values(), key=lambda r: r[0][2])
    p0, px, py, pz = [np.asarray(v, float) for v in regions[0]]
    return [p0 + a * (px - p0) + b * (py - p0) + c * (pz - p0) for a in (0, 1) for b in (0, 1) for c in (0, 1)]


def fixture_corners(env, fixture):
    """Bounding box of every visible geom of a fixture (its whole body)."""
    import numpy as np
    m = env.sim.model
    prefix = fixture.name + '_'
    corners = []
    for gid in range(m.ngeom):
        name = m.geom_id2name(gid) or ''
        if name.startswith(prefix) and m.geom_group[gid] == 1 and m.geom_rgba[gid][3] > 0:
            corners += geom_corners(env, gid)
    if not corners:
        raise ValueError(f'no visible geoms for fixture {fixture.name}')
    arr = np.array(corners)
    lo, hi = arr.min(0), arr.max(0)
    return box_corners((lo + hi) / 2, (hi - lo) / 2)


# ------------------------------------------------------------------ one pair

def verdict(env, fixture):
    """The verdict a run reports as native_success (worker.native_evaluation)."""
    if fixture:
        from openarm365_dev_v2.fixture_success import evaluate
        return bool(evaluate(env)['native_success'])
    return bool(env._check_success())


def blank_view(jpeg: bytes) -> bool:
    """True when the side camera sees (almost) one flat colour, e.g. a wall between it and the task."""
    from PIL import Image, ImageStat
    image = Image.open(io.BytesIO(jpeg)).convert('L')
    return max(ImageStat.Stat(image).stddev) < 12


def run_pair(task: str, seed: int, out: Path) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import numpy as np
    import task_picker_goals as goals
    started = time.time()
    env, fixture = build(task, seed)
    try:
        camera = Camera(env, lookat(env, task, fixture))
        lang = env.get_ep_meta().get('lang')
        start_success = verdict(env, fixture)
        start = camera.render()
        view = 'side'
        blank = blank_view(start)
        if blank:
            camera = OverviewCamera(env, task, fixture)
            start, view = camera.render(), 'overview'
            blank = blank_view(start)
        spec = goals.SPECS[task]
        words = goals.object_words(env)
        rec = goals.start_shapes(env, camera, sys.modules[__name__])
        with rec:
            detail = spec.goal(env) or {}               # teleport into the goal state (settles inside)
        done_success = verdict(env, fixture)
        marks = goals.derive_marks(env, camera, sys.modules[__name__], rec, spec, rec.pressed)
        done = camera.render()
    finally:
        env.close()
    stem = f'{task}-{seed}'
    if blank:
        return dict(task=task, seed=seed, lang=lang, start_success=start_success, done_success=done_success,
                    start=None, done=None, blank=True, view=view, seconds=round(time.time() - started, 1))
    if done_success:
        (out / f'{stem}-start.jpg').write_bytes(start)
        (out / f'{stem}-done.jpg').write_bytes(done)
    return dict(task=task, seed=seed, lang=lang, start_success=start_success, done_success=done_success,
                start=f'{stem}-start.jpg' if done_success else None, done=f'{stem}-done.jpg' if done_success else None,
                marks=marks, words=words, teleport=detail, view=view, seconds=round(time.time() - started, 1))


def jsonable(x):
    """numpy scalars/arrays (teleport details) -> plain JSON values."""
    import numpy as np
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return round(float(x), 4) if math.isfinite(float(x)) else None
    return x if isinstance(x, (int, str, bool)) or x is None else str(x)


# ------------------------------------------------------------------ all pairs

def offered_pairs():
    """Every (task, seed) the New run form can offer: Counter -> Sink seeds and the enabled R5 fixture seeds."""
    from benchmarks.attention_harness.dev_tasks import get_task
    from benchmarks.attention_harness.dev_fixture_tasks import options
    pairs = [('pick_place_counter_to_sink', s) for s in (101, 102)]
    for row in options():
        pairs += [(row['task_id'], o['seed']) for o in row['seed_options'] if o['enabled']]
    return pairs


def fresh(task, seed, out):
    environ = os.environ | {'PYTHONHASHSEED': '0', 'PYTHONDONTWRITEBYTECODE': '1', 'MUJOCO_GL': 'egl',
                            'OMP_NUM_THREADS': '1'}
    command = ['nice', '-n', '19', sys.executable, '-B', str(Path(__file__).resolve()), '--pair', task, str(seed),
               '--out', str(out)]
    proc = subprocess.run(command, env=environ, text=True, capture_output=True, timeout=900)
    line = next((l for l in reversed(proc.stdout.splitlines()) if l.startswith('PAIR ')), None)
    if proc.returncode or line is None:
        # Last stderr line only (the error itself); the full traceback is printed, not stored (it holds host paths).
        print(proc.stderr[-3000:], file=sys.stderr)
        last = [l for l in proc.stderr.splitlines() if l.strip()][-1:] or ['no output']
        return dict(task=task, seed=seed, error=f'exit {proc.returncode}: {last[0].strip()[:300]}')
    return json.loads(line[5:])


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--pair', nargs=2, metavar=('TASK', 'SEED'))
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--only', nargs='*', default=None, help='task ids to (re)render with --all')
    parser.add_argument('--out', type=Path, default=OUT)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.pair:
        row = run_pair(args.pair[0], int(args.pair[1]), args.out)
        print('PAIR ' + json.dumps(jsonable(row), sort_keys=True, allow_nan=False))
        return 0
    if not args.all:
        parser.error('--pair or --all')
    raw = args.out / 'render-log.jsonl'
    previous = {}
    if raw.exists():
        for line in raw.read_text().splitlines():
            row = json.loads(line); previous[(row['task'], row['seed'])] = row
    for task, seed in offered_pairs():
        if args.only is not None and task not in args.only and (task, seed) in previous:
            continue
        row = fresh(task, seed, args.out)
        previous[(task, seed)] = row
        print(task, seed, 'ERROR ' + row['error'] if 'error' in row else f"done_success={row['done_success']} {row['seconds']}s",
              flush=True)
        raw.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in previous.values()))
    import task_picker_goals as goals  # noqa: E402  (manifest text lives with the specs)
    manifest = goals.manifest(list(previous.values()), SCHEMA)
    (args.out / 'manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + '\n')
    bad = [r for r in previous.values() if 'error' in r or not r.get('done_success')]
    for r in bad:
        print('NOT USED', r['task'], r['seed'], r.get('error') or 'goal state not native success', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
