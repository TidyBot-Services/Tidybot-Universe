"""C6 R3 reseed context coverage outside the frozen deployment-06 manifest."""

import json

import pytest
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment

from benchmarks.attention_harness.tests.test_reseed_test_notes import (
    NOTES,
    FakeMemory,
    context,
)
from scripts import reseed_test_notes as command


@pytest.fixture
def configured_checkout(tmp_path, monkeypatch, base, r4_deployment):
    # The real child builds locks/context against an authenticated synthetic
    # deployment. Native model inputs are explicit placeholders in this test.
    from curobo_v2 import identity as auth, dev_local as dl, execution_settings as es
    bootstrap = """
import json
from pathlib import Path
from scripts.c21_test_guard import pytest_configure
pytest_configure(None)
from curobo_v2 import identity as auth, dev_local as dl, execution_settings as es
from openarm365_dev_v2 import dev_config as d, environment
from benchmarks.attention_harness import dev_planner_memory as memory
"""
    bootstrap += f"auth.TRUSTED_LOCKS = {auth.TRUSTED_LOCKS!r}\n"
    bootstrap += f"auth.OFFICIAL_PARENT_LOCK = Path({str(auth.OFFICIAL_PARENT_LOCK)!r})\n"
    bootstrap += f"auth.DEPLOYMENT_REGISTRY = Path({str(auth.DEPLOYMENT_REGISTRY)!r})\n"
    bootstrap += f"dl.DEV_LOCAL_ROOT = Path({str(dl.DEV_LOCAL_ROOT)!r})\n"
    bootstrap += f"es.D06_LOCK = {es.D06_LOCK!r}\n"
    bootstrap += f"dl.materialize = lambda source: {dict(r4_deployment.planner_deployment)!r}\n"
    bootstrap += """
d.model_sources = lambda *a, **kw: {}
environment.initial_joints = lambda task: {'left': [0.]*7, 'right': [0.]*7}
original_sha = d.sha
def synthetic_sha(path):
    name = str(path)
    if '/openarm365/model/' in name or name.endswith('/openarm365/configs/robot_parameters_v1.json'):
        return 'c21-offline-native-placeholder'
    return original_sha(path)
d.sha = memory.sha = synthetic_sha
"""
    monkeypatch.setattr(command, '_BUILD_CONTEXT', bootstrap + command._BUILD_CONTEXT)
    root = tmp_path / 'experiments'
    lock = root / 'devexp-test/artifacts/devexp-test/identity-lock.json'
    lock.parent.mkdir(parents=True)
    lock.write_text(json.dumps({'source_root': str(command.HERE.parent)}))
    return root, context()['namespace']


def test_current_identity_is_deterministic_against_configured_checkout(configured_checkout):
    root, namespace = configured_checkout
    first = command.current_context(root, namespace)
    second = command.current_context(root, namespace)
    assert first == second
    assert len(first['sdk_sha256']) == 64
    assert first['execution_profile'] == 'r4-20261027'


@pytest.mark.parametrize('ready', [False, True])
def test_check_current_tree_child_with_fake_service(configured_checkout, monkeypatch, capsys, ready):
    gateway = FakeMemory()
    built = []
    build = command.current_context

    def current_context(root, namespace):
        value = build(root, namespace)  # Real child, imports and lock validation.
        built.append(value)
        if ready:
            for note in NOTES:
                note_id = f"t-{command.identity(value)[1][:8]}-{note['key']}"
                gateway.rows[note_id] = dict(memory_id=note_id, title=note['title'],
                    seeded_for_tests=True, status='trusted', applicability_summary=value)
        return value

    monkeypatch.setattr(command, 'service_settings', lambda: configured_checkout)
    monkeypatch.setattr(command, 'current_context', current_context)
    monkeypatch.setattr(command, 'keys', lambda _: {})  # Never read operator credentials.
    monkeypatch.setattr(command, 'MemoryHTTP', lambda *_: gateway)
    assert command.main(['--check']) == (0 if ready else 1)
    assert built[0]['execution_profile'] == 'r4-20261027'
    assert gateway.writes == []
    captured = capsys.readouterr()
    assert not captured.err
    assert ('Ready' if ready else 'Needs reseed: 0 old active, 5 missing') in captured.out


def test_child_error_hides_paths_and_secrets(configured_checkout, monkeypatch, capsys):
    monkeypatch.setattr(command, 'service_settings', lambda: configured_checkout)
    monkeypatch.setattr(command, '_BUILD_CONTEXT',
        "import sys; print('/private/path stdout-secret'); "
        "sys.exit('/private/path stderr-secret')")
    assert command.main(['--check']) == 2
    captured = capsys.readouterr()
    assert captured.out == ''
    assert captured.err == ('Error: Could not build the current Dev SDK context '
                            'from the configured checkout\n')
