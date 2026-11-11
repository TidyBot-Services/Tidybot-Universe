import json
import sqlite3
from types import SimpleNamespace

from benchmarks.attention_harness import notices, ui_server
from benchmarks.attention_harness.core.store import AttentionStore


class Bridge:
    def __init__(self, path, cells=None, state='needs_review'):
        self.store = SimpleNamespace(path=path)
        self.cells = cells or [{'key': 'cell-1', 'state': 'needs_review', 'reason': 'worker_lost_no_claim_retry', 'versions': []}]
        self.state = state

    def experiments(self):
        return [{'experiment_id': 'devexp-' + 'a' * 24, 'created_at': 100}]

    def experiment(self, eid):
        assert eid == 'devexp-' + 'a' * 24
        return {'experiment_id': eid, 'state': self.state, 'cells': self.cells}


def ledger(tmp_path, events=()):
    path = tmp_path / 'ledger.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE v2_experiments (id TEXT, payload TEXT)')
        db.execute('CREATE TABLE v2_cells (id TEXT, exp TEXT, payload TEXT)')
        db.execute('CREATE TABLE v2_jobs (cell TEXT, state TEXT, payload TEXT)')
        db.execute('CREATE TABLE v2_versions (cell TEXT, payload TEXT)')
        db.execute('CREATE TABLE v2_events (at REAL, kind TEXT, payload TEXT)')
        eid = 'devexp-' + 'a' * 24
        db.execute('INSERT INTO v2_experiments VALUES (?,?)', (eid, json.dumps({'experiment_id': eid, 'state': 'needs_review', 'created_at': 100})))
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)', ('cell-1', eid, json.dumps({'key': 'cell-1', 'state': 'needs_review', 'reason': 'worker_lost_no_claim_retry'})))
        for at, payload in events:
            db.execute('INSERT INTO v2_events VALUES (?,?,?)', (at, 'provider_retry', json.dumps(payload)))
    return path


def test_notice_reader_sources_and_safety(tmp_path):
    eid = 'devexp-' + 'a' * 24
    path = ledger(tmp_path, [(100, {'cell': 'cell-1', 'version': 1, 'kind': 'model', 'attempt': 2, 'max_attempts': 5})])
    cells = [{'key': 'cell-1', 'state': 'needs_review', 'reason': 'worker_lost_no_claim_retry; /private/key https://user:pass@example.org; Authorization: secret; ' + 'a' * 64,
              'versions': [{'version': 1, 'state': 'failed', 'reason': 'Model failed'}]}]
    with sqlite3.connect(path) as db:
        db.execute('UPDATE v2_cells SET payload=?', (json.dumps({k:v for k,v in cells[0].items() if k != 'versions'}),))
        db.execute('INSERT INTO v2_versions VALUES (?,?)', ('cell-1', json.dumps(cells[0]['versions'][0])))
    memory = SimpleNamespace(source='service', gateway=SimpleNamespace(health=lambda: (_ for _ in ()).throw(OSError('down'))))
    reader = notices.NoticeReader(Bridge(path, cells), memory, lambda: {'used_percent': 80}, lambda: False)
    rows = reader.read(eid)['notices']
    assert {'run', 'model', 'memory'} <= {row['source'] for row in rows}
    assert any(row['title'] == 'Run needs review' for row in rows)
    assert any(row['title'] == 'Model service unavailable, retry 2 of 5' for row in rows)
    assert any(row['title'] == 'PARCC model service is not responding' for row in rows)
    assert any(row['title'] == 'Memory service is not reachable; notes cannot be read' for row in rows)
    assert any(row['title'] == 'Codex quota is running low this week' for row in rows)
    assert all(len(row[field]) <= 200 for row in rows for field in ('title', 'detail', 'hint'))
    assert all(word not in json.dumps(rows) for word in ('/private/key', 'user:pass', 'Authorization:', 'a' * 64))
    assert rows[0]['level'] == 'error'


def test_notice_reader_bounded_and_never_raises(tmp_path):
    path = ledger(tmp_path, [(float(i), {'cell': 'cell-1', 'attempt': i % 5 + 1, 'version': i, 'kind': 'model'}) for i in range(40)])
    reader = notices.NoticeReader(Bridge(path), probe=lambda: True)
    assert len(reader.read()['notices']) == 20
    reader.bridge = SimpleNamespace(store=SimpleNamespace(path=tmp_path / 'missing.sqlite3'))
    reader._cache.clear()
    assert reader.read()['notices'][0]['source'] == 'system'


def test_get_notices_route(tmp_path, monkeypatch):
    path = ledger(tmp_path)
    monkeypatch.setattr(notices, 'parcc_probe', lambda: False)
    monkeypatch.setattr(ui_server, 'read_quota', lambda: {'used_percent': 80})
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: SimpleNamespace(handler=handler))
    server = ui_server.create_server(dev_v2_bridge=Bridge(path), port=0)
    handler = object.__new__(server.handler)
    handler.path = '/api/notices?exp=devexp-' + 'a' * 24
    result = []
    handler._json = lambda value, *args: result.append(value)
    handler.do_GET()
    assert result[0]['generated_at']
    assert any(row['source'] == 'run' for row in result[0]['notices'])


def test_notices_route_uses_latest_experiment_and_ignores_old_saved_runs(tmp_path, monkeypatch):
    path = ledger(tmp_path)
    old = 'devexp-' + 'a' * 24
    new = 'devexp-' + 'b' * 24
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO v2_experiments VALUES (?,?)', (new, json.dumps({'experiment_id': new, 'state': 'running', 'created_at': 200})))
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)', ('cell-2', new, json.dumps({'key': 'cell-2', 'state': 'running'})))
    store = AttentionStore(tmp_path / 'ui.sqlite3')
    with store._connect() as db:
        db.execute('INSERT INTO runs VALUES (?,?)', ('old-run', json.dumps({'run_id': 'old-run', 'status': 'needs_review', 'reason': 'old run'})))
    monkeypatch.setattr(notices, 'parcc_probe', lambda: False)
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: SimpleNamespace(handler=handler))
    server = ui_server.create_server(store=store, dev_v2_bridge=Bridge(path), port=0)
    handler = object.__new__(server.handler)
    result = []
    handler._json = lambda value, *args: result.append(value)
    for route in ('/api/notices', '/api/notices?exp=' + new, '/api/notices?exp=' + old):
        handler.path = route
        handler.do_GET()
    assert all(not any(row['source'] == 'run' for row in payload['notices']) for payload in result[:2])
    assert any(row['source'] == 'run' for row in result[2]['notices'])
    assert all(any(row['source'] == 'model' for row in payload['notices']) for payload in result)


def test_notice_detail_keeps_model_ids_and_hides_paths_and_exception_names():
    assert notices.safe_detail('ParccError: parcc/GLM unavailable at /tmp/private/key') == 'parcc/GLM unavailable at [file]'
    assert notices.safe_detail('DevConflict: retry parcc/GLM from C:\\private\\key') == 'retry parcc/GLM from [file]'
    assert notices.safe_detail('TimeoutError: request expired') == 'The model service timed out while answering.'


def test_daily_ledger_is_read_without_write_transaction(tmp_path):
    exp = 'devexp-' + 'a' * 24
    folder = tmp_path / exp
    folder.mkdir()
    ledger(folder)
    reader = notices.NoticeReader(SimpleNamespace(root=tmp_path), probe=lambda: True)
    assert any(row['source'] == 'run' for row in reader.read(exp)['notices'])


def test_selected_formal_run_and_unreadable_store(tmp_path):
    path = tmp_path / 'ui.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE runs (id TEXT, payload TEXT)')
        db.execute('CREATE TABLE dev_sessions (id TEXT, payload TEXT)')
        db.execute('INSERT INTO runs VALUES (?,?)', ('run-1', json.dumps({'run_id': 'run-1', 'status': 'failed', 'reason': 'budget exhausted'})))
    store = SimpleNamespace(_connect=lambda: sqlite3.connect(path))
    reader = notices.NoticeReader(store=store, probe=lambda: True)
    assert not any(row['source'] == 'run' for row in reader.read()['notices'])
    assert any(row['source'] == 'run' for row in reader.read(run='run-1')['notices'])
    broken = notices.NoticeReader(store=SimpleNamespace(_connect=lambda: (_ for _ in ()).throw(OSError('private /path'))), probe=lambda: True)
    assert any(row['source'] == 'system' and row['level'] == 'error' for row in broken.read()['notices'])


def test_memory_events_without_a_title_do_not_hide_the_other_notices(monkeypatch):
    from benchmarks.attention_harness import memory_monitor
    events = [{'event': 'origin_reopened', 'memory_id': 'p-1', 'reason': 'reopened by the operator', 'time': 5.0, 'status': 'reopened'},
              {'event': 'promoted', 'memory_id': 'p-2', 'time': 6.0},
              {'event': 'rejected', 'seq': 7, 'title': 'Open the door first', 'time': 7.0}]
    monkeypatch.setattr(memory_monitor, 'automatic_checks', lambda **_: {'notices': [], 'errors': [], 'events': events})
    reader = notices.NoticeReader(quota=lambda: {'used_percent': 75}, probe=lambda: True)
    titles = [row['title'] for row in reader.read()['notices']]
    assert 'Some status details are unavailable' not in titles
    assert 'Codex quota is running low this week' in titles
    assert 'Note checked and trusted: Note — the failure stopped when Dev had the note.' in titles
    assert 'Note rejected after check: Open the door first.' in titles
    assert len(titles) == 3
