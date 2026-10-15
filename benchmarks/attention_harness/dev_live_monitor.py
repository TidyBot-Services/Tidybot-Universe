"""Read-only monitor contract. Waiting is distinct from corrupt/scope-invalid evidence."""
import hashlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path

LOG = logging.getLogger(__name__)
TERMINAL = {'completed', 'failed', 'cancelled', 'needs_review', 'stopped'}

class MonitorError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


def frame_age(wall_time, now):
    if (isinstance(wall_time, bool) or not isinstance(wall_time, (int, float))
            or not math.isfinite(wall_time) or wall_time > now or wall_time <= 0):
        return None
    return now - wall_time


def stale_threshold():
    try:
        value = float(os.environ.get('ATTENTION_DEV_LIVE_STALE_AFTER_S', '5'))
        if not math.isfinite(value) or value <= 0:
            raise ValueError()
        return value
    except ValueError:
        return 5.0


def _phase(ep, version):
    path = ep / 'live/phase.json'
    if not path.exists():
        return 'unknown', None
    if not path.resolve().is_relative_to(ep):
        raise MonitorError('path_escape', 'Phase outside episode')
    try:
        value = json.loads(path.read_bytes())
        if value.get('version') != version:
            raise MonitorError('version_mismatch', 'Phase version mismatch')
        return str(value.get('phase') or 'unknown'), value.get('wall_time')
    except (ValueError, TypeError, AttributeError) as exc:
        if isinstance(exc, MonitorError):
            raise
        raise MonitorError('bad_phase', 'Malformed phase metadata') from exc


VIEW_KEYS = {'external', 'side', 'top', 'left_wrist', 'right_wrist'}


def live(store, exp_id, key, view=None):
    c = store.cell(key)
    if c['experiment_id'] != exp_id:
        raise MonitorError('cell_mismatch', 'Cell outside experiment', 404)
    now = time.time()
    row = dict(state='waiting_first_frame', monitor_state='waiting_first_frame',
               age_seconds=None, server_time=now, sequence=None, version=c.get('active_version'),
               episode_id=None, frame_sha256=None, stale_after_s=stale_threshold(),
               phase='unknown', cell_state=c['state'], waiting_since=None)
    # Do not serve active_episode after the execution has ended. Its existence
    # is not terminal evidence; the viewer can keep its already decoded frame.
    if c['state'] in TERMINAL:
        row.update(state='terminal_saved', monitor_state='terminal_saved')
        return c, row, None
    if not c.get('active_episode'):
        claimed = [j for j in c.get('jobs', []) if j.get('state') == 'claimed']
        if claimed:
            kind = claimed[-1].get('kind')
            row['phase'] = 'model' if kind == 'model' else 'advisor' if kind == 'advisor' else 'unknown'
            row['waiting_since'] = claimed[-1].get('claimed_at') or claimed[-1].get('reserved_at')
        return c, row, None
    ep = Path(c['active_episode']).resolve()
    root = Path(c['artifact_dir']).resolve()
    if ep == root or not ep.is_relative_to(root):
        raise MonitorError('episode_outside_cell', 'Episode outside registered cell')
    if not ep.is_dir():
        raise MonitorError('invalid_episode_path','Registered episode directory is missing')
    # Extra camera angles live in their own folder with the same metadata schema; 'external' is the original view.
    if view not in (None, 'external') and view not in VIEW_KEYS:
        raise MonitorError('unknown_view', 'Unknown camera view', 404)
    base = ep / 'live' if view in (None, 'external') else ep / 'live' / 'views' / view
    latest = base / 'latest.json'
    if not latest.resolve().is_relative_to(ep):
        raise MonitorError('path_escape', 'Live metadata outside episode')
    row['phase'], row['waiting_since'] = _phase(ep, c.get('active_version'))
    if not latest.exists():
        if row['phase'] != 'unknown':
            row.update(state='initializing', monitor_state='initializing')
        error_path=ep/'live/error.json'
        if error_path.exists():
            if not error_path.resolve().is_relative_to(ep):raise MonitorError('path_escape','Display error outside episode')
            try:row['error']=str(json.loads(error_path.read_bytes())['error'])
            except (ValueError,KeyError,TypeError) as exc:raise MonitorError('bad_display_error','Malformed display error') from exc
            row.update(state='error',monitor_state='error',code='render_error')
        return c, row, None
    try:
        meta = json.loads(latest.read_bytes())
        if not isinstance(meta, dict):
            raise ValueError()
    except (ValueError, TypeError) as exc:
        raise MonitorError('bad_json', 'Malformed live metadata') from exc
    if not re.fullmatch(r'frame-\d{6}\.jpg', str(meta.get('file', ''))):
        raise MonitorError('invalid_filename', 'Invalid frame identity')
    if type(meta.get('version')) is not int or meta['version'] != c.get('active_version'):
        raise MonitorError('version_mismatch', 'Live version differs from active version', 409)
    if not re.fullmatch(r'[a-f0-9]{32}', str(meta.get('episode_id', ''))):
        raise MonitorError('invalid_episode', 'Invalid frame episode identity')
    # Reset receipt, when present, is authoritative episode binding. Before
    # ready the worker publishes within this registered episode directory.
    receipt = ep / 'reset_receipt.json'
    if receipt.exists():
        if not receipt.resolve().is_relative_to(ep):
            raise MonitorError('path_escape', 'Reset receipt outside episode')
        try:
            if json.loads(receipt.read_bytes())['episode_id'] != meta['episode_id']:
                raise MonitorError('episode_mismatch', 'Live episode differs from reset receipt', 409)
        except (ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, MonitorError):
                raise
            raise MonitorError('bad_receipt', 'Invalid reset receipt') from exc
    if type(meta.get('sequence')) is not int or meta['sequence'] < 0:
        raise MonitorError('bad_sequence', 'Invalid frame sequence')
    frame = (base / meta['file']).resolve()
    if not frame.is_relative_to(ep):
        raise MonitorError('path_escape', 'Live frame outside episode')
    if not frame.is_file():
        raise MonitorError('frame_missing', 'Published frame is missing')
    if hashlib.sha256(frame.read_bytes()).hexdigest() != meta.get('sha256'):
        raise MonitorError('digest_mismatch', 'Frame digest mismatch')
    age = frame_age(meta.get('wall_time'), now)
    state = 'live' if age is not None and age <= row['stale_after_s'] else 'stale'
    row.update(meta)
    row.update(state=state, monitor_state=state, age_seconds=age,
               frame_sha256=meta['sha256'], server_time=now,
               stale_after_s=stale_threshold(), cell_state=c['state'])
    return c, row, frame


def response(store, exp_id, key, *, image=False, query=None):
    try:
        view = (query or {}).get('view', [None])[0]
        c, row, frame = live(store, exp_id, key, view)
        if not image:
            return row, 'application/json', 200, {}
        constraints = {'episode': 'episode_id', 'version': 'version', 'seq': 'sequence', 'sha': 'frame_sha256'}
        for parameter, field in constraints.items():
            if parameter in (query or {}) and str(query[parameter][0]) != str(row.get(field)):
                return dict(row, error='Frame identity changed', code='identity_changed'), 'application/json', 409, {}
        if frame is None:
            return dict(row, error='No published frame', code='no_frame'), 'application/json', 404, {}
        # Read bytes once, then revalidate the hash and cell identity before
        # handing them to HTTP. Never reopen a mutable path in the Handler.
        raw = frame.read_bytes()
        current = store.cell(key)
        if any(current.get(k) != c.get(k) for k in ('active_episode', 'active_version', 'state')):
            return dict(state='waiting_first_frame', error='Execution changed', code='identity_changed'), 'application/json', 409, {}
        if hashlib.sha256(raw).hexdigest() != row['frame_sha256']:
            raise MonitorError('digest_mismatch', 'Frame changed during read', 409)
        age = frame_age(row.get('wall_time'), time.time())
        state = 'live' if age is not None and age <= row['stale_after_s'] else 'stale'
        headers = {'X-Frame-Sequence': str(row['sequence']), 'X-Preview-State': state,
                   'X-Episode-Id': row['episode_id'], 'X-Frame-Version': str(row['version']),
                   'X-Frame-Sha256': row['frame_sha256']}
        if age is not None:
            headers['X-Frame-Age'] = str(age)
        return raw, 'image/jpeg', 200, headers
    except OSError as exc:
        LOG.warning('Live monitor IO %s/%s: %s',exp_id,key,exc)
        return dict(state='error',monitor_state='error',code='io_error',error=str(exc),phase='unknown',age_seconds=None,server_time=time.time()),'application/json',500,{}
    except MonitorError as exc:
        LOG.warning('Live monitor %s/%s: %s: %s', exp_id, key, exc.code, exc)
        return dict(state='error', monitor_state='error', code=exc.code, error=str(exc),
                    phase='unknown', age_seconds=None, server_time=time.time()), 'application/json', exc.status, {}
