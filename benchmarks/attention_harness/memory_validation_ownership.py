"""Durable experiment ownership, separate from the worker's transition snapshot."""
import hashlib
import json
import sqlite3
from pathlib import Path

from .core.dev_store import DevConflict


def owned_experiments(root):
    path = Path(root) / 'owned-experiments.json'
    value = json.loads(path.read_bytes()) if path.exists() else {}
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        raise DevConflict('Invalid worker experiment ownership record')
    return value


def record_experiment(root, operation, experiment_id):
    # Called under the worker lock, after successful create and before provision
    # returns. A conflicting create must never acquire ownership of a foreign run.
    from .memory_validation_worker import atomic
    owned = owned_experiments(root)
    if operation in owned and owned[operation] != experiment_id:
        raise DevConflict('Validation operation already owns another experiment')
    owned[operation] = experiment_id
    atomic(Path(root) / 'owned-experiments.json', owned)


def recover_pending(root, pending):
    """Explicit retry migration for pre-C13 pending records; never infer by marker alone."""
    from .dev_v2_loop import checked
    note, operation = pending['note'], pending['operation']
    expected = hashlib.sha256(f"{note['memory_id']}:{note['version']}:{note['uses']}".encode()).hexdigest()[:24]
    if operation != expected:
        raise DevConflict('Pending validation operation differs from the frozen note')
    config = json.loads((Path(root) / 'runtime.json').read_bytes())
    request = 'validation-' + operation
    eid = 'devexp-' + hashlib.sha256(request.encode()).hexdigest()[:24]
    ledger = Path(config['experiments_root']) / eid / 'ledger.sqlite3'
    if not ledger.exists():
        return
    with sqlite3.connect(ledger.resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute('SELECT payload FROM v2_experiments WHERE id=?', (eid,)).fetchone()
    if row is None:
        raise DevConflict('Pending validation experiment is incomplete')
    experiment = json.loads(row[0])
    manifest = json.loads(checked(experiment['manifest']).read_bytes())
    origin = manifest.get('validation_origin', {})
    summary = note['source_summary']
    if (experiment.get('validation_experiment') is not True
            or experiment['selection'].get('request_key') != request
            or experiment['selection'].get('budget_tier') != 'dev'
            or manifest.get('validation_experiment') is not True
            or manifest.get('experiment_id') != eid
            or origin.get('cell') != summary['cell']
            or origin.get('version') != summary['version']
            or json.loads(checked(origin['manifest']).read_bytes())['experiment_id'] != summary['experiment_id']):
        raise DevConflict('Pending validation experiment differs from the frozen origin')
    record_experiment(root, operation, eid)
    return True
