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


def automatic_checks(root=None,gateway=None):
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
            value['queue']=[{k:r.get(k) for k in ('memory_id','title','uses','failure_gone')} for r in gateway.validation_queue()]
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
        notes[-1].update({k:row[k] for k in ('level','skill_tags','applies_when','source_run','source_version','source_verified','generalized_from','generalized_copy') if k in row})
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
                                  path=f"{_learned(dict(experiment_id=experiment_id, help_version=event['help_version']))} → this run only", title=short(event['title'], 80),
                                  help_version=event['help_version'], from_version=event['help_version'] + 1,
                                  uses=len(uses), failure_gone=len(gone), off=note_id in off))
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
            'notes': notes, 'run_notes': run_notes, 'events': events[-EVENT_LIMIT:], 'warnings': warnings}
