"""Read only durable RGB from the exact Dev version; never contact an executor."""
from pathlib import Path
import hashlib
import json
import math
import re
import time


def _regular(path, root):
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Dev preview escaped its session')
    if any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root)):
        raise ValueError('Linked Dev preview evidence rejected')
    return path.is_file()


def read_dev_preview(session, version):
    sid = session['session_id']
    if not re.fullmatch(r'dev-[0-9a-f]{24}', sid) or type(version) is not int or not 1 <= version <= 10:
        raise ValueError('Invalid Dev preview identity')
    job = next((j for j in session['jobs'] if j['kind'] == 'execute' and j['version'] == version), None)
    policy = next((v for v in session['versions'] if v['version'] == version), None)
    if job is None or policy is None:
        raise FileNotFoundError('Dev execution version not found')
    active = job['state'] == 'claimed' and session['state'] in {'running', 'stopping'}
    run_id, attempt_id = 'run:attention-' + sid, f'attempt:attention-{sid}:{version-1}'
    state = dict(run_id=run_id, attempt_id=attempt_id, version=version,
                 state='starting' if active else 'ended', sequence=None, age_seconds=None)
    root = Path(session['artifact_dir'])
    executions = root / f'v{version}' / ('attention-' + sid) / 'executions'
    if not executions.exists():
        return state, None
    if executions.is_symlink() or not executions.resolve().is_relative_to(root.resolve()):
        raise ValueError('Invalid Dev execution directory')
    episodes = [p for p in executions.iterdir() if p.is_dir() and (p / 'recording_context.json').exists()]
    if len(episodes) > 1:
        raise ValueError('Ambiguous Dev execution camera')
    if not episodes:
        return state, None
    episode = episodes[0]
    for name, expected in [('config.json', session['locked']['config_sha256']),
                           ('policy.py', policy['source']['sha256'])]:
        file = episode / name
        if not _regular(file, root) or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
            raise ValueError('Dev preview configuration/policy identity mismatch')
    journal = episode / 'recording_frames/frames.jsonl'
    if not _regular(journal, root):
        return state, None
    # At most a bounded tail is read. Ignore a partially appended last row.
    with journal.open('rb') as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - 16384))
        tail = stream.read(16384)
    rows = tail.split(b'\n')[:-1]
    if not rows:
        return state, None
    row = json.loads(rows[-1])
    camera = session['locked']['config'].get('camera_names', ['robot0_agentview_left'])[0]
    if (row.get('run_id') != run_id or row.get('attempt_id') != attempt_id
            or row.get('camera_name') != camera or not re.fullmatch(r'[0-9]{6}\.jpg', row.get('file', ''))
            or type(row.get('physical_sequence')) is not int or row['physical_sequence'] < 0
            or not isinstance(row.get('captured_at'), (int, float)) or not math.isfinite(row['captured_at'])
            or row['captured_at'] <= 0
            or any(type(row.get(k)) is not int or not 0 < row[k] <= 2048 for k in ('width', 'height'))):
        raise ValueError('Dev camera frame identity mismatch')
    file = journal.parent / row['file']
    if not _regular(file, root) or not 0 < file.stat().st_size <= 8_000_000:
        raise ValueError('Invalid Dev camera frame')
    frame = file.read_bytes()
    if (not frame.startswith(b'\xff\xd8') or not frame.endswith(b'\xff\xd9')
            or hashlib.sha256(frame).hexdigest() != row.get('sha256')):
        raise ValueError('Dev camera frame integrity mismatch')
    age = max(0., time.time() - row['captured_at'])
    state.update(state='ended' if not active else 'stale' if age > 2 else 'live',
                 sequence=row['physical_sequence'] + 1, captured_at=row['captured_at'],
                 age_seconds=round(age, 3), source_width=row['width'], source_height=row['height'])
    return state, frame


def with_dev_preview(session):
    jobs = [j for j in session['jobs'] if j['kind'] == 'execute']
    if not jobs:
        return session
    version = max(j['version'] for j in jobs)
    base = f"/api/dev/sessions/{session['session_id']}"
    try:
        state, frame = read_dev_preview(session, version)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        state, frame = dict(state='unavailable', error=str(exc)[:180]), None
    return {**session, 'preview_station': {
        'camera_url': f'{base}/preview-frame?version={version}' if frame else None,
        'camera_preview_url': f'{base}/preview-frame?version={version}',
        'camera_state_url': f'{base}/preview-state?version={version}',
        'preview_state': state,
    }}
