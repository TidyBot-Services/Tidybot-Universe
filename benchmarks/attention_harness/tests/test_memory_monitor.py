"""Read-only monitor route against a frozen synthetic v2 run and fake client."""
import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import memory_monitor, ui_server
from benchmarks.attention_harness.dev_ledger import DevV2Store

FIXTURE = Path(__file__).parent / 'fixtures' / 'memory_monitor_run'
EXPERIMENT = 'devexp-53cbf969f72783537ce5de61'


@pytest.fixture(name='RUN')
def synthetic_run(tmp_path):
    """Materialize only repository data, using the production v2 table schema."""
    run = tmp_path / EXPERIMENT
    store = DevV2Store(run / 'ledger.sqlite3')
    artifacts = run / 'artifacts'
    artifacts.mkdir()
    for source in (FIXTURE / 'artifacts').iterdir():
        (artifacts / source.name).write_bytes(source.read_bytes())

    def relocate(value):
        if isinstance(value, dict):
            if set(value) == {'$artifact'}:
                path = artifacts / value['$artifact']
                return {'uri': str(path), 'sha256': memory_monitor.sha(path)}
            return {key: relocate(item) for key, item in value.items()}
        if isinstance(value, list):
            return [relocate(item) for item in value]
        return value

    data = relocate(json.loads((FIXTURE / 'run.json').read_text()))
    cell = data['cell']
    with store.tx() as db:
        db.execute('INSERT INTO v2_cells VALUES (?, ?, ?)',
                   (cell['key'], cell['experiment_id'], json.dumps(cell)))
        for version in data['versions']:
            db.execute('INSERT INTO v2_versions VALUES (?, ?, ?)',
                       (cell['key'], version['version'], json.dumps(version)))
        for record in data['jobs']:
            job = record['payload']
            db.execute('INSERT INTO v2_jobs VALUES (?, ?, ?, ?, ?, ?)',
                       (job['job_id'], job['cell'], job['version'], job['kind'], record['state'], json.dumps(job)))
        for event in data['events']:
            db.execute('INSERT INTO v2_events (at, kind, payload) VALUES (?, ?, ?)',
                       (event['at'], event['kind'], json.dumps(event['payload'])))
    return run


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
    handler.server = handler_class
    handler.path = path
    handler.headers = {}
    handler.wfile = io.BytesIO()
    response = {}
    handler.send_response = lambda status: response.update(status=int(status))
    handler.send_header = lambda key, value: response.update({key.lower(): value})
    handler.end_headers = lambda: None
    handler.do_GET()
    return response['status'], handler.wfile.getvalue(), response


def test_projection_uses_real_ledger_packet_and_scrubs_service_secrets(RUN):
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


def test_route_serves_monitor_files_and_bounds_payload_with_errors(monkeypatch, capsys, RUN):
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


def test_bad_note_extras_degrade_without_failing_route(monkeypatch, capsys, RUN):
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


def test_usage_scans_sibling_ledgers_and_caches_briefly(tmp_path, monkeypatch, RUN):
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


def test_projection_shows_unvalidated_uses_expiry_and_other_scene_reference(RUN):
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


@pytest.fixture
def history_runs(tmp_path):
    from benchmarks.attention_harness.memory_use import analyse
    original, generalized = 'p-history', 'p-history-g'
    runs = []
    for index, (mid, level, text) in enumerate([
        ('r-history', 'same_task', 'Using note r-history: local advice.'),
        (original, 'same_task', 'Using note p-history: retreat before lifting.'),
        (generalized, 'same_skill', 'Not using note p-history-g: no held object.'),
    ], 1):
        run = tmp_path / ('devexp-' + str(index)*24)
        directory = run/'artifacts'/run.name/'cells'/'cell'
        exported = directory/'v1'
        private = directory/'agent/private/memory/v1'
        exported.mkdir(parents=True)
        private.mkdir(parents=True)
        entry = dict(memory_id=mid, title='Retreat', level=level)
        for target in (exported, private):
            (target/'memory-input.json').write_text(json.dumps(dict(entries=[entry])))
        audit = analyse([entry], [dict(file='v1/policy.py', line=1, text=text)])
        (exported/'memory_use.json').write_text(json.dumps(audit))
        store = DevV2Store(run/'ledger.sqlite3')
        cell = dict(key='cell', experiment_id=run.name, task_id='cabinet' if index<3 else 'condiments',
                    seed=100+index, memory_family=True, run_notes=True, artifact_dir=str(directory))
        version = dict(version=1, agent=dict(native_success=False, eval=dict(status='completed', passed=index==2)))
        with store.tx() as db:
            db.execute('INSERT INTO v2_cells VALUES (?,?,?)', ('cell', run.name, json.dumps(cell)))
            db.execute('INSERT INTO v2_versions VALUES (?,?,?)', ('cell', 1, json.dumps(version)))
        runs.append((run, directory))
    return tmp_path, runs, original, generalized


def test_note_history_exact_ids_levels_decisions_and_results(history_runs):
    root, runs, original, generalized = history_runs
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    value = memory_monitor.note_history(root, [original, generalized])
    assert not value['errors']
    rows = sorted(value['rows'], key=lambda r:r['experiment_id'])
    assert len(rows) == 2  # No invented r- to p- alias and no private/public duplicate.
    assert [(r['memory_id'], r['level'], r['use']) for r in rows] == [
        (original, 'same_task', 'used'), (generalized, 'same_skill', 'considered_not_used')]
    assert rows[0]['reason'] == 'Using: retreat before lifting.'
    assert rows[1]['reason'] == 'Not using: no held object.'
    assert [(r['native'], r['eval']) for r in rows] == [('failed', 'passed'), ('failed', 'failed')]
    assert before == {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert len(memory_monitor.note_history(runs[0][0], ['r-history'])['rows']) == 1


def test_note_history_not_mentioned_both_decisions_and_mtime_cache(history_runs):
    root, runs, original, generalized = history_runs
    audit_path = runs[1][1]/'v1/memory_use.json'
    first = memory_monitor.note_history(root, [original])['rows'][0]
    assert first['use'] == 'used'
    audit = json.loads(audit_path.read_text())
    audit['notes'][0].update(referenced='none', evidence=[])
    audit_path.write_text(json.dumps(audit))
    assert memory_monitor.note_history(root, [original])['rows'][0]['use'] == 'not_mentioned'
    from benchmarks.attention_harness.memory_use import analyse
    audit = analyse([dict(memory_id=original, title='Retreat', level='same_task')], [dict(file='policy.py', line=1,
        text='Using note p-history: '+ 'x'*300+'\nNot using note p-history: later.')])
    audit_path.write_text(json.dumps(audit))
    row = memory_monitor.note_history(root, [original])['rows'][0]
    assert row['use'] == 'considered_not_used' and row['considered_not_used'] is True
    assert row['reason'] == 'Not using: later.'
    assert len(row['reason']) <= 200
    # Ledger changes invalidate cached opt-in metadata too.
    with sqlite3.connect(runs[1][0]/'ledger.sqlite3') as db:
        cell = json.loads(db.execute('SELECT payload FROM v2_cells').fetchone()[0])
        cell.update(memory_family=False, run_notes=False)
        db.execute('UPDATE v2_cells SET payload=?', (json.dumps(cell),))
    assert not memory_monitor.note_history(root, [original])['rows']


def test_note_history_private_pending_corruption_and_missing_audit(history_runs):
    root, runs, original, generalized = history_runs
    directory = runs[2][1]
    for path in (directory/'v1').iterdir():path.unlink()
    row = memory_monitor.note_history(root, [generalized])['rows'][0]
    assert row['use'] == 'pending' and any('audit pending' in e for e in row['errors'])
    (directory/'v1/memory-input.json').write_text('{bad')
    value = memory_monitor.note_history(root, [generalized])
    assert not value['rows']  # Corrupt public input must not fall back to a stale private copy.
    assert any('memory-input.json' in e['error'] for e in value['errors'])
    (directory/'v1/memory-input.json').unlink()
    (directory/'v1/memory_use.json').write_text('{bad audit')
    row = memory_monitor.note_history(root, [generalized])['rows'][0]
    assert row['use'] == 'pending' and any('memory_use.json' in e for e in row['errors'])
    (directory/'v1/memory_use.json').write_text(json.dumps(dict(notes=[], errors=[dict(error='analysis failure')])))
    row = memory_monitor.note_history(root, [generalized])['rows'][0]
    assert 'analysis failure' in row['errors']
    assert 'Delivered note is absent from memory_use.json' in row['errors']
    (runs[0][0]/'ledger.sqlite3').write_bytes(b'broken database')
    value = memory_monitor.note_history(root, [original])
    assert any(e.get('experiment_id') == runs[0][0].name for e in value['errors'])


def test_note_history_bounds_root_and_native_identity(history_runs, monkeypatch):
    root, runs, original, generalized = history_runs
    assert memory_monitor.note_history(None, [original])['errors']
    assert not memory_monitor.note_history(None, [])['errors']
    monkeypatch.setattr(memory_monitor, 'EXPERIMENT_LIMIT', 1)
    assert any('limited to 1' in e['error'] for e in memory_monitor.note_history(root, [original])['errors'])
    monkeypatch.setattr(memory_monitor, 'EXPERIMENT_LIMIT', 200)
    bundle = runs[1][1]/'v1/bundle.json'
    bundle.write_text(json.dumps(dict(native_success=True)))
    job = dict(version=1, state='settled', kind='execute', receipt=dict(execution_status='completed',
        bundle=dict(uri=str(bundle), sha256=memory_monitor.sha(bundle))))
    with sqlite3.connect(runs[1][0]/'ledger.sqlite3') as db:
        db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)', ('job', 'cell', 1, 'execute', 'settled', json.dumps(job)))
    row = memory_monitor.note_history(root, [original])['rows'][0]
    assert row['native'] == 'unavailable'
    assert any('identity' in e for e in row['errors'])


def test_automatic_checks_joins_history_by_original_and_generalized_ids(history_runs):
    root, runs, original, generalized = history_runs
    class Gateway:
        def reuse_notes_list(self):
            return dict(notes=[dict(original=dict(memory_id=original), generalized=dict(memory_id=generalized))], errors=[])
        def list_dev_v2_memories(self):
            return [dict(memory_id=original, status='disabled', provider='generic-source'), dict(memory_id=generalized, status='trusted')]
    value = memory_monitor.automatic_checks(gateway=Gateway(), experiments_root=root)
    pair = value['reuse']['notes'][0]
    assert {r['memory_id'] for r in pair['history']} == {original, generalized}
    assert pair['original']['status'] == 'disabled' and pair['original']['provider'] == 'generic-source'
    assert pair['generalized']['status'] == 'trusted'


def test_automatic_checks_reuse_metadata_avoids_full_catalogue(history_runs):
    root, _, original, generalized = history_runs
    class Gateway:
        def reuse_notes_list(self):
            return dict(notes=[dict(original=dict(memory_id=original, status='disabled', provider='advisor'),
                generalized=dict(memory_id=generalized, status='provisional', expires_at=123))], errors=[])
        def list_dev_v2_memories(self):
            raise AssertionError('full catalogue verifies unrelated source evidence')
    value = memory_monitor.automatic_checks(gateway=Gateway(), experiments_root=root)
    pair = value['reuse']['notes'][0]
    assert pair['original']['status'] == 'disabled' and pair['original']['provider'] == 'advisor'
    assert pair['generalized']['expires_at'] == 123
    assert not value['reuse']['errors']


def test_project_passes_metadata_and_current_run_citations(RUN):
    bridge = Bridge(RUN/'ledger.sqlite3')
    with sqlite3.connect(RUN/'ledger.sqlite3') as db:
        cell = json.loads(db.execute('SELECT payload FROM v2_cells').fetchone()[0])
        cell['memory_family'] = True
        directory = RUN/'artifacts'/RUN.name/'cells'/cell['key']/'v2'
        directory.mkdir(parents=True)
        (directory/'memory-input.json').write_text(json.dumps(dict(entries=[dict(memory_id='p-current', level='same_task')])))
        (directory/'memory_use.json').write_text(json.dumps(dict(notes=[dict(memory_id='p-current', title='Retreat', level='same_task',
            delivered=True, referenced='explicit', evidence=[dict(file='v2/policy.py', line=1, excerpt='Using note p-current: clear the door.')])], errors=[])))
        db.execute('UPDATE v2_cells SET payload=?', (json.dumps(cell),))
    metadata = dict(level='same_task', skill_tags=['cabinet'], applies_when='Carrying', source_run=EXPERIMENT,
                    source_version=1, source_verified=True, provider='future-provider', status='trusted')
    value = memory_monitor.project(bridge, Memory([row('p-current', **metadata)]), EXPERIMENT)
    note = value['notes'][0]
    assert {k:note[k] for k in metadata if k!='status'} == {k:v for k,v in metadata.items() if k!='status'}
    assert note['note_status'] == 'trusted'
    assert note['dev_use'][0]['version'] == 2 and note['dev_use'][0]['use'] == 'used'


def test_automatic_checks_route_passes_bridge_history_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_server, 'ThreadingHTTPServer', lambda address, handler: handler)
    received = {}
    def checks(**kwargs):
        received.update(kwargs)
        return dict(reuse=dict(notes=[], errors=[]))
    monkeypatch.setattr(memory_monitor, 'automatic_checks', checks)
    memory = Memory([])
    handler = ui_server.create_server(dev_v2_bridge=SimpleNamespace(root=tmp_path), memory_view=memory)
    status, raw, _ = request(handler, '/api/dev-v2/automatic-checks')
    assert status == 200 and json.loads(raw)['reuse']['notes'] == []
    assert received == dict(gateway=memory.gateway, experiments_root=tmp_path)


def test_note_history_local_packet_schema_and_visible_malformed_results(history_runs):
    root, runs, original, generalized = history_runs
    directory = runs[0][1]
    for target in (directory/'v1', directory/'agent/private/memory/v1'):
        (target/'memory-input.json').write_text(json.dumps(dict(entries=[])))
        (target/'memory-run-notes-input.json').write_text(json.dumps(dict(entries=[dict(note_id='r-history', layer='run', level='same_task')])))
    value = memory_monitor.note_history(root, ['r-history'])
    assert len(value['rows']) == 1 and not value['rows'][0]['errors']
    with sqlite3.connect(runs[0][0]/'ledger.sqlite3') as db:
        db.execute('UPDATE v2_versions SET payload=?', (json.dumps(dict(version=1, agent=['invalid'])),))
    value = memory_monitor.note_history(root, ['r-history'])
    assert value['rows'][0]['native'] == 'unavailable'
    assert value['rows'][0]['errors']


def test_history_reason_prefers_explicit_decision_over_generic_citation():
    note = dict(memory_id='p-1', evidence=[dict(excerpt='An earlier generic citation of p-1.'),
                dict(excerpt='Using note p-1: retreat first.')])
    assert memory_monitor._history_reason(note) == 'Using: retreat first.'


@pytest.mark.parametrize('index, expected, reason', [(1, 'used', 'Using: test the other closing direction.'),
    (2, 'considered_not_used', 'Not using: no held object.')])
def test_note_history_turn_log_fallback_reuses_export_detector(history_runs, monkeypatch, index, expected, reason):
    from benchmarks.attention_harness import memory_use
    root, runs, original, generalized = history_runs
    directory = runs[index][1]
    mid = original if index == 1 else generalized
    (directory/'v1/memory_use.json').unlink()
    saved = directory/'agent/private/memory/v1'
    entry = json.loads((saved/'memory-input.json').read_text())['entries'][0]
    (saved/'memory-delivered.json').write_text(json.dumps(dict(notes=[entry])))
    (saved/'memory-use-start.json').write_text('{}')
    log = directory/'agent/private/turns/t000001/events.jsonl'
    log.parent.mkdir(parents=True)
    decision = ('Using note '+mid+': test the other closing direction.' if index == 1 else
                'Not using note '+mid+': no held object.')
    events = [dict(type='item.completed', item=dict(type='command_execution', aggregated_output='Using note '+mid+': tool output is not a citation.')),
              dict(type='item.completed', item=dict(type='reasoning', text=decision))]
    log.write_text(''.join(json.dumps(e)+'\n' for e in events))
    calls = []
    analyse = memory_use.analyse
    def detector(*args, **kwargs):
        calls.append(True)
        return analyse(*args, **kwargs)
    monkeypatch.setattr(memory_use, 'analyse', detector)
    before = {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    row = memory_monitor.note_history(root, [mid])['rows'][0]
    assert calls and row['use'] == expected and row['reason'] == reason
    assert row['decision_source'] == 'turn_log' and not row['errors']
    assert before == {p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    # Polling an unchanged version must reuse the export detector's result.
    assert memory_monitor.note_history(root, [mid])['rows'][0] == row
    assert len(calls) == 1
    # A later delivery bounds this version; subsequent messages cannot change it.
    next_delivery = saved.parent/'v2'
    next_delivery.mkdir()
    (next_delivery/'memory-use-start.json').write_text(json.dumps({'turns/t000001/events.jsonl':log.stat().st_size}))
    log.write_text(log.read_text()+json.dumps(dict(type='item.completed', item=dict(type='agent_message',
        text='Not using note '+mid+': later version.')))+'\n')
    row = memory_monitor.note_history(root, [mid])['rows'][0]
    assert row['use'] == expected and row['reason'] == reason
    # Unreadable/malformed authored evidence remains a visible pending error.
    log.write_text('{broken event\n')
    row = memory_monitor.note_history(root, [mid])['rows'][0]
    assert row['use'] == 'pending' and row['errors']
    log.unlink()
    row = memory_monitor.note_history(root, [mid])['rows'][0]
    assert row['use'] == 'pending' and row['errors']


@pytest.mark.parametrize('evidence', ['run_note', 'help', 'origin', 'absent'])
def test_history_source_row_and_provider_are_recorded_not_inferred(history_runs, evidence):
    root, runs, original, generalized = history_runs
    run, directory = runs[0]
    metadata = dict(memory_id=original, source_run=run.name, source_version=1, level='same_task')
    if evidence == 'run_note':
        (directory/'v1/memory-run-note.json').write_text(json.dumps(dict(experiment_id=run.name,
            cell='cell', help_version=1, provider='advisor')))
    elif evidence == 'help':
        (directory/'v1/help.json').write_text(json.dumps(dict(provider='advisor')))
    elif evidence == 'origin':metadata['origin'] = dict(provider='advisor')
    class Gateway:
        def reuse_notes_list(self):
            return dict(notes=[dict(original=metadata, generalized=dict(metadata, memory_id=generalized))], errors=[])
    pair = memory_monitor.automatic_checks(gateway=Gateway(), experiments_root=root)['reuse']['notes'][0]
    source = pair['history'][0]
    assert (source['use'], source['experiment_id'], source['version']) == ('source', run.name, 1)
    assert len([r for r in pair['history'] if r['use'] == 'source']) == 1
    assert (pair['original'].get('provider'), pair['generalized'].get('provider')) == ((None, None) if evidence == 'absent' else ('advisor', 'advisor'))
    assert not memory_monitor.note_history(root, [original])['rows'][0]['use'] == 'source'


def test_source_identity_errors_are_visible_and_origin_provider_needs_no_inference(history_runs):
    root, runs, original, _ = history_runs
    value = memory_monitor.note_history(root, [original], notes=[dict(memory_id=original,
        source_run='../outside', source_version=1)])
    assert any('source identity' in e['error'] for e in value['errors'])
    assert not any(r['use'] == 'source' for r in value['rows'])
    pair = dict(original=dict(memory_id=original, origin=dict(provider='advisor')))
    memory_monitor.join_reuse_history(dict(notes=[pair]), dict(rows=[], errors=[]))
    assert pair['original']['provider'] == 'advisor'


def test_note_history_opens_only_recipient_evidence_and_indexes_packets(tmp_path, monkeypatch):
    """50 runs, two versions each: unrelated audits/logs/results stay unopened."""
    from benchmarks.attention_harness.memory_use import analyse
    directories = []
    for index in range(50):
        run = tmp_path / f'devexp-{index:024x}'
        directory = run/'artifacts/cell'
        store = DevV2Store(run/'ledger.sqlite3')
        cell = dict(key='cell', experiment_id=run.name, artifact_dir=str(directory),
                    memory_family=index != 49, run_notes=False)
        with store.tx() as db:
            db.execute('INSERT INTO v2_cells VALUES (?,?,?)', ('cell', run.name, json.dumps(cell)))
            for number in (1, 2):
                db.execute('INSERT INTO v2_versions VALUES (?,?,?)', ('cell', number, json.dumps(dict(version=number))))
                target = directory/f'v{number}'
                target.mkdir(parents=True)
                mid = 'p-requested' if (index, number) == (0, 1) else f'p-other-{index}-{number}'
                note = dict(memory_id=mid, title=mid, level='same_task')
                (target/'memory-input.json').write_text(json.dumps(dict(entries=[note])))
                (target/'memory_use.json').write_text(json.dumps(analyse([note], [])))
        directories.append(directory)
    reads = []
    read = Path.read_bytes
    def tracked(path):
        reads.append(path)
        if path.name not in ('memory-input.json', 'memory-run-notes-input.json'):
            assert path == directories[0]/'v1/memory_use.json'
        assert not path.is_relative_to(directories[49])  # Not opted in.
        return read(path)
    monkeypatch.setattr(Path, 'read_bytes', tracked)
    turn_use = memory_monitor._history_turn_use
    def recipient_log(directory, number, numbers):
        assert directory == directories[1] and number == 2  # Changed recipient below only.
        return turn_use(directory, number, numbers)
    monkeypatch.setattr(memory_monitor, '_history_turn_use', recipient_log)
    first = memory_monitor.note_history(tmp_path, ['p-requested'])
    assert len(first['rows']) == 1 and not first['errors']
    assert len(reads) == 99  # 98 cheap packets, exactly one recipient audit.
    reads.clear()
    assert memory_monitor.note_history(tmp_path, ['p-requested']) == first
    assert not reads  # Warm index/file caches never reopen unrelated files.
    # Changed packets must update the recipient index immediately.
    target = directories[1]/'v2'
    note = dict(memory_id='p-requested', title='Requested', level='same_task')
    (target/'memory-input.json').write_text(json.dumps(dict(entries=[note])))
    (target/'memory_use.json').unlink()
    second = memory_monitor.note_history(tmp_path, ['p-requested'])
    assert len(second['rows']) == 2
    assert target/'memory-input.json' in reads


def test_project_history_is_scoped_to_viewed_run(RUN, monkeypatch):
    history = memory_monitor.note_history
    calls = []
    def scoped(root, *args, **kwargs):
        calls.append(root)
        assert root == RUN
        return history(root, *args, **kwargs)
    monkeypatch.setattr(memory_monitor, 'note_history', scoped)
    memory_monitor.project(Bridge(RUN/'ledger.sqlite3'), Memory([row('p-current')]), EXPERIMENT)
    assert calls == [RUN]


def test_history_cache_is_bounded_lru_and_thread_safe():
    from concurrent.futures import ThreadPoolExecutor
    cache = memory_monitor._HistoryCache(3, byte_limit=300)
    cache.put('first', 'value')
    cache.put('second', 'value')
    cache.put('third', 'value')
    assert cache.get('first') == 'value'
    cache.put('fourth', 'value')
    assert cache.get('second') is None
    cache.put('too-large', 'x'*301)
    assert cache.get('too-large') is None
    def poll(index):
        cache.put(str(index % 7), dict(index=index))
        cache.get(str((index+1) % 7))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(poll, range(200)))
    assert len(cache.data) <= 3 and cache.bytes <= 300
    cache.clear()
    assert cache.bytes == 0 and not cache.data
