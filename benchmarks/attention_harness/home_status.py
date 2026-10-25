"""Idle home page (the general cover): readiness, today's runs and recent scenes, from existing records.

Display only; nothing here starts or changes a run. New runs start from the main UI's dialog.
"""
import json
import os
import re
import shutil
import ssl
import statistics
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .dev_loop import owner_alive
from .parcc_client import DEFAULT_PARCC_URL

TASK_TITLES = {'pick_place_counter_to_sink': 'Pick Place: Counter → Sink', 'open_cabinet': 'Open the cabinet',
               'counter_to_sink': 'Move to the sink', 'counter_to_cab': 'Move into the cabinet'}
PROBE_TTL_SECONDS = 60
ENDED = {'needs_review', 'cancelled', 'condition_invalid', 'completed', 'failed'}
# Same test as the main UI's recordStatus: these reasons are a system problem, not the robot's.
SYSTEM_PROBLEM = re.compile(r'infrastructure|dispatch unknown|outcome_unknown', re.I)


def attempts_used(row):
    """Attempts the robot actually ran, counted like the main UI (used.executions); a version that never ran is not an attempt."""
    used = (row.get('used') or {}).get('executions')
    return used if isinstance(used, int) else len(row.get('versions') or [])


def attempts_limit(row):
    limit = (row.get('budget') or {}).get('executions')
    return limit if isinstance(limit, int) else None


def all_attempts_failed(row):
    """Ran to the end with every execution failed, as the main UI marks it."""
    runs = [j for j in row.get('jobs') or [] if j.get('kind') == 'execute']
    return row.get('state') == 'completed' and bool(runs) and all((j.get('receipt') or {}).get('execution_status') == 'failed' for j in runs)


def plain_reason(row):
    """One plain sentence for why a run ended, for an operator who does not read logs."""
    reason, state = str(row.get('reason') or ''), row.get('state')
    versions = len(row.get('versions') or [])
    attempts = attempts_used(row)
    if reason == 'eval_passed':
        return 'Solved: the reviewer confirmed the task was done.'
    if 'unreachable' in reason or ('TimeoutError' in reason and 'parcc' in reason):
        return 'The PARCC model service could not be reached.'
    if 'provider interrupted' in reason:
        return f'You stopped it while the AI was writing v{versions + 1}.'
    if 'operator_cancel' in reason or state == 'cancelled':
        return 'You stopped it.'
    if 'input_bound_exceeded' in reason:
        return "The AI's input for the next attempt got too long."
    if 'resource source drift' in reason:
        return 'A program file changed while it was running.'
    if 'dispatch unknown' in reason or 'infrastructure' in reason:
        return 'A system problem stopped it (not the robot).'
    if reason in {'target_G_E_reached', 'max_versions_reached'}:
        return f'All {attempts} attempts were used without solving the task.'
    if state == 'running':
        return 'Running now.'
    return reason.split(':')[0][:120] or 'Ended.'


def shown_state(row):
    """A cell can still read 'planned' after its experiment stopped; the experiment's end state wins."""
    return row['experiment_state'] if row.get('experiment_state') in ENDED and row.get('state') in {'planned', 'running'} else row.get('state')


def solved(row):
    return row.get('state') == 'completed' and row.get('reason') == 'eval_passed'


def today_summary(rows, now):
    day = datetime.fromtimestamp(now).date()
    today = [r for r in rows if datetime.fromtimestamp(r['created_at']).date() == day]
    return {'runs': len(today), 'solved': sum(solved(r) for r in today),
            'attempts': sum(attempts_used(r) for r in today)}


class ProviderProbe:
    """Small background checks of the PARCC models, cached so page polling never waits on them."""

    def __init__(self, url=None, key=None, timeout=20):
        self.url = url or os.environ.get('PARCC_URL', DEFAULT_PARCC_URL)
        self.key = key if key is not None else os.environ.get('PARCC_API_KEY') or os.environ.get('LITELLM_KEY')
        self.timeout, self.lock, self.result, self.checked_at, self.running = timeout, threading.Lock(), None, 0, False

    def _one(self, model):
        if not self.key:
            return {'state': 'unknown', 'detail': 'No PARCC key in this server environment.'}
        body = json.dumps({'model': model, 'messages': [{'role': 'user', 'content': 'Reply with exactly: pong'}],
                           'max_tokens': 64, 'temperature': 0}).encode()
        request = urllib.request.Request(self.url, data=body, headers={'Authorization': 'Bearer ' + self.key,
                                                                       'Content-Type': 'application/json'})
        begun = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                json.load(response)
            return {'state': 'ready', 'detail': f'Answered in {time.monotonic() - begun:.1f} s.'}
        except urllib.error.HTTPError as exc:
            text = exc.read().decode(errors='replace')
            if exc.code == 403 and 'blocked' in text.lower():
                return {'state': 'blocked', 'detail': 'PARCC says "Model is blocked" (403). Ask the PARCC admin.'}
            return {'state': 'down', 'detail': f'PARCC answered HTTP {exc.code}.'}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if isinstance(getattr(exc, 'reason', exc), ssl.SSLCertVerificationError):
                return {'state': 'down', 'detail': "This computer cannot verify PARCC's security certificate, so every call fails."}
            return {'state': 'down', 'detail': f'No answer: {type(getattr(exc, "reason", exc)).__name__}.'}

    def _run(self):
        result = {'glm': self._one('parcc/GLM'), 'qwen': self._one('parcc/Qwen')}
        with self.lock:
            self.result, self.checked_at, self.running = result, time.time(), False

    def snapshot(self, force=False):
        with self.lock:
            stale = force or time.time() - self.checked_at >= PROBE_TTL_SECONDS
            if stale and not self.running:
                self.running = True
                threading.Thread(target=self._run, daemon=True).start()
            return {'result': self.result, 'checked_at': self.checked_at or None, 'checking': self.running}


def planner_seconds(row):
    """Median planner answer time in this run's executions (no live probe; the planner starts per run)."""
    root = Path(row.get('artifact_dir') or '/nonexistent')
    times = []
    for log in root.glob('v*/executions/*/planner/planner_http.jsonl'):
        for line in log.read_text(errors='replace').splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get('path') in {'/plan', '/plan/ik_path'} and isinstance(item.get('elapsed_seconds'), (int, float)):
                times.append(item['elapsed_seconds'])
    return round(statistics.median(times), 1) if times else None


SCENE_VIEWS = {'overview': 'camera_initial.png', 'side': 'recording_views/side/000000.jpg', 'top': 'recording_views/top/000000.jpg'}


def scene_image(row, dev_root, view='overview'):
    """The newest recorded first frame of this run for one camera view, if it lies inside the Dev v2 root."""
    root = Path(dev_root).resolve()
    frames = sorted(Path(row.get('artifact_dir') or '/nonexistent').glob(f'v*/executions/*/{SCENE_VIEWS[view]}'),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    for frame in frames:
        if frame.resolve().is_relative_to(root) and frame.is_file():
            return frame
    return None


def latest_scene(rows, dev_root):
    """First frame of the newest run (of the last ten) that recorded one."""
    return next((frame for frame in (scene_image(r, dev_root) for r in rows[:10]) if frame is not None), None)


def glm_failures_today(rows, now):
    day = datetime.fromtimestamp(now).date()
    return sum('parcc/GLM' in str(r.get('reason') or '') for r in rows if datetime.fromtimestamp(r['created_at']).date() == day)


def readiness(probe, quota, failures, disk_root):
    """Five instruments for the page, each with a state word and a plain detail line."""
    glm = (probe.get('result') or {}).get('glm') or {'state': 'checking', 'detail': 'Checking…'}
    qwen = (probe.get('result') or {}).get('qwen') or {'state': 'checking', 'detail': 'Checking…'}
    if glm['state'] == 'ready' and failures:
        glm = {'state': 'unstable', 'detail': f"Answers now, but {failures} run{'s' if failures > 1 else ''} lost it today."}
    used = (quota or {}).get('used_percent')
    codex = ({'state': 'ready' if used < 90 else 'low', 'detail': f'{used:.0f}% of this week used.'} if isinstance(used, (int, float))
             else {'state': 'unknown', 'detail': 'Quota could not be read.'})
    usage = shutil.disk_usage(disk_root)
    free_gb = usage.free / 1e9
    disk = {'state': 'ready' if free_gb > 50 else 'low', 'value': f'{free_gb:.0f} GB', 'detail': f'{100 * usage.used / usage.total:.0f}% used.'}
    return {'codex': codex, 'glm': glm, 'qwen': qwen, 'disk': disk}


def scenes(rows, dev_root, limit=4):
    """Cover backdrops: the newest overview of each task, then other camera views of the newest run."""
    out, seen = [], set()
    for row in rows:
        if row['task_id'] not in seen and scene_image(row, dev_root):
            seen.add(row['task_id']); out.append({'experiment_id': row['experiment_id'], 'view': 'overview'})
    newest = next((r for r in rows if scene_image(r, dev_root)), None)
    if newest:
        out += [{'experiment_id': newest['experiment_id'], 'view': v} for v in ('side', 'top') if scene_image(newest, dev_root, v)]
    return out[:limit]


def scene_for(rows, dev_root, experiment_id, view='overview'):
    """One listed run's frame for a known view; unknown runs or views are refused."""
    row = next((r for r in rows if r['experiment_id'] == experiment_id), None)
    return scene_image(row, dev_root, view) if row and view in SCENE_VIEWS else None


def status(bridge, probe, quota, *, now=None):
    now = now or time.time()
    rows = sorted(bridge.list(), key=lambda r: r['created_at'], reverse=True)
    running = [r for r in rows if r.get('owner') and owner_alive(r['owner'])]
    probed = probe.snapshot()
    ready = readiness(probed, quota, glm_failures_today(rows, now), bridge.root)
    # The newest run may have ended before the robot moved; use the newest one that has data.
    timing = next((t for t in (planner_seconds(r) for r in rows[:10]) if t is not None), None)
    return {'checked_at': probed['checked_at'], 'checking': probed['checking'],
            'running': [{'experiment_id': r['experiment_id'], 'key': r['key']} for r in running],
            'today': today_summary(rows, now), 'readiness': ready, 'planner_seconds': timing,
            'scenes': scenes(rows, bridge.root),
            'recent': [{**{k:r[k] for k in ('execution_profile','planner_lock_sha256') if k in r},'experiment_id': r['experiment_id'], 'key': r['key'], 'at': r['created_at'], 'task': TASK_TITLES.get(r['task_id'], r['task_id']),
                        'policy': r['policy_id'], 'state': shown_state(r), 'attempts': attempts_used(r), 'attempts_limit': attempts_limit(r),
                        'why': plain_reason(r), 'solved': solved(r), 'all_failed': all_attempts_failed(r),
                        'system': bool(SYSTEM_PROBLEM.search(str(r.get('reason') or '')))} for r in rows[:12]]}
