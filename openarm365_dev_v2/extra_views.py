"""Additional camera angles for humans and the Eval agent. Display only.

Nothing here feeds the policy, the Dev agent or the Advisor: frames are rendered after each control step on the
execution thread (rendering never advances physics, and policy observations use their own render calls), written
beside the original recording, and every failure is contained. The original recording and its strict evidence
checks are untouched; `external` in the manifest is that original recording.
"""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

WIDTH, HEIGHT = 960, 540
QUALITY = 88
FRAME_LIMIT = 5000
LIVE_KEEP = 3
SCHEMA = 'attentionbench.camera-views.v1'
EXTERNAL = dict(label='Overview', name='robot0_agentview_left', width=1920, height=1080)
# Order is the order of the picker. Free cameras are defined relative to the task's target (sink / fixture), so
# they need no scene or model change; the wrist cameras already exist in the scene.
VIEWS = {
    'side': dict(label='Side', free=dict(azimuth=90.0, elevation=-30.0, distance=2.6)),
    'top': dict(label='Top-down', free=dict(azimuth=90.0, elevation=-75.0, distance=3.2)),
    'left_wrist': dict(label='Left wrist', camera='gripper0_left_eye_in_hand'),
    'right_wrist': dict(label='Right wrist', camera='gripper0_right_eye_in_hand'),
}
VIDEO_NAMES = {key: f'advisor_replay_{key}.mp4' for key in VIEWS}


def live_version(context):
    """Live metadata carries the Dev version as the int the live monitor compares with the cell's active version."""
    value = context.get('dev_version')
    return int(value) if str(value).isdigit() else value


def _json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, allow_nan=False) + '\n')
    temporary.replace(path)


def look_target(env):
    """World point the free cameras look at: the sink (pick/place) or the fixture (cabinet), slightly below the
    main display camera's own target so arms and objects stay in frame."""
    import numpy as np
    if hasattr(env, 'sink'):
        target = np.array(env.sink.pos, dtype=float); target[2] = 1.0
    elif hasattr(env, 'cab'):
        target = np.array(env.cab.pos, dtype=float)
    else:
        target = np.array(env.fxtr.pos, dtype=float); target[2] = 1.3
    return target


class ExtraViews:
    def __init__(self, env, evidence, context, episode, lookat):
        self.sim = env.sim
        self.directory = Path(evidence) / 'recording_views'
        self.live = Path(evidence) / 'live' / 'views'
        self.context = context
        self.episode = episode
        self.lookat = [float(v) for v in lookat]
        self.active = dict(VIEWS)
        self.counts = {key: 0 for key in VIEWS}
        self.errors = {}
        self.last_sequence = -1
        try:
            self.cam = self.sim._render_context_offscreen.cam
            import mujoco
            self.mujoco = mujoco
            for key in VIEWS:
                (self.directory / key).mkdir(parents=True, exist_ok=True)
                (self.live / key).mkdir(parents=True, exist_ok=True)
            _json(Path(evidence) / 'live' / 'views.json', dict(schema=SCHEMA, views={
                'external': dict(label=EXTERNAL['label']), **{k: dict(label=v['label']) for k, v in VIEWS.items()}}))
        except Exception as exc:
            self.active = {}
            self._error('all', exc)

    def _error(self, key, exc):
        self.errors[key] = f'{type(exc).__name__}: {exc}'[:300]
        try:
            _json(self.directory / 'error.json', dict(errors=self.errors, frame_counts=self.counts))
        except OSError:
            pass  # A camera fault must never reach the execution.

    def _render(self, spec):
        from PIL import Image
        import numpy as np
        if 'free' in spec:
            self.cam.type = self.mujoco.mjtCamera.mjCAMERA_FREE
            self.cam.lookat[:] = self.lookat
            self.cam.azimuth = spec['free']['azimuth']
            self.cam.elevation = spec['free']['elevation']
            self.cam.distance = spec['free']['distance']
            raw = self.sim.render(WIDTH, HEIGHT)
        else:
            raw = self.sim.render(WIDTH, HEIGHT, camera_name=spec['camera'], depth=False)
        raw = np.asarray(raw)
        if raw.shape != (HEIGHT, WIDTH, 3) or raw.dtype != np.uint8:
            raise ValueError('extra view requires native uint8 RGB')
        out = __import__('io').BytesIO()
        Image.fromarray(raw[::-1].copy()).save(out, format='JPEG', quality=QUALITY)  # MuJoCo rows are bottom-up.
        return out.getvalue()

    def capture(self, sequence):
        """Render every view for this control step. Never raises."""
        if sequence <= self.last_sequence:
            return
        self.last_sequence = sequence
        wall = time.time()
        for key, spec in list(self.active.items()):
            try:
                if self.counts[key] >= FRAME_LIMIT:
                    raise ValueError('extra view frame limit reached')
                data = self._render(spec)
                digest = hashlib.sha256(data).hexdigest()
                name = f'{self.counts[key]:06d}.jpg'
                folder = self.directory / key
                temporary = folder / (name + '.tmp')
                temporary.write_bytes(data); temporary.replace(folder / name)
                row = dict(file=name, sha256=digest, physical_sequence=sequence, episode_id=self.episode,
                           captured_at=wall, width=WIDTH, height=HEIGHT)
                with (folder / 'frames.jsonl').open('a') as stream:
                    stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
                self.counts[key] += 1
                self._publish(key, data, digest, sequence, wall)
            except Exception as exc:
                self.active.pop(key, None)  # One failing view never disables the others.
                self._error(key, exc)

    def _publish(self, key, data, digest, sequence, wall):
        folder = self.live / key
        image = folder / f'frame-{sequence:06d}.jpg'
        temporary = image.with_suffix('.jpg.tmp')
        temporary.write_bytes(data); temporary.replace(image)
        _json(folder / 'latest.json', dict(episode_id=self.episode, version=live_version(self.context),
              sequence=sequence, control_step=sequence, monotonic=time.monotonic(), wall_time=wall,
              file=image.name, sha256=digest, source_width=WIDTH, source_height=HEIGHT, view=key))
        for old in sorted(folder.glob('frame-*.jpg'))[:-LIVE_KEEP]:
            try: old.unlink()
            except OSError: pass


def _main_times(episode):
    journal = Path(episode) / 'recording_frames' / 'frames.jsonl'
    rows = [json.loads(line) for line in journal.read_text().splitlines() if line.strip().endswith('}')]
    return {r['physical_sequence']: r['trace_seconds'] for r in rows}


def _encode(frames, times, target):
    """Same encoding as the original recording: one frame per control step on the original timeline."""
    scratch = target.parent / (target.stem + '.encoding')
    shutil.rmtree(scratch, ignore_errors=True); scratch.mkdir()
    try:
        base = times[0]
        offsets = [t - base for t in times]
        for index, (frame, seconds) in enumerate(zip([*frames, frames[-1]], [*offsets, offsets[-1] + .04])):
            copy = scratch / f'{index:06d}.jpg'
            shutil.copyfile(frame, copy)
            stamp = 1_000_000_000_000_000_000 + round(seconds * 1_000_000_000)
            os.utime(copy, ns=(stamp, stamp))
        temporary = target.with_name(target.stem + '.encoding.mp4')
        command = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'image2', '-ts_from_file', '2',
                   '-framerate', '25', '-i', str(scratch / '%06d.jpg'), '-vsync', 'vfr', '-c:v', 'libx264',
                   '-pix_fmt', 'yuv420p', '-enc_time_base', '1:1000', '-video_track_timescale', '1000', '-r', '25',
                   '-movflags', '+faststart', str(temporary)]
        process = subprocess.run(command, capture_output=True, timeout=30)
        if process.returncode:
            raise RuntimeError('video encoding failed: ' + process.stderr.decode(errors='replace')[-300:])
        temporary.replace(target)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def finalize_extra_views(episode):
    """Encode one replay per extra view on the main recording's timeline and write camera_views.json.
    Best effort: any failure is recorded in the manifest and never raises."""
    episode = Path(episode)
    manifest = dict(schema=SCHEMA, source='display_only_not_policy_input', views={})
    try:
        main_ok = (episode / 'advisor_replay.mp4').is_file()
        manifest['views']['external'] = dict(label=EXTERNAL['label'], name=EXTERNAL['name'], width=EXTERNAL['width'],
                                             height=EXTERNAL['height'], video='advisor_replay.mp4' if main_ok else None,
                                             status='saved' if main_ok else 'missing')
        try:
            times_by_sequence = _main_times(episode)
        except (OSError, ValueError, KeyError):
            times_by_sequence = {}
        for key, spec in VIEWS.items():
            entry = dict(label=spec['label'], name=spec.get('camera') or 'free', width=WIDTH, height=HEIGHT,
                         video=None, frame_count=0, status='missing', error=None)
            manifest['views'][key] = entry
            try:
                folder = episode / 'recording_views' / key
                journal = folder / 'frames.jsonl'
                if not journal.is_file():
                    entry['error'] = 'no frames were recorded'; continue
                rows = [json.loads(line) for line in journal.read_text().splitlines() if line.strip().endswith('}')]
                rows = [r for r in rows if r['physical_sequence'] in times_by_sequence]
                entry['frame_count'] = len(rows)
                if len(rows) < 2:
                    entry['error'] = 'fewer than two frames on the recording timeline'; continue
                times = [times_by_sequence[r['physical_sequence']] for r in rows]
                if any(b <= a for a, b in zip(times, times[1:])):
                    raise ValueError('frame times are not increasing')
                for r in rows:
                    if hashlib.sha256((folder / r['file']).read_bytes()).hexdigest() != r['sha256']:
                        raise ValueError('frame hash mismatch')
                if not shutil.which('ffmpeg'):
                    raise RuntimeError('ffmpeg unavailable')
                _encode([folder / r['file'] for r in rows], times, episode / VIDEO_NAMES[key])
                entry.update(video=VIDEO_NAMES[key], status='saved')
            except Exception as exc:
                entry.update(status='failed', error=f'{type(exc).__name__}: {exc}'[:300])
        errors = episode / 'recording_views' / 'error.json'
        if errors.is_file():
            try: manifest['capture_errors'] = json.loads(errors.read_text()).get('errors', {})
            except ValueError: pass
    except Exception as exc:
        manifest['error'] = f'{type(exc).__name__}: {exc}'[:300]
    try:
        _json(episode / 'camera_views.json', manifest)
    except OSError:
        pass
    return manifest
