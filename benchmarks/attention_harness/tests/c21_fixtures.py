"""Small authenticated R4 snapshot for offline new-run tests.

Keep materialization tests on real synthetic Git trees; never copy the production
snapshot or invoke Git through an unrelated daily subprocess mock.
"""
from dataclasses import replace
import pytest
from curobo_v2 import dev_local, execution_settings as es
from openarm365_dev_v2 import dev_config
from benchmarks.attention_harness.tests.test_dev_local_deployment import base


@pytest.fixture
def r4_deployment(base, monkeypatch):
    # This fixture deliberately has no planner runtime modules; applicability
    # derivation itself is exercised by test_planner_behavior's closed runtime.
    from curobo_v2 import planner_behavior
    monkeypatch.setattr(planner_behavior, 'from_config', lambda config: 'c'*64)
    snapshot = dev_local.materialize(base.repo)
    settings = es.from_config(replace(es.R4_20261027,
        planner_deployment={k: snapshot[k] for k in ('lock_path', 'sha256')}).to_dict())
    def materialize(source):
        assert str(source) in (str(dev_config.ROOT), str(base.repo))
        return dict(snapshot)
    monkeypatch.setattr(dev_local, 'materialize', materialize)
    return settings


@pytest.fixture
def offline_dev_identity(monkeypatch, r4_deployment):
    """Synthetic model identity for routing tests; never open installed models."""
    from pathlib import Path
    import hashlib
    original = dev_config.sha
    identity = dev_config.ROOT / 'openarm365/model/identity.json'
    monkeypatch.setattr(dev_config, 'model_sources', lambda *a, **kw: {})
    monkeypatch.setattr(dev_config, 'sha', lambda path: hashlib.sha256(b'{}').hexdigest()
                        if Path(path) == identity else original(path))
