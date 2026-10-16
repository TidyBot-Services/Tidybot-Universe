"""Test-wide settings.

Experiment-mode Dev runs must declare reproducible reset and a registered scene contract
(benchmarks.attention_harness.dev_configuration.require_scene_gate). Most tests drive simulated
runners with no physical scene, so the gate is switched off for them; tests that exercise the gate
carry ``@pytest.mark.scene_gate`` and run against the real check.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'scene_gate: run with the real experiment-mode scene gate enabled')


@pytest.fixture(autouse=True)
def _scene_gate_off_unless_marked(request, monkeypatch):
    if request.node.get_closest_marker('scene_gate'):
        return
    import benchmarks.attention_harness.dev_experiment as dev_experiment
    monkeypatch.setattr(dev_experiment, 'require_scene_gate', lambda *args, **kwargs: None)
