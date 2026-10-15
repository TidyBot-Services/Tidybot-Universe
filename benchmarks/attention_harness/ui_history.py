"""Recoverable UI history and names, separate from execution evidence and locks."""
import json
import time

from .core.store import StateConflictError


def name_suffix(value):
    if not isinstance(value, str):
        raise ValueError('name suffix must be text')
    value = value.strip()
    if len(value) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('name suffix must be at most 80 characters on one line')
    return value


class UIHistory:
    def __init__(self, store):
        self.store = store
        with store._connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS ui_history (kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,id))')

    @staticmethod
    def _read(db, kind, record_id):
        row = db.execute('SELECT payload FROM ui_history WHERE kind=? AND id=?', (kind, record_id)).fetchone()
        return json.loads(row[0]) if row else {}

    @staticmethod
    def _save(db, kind, record_id, metadata):
        db.execute('INSERT INTO ui_history VALUES (?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',
                   (kind, record_id, json.dumps(metadata, ensure_ascii=False)))

    def assign_suffix(self, kind, record_id, suffix):
        suffix = name_suffix(suffix)
        with self.store._transaction() as db:
            metadata = self._read(db, kind, record_id)
            if 'name_suffix' in metadata and metadata['name_suffix'] != suffix:
                raise StateConflictError('retry must keep the original name suffix')
            metadata['name_suffix'] = suffix
            self._save(db, kind, record_id, metadata)

    def change(self, kind, record_id, *, deleted):
        if kind not in {'run', 'dev'} or not isinstance(record_id, str) or not record_id or len(record_id) > 256:
            raise ValueError('valid record kind and ID required')
        with self.store._transaction() as db:
            table = 'runs' if kind == 'run' else 'dev_sessions'
            row = db.execute(f'SELECT payload FROM {table} WHERE id=?', (record_id,)).fetchone()
            if not row:
                raise StateConflictError('record not found')
            record = json.loads(row[0])
            if deleted and (record.get('status') == 'running' or record.get('state') in {'ready', 'running', 'stopping'} or record.get('owner')):
                raise StateConflictError('Stop the active record before deleting it')
            metadata = self._read(db, kind, record_id)
            metadata['history_deleted_at'] = (metadata.get('history_deleted_at') or time.time()) if deleted else None
            self._save(db, kind, record_id, metadata)
        return {'kind': kind, 'record_id': record_id, **metadata}

    def project_many(self, kind, records):
        with self.store._connect() as db:
            metadata = {(r[0], r[1]): json.loads(r[2]) for r in db.execute('SELECT kind,id,payload FROM ui_history')}
        inherited = {}
        if kind == 'run' and any(k == 'launch' for k, _ in metadata):
            inherited = {r['run_id']: metadata.get(('launch', r['launch_id']), {})
                         for r in self.store.list_launches() if r.get('run_id')}
        key = 'session_id' if kind == 'dev' else 'run_id'
        return [{**r, **inherited.get(r[key], {}), **metadata.get((kind, r[key]), {})} for r in records]

    def project(self, kind, record):
        return self.project_many(kind, [record])[0]
