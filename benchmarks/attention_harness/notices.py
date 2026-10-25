"""Small, read-only operator notices from persisted state and bounded health reads."""
from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .parcc_client import DEFAULT_PARCC_URL


def safe_text(value, fallback="A problem needs attention"):
    text = str(value or "")
    if 'worker_lost' in text.lower():
        return 'The worker stopped before finishing.'
    text = re.sub(r"(?is)traceback \(most recent call last\).*", "", text)
    text = re.sub(r"(?i)https?://\S+", "[service]", text)
    text = re.sub(r"(?i)(?:authorization|api[_ -]?key|token|password|cookie|secret)\s*[:=]\s*\S+", "[private detail]", text)
    text = re.sub(r"(?i)\bBearer\s+\S+", "[private detail]", text)
    text = re.sub(r"(?i)(?:X-[A-Za-z-]+|Host|Set-Cookie)\s*:\s*\S+", "[private detail]", text)
    text = re.sub(r"(?:[A-Za-z]:\\|(?<![\w.-])/)[^\s,;]+", "[file]", text)
    text = re.sub(r"\b(?:[a-fA-F0-9]{24,}|[A-Za-z0-9_-]{40,})\b", "[reference]", text)
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    if re.search(r'[\u3400-\u9fff]', text):
        return fallback[:200]
    text = text.replace('_', ' ')
    text = re.sub(r"\s+", " ", text).strip(" :;")
    return (text or fallback)[:200]


def safe_detail(value):
    text = str(value or "")
    if re.match(r"(?i)^\s*TimeoutError\s*:", text):
        return 'The model service timed out while answering.'
    text = re.sub(r"(?i)^\s*(?:ParccError|DevConflict|TimeoutError)\s*:\s*", "", text)
    return safe_text(text, '')


def parcc_probe():
    """GET the gateway's root, never a model endpoint."""
    endpoint = __import__('os').environ.get('PARCC_URL', DEFAULT_PARCC_URL)
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return False
    with urlopen(f'{parsed.scheme}://{parsed.netloc}/', timeout=5) as response:
        return response.status < 500


class NoticeReader:
    def __init__(self, bridge=None, memory_view=None, quota=None, probe=None, store=None):
        self.bridge, self.memory_view, self.quota, self.probe, self.store = bridge, memory_view, quota, probe or parcc_probe, store
        self._cache = {}
        self._health = {}
        self._seen = {}

    def _cached_health(self, name, fn):
        now = time.monotonic()
        until, value = self._health.get(name, (0, None))
        if now >= until:
            try: value = bool(fn())
            except Exception: value = False
            self._health[name] = (now + 30, value)
        return value

    def read(self, exp=None, run=None):
        now = time.monotonic()
        key = (exp or '', run or '')
        if key in self._cache and now < self._cache[key][0]:
            return self._cache[key][1]
        try: result = self._build(exp, run)
        except Exception:
            result = {'generated_at': datetime.now(timezone.utc).isoformat(), 'notices': [
                self._notice('system-read', 'error', 'system', 'Some status details are unavailable',
                             'The page could not read current status.', 'Try refreshing the page.', time.time())]}
        for row in result['notices']:
            row['since'] = self._seen.get(row['id'], row['since'])
        self._seen = {row['id']: row['since'] for row in result['notices']}
        self._cache[key] = (now + 5, result)
        return result

    @staticmethod
    def _notice(id, level, source, title, detail, hint, since):
        try: date = datetime.fromtimestamp(float(since or time.time()), timezone.utc)
        except (TypeError, ValueError, OverflowError): date = datetime.now(timezone.utc)
        return dict(id='n-' + hashlib.sha256(str(id).encode()).hexdigest()[:12], level=level, source=source, title=safe_text(title),
                    detail=safe_detail(detail), hint=safe_text(hint, ''),
                    since=date.isoformat())

    def _memory_health(self):
        gateway = self.memory_view.gateway
        if hasattr(gateway, 'base_url'):
            request = Request(gateway.base_url + '/health', headers={'X-Memory-Service-Key': gateway.api_key})
            with urlopen(request, timeout=5) as response:
                return response.status == 200
        return gateway.health()

    def _v2_records(self, exp):
        """Read SQLite snapshots without opening the ledger's write transaction."""
        if exp and not re.fullmatch(r'devexp-[a-f0-9]{24}', exp):
            return None, [], []
        bridge = self.bridge
        paths = ([Path(bridge.store.path)] if hasattr(bridge, 'store') and not callable(bridge.store)
                 else [bridge.root / exp / 'ledger.sqlite3'] if exp and hasattr(bridge, 'root')
                 else list(bridge.root.glob('devexp-*/ledger.sqlite3')) if hasattr(bridge, 'root') else [])
        latest = None
        cells = []
        events = []
        if hasattr(bridge, 'store') and not callable(bridge.store) and paths and not paths[0].is_file():
            raise FileNotFoundError('ledger unavailable')
        for path in paths:
            if not path.is_file(): continue
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1) as db:
                if exp:
                    row = db.execute('SELECT payload FROM v2_experiments WHERE id=?', (exp,)).fetchone()
                else:
                    row = db.execute('SELECT payload FROM v2_experiments ORDER BY rowid DESC LIMIT 1').fetchone()
                if not row: continue
                saved = json.loads(row[0])
                if getattr(bridge, 'goal_id', None) and saved.get('goal_id') != bridge.goal_id:
                    continue
                if latest and (saved.get('created_at') or 0) < (latest.get('created_at') or 0):
                    continue
                latest = saved
                eid = saved['experiment_id']
                cells = []
                for key, payload in db.execute('SELECT id,payload FROM v2_cells WHERE exp=?', (eid,)):
                    cell = json.loads(payload)
                    cell['jobs'] = [{**json.loads(body), 'state': state} for state, body in db.execute('SELECT state,payload FROM v2_jobs WHERE cell=?', (key,))]
                    cell['versions'] = [json.loads(body) for body, in db.execute('SELECT payload FROM v2_versions WHERE cell=?', (key,))]
                    cells.append(cell)
                keys = [cell.get('key') for cell in cells if cell.get('key')]
                events = (db.execute("SELECT at,payload FROM v2_events WHERE kind='provider_retry' AND json_extract(payload,'$.cell') IN (" + ','.join('?' for _ in keys) + ") ORDER BY rowid DESC LIMIT 100", keys).fetchall()
                          if keys else [])
        return latest, cells, events

    def _build(self, exp, run=None):
        rows = []
        add = lambda *args: rows.append(self._notice(*args))
        stamp = time.time()
        if self.bridge is None and self.store is not None:
            add('bridge-missing', 'warning', 'system', 'Development run status is unavailable',
                'The development run service is not configured.', 'Open a configured development workspace to see its runs.', stamp)
        if self.store is not None:
            try:
                with self.store._connect() as db:
                    db.execute('SELECT 1 FROM runs LIMIT 1').fetchone()
                    if run and not exp:
                        record = db.execute('SELECT payload FROM runs WHERE id=?', (run,)).fetchone()
                        if not record:
                            record = db.execute('SELECT payload FROM dev_sessions WHERE id=?', (run,)).fetchone()
                        if record:
                            saved = json.loads(record[0])
                            state = saved.get('status') or saved.get('state')
                            if state in {'needs_review', 'failed', 'condition_invalid', 'budget_exhausted'}:
                                add('saved-run-'+str(saved.get('run_id') or saved.get('session_id'))+'-'+state,
                                    'error', 'run', 'Run stopped' if state == 'failed' else 'Run needs review',
                                    saved.get('reason') or state.replace('_', ' '), 'Check the run before trying again.',
                                    saved.get('updated_at') or saved.get('created_at') or stamp)
            except Exception:
                add('ui-store-unreadable', 'error', 'system', 'Run records are unavailable',
                    'The server could not read saved runs.', 'Try refreshing the page.', stamp)
        if self.bridge is not None:
            try:
                snap, v2_cells, events = self._v2_records(exp) if not run else (None, [], [])
                if snap:
                    eid = snap['experiment_id']
                    for cell in v2_cells:
                        state = cell.get('state')
                        if state in {'needs_review', 'failed', 'condition_invalid', 'budget_exhausted'}:
                            reason = safe_text(cell.get('reason'), 'The run needs attention.')
                            title = {'needs_review':'Run needs review', 'failed':'Run stopped', 'condition_invalid':'Run condition is invalid', 'budget_exhausted':'Run budget is exhausted'}[state]
                            add(f'cell-{cell.get("key", state)}-{state}', 'error', 'run', title, reason,
                                'Check the run and start a new run when ready.', cell.get('updated_at') or stamp)
                        for version in cell.get('versions', []):
                            if version.get('state') in {'failed', 'condition_invalid'} or version.get('valid') is False:
                                add(f'version-{cell.get("key")}-{version.get("version",version.get("n"))}', 'error', 'run',
                                    'A run version failed', version.get('validation_error') or version.get('reason') or 'This version could not finish.',
                                    'Check the run before trying again.', version.get('at') or stamp)
                        for job in cell.get('jobs', []):
                            if job.get('state') == 'needs_review' and state != 'needs_review':
                                add(f'job-{cell.get("key")}-{job.get("version")}-{job.get("kind")}', 'error', 'run',
                                    'Run work needs review', job.get('reason') or 'The worker did not finish this step.',
                                    'Check the run before trying again.', job.get('updated_at') or stamp)
                    if snap.get('state') in {'needs_review', 'failed', 'condition_invalid', 'budget_exhausted'} and not any(n['source']=='run' for n in rows):
                        add(f'experiment-{eid}', 'error', 'run', 'Run stopped', snap.get('reason') or snap['state'].replace('_',' '),
                            'Check the run and start a new run when ready.', snap.get('updated_at') or stamp)
                    keys = {c.get('key') for c in v2_cells}
                    final_retry = 0
                    for at, raw in events:
                        if snap.get('state') == 'completed': break
                        try:event = json.loads(raw)
                        except (ValueError, TypeError):continue
                        if event.get('cell') not in keys: continue
                        try:
                            attempt = min(5, max(1, int(event.get('attempt') or 1)))
                            limit = min(5, max(1, int(event.get('max_attempts') or 5)))
                        except (ValueError, TypeError):continue
                        final_retry = max(final_retry, attempt)
                        add(f'retry-{event.get("cell")}-{event.get("version")}-{event.get("kind")}-{attempt}',
                            'warning', 'run', f'Model service unavailable, retry {attempt} of {limit}',
                            'The model gateway did not answer.',
                            'The run retries automatically.', at)
                    reason = ' '.join(safe_text(c.get('reason'), '') for c in v2_cells)
                    if re.search(r'unreachable|unavailable|deadline|timeout', reason, re.I) and snap.get('state') in {'needs_review','failed','condition_invalid'}:
                        exhausted = final_retry >= 5
                        add(f'unreachable-{eid}', 'error', 'run',
                            'Run stopped: PARCC model service unreachable after 5 attempts' if exhausted else 'Run stopped: model service unreachable',
                            'The model service could not complete the request.', 'Start a new run.', stamp)
            except Exception:
                add('store-unreadable', 'error', 'system', 'Run records are unavailable',
                    'The server could not read saved runs.', 'Try refreshing the page.', stamp)
        if not self._cached_health('parcc', self.probe):
            add('parcc-down', 'warning', 'model', 'PARCC model service is not responding',
                'The model gateway did not answer a health check.',
                'Runs retry automatically up to 5 times; Codex source is an alternative.', stamp)
        try:
            quota = self.quota() if self.quota else {}
            if float(quota.get('used_percent', 0)) >= 70:
                add('codex-quota', 'warning', 'model', 'Codex quota is running low this week',
                    f'{int(quota["used_percent"])}% of the weekly quota has been used.', 'Check the quota before starting another Codex run.', stamp)
        except Exception: pass
        if self.memory_view is not None and self.memory_view.source == 'service' and not self._cached_health('memory', self._memory_health):
            add('memory-down', 'error', 'memory', 'Memory service is not reachable; notes cannot be read',
                'The Memory service did not answer a health check.', 'Try again when the service is available.', stamp)
        from .memory_monitor import automatic_checks
        auto=automatic_checks(gateway=self.memory_view.gateway if self.memory_view is not None else None)
        for n in auto['notices']:
            add('auto-'+n['title'],'warning' if 'paused' in n['title'].lower() else 'info','memory',n['title'],'','',n['at'])
        for error in auto['errors']:add('auto-error-'+error,'error','memory',error,'','',stamp)
        for event in auto['events']:
            kind=event['event'];title=safe_text(event['title'],'Note')
            wording={'promoted':f'Note checked and trusted: {title} — the failure stopped when Dev had the note.',
                     'rejected':f'Note rejected after check: {title}.',
                     'inconclusive':safe_text(event.get('reason'),'Could not finish the check')+' — '+title,
                     'demoted':f'Note moved back to Unvalidated: {title} — the failure came back in 3 of its last 5 uses.'}
            if kind=='promoted' and event.get('trust_scope')=='task_scoped':wording[kind]=f'Trusted for this task: {title} — checks on other seeds passed.'
            if kind=='demoted' and event.get('trust_scope')=='scene_scoped':wording[kind]=f'Note moved back to trusted on its origin scene: {title} — the failure came back in 3 of its last 5 uses.'
            if kind in wording:add('memory-event-'+str(event['seq']),'info','memory',wording[kind],'','',event.get('time') or stamp)
        priority = {'error': 0, 'warning': 1, 'info': 2}
        rows.sort(key=lambda n: (priority[n['level']], n['since']), reverse=False)
        rows = sorted(rows, key=lambda n: (priority[n['level']], -datetime.fromisoformat(n['since']).timestamp()))[:20]
        return {'generated_at': datetime.now(timezone.utc).isoformat(), 'notices': rows}
