"""Minimal source-only locks for C22a serialization/Memory tests; no model reads."""
import json

import pytest

from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment


@pytest.fixture
def source_only_lock(monkeypatch):
    from benchmarks.attention_harness import dev_experiment
    from openarm365_dev_v2 import dev_config as d

    def make_lock(path, *, execution_settings=None, **kwargs):
        pin = (dict(path=str(d.LOCK), sha256=d.LOCK_SHA) if execution_settings is None else
               dict(path=execution_settings.planner_deployment['lock_path'],
                    sha256=execution_settings.planner_deployment['sha256']))
        source = d.ROOT / 'openarm365_dev/executor.py'
        lock = dict(schema='attentionbench.dev-identity-lock.v2', source_root=str(d.ROOT),
                    source_sets={}, model_sources={}, files={str(source): d.sha(source)},
                    planner_lock=pin)
        if execution_settings is not None and execution_settings.profile == 'r4-20261027':
            lock['planner_deployment_kind'] = 'dev-local'
        path.write_text(json.dumps(lock, sort_keys=True))
        return dict(path=str(path), sha256=d.sha(path))

    monkeypatch.setattr(d, 'model_sources', lambda *a, **kw: {})
    monkeypatch.setattr(dev_experiment, 'make_lock', make_lock)


@pytest.fixture
def frozen_r3_experiment(monkeypatch, r3_deployment, source_only_lock):
    from benchmarks.attention_harness import dev_experiment
    from openarm365_dev_v2 import dev_config as d
    # Reconstruct the frozen registered-r3 path, independent of new-run defaults.
    monkeypatch.setattr(dev_experiment, 'new_execution_settings', lambda **kw: r3_deployment)
    monkeypatch.setattr(dev_experiment, 'make_dev_config', d._make_dev_config)
