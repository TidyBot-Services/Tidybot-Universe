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
    snapshot = dev_local.materialize(base.repo)
    settings = es.from_config(replace(es.R4_20261027,
        planner_deployment={k: snapshot[k] for k in ('lock_path', 'sha256')}).to_dict())
    def materialize(source):
        assert str(source) in (str(dev_config.ROOT), str(base.repo))
        return dict(snapshot)
    monkeypatch.setattr(dev_local, 'materialize', materialize)
    return settings
