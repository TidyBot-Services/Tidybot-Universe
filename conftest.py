"""Test-wide settings for simulated Dev runs and explicit gate coverage."""
import pytest


def pytest_configure(config):
    config.addinivalue_line('markers', 'scene_gate: run with the real experiment-mode scene gate enabled')
    config.addinivalue_line('markers', 'eval_gate: run with the real experiment-mode Eval gate enabled')


@pytest.fixture(autouse=True)
def _scene_gate_off_unless_marked(request, monkeypatch):
    if request.node.get_closest_marker('scene_gate'):
        return
    import benchmarks.attention_harness.dev_experiment as dev_experiment
    monkeypatch.setattr(dev_experiment, 'require_scene_gate', lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def _eval_gate_off_unless_marked(request, monkeypatch):
    if request.node.get_closest_marker('eval_gate'):
        return
    from benchmarks.attention_harness import dev_configuration, dev_experiment
    monkeypatch.setattr(dev_configuration, 'require_eval_gate', lambda *args, **kwargs: None)
    monkeypatch.setattr(dev_experiment, 'require_eval_gate', lambda *args, **kwargs: None)
