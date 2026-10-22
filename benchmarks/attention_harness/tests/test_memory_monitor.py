"""Read-only monitor route against a trimmed real ledger and fake Memory client."""
import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from benchmarks.attention_harness import memory_monitor, ui_server

RUN = Path('/home/truares/Documents/Codex/2026-10-14/goal-attentionbench-robocasa365-dev-v2-home/outputs/daily-experiments/devexp-53cbf969f72783537ce5de61')
EXPERIMENT = 'devexp-53cbf969f72783537ce5de61'


class Bridge:
    def __init__(self, ledger):
        self.store = SimpleNamespace(path=ledger)
        with sqlite3.connect(f'file:{ledger}?mode=ro', uri=True) as db:
            self.cell = json.loads(db.execute("SELECT payload FROM v2_events WHERE kind='planner_memory_retrieved' LIMIT 1").fetchone()[0])['cell']

    def get(self, path):
        if path != '/api/dev-v2/experiments/' + EXPERIMENT:
            raise FileNotFoundError('run missing')
        with sqlite3.connect(f'file:{self.store.path}?mode=ro', uri=True) as db:
            cell = json.loads(db.execute('SELECT payload FROM v2_cells WHERE id=?', (self.cell,)).fetchone()[0])
            cell['versions'] = [json.loads(row[0]) for row in db.execute('SELECT payload FROM v2_versions WHERE cell=? ORDER BY version', (self.cell,))]
            cell['jobs'] = [dict(json.loads(row[0]), state=row[1]) for row in db.execute('SELECT payload, state FROM v2_jobs WHERE cell=?', (self.cell,))]
        return {'state': 'completed', 'cells': [cell]}, 'application/json'


class Gateway:
    def uses(self, note_id):
        raise PermissionError('offline importer authorization required')

    def impact_report(self, note_id):
        raise PermissionError('offline importer authorization required')


class Memory:
    gateway = Gateway()
    def __init__(self, rows):
        self.rows = rows
    def dev_catalogue(self):
        return self.rows


def row(note_id, **changes):
    return {'memory_id': note_id, 'title': 'T' * 200, 'summary': 'S' * 300,
            'group': 'Grasp', 'status': 'trusted', 'seeded_for_tests': False,
            'validation_summary': {'decision': 'promoted', 'completed_pairs': 2, 'lift_pt': 3.5},
            'api_key': 'top-secret', **changes}


def request(handler_class, path):
    handler = object.__new__(handler_class)
    handler.path = path
    handler.headers = {}
    handler.wfile = io.BytesIO()
    response = {}
    handler.send_response = lambda status: response.update(status=int(status))
    handler.send_header = lambda key, value: response.update({key.lower(): value})
    handler.end_headers = lambda: None
    handler.do_GET()
    return response['status'], handler.wfile.getvalue(), response


def test_projection_uses_real_ledger_packet_and_scrubs_service_secrets():
    assert RUN.joinpath('ledger.sqlite3').is_file()
    bridge = Bridge(RUN / 'ledger.sqlite3')
    memory = Memory([row('c2-seeded-small-steps-001', seeded_for_tests=True),
                     row('promoted-001', title='Useful tip'),
                     row('rejected-002', status='disabled', validation_summary={'decision': 'rejected', 'completed_pairs': 1})])
    payload = memory_monitor.project(bridge, memory, EXPERIMENT, now=1792471444)
    assert payload['run']['experiment_id'] == EXPERIMENT
    assert any('c2-seeded-small-steps-001' in v['note_ids'] for v in payload['run']['versions'])
    assert any(e['type'] == 'planner_memory_retrieved' and e['ts'] for e in payload['events'])
    assert any(e['type'] == 'eval_failed' and e['version'] == 1 for e in payload['events'])
    assert [v['state'] for v in payload['run']['versions']] == ['failed'] * 5
    assert payload['notes'][0]['effect'] == 'not_tested'
    assert payload['notes'][1]['effect'] == 'helps' and payload['notes'][1]['lift_pt'] == 3.5
    assert payload['notes'][2]['effect'] == 'not_helps' and payload['notes'][2]['status'] == 'off'
    assert payload['notes'][0]['reads'] == 5
    assert payload['notes'][0]['last_used_at'] == 1792472286.534213
    assert payload['warnings'] == []
    assert len(payload['notes'][0]['title']) == 80 and len(payload['notes'][0]['summary']) == 180
    assert 'top-secret' not in json.dumps(payload) and 'api_key' not in json.dumps(payload)


def test_skipped_events_preserve_reason_and_exclusion_text(tmp_path):
    cases = [
        ('test_seeded_excluded_from_results', 'test note excluded'),
        ('not_trusted_here_excluded_from_results', 'not counted in results'),
        ('reference_only_no_scene_contract', 'reference only · not counted'),
        ('unknown_reason', 'test note excluded'),
        (None, 'test note excluded'),
    ]
    ledger = tmp_path / 'ledger.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE v2_events (seq INTEGER PRIMARY KEY, at REAL, kind TEXT, payload TEXT)')
        for version, (reason, _) in enumerate(cases, 1):
            detail = {'cell': 'fixture-cell', 'version': version, 'grants': []}
            if reason is not None:
                detail['reason'] = reason
            db.execute('INSERT INTO v2_events (at, kind, payload) VALUES (?, ?, ?)',
                       (version, 'planner_memory_result_skipped', json.dumps(detail)))
    events = memory_monitor._events(SimpleNamespace(path=ledger), [{'key': 'fixture-cell'}])
    assert [(e['reason'], e['text']) for e in events] == cases
    assert [e['version'] for e in events] == list(range(1, 6))
    assert all(e['type'] == 'planner_memory_result_skipped' for e in events)


def test_effect_requires_completed_real_validation_and_status_is_conservative():
    assert memory_monitor.effect(row('n', validation_summary={'decision': 'promoted', 'completed_pairs': 0})) == ('not_tested', None)
    assert memory_monitor.effect(row('n', validation_summary={'decision': 'rejected', 'completed_pairs': 0})) == ('not_tested', None)
    assert memory_monitor.effect(row('n', seeded_for_tests=True)) == ('not_tested', None)
    assert memory_monitor.effect(row('n', validation_summary={'decision': 'promoted', 'completed_pairs': 2})) == ('helps', None)
    for source, expected in [('trusted', 'active'), ('disabled', 'off'), ('expired', 'expired'),
                             ('rolled_back', 'withdrawn'), ('mystery', 'off')]:
        assert memory_monitor.status({'status': source}) == expected


def test_route_reports_passed_and_unknown_attempt_outcomes(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: handler)
    ledger = tmp_path / 'ledger.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE v2_events (seq INTEGER PRIMARY KEY, at REAL, kind TEXT, payload TEXT)')

    class FixtureBridge:
        root = tmp_path
        store = SimpleNamespace(path=ledger)

        def __init__(self, final_status):
            self.final_status = final_status

        def get(self, path):
            assert path == '/api/dev-v2/experiments/' + EXPERIMENT
            versions = [{'version': n, 'valid': True} for n in range(1, 6)]
            jobs = [{'version': n, 'kind': 'execute', 'state': 'settled',
                     'receipt': {'execution_status': self.final_status if n == 5 else 'failed'}} for n in range(1, 6)]
            return {'state': 'completed', 'cells': [{'key': 'fixture-cell', 'state': 'completed',
                    'versions': versions, 'jobs': jobs}]}, 'application/json'

    for final_status, expected in [('completed', 'passed'), ('unknown', 'finished')]:
        handler = ui_server.create_server(dev_v2_bridge=FixtureBridge(final_status), memory_view=Memory([]))
        status, raw, _ = request(handler, '/api/dev-v2/memory-monitor?exp=' + EXPERIMENT)
        assert status == 200
        assert [v['state'] for v in json.loads(raw)['run']['versions']] == ['failed'] * 4 + [expected]


def test_route_uses_manifest_version_budget_and_keeps_later_versions_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: handler)
    ledger = tmp_path / 'ledger.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE v2_events (seq INTEGER PRIMARY KEY, at REAL, kind TEXT, payload TEXT)')

    class FixtureBridge:
        root = tmp_path
        store = SimpleNamespace(path=ledger)

        def __init__(self, limit):
            self.limit = limit

        def get(self, path):
            assert path == '/api/dev-v2/experiments/' + EXPERIMENT
            manifest = tmp_path / f'manifest-{self.limit}.json'
            manifest.write_text(json.dumps({'max_versions': self.limit}))
            return {'state': 'completed', 'manifest': {'uri': str(manifest), 'sha256': memory_monitor.sha(manifest)},
                    'cells': [{'key': 'fixture-cell', 'versions': [{'version': 1, 'valid': True}],
                               'jobs': [{'version': 1, 'kind': 'execute', 'state': 'settled',
                                         'receipt': {'execution_status': 'completed'}}]}]}, 'application/json'

    for limit in (3, 8):
        handler = ui_server.create_server(dev_v2_bridge=FixtureBridge(limit), memory_view=Memory([]))
        status, raw, _ = request(handler, '/api/dev-v2/memory-monitor?exp=' + EXPERIMENT)
        assert status == 200
        run = json.loads(raw)['run']
        assert run['max_versions'] == limit
        assert [v['n'] for v in run['versions']] == list(range(1, limit + 1))
        assert [v['state'] for v in run['versions']] == ['passed'] + ['waiting'] * (limit - 1)


def test_version_budget_fallback_and_clamp(tmp_path):
    cells = [{'active_version': 8, 'versions': [{'version': 3}], 'jobs': []}]
    assert memory_monitor._max_versions({}, cells) == 8
    assert memory_monitor._max_versions({}, []) == 5
    assert memory_monitor._max_versions({}, [{'versions': [{'version': 0}], 'jobs': []}]) == 5
    manifest = tmp_path / 'manifest.json'
    for configured, expected in ((0, 1), (20, 12)):
        manifest.write_text(json.dumps({'max_versions': configured}))
        ref = {'uri': str(manifest), 'sha256': memory_monitor.sha(manifest)}
        assert memory_monitor._max_versions({'manifest': ref}, cells) == expected


def test_route_serves_monitor_files_and_bounds_payload_with_errors(monkeypatch, capsys):
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: handler)
    bridge = Bridge(RUN / 'ledger.sqlite3')
    memory = Memory([row(f'n-{i:04d}') for i in range(610)])
    handler = ui_server.create_server(dev_v2_bridge=bridge, memory_view=memory)
    for filename, content_type in [('memory.html', 'text/html'), ('memory.js', 'text/javascript'), ('memory.css', 'text/css')]:
        status, raw, headers = request(handler, '/ui/' + filename)
        assert status == 200 and raw and content_type in headers['content-type']
        if filename == 'memory.html':
            assert b'__CONTROL_TOKEN__' not in raw and b'data-control-token=' in raw
    status, raw, _ = request(handler, '/api/dev-v2/memory-monitor?exp=' + EXPERIMENT)
    assert status == 200
    value = json.loads(raw)
    assert len(value['notes']) == 600 and len(value['events']) <= 500
    assert [v['state'] for v in value['run']['versions']] == ['failed'] * 5
    assert value['warnings'] == []
    assert 'top-secret' not in raw.decode()
    assert request(handler, '/api/dev-v2/memory-monitor?exp=bad')[0] == 400
    memory.dev_catalogue = lambda: (_ for _ in ()).throw(PermissionError('top-secret'))
    status, raw, _ = request(handler, '/api/dev-v2/memory-monitor?exp=' + EXPERIMENT)
    assert status == 502 and json.loads(raw) == {'error': 'Memory monitor unavailable'}
    line = capsys.readouterr().err
    assert 'Memory monitor online catalogue: PermissionError: details unavailable' in line
    assert 'top-secret' not in line


def test_bad_note_extras_degrade_without_failing_route(monkeypatch, capsys):
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: handler)
    bridge = Bridge(RUN / 'ledger.sqlite3')

    class BrokenValidation:
        def get(self, key, default=None):
            raise PermissionError('secret guidance')

    memory = Memory([row('c2-seeded-small-steps-001', validation_summary=BrokenValidation()),
                     row('healthy-001', validation_summary={})])
    handler = ui_server.create_server(dev_v2_bridge=bridge, memory_view=memory)
    status, raw, _ = request(handler, '/api/dev-v2/memory-monitor?exp=' + EXPERIMENT)
    value = json.loads(raw)
    assert status == 200
    assert value['notes'][0]['reads'] == 0
    assert value['notes'][0]['last_used_at'] is None
    assert value['notes'][0]['effect'] == 'not_tested'
    assert value['notes'][1]['effect'] == 'not_tested'
    assert value['warnings'] == ['Some note details are unavailable.']
    line = capsys.readouterr().err
    assert 'Memory monitor note details: PermissionError: details unavailable' in line
    assert 'secret guidance' not in line


def test_usage_scans_sibling_ledgers_and_caches_briefly(tmp_path, monkeypatch):
    bridge = Bridge(RUN / 'ledger.sqlite3')
    bridge.root = tmp_path
    other = tmp_path / ('devexp-' + 'a' * 24) / 'ledger.sqlite3'
    other.parent.mkdir()
    with sqlite3.connect(other) as db:
        db.execute('CREATE TABLE v2_events (seq INTEGER PRIMARY KEY, at REAL, kind TEXT, payload TEXT)')
        retrieved = {'cell': 'other-cell', 'version': 1,
                     'versions': [{'memory_id': 'c2-seeded-small-steps-001', 'grant_id': 'grant-x'}]}
        skipped = {'cell': 'other-cell', 'version': 1, 'grants': ['grant-x']}
        db.execute('INSERT INTO v2_events (at, kind, payload) VALUES (?, ?, ?)',
                   (1792473000, 'planner_memory_retrieved', json.dumps(retrieved)))
        db.execute('INSERT INTO v2_events (at, kind, payload) VALUES (?, ?, ?)',
                   (1792473100, 'planner_memory_result_skipped', json.dumps(skipped)))
    memory_monitor._USAGE.clear()
    memory = Memory([row('c2-seeded-small-steps-001')])
    first = memory_monitor.project(bridge, memory, EXPERIMENT)
    assert first['notes'][0]['reads'] == 6  # skipped result is the same retrieval
    assert first['notes'][0]['last_used_at'] == 1792473000
    with sqlite3.connect(other) as db:
        db.execute('INSERT INTO v2_events (at, kind, payload) VALUES (?, ?, ?)',
                   (1792473200, 'planner_memory_retrieved', json.dumps({**retrieved, 'version': 2})))
    assert memory_monitor.project(bridge, memory, EXPERIMENT)['notes'][0]['reads'] == 6
    monkeypatch.setattr(memory_monitor, 'CACHE_SECONDS', 0)
    assert memory_monitor.project(bridge, memory, EXPERIMENT)['notes'][0]['reads'] == 7


def test_projection_shows_unvalidated_uses_expiry_and_other_scene_reference():
    bridge = Bridge(RUN / 'ledger.sqlite3')
    seed = bridge.get('/api/dev-v2/experiments/' + EXPERIMENT)[0]['cells'][0]['seed']
    now = 1792471444
    memory = Memory([row('p-here', status='provisional', unvalidated=True, source_seed=seed, validation_summary=None,
                         uses={'grants': 4, 'outcomes': 4, 'failure_gone': 3, 'bogus': 'x'}, expires_at=now + 12 * 86400 - 5),
                     row('other-scene', source_seed=seed + 7, applicability_summary={'task_id': bridge.get('/api/dev-v2/experiments/' + EXPERIMENT)[0]['cells'][0]['task_id']}),
                     row('same-scene', source_seed=seed)])
    payload = memory_monitor.project(bridge, memory, EXPERIMENT, now=now)
    notes = {n['id']: n for n in payload['notes']}
    assert notes['p-here']['status'] == 'unvalidated' and notes['p-here']['unvalidated'] is True
    assert notes['p-here']['uses'] == {'grants': 4, 'outcomes': 4, 'failure_gone': 3}
    assert notes['p-here']['expires_in_days'] == 12 and notes['p-here']['reference'] is False
    assert notes['other-scene']['reference'] is True and notes['other-scene']['scene_seed'] == seed + 7
    assert notes['same-scene']['reference'] is False and notes['same-scene']['uses'] is None
    assert payload['run']['unvalidated_used'] is False
    assert 'top-secret' not in json.dumps(payload)
