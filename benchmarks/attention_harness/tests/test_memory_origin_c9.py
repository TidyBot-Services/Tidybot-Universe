"""C9 preparation recovery: synthetic evidence only; no HTTP or simulator."""
import fcntl
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from benchmarks.attention_harness import memory_origin_evidence as origin
from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.tests.autovalidate_fakes import AutomaticOriginFakeRunner, synthetic_creation
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment

pytestmark = pytest.mark.usefixtures('r3_deployment')


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def fake(tmp_path):
    fake = AutomaticOriginFakeRunner(tmp_path/'fake', tmp_path/'worker')
    fake.deps(fake.provision.state_root)
    worker.atomic(fake.provision.state_root/'runtime.json', dict(
        fake.provision.config, validator_root=str(fake.f.validator)))
    return fake


@contextmanager
def synthetic_export(fake):
    from benchmarks.attention_harness import dev_planner_memory as memory
    with synthetic_creation(fake.f), patch.object(memory, 'context', lambda *a, **kw: fake.failed['context']), patch.object(memory, 'sdk_identity', lambda *a: fake.f.sdk):
        yield


def error_receipt(fake, monkeypatch):
    deps = fake.deps(fake.provision.state_root)
    worker.set_enabled(deps.root, True)
    state = {}
    with monkeypatch.context() as m:
        m.setattr(origin, 'locate', lambda *a: (_ for _ in ()).throw(RuntimeError('Controlled export bug')))
        worker.tick(state, deps)
        worker.tick(state, deps)
    return state, deps


def old_failure(fake):
    note = fake.service.validation_queue()[0]
    current = fake.origin_needed(note)
    directory = fake.origin_runner.origins.directory(current)
    receipt = dict(status='refused', reason='Could not finish: no such table: v2_planner_requests',
                   catalogs={}, origin_key=current['origin_key'], note=current['note'])
    worker.atomic(directory/'receipt.json', receipt)
    worker.atomic(directory/'dispatch.json', {'origin_key': current['origin_key']})
    state = dict(runs_today=2, counted={current['origin_key']: 'old-day'},
                 results=[dict(memory_id=fake.mid, title='Note', status='inconclusive', origin_failure=receipt)],
                 origin_failures={current['origin_key']: receipt}, current=None)
    worker.atomic(fake.provision.state_root/'state.json', state)
    return directory, receipt


def test_missing_planner_is_empty_and_does_not_initialize(tmp_path):
    path = tmp_path/'ledger.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE v2_cells(id TEXT,payload TEXT)')
    before = path.read_bytes()
    assert origin.OriginalStore(path).planner_snapshot('cell') == {'requests': []}
    assert path.read_bytes() == before
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE v2_planner_requests(id TEXT,cell TEXT)')
    with pytest.raises(DevConflict, match='missing required evidence fields'):
        origin.OriginalStore(path).planner_snapshot('cell')


def test_unexpected_preparation_error_is_queued_and_visible(fake, monkeypatch):
    from benchmarks.attention_harness.memory_monitor import automatic_checks
    state, deps = error_receipt(fake, monkeypatch)
    key = next(iter(state['origin_errors']))
    receipt = json.loads((deps.root/'origins'/key/'receipt.json').read_bytes())
    assert receipt['status'] == 'error' and receipt['run_reservation'] == 'counted'
    assert 'reserved run remains counted' in receipt['reason']
    assert not state.get('results') and not state.get('origin_failures')
    assert state['pending']['note']['memory_id'] == fake.mid
    assert [n['memory_id'] for n in state['queue']] == [fake.mid]
    worker.tick(state, deps)
    assert state['runs_today'] == 1 and fake.replays == 0 and not fake.starts
    worker.set_enabled(deps.root, False)
    view = automatic_checks(deps.root, fake.service)
    assert view['errors'] and 'Controlled export bug' in view['errors'][0]
    assert not view['results'] and view['queue'][0]['memory_id'] == fake.mid


@pytest.mark.parametrize('marker', ['validation', 'validation_file', 'passed', 'candidate', 'linked_candidate'])
def test_reopen_refuses_verdicts_and_candidates(fake, marker, monkeypatch):
    directory, receipt = old_failure(fake)
    if marker == 'validation':
        receipt['validation'] = {'uri': 'sealed', 'sha256': 'a'*64}
    elif marker == 'validation_file':
        worker.atomic(directory/'validation.json', {'status': 'refused'})
    elif marker == 'passed':
        receipt['status'] = 'passed'
    else:
        rows = fake.service.list_dev_v2_memories()
        if marker == 'candidate': rows[0]['status'] = 'candidate'
        else: rows.append(dict(memory_id='candidate', status='rejected', from_provisional=fake.mid))
        monkeypatch.setattr(fake.service, 'list_dev_v2_memories', lambda: rows)
    worker.atomic(directory/'receipt.json', receipt)
    before = snapshot(fake.provision.state_root)
    with pytest.raises(DevConflict):
        worker.reopen_origin(fake.provision.state_root, fake.mid, 'C9 fixed', client=fake.service)
    after = snapshot(fake.provision.state_root)
    after.pop('worker.lock', None)
    assert after == before


def test_reopen_archives_requeues_preserves_count_and_reserves_again(fake):
    directory, receipt = old_failure(fake)
    original = snapshot(directory)
    service_before = fake.service.store.path.read_bytes()
    root = fake.provision.state_root
    assert worker.main(['--state-dir', str(root), '--reopen-origin', fake.mid, '--reason', 'C9 optional-table fix'], client=fake.service) == 0
    archives = list((root/'origins-archive').iterdir())
    assert len(archives) == 1 and snapshot(archives[0]) == original and not directory.exists()
    state = json.loads((root/'state.json').read_bytes())
    event = state['origin_reopens'][0]
    assert event['actor'] and isinstance(event['uid'], int) and event['at'] > 0
    assert event['reason'] == 'C9 optional-table fix' and event['status'] == 'reopened'
    assert state['runs_today'] == 2 and state['counted'][archives[0].name] == 'old-day'
    assert not state['results'] and not state['origin_failures'] and state['queue'][0]['memory_id'] == fake.mid
    assert fake.service.store.path.read_bytes() == service_before
    # A new attempt consumes a fresh slot; the two old reservations stay counted.
    from datetime import datetime
    deps = fake.deps(root); worker.set_enabled(root, True)
    state['day'] = datetime.fromtimestamp(deps.clock()).astimezone().date().isoformat()
    worker.tick(state, deps)
    assert state['runs_today'] == 3 and fake.replays == 2


def test_reopen_error_and_unknown_worker_are_safe(fake, monkeypatch):
    state, deps = error_receipt(fake, monkeypatch)
    with (deps.root/'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(DevConflict, match='still holds its lock'):
            worker.reopen_origin(deps.root, fake.mid, 'fixed', client=fake.service)
    worker.reopen_origin(deps.root, fake.mid, 'fixed', client=fake.service)
    state = json.loads((deps.root/'state.json').read_bytes())
    assert not state['origin_errors'] and not state.get('pending') and state['runs_today'] == 1


def no_planner_visual_origin(fake):
    """Synthetic visual-help branch, which has no planner adoption request."""
    ledger = Path(fake.failed['descriptor']['ledger']['uri'])
    with sqlite3.connect(ledger) as db:
        record = json.loads(db.execute('SELECT payload FROM v2_versions WHERE version=2').fetchone()[0])
        help_ref = record['help']; value = json.loads(Path(help_ref['uri']).read_bytes())
        value['provider'] = 'human_proxy'
        record['help'] = fake.f.write(Path(help_ref['uri']), value)
        fake.f.write(fake.f.root/'failed/v2/version.json', record)
        db.execute('UPDATE v2_versions SET payload=? WHERE version=2', (json.dumps(record),))
        db.execute('DROP TABLE v2_planner_requests')
    provenance = fake.service.provenance(fake.mid)
    provenance['origin']['digests']['help'] = record['help']['sha256']
    return SimpleNamespace(provenance=lambda mid: provenance)


@pytest.mark.parametrize('without_planner', [False, True])
def test_dry_run_exports_checks_but_never_writes_state_or_replays(fake, without_planner, monkeypatch, capsys):
    client = no_planner_visual_origin(fake) if without_planner else fake.service
    root = fake.provision.state_root
    before = snapshot(root); history = snapshot(fake.f.root); validator = snapshot(fake.f.validator)
    def forbidden(*a, **kw): raise AssertionError('dry-run tried to use replay or GPU')
    from benchmarks.attention_harness import memory_independent_validator as validator_module
    monkeypatch.setattr(validator_module, 'GpuMeter', forbidden)
    monkeypatch.setattr(validator_module, 'replay_fresh', forbidden)
    # Prove writes were never attempted under state_root, not merely cleaned up.
    original_open = Path.open
    def guarded_open(path, mode='r', *a, **kw):
        if path.is_relative_to(root) and any(c in mode for c in 'wax+'):
            raise AssertionError('dry-run state write')
        return original_open(path, mode, *a, **kw)
    monkeypatch.setattr(Path, 'open', guarded_open)
    with synthetic_export(fake):
        assert worker.main(['--state-dir', str(root), '--check-origin', fake.mid], client=client) == 0
    assert capsys.readouterr().out.startswith('PASS:')
    assert snapshot(root) == before and snapshot(fake.f.root) == history and snapshot(fake.f.validator) == validator
    assert fake.replays == 0 and not fake.starts


def test_dry_run_reports_first_problem_without_writes(fake, capsys):
    root = fake.provision.state_root
    Path(fake.failed['record']['source']['uri']).write_text('tampered')
    before = snapshot(root); history = snapshot(fake.f.root)
    with synthetic_export(fake):
        assert worker.main(['--state-dir', str(root), '--check-origin', fake.mid], client=fake.service) == 1
    out = capsys.readouterr().out
    assert out.startswith('FAIL:') and 'digest mismatch' in out and out.count('\n') == 1
    assert snapshot(root) == before and snapshot(fake.f.root) == history


def test_missing_advisor_adoption_is_plain_refusal(fake, capsys):
    with sqlite3.connect(fake.failed['descriptor']['ledger']['uri']) as db:
        db.execute('DROP TABLE v2_planner_requests')
    before = snapshot(fake.provision.state_root)
    assert worker.main(['--state-dir', str(fake.provision.state_root), '--check-origin', fake.mid], client=fake.service) == 1
    out = capsys.readouterr().out
    assert 'Original adopted help request is missing or ambiguous' in out and 'no such table' not in out
    assert snapshot(fake.provision.state_root) == before


def test_dry_run_does_not_create_missing_state_root(tmp_path, capsys):
    root = tmp_path/'missing'
    assert worker.main(['--state-dir', str(root), '--check-origin', 'note'], client=object()) == 1
    assert not root.exists() and capsys.readouterr().out.startswith('FAIL:')


def test_reopen_recovers_after_archive_before_state_save(fake, monkeypatch):
    directory, _ = old_failure(fake)
    before = snapshot(directory)
    root = fake.provision.state_root
    original_atomic = worker.atomic
    def crash(path, value):
        if path == root/'state.json' and value.get('origin_reopens', [{}])[-1].get('status') == 'reopened':
            raise OSError('Controlled crash after archive')
        return original_atomic(path, value)
    with monkeypatch.context() as m:
        m.setattr(worker, 'atomic', crash)
        with pytest.raises(OSError, match='Controlled crash'):
            worker.reopen_origin(root, fake.mid, 'original reason', client=fake.service)
    assert not directory.exists()
    worker.reopen_origin(root, fake.mid, 'resume', client=fake.service)
    archives = list((root/'origins-archive').iterdir())
    state = json.loads((root/'state.json').read_bytes())
    assert len(archives) == 1 and snapshot(archives[0]) == before
    assert len(state['origin_reopens']) == 1 and state['origin_reopens'][0]['reason'] == 'original reason'
    assert state['origin_reopens'][0]['status'] == 'reopened' and state['runs_today'] == 2
    assert not state['results'] and state['queue'][0]['memory_id'] == fake.mid


def test_dry_run_redacts_injected_capabilities(fake, capsys):
    client = SimpleNamespace(api_key='short-secret', importer_key='other-secret')
    def broken(mid): raise RuntimeError('bad response short-secret other-secret')
    client.provenance = broken
    assert worker.main(['--state-dir', str(fake.provision.state_root), '--check-origin', fake.mid], client=client) == 1
    output = capsys.readouterr().out
    assert 'short-secret' not in output and 'other-secret' not in output and 'redacted' in output


def test_dry_run_failure_after_export_removes_scratch(fake, monkeypatch, capsys):
    before = snapshot(fake.provision.state_root); history = snapshot(fake.f.root)
    monkeypatch.setattr(origin, 'validate_origin', lambda *a, **kw: dict(status='refused', reason='Controlled binding mismatch'))
    with synthetic_export(fake):
        assert worker.main(['--state-dir', str(fake.provision.state_root), '--check-origin', fake.mid], client=fake.service) == 1
    assert 'Controlled binding mismatch' in capsys.readouterr().out
    assert snapshot(fake.provision.state_root) == before and snapshot(fake.f.root) == history
