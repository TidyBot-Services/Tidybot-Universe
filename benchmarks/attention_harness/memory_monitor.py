"""Bounded, read-only Dev v2 Memory projection for the monitor.

Usage comes from local experiment ledgers, including test-seeded retrievals.
The online Memory catalogue supplies note metadata and completed decisions.
Importer-only use and impact routes are never called; absent validation is
shown as not tested, with no inferred lift.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
import time
from pathlib import Path

from .dev_loop import sha

EXPERIMENT = re.compile(r'devexp-[0-9a-f]{24}\Z')
LIMIT = 600
EVENT_LIMIT = 500
_USAGE = {}
EXPERIMENT_LIMIT = 200
CACHE_SECONDS = 5
EXTRA_ERRORS = (PermissionError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError)


class CatalogueUnavailable(RuntimeError):
    """The online catalogue could not be read."""


def _log_failure(error, context):
    # Exception messages may contain service responses or note text.
    print(f'Memory monitor {context}: {type(error).__name__}: details unavailable', file=sys.stderr)


def short(value, length):
    return str(value)[:length] if value is not None else None


def effect(row):
    if row.get('seeded_for_tests') is True:
        return 'not_tested', None
    validation = row.get('validation_summary') or {}
    decision = validation.get('decision')
    complete = bool(validation.get('completed_pairs'))
    if decision == 'promoted' and complete:
        report = validation.get('report') or {}
        lift = validation.get('lift_pt', report.get('lift_pt'))
        return 'helps', round(float(lift), 2) if isinstance(lift, (int, float)) and not isinstance(lift, bool) else None
    if decision == 'rejected' and complete:
        return 'not_helps', None
    return 'not_tested', None


def status(row):
    return {'trusted': 'active', 'active': 'active', 'provisional': 'unvalidated', 'disabled': 'off',
            'expired': 'expired', 'rolled_back': 'withdrawn', 'withdrawn': 'withdrawn'}.get(row.get('status'), 'off')


def _packet_ids(detail):
    ref = detail.get('packet') or {}
    path = Path(ref.get('uri', '/missing'))
    if not path.is_file() or sha(path) != ref.get('sha256'):
        return []
    packet = json.loads(path.read_bytes())
    return [short(row.get('memory_id'), 120) for row in packet.get('entries', [])[:LIMIT]
            if isinstance(row, dict) and row.get('memory_id')]


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def _uses(row):
    uses = row.get('uses')
    if not isinstance(uses, dict):
        return None
    return {key: _count(uses.get(key)) for key in ('grants', 'outcomes', 'failure_gone')}


def _expires_in_days(row, now):
    expires = row.get('expires_at')
    if not isinstance(expires, (int, float)) or isinstance(expires, bool):
        return None
    return max(0, math.ceil((expires - now) / 86400))


def _note_seed(row):
    seed = row.get('source_seed', (row.get('applicability_summary') or {}).get('seed'))
    return seed if type(seed) is int else None


def _layered_reads(store, cells):
    """Run flag and per-note match levels from this run's own ledger."""
    keys = {cell['key'] for cell in cells}
    flagged, levels = False, {}
    with sqlite3.connect('file:' + str(Path(store.path).resolve()) + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT kind, payload FROM v2_events WHERE kind IN "
                          "('planner_memory_unvalidated_used', 'planner_memory_retrieved') ORDER BY seq").fetchall()
    for kind, raw in rows:
        detail = json.loads(raw)
        if detail.get('cell') not in keys:
            continue
        if kind == 'planner_memory_unvalidated_used':
            flagged = True
        for entry in detail.get('versions') or []:
            if isinstance(entry, dict) and entry.get('match_level') in ('same_scene', 'same_task'):
                levels[entry.get('memory_id')] = entry['match_level']
    return flagged, levels


def _events(store, cells):
    keys = {cell['key'] for cell in cells}
    with sqlite3.connect('file:' + str(Path(store.path).resolve()) + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT seq, at, kind, payload FROM v2_events ORDER BY seq DESC LIMIT 5000').fetchall()
    result = []
    kinds = {'planner_memory_query_started': 'checking notes',
             'planner_memory_query_finished': 'note check finished',
             'planner_memory_retrieved': 'used notes',
             'planner_memory_result_skipped': 'test note excluded'}
    grants = {}
    run_titles = {}
    for seq, ledger_at, kind, raw in reversed(rows):
        if kind not in kinds and kind not in {'settled', 'cell_updated'} and not kind.startswith('planner_memory_run_note'):
            continue
        detail = json.loads(raw)
        if detail.get('cell') not in keys:
            continue
        if kind.startswith('planner_memory_run_note'):
            note_id = detail.get('note_id')
            if kind == 'planner_memory_run_note_created':
                run_titles[note_id] = short(detail.get('title'), 80)
                text = f"v{detail['help_version']} learned: {run_titles[note_id]}"
            elif kind == 'planner_memory_run_note_off':
                text = 'turned off for this run: ' + (run_titles.get(note_id) or 'note')
            elif kind == 'planner_memory_run_note_outcome':
                text = 'failure gone' if detail.get('failure_gone') else 'failure seen'
            elif kind == 'planner_memory_run_notes_delivered':
                text = 'used run notes'
            else:
                continue
            result.append(dict(seq=seq, ts=ledger_at, type=kind, version=detail.get('version'),
                               note_ids=detail.get('note_ids', [note_id] if note_id else []), text=text))
            continue
        if kind == 'settled':
            if detail.get('kind') != 'eval':
                continue
            ref = (detail.get('receipt') or {}).get('eval_review') or (detail.get('receipt') or {}).get('review')
            if not isinstance(ref, dict):
                continue
            path = Path(ref.get('uri', '/missing'))
            if not path.is_file() or sha(path) != ref.get('sha256'):
                continue
            review = json.loads(path.read_bytes())
            if review.get('status') != 'completed' or not isinstance(review.get('passed'), bool):
                continue
            passed = review['passed']
            result.append({'seq': seq, 'ts': detail.get('settled_at') or ledger_at,
                           'type': 'eval_passed' if passed else 'eval_failed',
                           'version': detail.get('version'), 'note_ids': [],
                           'text': 'passed' if passed else 'failed'})
            continue
        if kind == 'cell_updated':
            n = detail.get('active_version')
            if isinstance(n, int) and 1 <= n <= 12:
                result.append({'seq': seq, 'ts': ledger_at, 'type': 'version_started',
                               'version': n, 'note_ids': [], 'text': 'started'})
            continue
        if kind == 'planner_memory_retrieved':
            versions = detail.get('versions') or []
            ids = [short(entry.get('memory_id'), 120) for entry in versions
                   if isinstance(entry, dict) and entry.get('memory_id')]
            if not ids:
                ids = _packet_ids(detail)
            for entry in versions:
                if isinstance(entry, dict) and entry.get('grant_id') and entry.get('memory_id'):
                    grants[entry['grant_id']] = entry['memory_id']
        elif kind == 'planner_memory_result_skipped':
            ids = [grants[grant] for grant in detail.get('grants', []) if grant in grants]
        else:
            ids = []
        ts = detail.get('ended_at') or detail.get('started_at') or detail.get('at') or ledger_at
        event = {'seq': seq, 'ts': ts, 'type': kind, 'version': detail.get('version'),
                 'note_ids': ids, 'text': kinds[kind]}
        if kind == 'planner_memory_result_skipped':
            event['reason'] = short(detail.get('reason'), 120)
            event['text'] = {
                'test_seeded_excluded_from_results': 'test note excluded',
                'not_trusted_here_excluded_from_results': 'not counted in results',
                'reference_only_no_scene_contract': 'reference only · not counted',
                'run_note_excluded_from_results': 'not counted in results',
            }.get(event['reason'], 'test note excluded')
        result.append(event)
    return result


def _usage_from_ledgers(bridge, store):
    root = Path(getattr(bridge, 'root', Path(store.path).parent))
    current = Path(store.path).resolve()
    key = (str(root.resolve()), str(current))
    cached = _USAGE.get(key)
    tick = time.monotonic()
    if cached and tick - cached[0] < CACHE_SECONDS:
        return cached[1]
    paths = sorted(root.glob('devexp-*/ledger.sqlite3'),
                   key=lambda path: path.stat().st_mtime, reverse=True)[:EXPERIMENT_LIMIT]
    if current not in {path.resolve() for path in paths}:
        paths = ([current] + paths)[:EXPERIMENT_LIMIT]
    usage = {}
    for path in paths:
        with sqlite3.connect('file:' + str(path.resolve()) + '?mode=ro', uri=True) as db:
            rows = db.execute("SELECT at, kind, payload FROM v2_events WHERE kind IN "
                              "('planner_memory_retrieved', 'planner_memory_result_skipped') "
                              "ORDER BY seq").fetchall()
        grants = {}
        seen = set()
        for at, kind, raw in rows:
            detail = json.loads(raw)
            if kind == 'planner_memory_retrieved':
                versions = detail.get('versions') or []
                ids = [entry.get('memory_id') for entry in versions if isinstance(entry, dict)]
                if not ids:
                    ids = _packet_ids(detail)
                for entry in versions:
                    if isinstance(entry, dict) and entry.get('grant_id') and entry.get('memory_id'):
                        grants[entry['grant_id']] = entry['memory_id']
            else:
                ids = [grants[grant] for grant in detail.get('grants', []) if grant in grants]
            for note_id in ids:
                if not isinstance(note_id, str) or not note_id:
                    continue
                occurrence = (detail.get('cell'), detail.get('version'), note_id)
                if occurrence in seen:
                    continue
                seen.add(occurrence)
                count, last = usage.get(note_id, (0, None))
                usage[note_id] = (count + 1, max(last or 0, at) if isinstance(at, (int, float)) else last)
    if len(_USAGE) > 16:
        _USAGE.clear()
    _USAGE[key] = (tick, usage)
    return usage


def _attempt_outcome(cell, version, job):
    """Use the persisted attempt and verified native bundle, as the main attempt strip does."""
    if not job or job.get('state') != 'settled':
        return None
    receipt = job.get('receipt') or {}
    native_success = None
    ref = receipt.get('bundle')
    if isinstance(ref, dict):
        path = Path(ref.get('uri', '/missing'))
        if path.is_file() and sha(path) == ref.get('sha256'):
            bundle = json.loads(path.read_bytes())
            expected_attempt = f"attempt:{cell['key']}:{version['version'] - 1}"
            evaluator = bundle.get('native_evaluator') or {}
            if (bundle.get('run_id') == 'run:' + cell['key']
                    and bundle.get('attempt_id') == expected_attempt
                    and bundle.get('dev_version') == version['version']
                    and bundle.get('config_sha256') == version.get('config_sha256')
                    and bundle.get('policy_sha256') == (version.get('source') or {}).get('sha256')
                    and bundle.get('formal_eligible') is False
                    and evaluator.get('run_id') == bundle['run_id']
                    and evaluator.get('attempt_id') == expected_attempt
                    and evaluator.get('evaluated') is True
                    and isinstance(bundle.get('native_success'), bool)):
                native_success = bundle['native_success']
    # The main page treats a verified native success OR a persisted completed
    # execution as success; a known native failure or failed execution as failure.
    if native_success is True or receipt.get('execution_status') == 'completed':
        return 'passed'
    if native_success is False or receipt.get('execution_status') in {'failed', 'timed_out', 'timeout'}:
        return 'failed'
    return 'finished'


def _max_versions(experiment, cells, events=()):
    """Use the frozen run limit, falling back to observed versions for old runs."""
    ref = experiment.get('manifest') or {}
    try:
        path = Path(ref.get('uri', '/missing')) if isinstance(ref, dict) else Path('/missing')
        if path.is_file() and sha(path) == ref.get('sha256'):
            manifest = json.loads(path.read_bytes())
            configured = manifest.get('max_versions', manifest.get('max_iterations'))
            if configured is None:
                configured = ((manifest.get('selection') or {}).get('configuration') or {}).get('max_iterations')
            if isinstance(configured, int) and not isinstance(configured, bool):
                return max(1, min(12, configured))
    except (OSError, ValueError, TypeError, AttributeError):
        # An unavailable manifest must not prevent displaying observed versions.
        pass
    seen = [n for cell in cells for n in
            [cell.get('active_version')] + [v.get('version') for v in cell.get('versions', [])] +
            [job.get('version') for job in cell.get('jobs', [])]
            if isinstance(n, int) and not isinstance(n, bool) and n > 0]
    seen.extend(event['version'] for event in events if isinstance(event.get('version'), int)
                and not isinstance(event['version'], bool) and event['version'] > 0)
    return max(1, min(12, max(seen))) if seen else 5


def project(bridge, memory_view, experiment_id, *, now=None):
    if not EXPERIMENT.fullmatch(experiment_id):
        raise ValueError('invalid experiment id')
    experiment, _ = bridge.get('/api/dev-v2/experiments/' + experiment_id)
    store = bridge.store(experiment_id) if callable(getattr(bridge, 'store', None)) else bridge.store
    cells = experiment.get('cells', [])
    local_enabled = any(cell.get('run_notes') is True for cell in cells)
    if memory_view is None and not local_enabled:
        raise FileNotFoundError('Memory service unavailable')
    events = _events(store, cells)
    max_versions = _max_versions(experiment, cells, events)
    used = {}
    for event in events:
        if event['type'] in {'planner_memory_retrieved', 'planner_memory_run_notes_delivered'}:
            for note_id in event['note_ids']:
                used.setdefault(event['version'], {})[note_id] = event['ts']
    versions = []
    for n in range(1, max_versions + 1):
        related = [cell for cell in cells if any(v.get('version') == n for v in cell.get('versions', []))]
        jobs = [job for cell in cells for job in cell.get('jobs', []) if job.get('version') == n]
        memory_events = [event for event in events if event['version'] == n and event['type'] in
                         {'planner_memory_query_started', 'planner_memory_query_finished'}]
        outcomes = []
        for cell in related:
            version = next(v for v in cell['versions'] if v.get('version') == n)
            execute = next((job for job in cell.get('jobs', []) if job.get('version') == n and job.get('kind') == 'execute'), None)
            outcome = _attempt_outcome(cell, version, execute)
            if outcome:
                outcomes.append(outcome)
            elif version.get('valid') is False:
                outcomes.append('failed')
        if 'passed' in outcomes:
            state = 'passed'
        elif 'failed' in outcomes:
            state = 'failed'
        elif 'finished' in outcomes:
            state = 'finished'
        elif any(job.get('kind') == 'execute' and job.get('state') in {'claimed', 'reserved'} for job in jobs) or any(cell.get('active_version') == n and cell.get('active_episode') for cell in cells):
            state = 'running'
        elif any(job.get('kind') == 'model' and job.get('state') in {'claimed', 'reserved'} for job in jobs):
            state = 'generating'
        elif memory_events and memory_events[-1]['type'] == 'planner_memory_query_started':
            state = 'reading'
        elif related or jobs:
            state = 'generating'
        else:
            state = 'waiting'
        versions.append({'n': n, 'state': state,
                         'note_ids': list(used.get(n, {}))[:LIMIT], 'started_at': None, 'ended_at': None})
    for event in events:
        n = event['version']
        if not isinstance(n, int) or not 1 <= n <= max_versions:
            continue
        if event['type'] == 'planner_memory_query_started':
            versions[n-1]['started_at'] = versions[n-1]['started_at'] or event['ts']
        elif event['type'] == 'planner_memory_query_finished':
            versions[n-1]['ended_at'] = event['ts']
    timestamp = now if now is not None else time.time()
    warnings = []
    usage_available = True
    try:
        usage = _usage_from_ledgers(bridge, store)
    except EXTRA_ERRORS as error:
        _log_failure(error, 'ledger usage')
        warnings.append('Some note details are unavailable.')
        usage = {}
        usage_available = False
    try:
        catalogue = memory_view.dev_catalogue() if memory_view is not None else []
    except EXTRA_ERRORS as error:
        if not local_enabled:
            raise CatalogueUnavailable('online catalogue unavailable') from error
        catalogue = []
        warnings.append('Service notes are unavailable; run notes are shown.')
    seeds = {cell.get('seed') for cell in cells if type(cell.get('seed')) is int}
    try:
        unvalidated_used, levels = _layered_reads(store, cells)
    except EXTRA_ERRORS as error:
        _log_failure(error, 'memory read levels')
        unvalidated_used, levels = False, {}
    notes = []
    for row in catalogue[:LIMIT]:
        note_id = short(row.get('memory_id'), 120)
        if not note_id:
            continue
        if not usage_available:
            validation, lift, reads, last = 'not_tested', None, 0, None
        else:
            try:
                validation, lift = effect(row)
                reads, last = usage.get(row['memory_id'], (0, None))
            except EXTRA_ERRORS as error:
                _log_failure(error, 'note details')
                if not warnings:
                    warnings.append('Some note details are unavailable.')
                validation, lift, reads, last = 'not_tested', None, 0, None
        notes.append({'id': note_id, 'title': short(row.get('title') or 'Untitled note', 80),
                      'summary': short(row.get('summary'), 180), 'group': short(row.get('group') or 'General', 40),
                      'status': status(row), 'effect': validation, 'lift_pt': lift, 'reads': reads,
                      'last_used_at': last, 'test_seeded': row.get('seeded_for_tests') is True})
        seed = _note_seed(row)
        level = levels.get(row.get('memory_id'))
        notes[-1].update(unvalidated=row.get('status') == 'provisional' or row.get('unvalidated') is True,
                         uses=_uses(row), expires_in_days=_expires_in_days(row, timestamp), scene_seed=seed,
                         # Another scene of the same task: readable only as a labelled reference.
                         reference=level == 'same_task' or level is None and seed is not None and bool(seeds) and seed not in seeds)
    from .memory_run_notes import events as local_events, PREFIX
    run_notes = []
    for cell in cells:
        history = local_events(store, cell['key'])
        off = {e['note_id'] for kind, e in history if kind == PREFIX + 'off'}
        for kind, event in history:
            if kind != PREFIX + 'created':
                continue
            note_id = event['note_id']
            uses = {e['version'] for k, e in history if k == 'planner_memory_run_notes_delivered'
                    and note_id in e['note_ids']}
            gone = {e['version'] for k, e in history if k == PREFIX + 'outcome'
                    and e['note_id'] == note_id and e['failure_gone']}
            run_notes.append(dict(id=note_id, cell=cell['key'], title=short(event['title'], 80),
                                  help_version=event['help_version'], from_version=event['help_version'] + 1,
                                  uses=len(uses), failure_gone=len(gone), off=note_id in off))
    from .dev_memory_view import scope_projection
    scopes = [scope_projection(store, cell) for cell in cells]
    # A multi-cell run must still explain any cell restricted to references.
    scope = next((s for s in scopes if s and s['level'] == 'reference_only'), next((s for s in scopes if s), None))
    return {'generated_at': timestamp,
            'run': {'experiment_id': experiment_id, 'state': short(experiment.get('state'), 40),
                    'max_versions': max_versions, 'versions': versions, 'unvalidated_used': unvalidated_used,
                    'memory_scope': scope, 'can_operate_run_notes': local_enabled},
            'notes': notes, 'run_notes': run_notes, 'events': events[-EVENT_LIMIT:], 'warnings': warnings}
