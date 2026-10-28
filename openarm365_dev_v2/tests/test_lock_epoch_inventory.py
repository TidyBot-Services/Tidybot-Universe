"""Cross-root identity verification must use the frozen epoch's inventory."""
import json

import pytest

from openarm365_dev_v2 import dev_config as d


def test_verify_lock_uses_epoch_inventory_and_rejects_drift(tmp_path, monkeypatch):
    epoch = tmp_path / 'epoch'
    identity = epoch / 'openarm365/model/identity.json'
    identity.parent.mkdir(parents=True)
    resource = epoch / 'fixture-resource'
    resource.write_text('frozen fixture')
    models = {str(resource): d.sha(resource)}
    identity.write_text(json.dumps({'sources': models}))
    planner = tmp_path / 'planner.json'
    planner.write_text('{}')
    monkeypatch.setattr(d, 'ROOT', tmp_path / 'unrelated-checkout')
    monkeypatch.setattr(d, 'LOCK', planner)
    monkeypatch.setattr(d, 'LOCK_SHA', d.sha(planner))
    lock = dict(schema='attentionbench.dev-identity-lock.v2', source_root=str(epoch),
                source_sets={}, model_sources=models,
                files={**models, str(identity): d.sha(identity)},
                planner_lock=dict(path=str(planner), sha256=d.sha(planner)))
    path = tmp_path / 'lock.json'
    path.write_text(json.dumps(lock))
    ref = dict(path=str(path), sha256=d.sha(path))
    assert d.verify_lock(ref, expected_source_root=epoch) == lock
    resource.write_text('drifted fixture')
    with pytest.raises(ValueError, match='native robot model identity drift'):
        d.verify_lock(ref, expected_source_root=epoch)
