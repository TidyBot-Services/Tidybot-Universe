"""Effective hints retained locally for later Dev versions of the same cell.

This module never creates Memory grants or import queues. Effectiveness and
observations use the same public-evidence rules as provisional proposals.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .dev_help import public_screen
from .dev_loop import immutable, sha
from .memory_provisional import (PROVIDERS, NotStaged, _ref, derive_signature,
                                 effective_reason, progress, signature_present)

from .memory_layers import RUN_NOTE_CAP, order
from .memory_titles import note_title, unique_title

SCHEMA = 'attentionbench.dev-v2-run-note.v1'
INPUT_SCHEMA = 'attentionbench.dev-v2-run-notes-input.v1'
PREFIX = 'planner_memory_run_note_'


def events(store, key):
    with sqlite3.connect('file:' + str(Path(store.path).resolve()) + '?mode=ro', uri=True) as db:
        return [(kind, json.loads(raw)) for kind, raw in db.execute(
            "SELECT kind, payload FROM v2_events WHERE json_extract(payload, '$.cell')=? ORDER BY seq", (key,))]


def _once(store, kind, value, fields=('cell', 'version', 'note_id')):
    with store.tx() as db:
        clause = ' AND '.join("json_extract(payload, '$.%s') IS ?" % field for field in fields)
        values = [json.dumps(value[field], separators=(',', ':')) if isinstance(value.get(field), (list, dict))
                  else value.get(field) for field in fields]
        if not db.execute('SELECT 1 FROM v2_events WHERE kind=? AND ' + clause,
                          (kind, *values)).fetchone():
            store.event(db, kind, value)


def _skip(store, key, version, reason, **extra):
    try:
        _once(store, PREFIX + 'skipped', dict(cell=key, version=version, reason=reason[:200], **extra),
              ('cell', 'version', 'note_id', 'reason'))
    except Exception:
        pass  # Local observations must never interrupt execution.


title = note_title


def create(store, key, version, directory):
    """Safely create one immutable note, or record why an answer did not qualify."""
    try:
        cell = store.cell(key)
        directory = Path(directory)
        help_path = directory / 'help.json'
        if cell.get('run_notes') is not True or not help_path.is_file():
            return None
        from .memory_fixture_guard import fixture_reason
        config_path=directory.parent / 'config.json'
        if config_path.is_file() and fixture_reason(json.loads(config_path.read_bytes())):
            raise NotStaged('test_fixture_receipt_excluded_from_memory')
        answer = json.loads(help_path.read_bytes())
        if version < 2:
            raise NotStaged('version_before_2')
        if answer.get('provider') not in PROVIDERS:
            raise NotStaged('provider_not_source')
        content = answer.get('content')
        if answer.get('rejected') or not isinstance(content, str) or not content.strip():
            raise NotStaged('help_rejected_or_empty')
        refs = dict(help=_ref(help_path),
                    origin_public_feedback=_ref(directory.parent / f'v{version - 1}' / 'public-feedback.json'),
                    help_public_feedback=_ref(directory / 'public-feedback.json'))
        origin = json.loads(Path(refs['origin_public_feedback']['uri']).read_bytes())
        helped = json.loads(Path(refs['help_public_feedback']['uri']).read_bytes())
        signature = derive_signature(origin)
        try:
            from attention_memory_service.dev_v2 import validate_signature
            validate_signature(signature, origin)
        except Exception as exc:
            raise NotStaged('signature_invalid') from exc
        reason = effective_reason(origin, helped, signature)
        taken = [e['title'] for kind, e in events(store, key)
                 if kind == PREFIX + 'created' and e['help_version'] < version]
        heading = unique_title(note_title(content, signature, task_id=cell['task_id']), taken, version)
        public_screen(heading)
        public_screen(content)
        target = directory / 'memory-run-note.json'
        saved = None
        if target.exists():
            try:
                saved = json.loads(target.read_bytes())
                if not isinstance(saved, dict) or 'policy_id' not in saved:
                    raise ValueError('invalid saved note')
                created = next((e for kind, e in events(store, key)
                                if kind == PREFIX + 'created' and e['help_version'] == version), None)
                if created and sha(target) != created['note']['sha256']:
                    raise ValueError('saved note changed')
            except (ValueError, KeyError) as exc:
                raise NotStaged('run_note_already_differs') from exc
        if saved is not None:
            heading = saved['title']  # Old immutable notes retain their original title.
        note = dict(schema_version=SCHEMA,
                    note_id='r-' + hashlib.sha256(f"{cell['experiment_id']}:{key}:{version}".encode()).hexdigest()[:24],
                    experiment_id=cell['experiment_id'], cell=key, help_version=version, origin_version=version - 1,
                    policy_id=saved['policy_id'] if saved else cell['policy_id'], provider=answer['provider'],
                    title=heading, content=content, failure_signature=signature, effective_reason=reason,
                    origin_progress=progress(origin), help_progress=progress(helped), refs=refs,
                    run_local=True, unvalidated=True, excluded_from_results=True)
        if saved is None or 'layer' in saved:
            note['layer'] = 'run'
        if saved is not None and saved != note:
            raise NotStaged('run_note_already_differs')
        ref = _ref(target) if saved is not None else immutable(target, note)
        _once(store, PREFIX + 'created', dict(cell=key, version=version, note_id=note['note_id'],
              title=heading, help_version=version, note=ref))
        return ref
    except Exception as exc:
        _skip(store, key, version, str(exc) if isinstance(exc, NotStaged) else 'error:' + type(exc).__name__)
        return None


def _read_note(event):
    ref = event['note']
    path = Path(ref['uri'])
    if not path.is_file() or sha(path) != ref['sha256']:
        raise ValueError('run note digest mismatch')
    note = json.loads(path.read_bytes())
    if any(note.get(field) != event.get(field) for field in ('cell', 'note_id', 'help_version')):
        raise ValueError('run note binding mismatch')
    return note


def delivered(directory):
    path = Path(directory) / 'memory-run-notes-input.json'
    return json.loads(path.read_bytes())['entries'] if path.is_file() else []


def deliver(store, key, version, directory, *, service_entries=()):
    cell = store.cell(key)
    if cell.get('run_notes') is not True:
        return []
    path = Path(directory) / 'memory-run-notes-input.json'
    if path.exists():
        packet = json.loads(path.read_bytes())
    else:
        history = events(store, key)
        off = {e['note_id'] for kind, e in history if kind == PREFIX + 'off'}
        active = {}
        for kind, event in history:
            if kind != PREFIX + 'created' or event['help_version'] >= version or event['note_id'] in off:
                continue
            try:
                active[event['note_id']] = _read_note(event)
            except (OSError, ValueError, KeyError):
                continue
        with sqlite3.connect('file:' + str(Path(store.path).resolve()) + '?mode=ro',uri=True) as db:
            created = {json.loads(raw)['note_id']:at for at,raw in db.execute(
                "SELECT at,payload FROM v2_events WHERE kind=? AND json_extract(payload,'$.cell')=?",(PREFIX+'created',key))}
        notes = sorted(active.values(), key=lambda n: (-n['help_version'], n['note_id']))
        entries = [dict({k: note[k] for k in ('note_id', 'help_version', 'title', 'content', 'failure_signature')},
                        created_at=created.get(note['note_id'],0), level='same_task',
                        source_run=note.get('experiment_id') or cell.get('experiment_id'),
                        source_version=note['help_version'],
                        layer='run', run_note=True, unvalidated=True, trusted_here=False, match_level='run_note',
                        excluded_from_results=True) for note in notes[:RUN_NOTE_CAP]]
        chosen = order(service_entries, entries)
        entries = [e for e in chosen if e.get('run_note')]
        packet = dict(schema=INPUT_SCHEMA, cell=key, version=version, cap=RUN_NOTE_CAP,
                      omitted=max(0, len(notes) - len(entries)), entries=entries)
        immutable(path, packet)
    entries = packet['entries']
    for entry in entries:
        if entry.get('source_run') is None:
            _once(store, PREFIX + 'source_run_missing', dict(cell=key, version=version,
                  note_id=entry['note_id'], reason='source_run_unavailable_in_delivery'))
    if entries:
        ids = [e['note_id'] for e in entries]
        _once(store, 'planner_memory_run_notes_delivered', dict(cell=key, version=version, packet=_ref(path),
              layer='run', layers=['run'] * len(ids), note_ids=ids, help_versions=[e['help_version'] for e in entries]))
        _once(store, 'planner_memory_unvalidated_used', dict(cell=key, version=version,
              memory_ids=ids, layers=['run'] * len(ids), match_levels=['run_note'] * len(ids), excluded_from_results=True),
              ('cell', 'version', 'match_levels'))
    return entries


def merge(memory, notes, *, family=False):
    return order(memory, notes, family=family)


def outcomes(store, key, version, directory, feedback):
    if store.cell(key).get('run_notes') is not True:
        return
    try:
        entries = delivered(directory)
        if not entries:
            return
        if feedback.get('execution_status') == 'condition_invalid':
            _skip(store, key, version, 'condition_invalid')
            return
        created = {e['note_id']: e for kind, e in events(store, key) if kind == PREFIX + 'created'}
        for entry in entries:
            try:
                note = _read_note(created[entry['note_id']])
                present = signature_present(feedback, entry['failure_signature'])
                _once(store, PREFIX + 'outcome', dict(cell=key, version=version, note_id=entry['note_id'],
                      help_version=entry['help_version'], signature_present=present, failure_gone=not present,
                      progress=progress(feedback), help_progress=note['help_progress']))
            except Exception as exc:
                _skip(store, key, version, 'error:' + type(exc).__name__, note_id=entry['note_id'])
    except Exception as exc:
        _skip(store, key, version, 'error:' + type(exc).__name__)


def turn_off(store, key, note_id):
    created = next((e for kind, e in events(store, key)
                    if kind == PREFIX + 'created' and e['note_id'] == note_id), None)
    if created is None:
        raise ValueError('run note does not exist in this cell')
    _read_note(created)
    _once(store, PREFIX + 'off', dict(cell=key, note_id=note_id, at=time.time()), ('cell', 'note_id'))
    return dict(cell=key, note_id=note_id, off=True)
