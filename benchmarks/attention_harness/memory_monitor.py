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
from .memory_layers import LAYERS, layer_of

EXPERIMENT = re.compile(r'devexp-[0-9a-f]{24}\Z')
LIMIT = 600
EVENT_LIMIT = 500
_USAGE = {}
EXPERIMENT_LIMIT = 200
CACHE_SECONDS = 5


def automatic_checks(root=None,gateway=None,*,experiments_root=None):
    """Public worker state only: no requests, guidance, keys, or evidence paths."""
    import os
    from .memory_validation_worker import settings
    from .notices import safe_text,safe_detail
    def validation_text(value):
        text=safe_text(value)
        for code in ('source_size_limit_exceeded','unparsable_output','public_source_validation_failed','source_validation_failed'):
            text=text.replace(code.replace('_',' '),code)
        return text
    root=root or os.environ.get('ATTENTION_MEMORY_VALIDATION_STATE')
    value=dict(enabled=False,status='Off',runs_today=0,daily_run_limit=20,weekly_quota_stop_percent=70,daily_quota_rise_stop_points=5,queue=[],current=None,results=[],events=[],errors=[],notices=[],cross_seed_enabled=False,cross_seed_notes=[],skipped_seeds=[],positive_controls=[])
    cross_notes={}
    if root:
        try:
            config=settings(root);value['enabled']=config['enabled'];value['cross_seed_enabled']=config.get('cross_seed',{}).get('enabled',False);path=Path(root)/'state.json'
            value['daily_run_limit']=config.get('daily_run_limit',20)
            value['weekly_quota_stop_percent']=config.get('weekly_quota_stop_percent',70)
            value['daily_quota_rise_stop_points']=config.get('daily_quota_rise_stop_points',5)
            state=json.loads(path.read_bytes()) if path.exists() else {}
            value['status']=safe_text(state.get('status'),'Waiting for worker' if value['enabled'] else 'Off') if value['enabled'] else 'Off'
            value['runs_today']=state.get('runs_today',0)
            quota=state.get('weekly_quota')
            if type(quota) in (int,float) and 0<=quota<=100:value['weekly_quota']=quota
            stamp=state.get('weekly_quota_at')
            if type(stamp) in (int,float) and math.isfinite(stamp) and stamp<=time.time():
                value['weekly_quota_age_seconds']=max(0,time.time()-stamp)
            value['quota_refreshes']=[{k:r.get(k) for k in ('at','finished_at','before_percent','after_percent','status')}
                                      for r in state.get('quota_refreshes',[])[-20:]]
            value['ignored_stale_runs']=[dict(id=safe_text(r['id']),reason=safe_text(r['reason']))
                                         for r in state.get('ignored_stale_runs',[])]
            value['notices']=[dict(title=safe_text(n['title']),at=n['at']) for n in state.get('notices',[])[-20:]]
            value['errors'] += [safe_text(g['title'])+': '+safe_detail(g['reason']) for g in state.get('service_gaps',[])[-20:]]
            value['errors'] += [safe_text(g['title'])+': '+safe_detail(g['reason']) for g in state.get('origin_errors',{}).values()]
            for event in [*state.get('origin_reopens',[]),*state.get('origin_interruptions',[]),*state.get('origin_registration_retries',[])][-20:]:
                # Operator evidence paths and identities stay in the local journal.
                value['events'].append(dict(event=event['event'],memory_id=event['memory_id'],
                    reason=safe_detail(event['reason']),time=event['at'],status=event['status']))
                if event['status'] in ('archiving','retrying'):
                    value['errors'].append(safe_text(event['memory_id'])+': Origin recovery is incomplete; rerun the operator recovery command.')
            for error in [*state.get('check_errors',[]),*state.get('note_errors',{}).values()]:
                message=(str(error['memory_id'])+': ' if error.get('memory_id') else '')+safe_detail(error['reason'])
                if message not in value['errors']:value['errors'].append(message)
            if state.get('disk_gate'):value['disk_gate']={k:state['disk_gate'].get(k) for k in ('free_bytes','required_bytes','estimated_bytes','reserve_bytes','stage','paused')}
            for mid,control in state.get('positive_controls',{}).items():
                worker=control.get('worker',{});decision=control.get('verdict') or {}
                row=dict(control_id=mid,title=safe_text(control.get('title')),label='Test · not counted in results',
                    status=safe_text(worker.get('status'),'Queued'),decision=decision.get('decision'),
                    failed_gates=[safe_text(g) for g in decision.get('failed_gates',[])],gates=decision.get('gates',{}),report=decision.get('report',{}),
                    pairs=[dict(seed=p['seed'],pair_id=p['pair_id'],control=p['control'].get('signature_present'),
                        treatment=p['treatment'].get('signature_present'),control_import_id=p['control_import_id'],
                        treatment_import_id=p['treatment_import_id']) for p in control.get('pairs',[])],
                    cross_seed=control.get('cross_seed',{}),errors=[])
                if control.get('error'):row['errors'].append(safe_detail(control['error']))
                row['errors'] += [safe_detail(e['reason']) for field in ('origin_errors','origin_failures') for e in worker.get(field,{}).values()]
                if 'paused:' in worker.get('status',''):row['errors'].append(safe_detail(worker['status']))
                row['errors'] += [safe_detail(e['reason']) for e in worker.get('skipped_seeds',[])]
                row['discarded_pairs']=[dict(pair=e['pair'],arm=e['arm'],reason=validation_text(e['reason'])) for e in (worker.get('current') or {}).get('discarded_pairs',[])]
                value['positive_controls'].append(row)
            current=state.get('current')
            if current:
                value['current']=dict(title=safe_text(current['title']),paused=bool(current.get('pause_requested')))
                if current.get('dev_mode')=='agent' or current.get('channel')=='dev_agent':
                    value['current'].update(memory_id=current.get('note_id',current.get('memory_id')),
                        dev_model=(current.get('dev_model') or {}).get('model'),
                        elapsed_seconds=max(0,time.time()-current['arm_started_at']) if current.get('arm_started_at') else 0,
                        disk_used_bytes=current.get('disk_used_bytes',0),arm_disk_bytes=current.get('arm_disk_bytes',0),
                        last_verdict=safe_text(current.get('last_verdict'),'Pending'),
                        paused=bool(current.get('pause_requested') or current.get('gate_pause_reason') or (state.get('disk_gate') or {}).get('paused') or 'paused' in state.get('status','').lower()))
                if current.get('discarded_pairs'):
                    value['current']['discarded_pairs']=[dict(pair=r['pair'],version=r['version'],arm=safe_text(r['arm']),reason=validation_text(r['reason'])) for r in current['discarded_pairs']]
                if current.get('replacement_notice'):value['current']['replacement_notice']=validation_text(current['replacement_notice'])
                if current.get('kind')=='begin':
                    value['current'].update(stage='begin',paused=True)
                    if current.get('begin_error'):value['errors'].append(safe_detail(current['begin_error']))
                elif current.get('kind')=='origin':value['current']['stage']='origin'
                elif current.get('evaluate_cycle') or current.get('dev_mode')=='agent' and current.get('pair_index')==5 and not current.get('active'):
                    value['current'].update(stage='evaluate',paused=bool(current.get('evaluate_error') or current.get('dev_mode')=='agent' and value['current']['paused']))
                    if current.get('evaluate_error'):value['errors'].append(safe_detail(current['evaluate_error']))
                else:value['current'].update(pair=current['pair_index']+1,arm=current['arm'])
                if current.get('kind')=='cross_seed':value['current'].update(seed=current['target_seed'],stage=current['stage'],pairs_needed=len(current.get('plan',{}).get('pairs',[])))
            cross_notes=state.get('cross_notes',{})
            value['skipped_seeds']=[dict(seed=r['seed'],reason=safe_detail(r['reason'])) for r in state.get('skipped_seeds',[])][-100:]
            for row in state.get('results',[])[-20:]:
                report=row.get('decision',{}).get('report',{})
                result=dict(title=safe_text(row['title']),status=row['status'],note_remains=row.get('note_remains'),reason=safe_text(row.get('decision',{}).get('reason')),pairs=report.get('paired_runs'),control=report.get('control_signature_count'),treatment=report.get('treatment_signature_count'))
                if row.get('discarded_pairs'):
                    result['discarded_pairs']=[dict(pair=r['pair'],version=r['version'],arm=safe_text(r['arm']),reason=validation_text(r['reason'])) for r in row['discarded_pairs']]
                value['results'].append(result)
        except Exception as exc:value['errors'].append('Automatic checks state unavailable: '+safe_detail(str(exc)))
    if root:
        known={r['control_id'] for r in value['positive_controls']}
        for path in sorted((Path(root)/'positive-control-queue').glob('*.json')):
            try:
                spec=json.loads(path.read_bytes())['spec']
                if spec['control_id'] not in known:
                    value['positive_controls'].append(dict(control_id=spec['control_id'],title=safe_text(spec['title']),
                        label='Test · not counted in results',status='Queued',pairs=[],failed_gates=[],errors=[]))
            except Exception as exc:value['errors'].append('Positive-control queue unavailable: '+safe_detail(str(exc)))
    value['reuse']=dict(notes=[],errors=[])
    if callable(getattr(gateway,'reuse_notes_list',None)):
        try:value['reuse']=gateway.reuse_notes_list()
        except Exception as exc:value['errors'].append('Reuse notes unavailable: '+str(exc))
    reuse = value['reuse']
    if reuse.get('notes'):
        # The reuse detail predates lifecycle/provider fields. Join the existing
        # public catalogue, without inferring trust from source verification.
        if callable(getattr(gateway, 'list_dev_v2_memories', None)):
            try:
                catalogue = {r['memory_id']: r for r in gateway.list_dev_v2_memories()}
                for pair in reuse['notes']:
                    for key in ('original', 'generalized'):
                        note = pair.get(key)
                        if note:
                            metadata = catalogue.get(note['memory_id'], {})
                            note.update({k: metadata[k] for k in ('status', 'provider', 'expires_at') if k in metadata})
            except Exception as exc:reuse.setdefault('errors', []).append(dict(error='Note status unavailable: '+safe_detail(str(exc))))
        ids = [n['memory_id'] for pair in reuse['notes'] for n in (pair.get('original'), pair.get('generalized')) if n]
        notes = [n for pair in reuse['notes'] for n in (pair.get('original'), pair.get('generalized')) if n]
        history = note_history(experiments_root, ids, notes=notes)
        join_reuse_history(reuse, history)
    value['family']=dict(fixtures=[],candidates=[],events=[])
    if gateway is not None:
        if callable(getattr(gateway,'family_list',None)):
            try:value['family']=gateway.family_list()
            except Exception as exc:value['errors'].append('Family feed unavailable: '+safe_detail(str(exc)))
        else:value['errors'].append('Family feed unavailable: memory gateway has no family_list')
        cross_available=True
        for method in ('cross_seed_queue','cross_seed_status'):
            if not callable(getattr(gateway,method,None)):
                value['errors'].append('Cross-seed feed unavailable: memory gateway has no '+method)
                cross_available=False
        try:
            value['queue']=[dict({k:r.get(k) for k in ('memory_id','title','uses','failure_gone')},
                                 **({'status':r['status'],'reason':safe_detail(r.get('reason'))}
                                    if r.get('status') in {'isolated','error'} else {}))
                            for r in gateway.validation_queue()]
            for row in value['queue']:
                if row.get('status') in {'isolated','error'}:
                    value['errors'].append('Isolated note '+safe_text(row['memory_id'])+': '+safe_detail(row.get('reason')))
            scopes={};events=[]
            for r in gateway.memory_events(after=0):
                event={k:r.get(k) for k in ('seq','memory_id','title','event','reason','time')};mid=r['memory_id']
                if r['event']=='promoted':scopes[mid]='task_scoped' if (r.get('reason') or '').startswith('Trusted for this task:') else 'scene_scoped'
                elif r['event']=='demoted':scopes[mid]='scene_scoped' if scopes.get(mid)=='task_scoped' else 'provisional'
                event['trust_scope']=scopes.get(mid);events.append(event)
            value['events']=(events+value['events'])[-100:]
            if cross_available:
                for row in gateway.cross_seed_queue():cross_notes[row['memory_id']]=row
                for mid,row in list(cross_notes.items()):
                    if row.get('status') in {'isolated','error'}:
                        value['errors'].append('Isolated cross-seed note '+safe_text(mid)+': '+safe_detail(row.get('reason')))
                        continue
                    cross_notes[mid]=dict(title=row.get('title'),**gateway.cross_seed_status(memory_id=mid))
        except Exception as exc:value['errors'].append('Automatic checks service feed unavailable: '+safe_detail(str(exc)))
    for mid,row in cross_notes.items():
        trust=row.get('task_trust',{})
        value['cross_seed_notes'].append(dict(memory_id=mid,title=safe_text(row.get('title'),'Untitled note'),
            task_trust=dict(trust_scope=trust.get('trust_scope'),scenes=trust.get('scenes',0),missing=[safe_text(s) for s in trust.get('missing',[])]),
            seeds=[{k:s.get(k) for k in ('target_seed','role','pairs_done','pairs_needed','verdict','reason')} for s in row.get('seeds',[])]))
    return value
EXTRA_ERRORS = (PermissionError, OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError)


class CatalogueUnavailable(RuntimeError):
    """The online catalogue could not be read."""


def _log_failure(error, context):
    # Exception messages may contain service responses or note text.
    print(f'Memory monitor {context}: {type(error).__name__}: details unavailable', file=sys.stderr)


def short(value, length):
    return str(value)[:length] if value is not None else None


# Bounded file/ledger caches are invalidated by file metadata, including WAL
# changes. Never open a writer or instantiate a store on historical runs.
_HISTORY_FILES = {}
_HISTORY_LEDGERS = {}
HISTORY_BYTES = 8 * 1024 * 1024
HISTORY_CELLS = 32
HISTORY_VERSIONS = 64
HISTORY_ROWS = 5000


def _history_stamp(path):
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size
    except FileNotFoundError:
        return None


def _history_json(path):
    stamp = _history_stamp(path)
    if stamp is None:raise FileNotFoundError(path.name+' is missing')
    if stamp[2] > HISTORY_BYTES:raise ValueError(path.name+' exceeds history size limit')
    key = str(path)
    cached = _HISTORY_FILES.get(key)
    if cached and cached[0] == stamp:return cached[1]
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):raise ValueError(path.name+' must contain an object')
    if len(_HISTORY_FILES) >= 4096:_HISTORY_FILES.clear()
    _HISTORY_FILES[key] = (stamp, value)
    return value


def _history_ledger(path):
    stamp = (_history_stamp(path), _history_stamp(Path(str(path)+'-wal')))
    if stamp[0] is None:raise FileNotFoundError('ledger.sqlite3 is missing')
    cached = _HISTORY_LEDGERS.get(str(path))
    if cached and cached[0] == stamp:return cached[1]
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True) as db:
        cells = [json.loads(r[0]) for r in db.execute('SELECT payload FROM v2_cells ORDER BY id LIMIT ?', (HISTORY_CELLS+1,))]
        if len(cells) > HISTORY_CELLS:raise ValueError('history cell limit exceeded')
        result = []
        for cell in cells:
            if not isinstance(cell, dict):raise ValueError('invalid history cell')
            if cell.get('memory_family') is not True and cell.get('run_notes') is not True:continue
            versions = [json.loads(r[0]) for r in db.execute('SELECT payload FROM v2_versions WHERE cell=? ORDER BY version LIMIT ?', (cell['key'], HISTORY_VERSIONS+1))]
            jobs = [dict(json.loads(r[0]), state=r[1]) for r in db.execute('SELECT payload,state FROM v2_jobs WHERE cell=? ORDER BY version,kind LIMIT ?', (cell['key'], HISTORY_VERSIONS*4+1))]
            if len(versions) > HISTORY_VERSIONS or len(jobs) > HISTORY_VERSIONS*4:
                raise ValueError('history ledger version/job limit exceeded')
            result.append((cell, versions, jobs))
    if len(_HISTORY_LEDGERS) >= EXPERIMENT_LIMIT:_HISTORY_LEDGERS.clear()
    _HISTORY_LEDGERS[str(path)] = (stamp, result)
    return result


def _history_reason(note):
    evidence = (note.get('considered_not_used_evidence', []) if note.get('considered_not_used') else note.get('evidence', []))
    # Explicit not-used evidence wins over generic and affirmative citations.
    reasons, excerpts = [], []
    pattern = re.compile(r'(?P<no>Not )?using note\s+`?'+re.escape(note['memory_id'])+r'`?\s*:\s*(.*)', re.I)
    for pointer in evidence:
        excerpt = pointer['excerpt']
        match = pattern.search(excerpt)
        target = reasons if match else excerpts
        text = ('Not using: ' if match['no'] else 'Using: ')+match[2] if match else excerpt
        if text not in target:target.append(text)
    return short(' · '.join(reasons or excerpts), 200) or ''


def join_reuse_history(reuse, history):
    """Attach exact linked IDs; one source row per recorded run/cell/version."""
    reuse.setdefault('errors', []).extend(history['errors'])
    for pair in reuse['notes']:
        ids = {n['memory_id'] for n in (pair.get('original'), pair.get('generalized')) if n}
        pair['history'], sources = [], set()
        for row in history['rows']:
            if row['memory_id'] not in ids:continue
            if row['use'] == 'source':
                key = (row['experiment_id'], row['cell'], row['version'])
                if key in sources:continue
                sources.add(key)
            pair['history'].append(row)
        pair['history'].sort(key=lambda r:r['use'] != 'source')
        for note in (pair.get('original'), pair.get('generalized')):
            if note and not note.get('provider'):
                origin = note.get('origin_summary') or note.get('origin') or {}
                note['provider'] = origin.get('provider') or next((r['provider'] for r in history['rows']
                    if r['memory_id'] == note['memory_id'] and r.get('provider')), None)
    return reuse


def _history_results(cell, version, jobs):
    """Separate native success from execution completion and Eval verdict."""
    agent = version.get('agent') or {}
    native = agent.get('native_success')
    if native is not None and type(native) is not bool:raise ValueError('invalid persisted native result')
    review = agent.get('eval') or {}
    if not isinstance(review, dict):raise ValueError('invalid persisted Eval result')
    if review.get('status') == 'completed' and type(review.get('passed')) is not bool:raise ValueError('invalid completed Eval result')
    evaluated = review.get('passed') if review.get('status') == 'completed' else None
    for job in jobs:
        if job.get('version') != version.get('version') or job.get('state') != 'settled':continue
        receipt = job.get('receipt') or {}
        if job.get('kind') == 'execute' and receipt.get('bundle'):
            ref = receipt['bundle'];path = Path(ref['uri'])
            bundle = _history_json(path)
            if sha(path) != ref.get('sha256'):raise ValueError('native bundle digest mismatch')
            expected = f"attempt:{cell['key']}:{version['version'] - 1}"
            evaluator = bundle.get('native_evaluator') or {}
            if not (bundle.get('run_id') == 'run:'+cell['key'] and bundle.get('attempt_id') == expected
                    and bundle.get('dev_version') == version['version']
                    and bundle.get('config_sha256') == version.get('config_sha256')
                    and bundle.get('policy_sha256') == (version.get('source') or {}).get('sha256')
                    and bundle.get('formal_eligible') is False and evaluator.get('run_id') == bundle['run_id']
                    and evaluator.get('attempt_id') == expected and evaluator.get('evaluated') is True):
                raise ValueError('native bundle identity/evaluation unavailable')
            native = bundle.get('native_success')
            if type(native) is not bool:raise ValueError('invalid native bundle success')
        if job.get('kind') == 'eval':
            ref = receipt.get('eval_review') or receipt.get('review')
            if ref:
                path = Path(ref['uri'])
                review = _history_json(path)
                if sha(path) != ref.get('sha256'):raise ValueError('Eval review digest mismatch')
                if review.get('status') == 'completed' and type(review.get('passed')) is not bool:raise ValueError('invalid completed Eval review')
                evaluated = review.get('passed') if review.get('status') == 'completed' else None
    label = lambda value: 'passed' if value is True else 'failed' if value is False else 'pending'
    return dict(native=label(native), eval=label(evaluated))


def _history_detail(message):
    from .notices import safe_detail
    # Preserve the audit filename after the general notice scrubber humanizes
    # underscores. It identifies which input needs attention.
    return safe_detail(str(message)).replace('memory use.json', 'memory_use.json') or 'History data unavailable'


def _history_turn_use(directory, number, numbers):
    """Use export's exact authored-event parser and citation detector, read-only.

    A paused version ends at the current cursor, or the next delivery cursor if
    that exists. Never include a later version's events in an earlier decision.
    """
    from .memory_use import analyse, _events, event_cursor, validate_report
    private = directory/'agent/private'
    saved = private/'memory'/f'v{number}'
    delivered = _history_json(saved/'memory-delivered.json')
    notes = delivered['notes']
    if not isinstance(notes, list) or len(notes) > LIMIT:raise ValueError('invalid delivered note limit')
    if delivered.get('errors'):raise ValueError('Memory delivery has errors')
    start = _history_json(saved/'memory-use-start.json')
    end_path = saved/'memory-use-end.json'
    if not end_path.exists():
        later = [n for n in numbers if n > number]
        end_path = private/'memory'/f'v{min(later)}'/'memory-use-start.json' if later else None
    end = _history_json(end_path) if end_path else event_cursor(private)
    if not end:raise FileNotFoundError('Dev turn log is missing')
    if len(end) > HISTORY_VERSIONS:raise ValueError('turn log stream limit exceeded')
    total = 0
    for name in end:
        # _events validates cursor paths and offsets again before parsing.
        if not re.fullmatch(r'turns/t\d+/events.jsonl', name):raise ValueError('invalid event cursor path')
        stamp = _history_stamp(private/name)
        if stamp is None:raise FileNotFoundError('Dev turn log is missing')
        total += stamp[2]
    if total > HISTORY_BYTES:raise ValueError('turn log history size limit exceeded')
    return validate_report(analyse(notes, _events(private, start, end)))


def _history_source(root, note):
    """Resolve only recorded source identities; no r-/p- alias inference."""
    origin = note.get('origin_summary') or note.get('origin') or {}
    rid = note.get('source_run') or origin.get('experiment_id')
    number = note.get('source_version') or origin.get('help_version')
    provider = note.get('provider') or origin.get('provider')
    if rid is None or number is None:return None, provider
    if not isinstance(rid, str) or not EXPERIMENT.fullmatch(rid) or type(number) is not int or not 1 <= number <= HISTORY_VERSIONS:
        raise ValueError('invalid note source identity')
    root = Path(root)
    run = (root.parent if EXPERIMENT.fullmatch(root.name) else root)/rid
    cells = _history_ledger(run/'ledger.sqlite3')
    key = note.get('source_cell') or origin.get('cell')
    candidates = [c for c in cells if not key or c[0]['key'] == key]
    if len(candidates) != 1:raise ValueError('note source cell is unavailable or ambiguous')
    cell, versions, jobs = candidates[0]
    directory = Path(cell.get('artifact_dir') or run/'artifacts'/rid/'cells'/cell['key'])
    if not directory.resolve().is_relative_to(run.resolve()):raise ValueError('source artifacts are outside their run')
    source = directory/f'v{number}'
    if not provider:
        for name in ('memory-run-note.json', 'help.json'):
            if not (source/name).exists():continue
            evidence = _history_json(source/name)
            if name == 'memory-run-note.json' and (evidence.get('experiment_id'), evidence.get('cell'), evidence.get('help_version')) != (rid, cell['key'], number):
                raise ValueError('source run note identity mismatch')
            provider = evidence.get('provider')
            if provider:break
    version = next((v for v in versions if v.get('version') == number), dict(version=number))
    row = dict(experiment_id=rid, cell=cell['key'], version=number, memory_id=note.get('memory_id', note.get('id')),
        task_id=cell.get('task_id'), seed=cell.get('seed'), level=note.get('level'), use='source', reason='',
        provider=provider, errors=[], **_history_results(cell, version, jobs))
    return row, provider


def note_history(root, memory_ids, *, notes=()):
    """Read exact note IDs across opted-in runs; no alias or outcome inference.

    Public vN files win over private delivery snapshots. The latter keep paused,
    pre-export deliveries visible. Original/generalized IDs are joined by the
    caller using catalogue links, never by stripping a suffix or a prefix.
    """
    from .memory_use import validate_report
    value = dict(rows=[], errors=[])
    ids = set(memory_ids)
    metadata = {n.get('memory_id', n.get('id')): dict(n) for n in notes if n.get('memory_id', n.get('id')) in ids}
    if not ids:return value
    def error(message, **context):
        return dict(**context, error=_history_detail(message))
    if root is None:
        value['errors'].append(error('Note history unavailable: daily experiment root is not configured'))
        return value
    root = Path(root)
    try:
        if not root.is_dir():raise FileNotFoundError('history root is unavailable')
        runs = [root] if EXPERIMENT.fullmatch(root.name) else sorted(
            (p for p in root.glob('devexp-*') if EXPERIMENT.fullmatch(p.name)),
            key=lambda p: p.stat().st_mtime_ns, reverse=True)
        if len(runs) > EXPERIMENT_LIMIT:
            value['errors'].append(error(f'Note history limited to {EXPERIMENT_LIMIT} newest runs'))
        for run in reversed(runs[:EXPERIMENT_LIMIT]):
            context = dict(experiment_id=run.name)
            try:cells = _history_ledger(run/'ledger.sqlite3')
            except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error) as exc:
                value['errors'].append(error(exc, **context));continue
            for cell, versions, jobs in cells:
                context = dict(experiment_id=run.name, cell=cell['key'])
                try:
                    directory = Path(cell.get('artifact_dir') or run/'artifacts'/run.name/'cells'/cell['key'])
                    if not directory.resolve().is_relative_to(run.resolve()):raise ValueError('history artifacts are outside their run')
                    public = {int(p.name[1:]): p for p in directory.glob('v[0-9]*') if re.fullmatch(r'v[1-9][0-9]*', p.name)}
                    private = {int(p.name[1:]): p for p in (directory/'agent/private/memory').glob('v[0-9]*') if re.fullmatch(r'v[1-9][0-9]*', p.name)}
                    numbers = sorted(public.keys() | private.keys() | {v['version'] for v in versions})
                    if len(numbers) > HISTORY_VERSIONS:
                        value['errors'].append(error('history version limit exceeded', **context))
                    for number in numbers[:HISTORY_VERSIONS]:
                        if len(value['errors']) >= HISTORY_ROWS:
                            value['errors'] = value['errors'][:HISTORY_ROWS] + [error('Note history error limit reached')]
                            return value
                        errors = []
                        row_context = dict(context, version=number)
                        entries = {}
                        for name in ('memory-input.json', 'memory-run-notes-input.json'):
                            candidates = [p/name for p in (public.get(number), private.get(number)) if p]
                            path = next((p for p in candidates if p.exists()), None)
                            if path is None:
                                if name == 'memory-input.json':errors.append('memory-input.json is missing')
                                continue
                            try:
                                packet = _history_json(path)
                                rows = packet.get('entries', packet.get('notes'))
                                if not isinstance(rows, list) or len(rows) > LIMIT:raise ValueError('invalid delivery entries or delivery limit exceeded')
                                for entry in rows:
                                    if not isinstance(entry, dict):raise ValueError('invalid delivery note')
                                    # Local run packets use note_id; service packets use memory_id.
                                    mid = entry.get('note_id') if name == 'memory-run-notes-input.json' else entry.get('memory_id')
                                    if not isinstance(mid, str):raise ValueError('invalid delivery note ID')
                                    entries[mid] = entry
                            except (OSError, ValueError, TypeError) as exc:errors.append(name+': '+str(exc))
                        audit_notes = {}
                        audit_path = public.get(number, directory/f'v{number}')/'memory_use.json'
                        audit_present = audit_path.exists()
                        if audit_present:
                            try:
                                audit = validate_report(_history_json(audit_path))
                                if len(audit['notes']) > LIMIT:raise ValueError('audit note limit exceeded')
                                audit_notes = {n['memory_id']: n for n in audit['notes']}
                                errors.extend(e['error'] for e in audit.get('errors', []))
                            except (OSError, ValueError, TypeError, KeyError) as exc:errors.append('memory_use.json: '+str(exc))
                        elif ids & entries.keys():
                            try:
                                audit = _history_turn_use(directory, number, numbers)
                                audit_notes = {n['memory_id']: n for n in audit['notes']}
                            except (OSError, ValueError, TypeError, KeyError) as exc:
                                errors.append('memory_use.json is missing; citation audit pending: '+str(exc))
                        matched = ids & (entries.keys() | audit_notes.keys())
                        if not matched:
                            # A corrupt packet has no recoverable IDs. Surface it
                            # globally, but do not hash unrelated native bundles.
                            value['errors'].extend(error(e, **row_context) for e in errors
                                if not e.startswith('memory_use.json is missing'))
                            continue
                        version = next((v for v in versions if v.get('version') == number), dict(version=number))
                        try:outcomes = _history_results(cell, version, jobs)
                        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                            outcomes = dict(native='unavailable', eval='unavailable');errors.append('Version result: '+str(exc))
                        for mid in sorted(matched):
                            entry = entries.get(mid, {})
                            metadata.setdefault(mid, {}).update({k: v for k, v in entry.items()
                                if k not in metadata.get(mid, {})})
                            audit = audit_notes.get(mid)
                            row_errors = list(errors)
                            if audit_present and audit is None:row_errors.append('Delivered note is absent from memory_use.json')
                            used = ('considered_not_used' if audit.get('considered_not_used') else
                                    'used' if audit['referenced'] == 'explicit' else 'not_mentioned') if audit else 'pending'
                            if not audit_present and audit is None and not errors:
                                row_errors.append('Dev turn log has no delivered note; citation audit pending')
                            level = entry.get('level') or (audit or {}).get('level')
                            if level is None:
                                level = {'same_scene':'same_task','same_task':'same_task','same_skill':'same_skill','general':'general',
                                         'scene':'same_task','task':'same_task','skill':'same_skill','run':'same_task','run_note':'same_task'}.get(entry.get('match_level') or entry.get('layer'))
                            if level is None:row_errors.append('Delivery level is unavailable')
                            value['rows'].append(dict(**row_context, memory_id=mid, task_id=cell.get('task_id'), seed=cell.get('seed'),
                                level=level, use=used, considered_not_used=bool((audit or {}).get('considered_not_used')),
                                decision_source='audit' if audit_present else 'turn_log' if audit else None,
                                reason=_history_reason(audit) if audit else '', **outcomes,
                                errors=[_history_detail(e) for e in row_errors]))
                        # Unattributable corrupt files still surface, rather than
                        # disappearing because their IDs could not be parsed.
                        value['errors'].extend(error(e, **row_context) for e in errors)
                        if len(value['rows']) >= HISTORY_ROWS:
                            value['rows'] = value['rows'][:HISTORY_ROWS]
                            value['errors'].append(error('Note history row limit reached'))
                            return value
                except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:value['errors'].append(error(exc, **context))
    except (OSError, ValueError) as exc:value['errors'].append(error(exc))
    sources = []
    for mid, note in metadata.items():
        try:
            source, provider = _history_source(root, dict(note, memory_id=mid))
            if source and (not EXPERIMENT.fullmatch(root.name) or source['experiment_id'] == root.name):sources.append(source)
            if provider:
                for row in value['rows']:
                    if row['memory_id'] == mid:row['provider'] = provider
        except (OSError, ValueError, KeyError, TypeError, AttributeError, sqlite3.Error) as exc:
            value['errors'].append(error('Note source: '+str(exc), memory_id=mid))
    value['rows'] = sorted(sources, key=lambda r:(r['experiment_id'], r['version'], r['memory_id'])) + value['rows']
    return value


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
        entries = detail.get('versions') or []
        ref = detail.get('packet') or {}
        path = Path(ref.get('uri', '/missing'))
        if path.is_file() and sha(path) == ref.get('sha256'):
            entries = json.loads(path.read_bytes()).get('entries', entries)
        for entry in entries:
            if isinstance(entry, dict) and entry.get('memory_id'):
                levels[entry['memory_id']] = layer_of(entry)
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


def _here_layer(row, cells):
    task = (row.get('applicability_summary') or {}).get('task_id')
    if row.get('level') == 'general':return 'general'
    if row.get('level') == 'same_skill':
        from attention_memory_service.skill_tags import tags_for
        return 'skill' if any(cell.get('task_id') != task and set(row.get('skill_tags',())) & set(tags_for(cell.get('task_id'))) for cell in cells) else None
    same = [cell for cell in cells if task is not None and cell.get('task_id') == task]
    if same:
        return 'scene' if any(cell.get('seed') == _note_seed(row) for cell in same) else 'task'
    try:
        from attention_memory_service.families import family_of
        family = family_of(task)
        if family is not None and any(family_of(cell.get('task_id')) == family for cell in cells):
            return 'family'
    except ImportError:
        pass
    return None


def _learned(origin):
    return f"Learned in {str(origin['experiment_id'])[:15]}… v{origin['help_version']}"


def _path(row, layer, catalogue):
    seed = _note_seed(row)
    origin = row.get('origin_summary')
    if origin:
        path = f"{_learned(origin)} → provisional on scene {seed}"
    elif row.get('seeded_for_tests'):
        path = f"Test note → scene {seed}"
    else:
        path = f"Validated on scene {seed}"
        prior = catalogue.get(row.get('from_provisional')) or {}
        if prior.get('origin_summary'):
            path = f"{_learned(prior['origin_summary'])} → provisional on scene {_note_seed(prior)} → " + path
    if layer in ('task', 'skill', 'general', 'family'):
        path += f" → read here as {layer} reference"
    return path


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
    execution={}
    if experiment.get('manifest'):
        try:
            from .execution_profile import manifest_metadata
            ref=experiment['manifest']
            if sha(ref['uri'])!=ref['sha256']:raise ValueError('Run manifest digest mismatch')
            execution=manifest_metadata(json.loads(Path(ref['uri']).read_bytes()))
        except EXTRA_ERRORS as error:
            _log_failure(error,'execution settings')
            warnings.append('Run execution settings are unavailable.')
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
    catalogue_by_id = {row.get('memory_id'): row for row in catalogue}
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
        notes[-1].update({k:row[k] for k in ('level','skill_tags','applies_when','source_run','source_version','source_verified','provider','generalized_from','generalized_copy') if k in row})
        notes[-1]['note_status'] = row.get('status')
        origin = row.get('origin_summary') or row.get('origin') or {}
        if not notes[-1].get('provider') and origin.get('provider'):notes[-1]['provider'] = origin['provider']
        if origin.get('cell'):notes[-1]['source_cell'] = origin['cell']
        for key, source in (('source_run', 'experiment_id'), ('source_version', 'help_version')):
            if key not in notes[-1] and source in origin:notes[-1][key] = origin[source]
        profile=row.get('execution_profile',(row.get('applicability_summary') or {}).get('execution_profile'))
        if profile:notes[-1]['execution_profile']=profile
        try:
            drift=(row.get('validation_summary') or {}).get('source_drift')
            if drift:notes[-1]['source_drift']=drift
        except EXTRA_ERRORS as error:
            _log_failure(error,'source drift details')
            if not warnings:warnings.append('Some note details are unavailable.')
        if row.get('motion_permission') == dict(action='dev_tier_sim_motion',actor='dev_tier_sim_motion'):
            notes[-1]['motion_permission']=row['motion_permission']
        digest=row.get('planner_dev_local_sha256',(row.get('applicability_summary') or {}).get('planner_dev_local_sha256'))
        if digest:notes[-1]['planner_dev_local_sha256']=digest
        behavior=row.get('planner_behavior_sha256',(row.get('applicability_summary') or {}).get('planner_behavior_sha256'))
        if behavior:notes[-1]['planner_behavior_sha256']=behavior
        seed = _note_seed(row)
        layer = levels.get(row.get('memory_id'), _here_layer(row, cells))
        notes[-1].update(layer=layer, path=_path(row, layer, catalogue_by_id))
        notes[-1].update(unvalidated=row.get('status') == 'provisional' or row.get('unvalidated') is True,
                         uses=_uses(row), expires_in_days=_expires_in_days(row, timestamp), scene_seed=seed,
                         # Another scene of the same task: readable only as a labelled reference.
                         reference=layer in ('task', 'skill', 'general', 'family'))
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
            run_notes.append(dict(id=note_id, cell=cell['key'], layer='run',
                                  source_run=experiment_id, source_version=event['help_version'],
                                  level='same_task', note_status='disabled' if note_id in off else 'provisional',
                                  path=f"{_learned(dict(experiment_id=experiment_id, help_version=event['help_version']))} → this run only", title=short(event['title'], 80),
                                  help_version=event['help_version'], from_version=event['help_version'] + 1,
                                  uses=len(uses), failure_gone=len(gone), off=note_id in off))
    current_history = note_history(Path(store.path).parent, [n['id'] for n in notes + run_notes], notes=notes + run_notes)
    for note in notes + run_notes:
        rows = [r for r in current_history['rows'] if r['memory_id'] == note['id']]
        note['dev_use'] = [r for r in rows if r['use'] != 'source']
        if not note.get('provider'):note['provider'] = next((r['provider'] for r in rows if r.get('provider')), None)
    from .dev_memory_view import scope_projection
    scopes = [scope_projection(store, cell) for cell in cells]
    # A multi-cell run must still explain any cell restricted to references.
    scope = next((s for s in scopes if s and s['level'] == 'reference_only'), next((s for s in scopes if s), None))
    return {'generated_at': timestamp,
            'run': {**execution,'experiment_id': experiment_id, 'state': short(experiment.get('state'), 40),
                    'max_versions': max_versions, 'versions': versions, 'unvalidated_used': unvalidated_used,
                    'memory_scope': scope, 'can_operate_run_notes': local_enabled,
                    'family_reference': any(cell.get('memory_family') is True for cell in cells),
                    'layer_counts': {layer: sum(n.get('layer') == layer for n in notes + run_notes) for layer in LAYERS}},
            'notes': notes, 'run_notes': run_notes, 'events': events[-EVENT_LIMIT:], 'warnings': warnings,
            'history_errors': current_history['errors']}
