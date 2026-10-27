"""Opt-in offline pytest audit guard for the C21 verification command."""
import os
import sys


def pytest_configure(config):
    def audit(event, args):
        if event == 'socket.connect':
            raise RuntimeError('C21 offline test prohibits live connections')
        if event == 'subprocess.Popen' and any(word in str(args[1]) for word in ('systemctl', 'openarm365_dev_v2.worker', 'curobo_service.server')):
            raise RuntimeError('C21 offline test prohibits native/service processes')
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            path = os.fsdecode(args[0]).replace('\\', '/')
            if any(part in path for part in ('/memory-m3/', '/openarm365/model/',
                    '/openarm_mobile/model/', '/openarm365/configs/robot_parameters_v1.json', '/keys.env', '/systemd/')):
                raise RuntimeError('C21 offline test prohibited file: ' + path)
    sys.addaudithook(audit)


import pytest


@pytest.fixture(autouse=True)
def synthetic_initial_joints(monkeypatch):
    # Config/serialization tests do not need robot model data. Both historical
    # and current constructors receive the same synthetic reset targets.
    from openarm365_dev_v2 import environment
    monkeypatch.setattr(environment, 'initial_joints',
                        lambda task: {'left': [0.] * 7, 'right': [0.] * 7})


@pytest.fixture(autouse=True)
def only_synthetic_snapshots(monkeypatch):
    from pathlib import Path
    from curobo_v2 import dev_local
    materialize = dev_local.materialize
    def isolated(source):
        if not Path(source).resolve().is_relative_to('/tmp'):
            raise RuntimeError('C21 tests require a synthetic /tmp snapshot source')
        return materialize(source)
    monkeypatch.setattr(dev_local, 'materialize', isolated)


@pytest.fixture(autouse=True)
def synthetic_lock_inputs(monkeypatch):
    # Source/identity contract tests still hash all allowed code and manifests.
    # The prohibited native resources are represented by explicit test digests.
    from openarm365_dev_v2 import dev_config
    original = dev_config.sha
    def digest(path):
        name = str(path)
        if '/openarm365/model/' in name or name.endswith('/openarm365/configs/robot_parameters_v1.json'):
            return 'c21-offline-native-placeholder'
        return original(path)
    monkeypatch.setattr(dev_config, 'sha', digest)
    monkeypatch.setattr(dev_config, 'model_sources', lambda *a, **kw: {})
    from benchmarks.attention_harness import dev_planner_memory
    monkeypatch.setattr(dev_planner_memory, 'sha', digest)
