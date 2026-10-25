"""C6 R3 reseed context coverage outside the frozen deployment-06 manifest."""

import json

import pytest

from benchmarks.attention_harness.tests.test_reseed_test_notes import (
    NOTES,
    FakeMemory,
    context,
)
from scripts import reseed_test_notes as command


@pytest.fixture
def configured_checkout(tmp_path):
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
    assert first['execution_profile'] == 'r3-20261025'


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
    assert built[0]['execution_profile'] == 'r3-20261025'
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
