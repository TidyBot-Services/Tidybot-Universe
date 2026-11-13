"""Physics rows are flushed per row and fsynced once per request (try speed-up 2, user OK 11-14)."""
import json, os, types
import pytest
from openarm365_dev.executor import NativeExecutor


def fake(tmp_path):
    ex = types.SimpleNamespace(journal=tmp_path/'native-actions.jsonl', physics=tmp_path/'native-physics.jsonl',
                               _execution_receipt={}, bimanual_collaboration='none')
    ex.log = NativeExecutor.log.__get__(ex)
    ex.sync_physics = NativeExecutor.sync_physics.__get__(ex)
    return ex


def test_physics_rows_fsync_once_per_request(tmp_path, monkeypatch):
    calls = []
    real = os.fsync
    monkeypatch.setattr(os, 'fsync', lambda fd: (calls.append(os.readlink(f'/proc/self/fd/{fd}')), real(fd)))
    ex = fake(tmp_path)
    for step in range(50):
        ex.log(ex.physics, dict(request_id='r1', physics_step=step))
    assert calls == []                                    # no per-row fsync
    assert len(ex.physics.read_text().splitlines()) == 50  # every row already visible (flushed)
    ex.sync_physics()
    assert calls == [str(ex.physics)]
    ex.sync_physics()                                     # nothing new -> no extra fsync
    assert calls == [str(ex.physics)]
    ex.log(ex.journal, dict(request_id='r1', event='receipt'))
    assert calls == [str(ex.physics), str(ex.journal)]    # action journal stays fsync-per-row


def test_row_content_unchanged(tmp_path):
    ex = fake(tmp_path)
    row = dict(request_id='r', physics_step=3, qpos=[0.1, 0.2])
    ex.log(ex.physics, row)
    assert json.loads(ex.physics.read_text()) == row
    assert ex.physics.read_text() == json.dumps(row, sort_keys=True, allow_nan=False) + '\n'


@pytest.mark.parametrize('module', ['openarm365_dev/worker.py', 'openarm365_dev_v2/worker.py'])
def test_worker_syncs_physics_before_receipt(module):
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2]/module).read_text()
    sync = text.index('executor.sync_physics()')
    receipt = text.index("open('a') as f:", text.index("'rpc-receipts.jsonl'", sync - 400))
    assert sync < receipt
