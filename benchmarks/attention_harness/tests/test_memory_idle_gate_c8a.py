"""C8a: matrix mode, rather than legacy tier, controls the unconditional gate."""
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.tests.test_memory_idle_gate_c8 import ledger


NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def empty_proc(monkeypatch):
    monkeypatch.setattr(worker, 'Path', lambda p: SimpleNamespace(iterdir=lambda: [])
                        if str(p) == '/proc' else Path(p))


def planned_ledger(root, *, mode, tier=None, receipt='missing', age=7*3600, state='planned'):
    path = ledger(root, state=state, job=None, receipt=receipt)
    with sqlite3.connect(path) as db:
        experiment = json.loads(db.execute('SELECT payload FROM v2_experiments').fetchone()[0])
        experiment['selection'] = {'mode': mode, 'policies': ['reactive_help']}
        if tier is not None:
            experiment['selection']['budget_tier'] = tier
        experiment.pop('started_at')
        experiment['created_at'] = '2026-10-14T00:00:00Z'
        db.execute('UPDATE v2_experiments SET payload=?', (json.dumps(experiment),))
    os.utime(path, (NOW-age,)*2)
    return path


@pytest.mark.parametrize('mode', ['interactive', 'diagnostic', 'probe'])
@pytest.mark.parametrize('tier', [None, 'legacy', 'formal'])
def test_non_matrix_planned_never_started_is_listed(tmp_path, mode, tier):
    path = planned_ledger(tmp_path, mode=mode, tier=tier)
    before = path.read_bytes(), path.stat().st_mtime_ns
    ignored = []
    for _ in range(2):
        assert worker.run_activity([path], now=NOW, ignored_stale_runs=ignored) is None
    assert ignored == [{'id': 'devexp-abandoned', 'reason': 'planned, never started'}]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize('state', ['planned', 'running'])
@pytest.mark.parametrize('tier', [None, 'legacy', 'formal'])
@pytest.mark.parametrize('receipt', ['missing', 'dead', 'live'])
def test_non_dev_matrix_blocks_unconditionally(tmp_path, state, tier, receipt):
    path = planned_ledger(tmp_path, mode='experiment', tier=tier, receipt=receipt, state=state)
    ignored = []
    assert worker.run_activity([path], now=NOW, ignored_stale_runs=ignored) == (
        'The frozen main matrix is planned or running: devexp-abandoned')
    assert ignored == []


@pytest.mark.parametrize('mode,tier', [('experiment', 'dev'), ('interactive', None),
                                      ('diagnostic', 'formal'), ('probe', 'legacy')])
@pytest.mark.parametrize('receipt,age,blocked', [
    ('dead', 0, False), ('dead', 7*3600, False), ('live', 7*3600, True),
    ('missing', 6*3600-1, True), ('missing', 6*3600, False),
])
def test_ordinary_scheduler_rule(tmp_path, mode, tier, receipt, age, blocked):
    path = planned_ledger(tmp_path, mode=mode, tier=tier, receipt=receipt, age=age)
    reason = worker.run_activity([path], now=NOW)
    if blocked:
        assert reason.startswith('Another run is active: devexp-abandoned')
    else:
        assert reason is None
