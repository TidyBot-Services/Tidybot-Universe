"""C13: real in-process catalog/import semantics, synthetic execution only."""
import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.memory_validation_ownership import owned_experiments
from benchmarks.attention_harness.tests.autovalidate_fakes import ProvisionedFakeRunner, synthetic_creation
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.test_memory_validation_worker import deps

pytestmark = pytest.mark.usefixtures('r3_deployment')


def provisioned(tmp_path):
    root = tmp_path / 'worker'
    fake = ProvisionedFakeRunner(tmp_path / 'evidence', root)
    d = fake.deps(root)
    worker.set_enabled(root, True)
    return fake, d


def test_ownership_persisted_before_provision_returns_and_gate_with_null_current(tmp_path, monkeypatch):
    from benchmarks.attention_harness import memory_validation_runtime as runtime
    from benchmarks.attention_harness.memory_service_client import MemoryServiceClient
    fake, d = provisioned(tmp_path)
    note = fake.service.validation_queue()[0]
    with synthetic_creation(fake.f):
        batch = fake.provision(note, 'operation-001')
    assert owned_experiments(d.root)['operation-001'] == batch['experiment_id']
    ledger = next(iter(batch['cells'].values()))['ledger']
    assert 'Another run is active' in worker.run_activity([ledger])
    worker.atomic(d.root / 'runtime.json', dict(base_url='http://unused', key_file='unused',
        experiments_root=str(fake.f.root), validator_root=str(fake.f.validator),
        activity_ledger_roots=[str(Path(ledger).parent)], activity_ledgers=[]))
    monkeypatch.setattr(MemoryServiceClient, 'offline', lambda *a, **kw: fake.service)
    runtime_deps = runtime.build_runtime(d.root)
    try:
        assert runtime_deps.activity(None) is None
        # Marker alone, ownership alone, or a mismatched operation never exempts.
        worker.atomic(d.root / 'owned-experiments.json', {})
        assert batch['experiment_id'] in runtime_deps.activity(None)
        worker.atomic(d.root / 'owned-experiments.json', {'different-operation': batch['experiment_id']})
        assert batch['experiment_id'] in runtime_deps.activity(None)
        worker.atomic(d.root / 'owned-experiments.json', {'operation-001': batch['experiment_id']})
        with sqlite3.connect(ledger) as db:
            e = json.loads(db.execute('SELECT payload FROM v2_experiments').fetchone()[0])
            e.pop('validation_experiment')
            db.execute('UPDATE v2_experiments SET payload=?', (json.dumps(e),))
        assert batch['experiment_id'] in runtime_deps.activity(None)
    finally:
        runtime_deps.runner.pool.shutdown()


def test_fifth_registration_failure_retries_four_identical_catalogs(tmp_path, monkeypatch, capsys):
    fake, d = provisioned(tmp_path)
    catalogs = fake.provision.catalogs
    def five_catalogs(note):
        result = catalogs(note)
        result['extra-validator'] = copy.deepcopy(result['validator-failed'])
        return result
    monkeypatch.setattr(fake.provision, 'catalogs', five_catalogs)
    original = fake.register
    calls = []
    def register(cid, catalog):
        calls.append((cid, copy.deepcopy(catalog)))
        if len(calls) == 5:
            raise ValueError('catalog outside configured roots for its role')
        return original(cid, catalog)
    monkeypatch.setattr(fake, 'register', register)
    state = {}
    worker.tick(state, d)
    current = state['current']
    assert current['kind'] == 'begin' and 'outside configured roots' in current['begin_error']
    assert len(calls) == 5 and not fake.starts and state['runs_today'] == 0
    operation = current['operation']
    eid = owned_experiments(d.root)[operation]
    assert not (d.root / operation / 'batch.json').exists()
    ledgers = list(fake.f.root.glob('devexp-*/ledger.sqlite3'))
    assert len(ledgers) == 1
    assert worker.run_activity(ledgers, owned_experiments=owned_experiments(d.root)) is None
    for _ in range(3):
        worker.tick(state, d)
    assert len(calls) == 5
    view = automatic_checks(d.root)
    assert view['current']['stage'] == 'begin' and view['current']['paused']
    assert any('outside configured roots' in e for e in view['errors'])
    # The CLI is a mailbox, including when the live worker owns the lock/Off.
    import fcntl
    worker.set_enabled(d.root, False)
    with (d.root / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert worker.main(['--state-dir', str(d.root), '--retry-current']) == 0
    assert 'retry requested' in capsys.readouterr().out
    worker.tick(state, d)
    assert len(calls) == 5 and not worker.settings(d.root)['enabled']
    assert any('outside configured roots' in e for e in automatic_checks(d.root)['errors'])
    worker.set_enabled(d.root, True)
    worker.tick(state, d)
    assert calls[5:9] == calls[:4]
    assert state['current']['experiment_id'] == eid
    assert (d.root / operation / 'batch.json').exists()
    assert len(fake.starts) == 1 and state['runs_today'] == 1
    assert not automatic_checks(d.root)['errors']
    count = len(calls)
    worker.tick(state, d)
    assert len(calls) == count and len(fake.starts) == 1


def test_new_cycle_attempts_once_and_all_gates_remain(tmp_path):
    d = deps(tmp_path)
    worker.set_enabled(tmp_path, True)
    calls = []
    def fail(note, operation):
        calls.append(operation)
        raise ValueError('registration refused')
    d.runner.begin = fail
    state = {}
    worker.tick(state, d)
    for _ in range(4):worker.tick(state, d)
    assert len(calls) == 1
    # New adapter/process cycle; a busy gate delays its one attempt.
    restarted = deps(tmp_path)
    restarted.runner.begin = fail
    restarted.activity = lambda c: 'Another run is active: foreign'
    state = json.loads((tmp_path / 'state.json').read_bytes())
    worker.tick(state, restarted)
    assert len(calls) == 1
    restarted.activity = lambda c: None
    worker.tick(state, restarted)
    worker.tick(state, restarted)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert worker.main(['--state-dir', str(tmp_path), '--retry-current']) == 0
    worker.tick(state, restarted)
    worker.tick(state, restarted)
    assert len(calls) == 3


@pytest.mark.parametrize('failure_point', ['import', 'candidate', 'plan'])
def test_interrupted_import_and_candidate_are_idempotent(tmp_path, monkeypatch, failure_point):
    fake, d = provisioned(tmp_path)
    method = {'import': 'import_dev_v2_source', 'candidate': 'create_dev_v2_candidate', 'plan': 'record_plan'}[failure_point]
    original = getattr(fake.service, method)
    calls = []
    def interrupt(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 1:raise ValueError('lost response after durable write')
        return result
    monkeypatch.setattr(fake.service, method, interrupt)
    state = {}
    worker.tick(state, d)
    assert state['current']['kind'] == 'begin' and not fake.starts
    assert worker.main(['--state-dir', str(d.root), '--retry-current']) == 0
    worker.tick(state, d)
    assert len(calls) == 2 and len(fake.starts) == 1
    assert state['current']['active']


def test_same_catalog_id_different_valid_digest_is_rejected(tmp_path):
    fake, _ = provisioned(tmp_path)
    file = fake.f.root / 'digest.json'
    file.write_text('{}')
    catalog = dict(role='executor', root=str(fake.f.root), context=fake.failed['context'],
                   files={file.name: hashlib.sha256(file.read_bytes()).hexdigest()})
    fake.register('conflict', catalog)
    fake.register('conflict', copy.deepcopy(catalog))
    changed = copy.deepcopy(catalog)
    # Both catalogs validate on disk, but the immutable ID cannot bind new bytes.
    file.write_text('{"different": true}')
    changed['files'][file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='immutable import/catalog ID conflict'):
        fake.register('conflict', changed)


@pytest.mark.parametrize('tamper', [False, True])
def test_cli_recovers_legacy_null_current_only_for_exact_pending_origin(tmp_path, tamper):
    fake, d = provisioned(tmp_path)
    note = fake.service.validation_queue()[0]
    operation = hashlib.sha256(f"{note['memory_id']}:{note['version']}:{note['uses']}".encode()).hexdigest()[:24]
    with synthetic_creation(fake.f):batch = fake.provision(note, operation)
    (d.root / 'owned-experiments.json').unlink()  # pre-C13 deployed state
    worker.atomic(d.root / 'runtime.json', dict(experiments_root=str(fake.f.root)))
    if tamper:note['source_summary']['cell'] = 'foreign-origin'
    state = dict(current=None, pending=dict(note=note, operation=operation))
    worker.atomic(d.root / 'state.json', state)
    worker.set_enabled(d.root, False)
    assert worker.main(['--state-dir', str(d.root), '--retry-current']) == 0
    worker.tick(state, d)
    if tamper:
        assert 'differs from the frozen origin' in state['status']
        assert not owned_experiments(d.root) and not fake.starts
    else:
        assert state['current']['kind'] == 'begin' and not fake.starts
        assert owned_experiments(d.root)[operation] == batch['experiment_id']
        worker.set_enabled(d.root, True)
        worker.tick(state, d)
        assert state['current']['experiment_id'] == batch['experiment_id'] and len(fake.starts) == 1


def test_retry_cli_rejects_active_arm_and_stale_request_cannot_retry_next_item(tmp_path, capsys):
    d = deps(tmp_path)
    worker.set_enabled(tmp_path, True)
    worker.atomic(tmp_path / 'retry-current.json', dict(operation='old-operation', request_id='old-request'))
    state = {}
    worker.tick(state, d)
    assert d.runner.started == ['c0']
    assert worker.main(['--state-dir', str(tmp_path), '--retry-current']) == 1
    assert 'No interrupted begin' in capsys.readouterr().out
