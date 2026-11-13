"""Try speed-up item 4: dev-tier approval cache keyed by every trust input (no model reads)."""
import json

import pytest

import curobo_v2.identity as identity


@pytest.fixture
def env(tmp_path, monkeypatch):
    registry = tmp_path / 'trusted_deployments.json'; registry.write_text('{}')
    parent = tmp_path / 'parent-lock.json'; parent.write_text('{"p": 1}')
    lock_path = tmp_path / 'runtime-lock.json'; lock_path.write_text('{"x": 1}')
    monkeypatch.setattr(identity, 'DEPLOYMENT_REGISTRY', registry)
    monkeypatch.setattr(identity, 'OFFICIAL_PARENT_LOCK', parent)
    monkeypatch.setattr(identity, '_local_registry_path', lambda: None)
    monkeypatch.setattr(identity, '_approval_cache', {})
    calls = []
    def checked(lock, sha, path, *, tier=None, boundary=None):
        calls.append((sha, tier, boundary)); return 'repository'
    monkeypatch.setattr(identity, '_approval_source_checked', checked)
    return dict(registry=registry, parent=parent, lock_path=lock_path, calls=calls, tmp=tmp_path)


def call(env, lock=None, sha='a' * 64, tier='dev', boundary='dev_loop'):
    return identity._approval_source(lock or {'x': 1}, sha, env['lock_path'], tier=tier, boundary=boundary)


def test_dev_tier_approval_is_checked_once_per_unchanged_inputs(env):
    assert call(env) == call(env) == call(env) == 'repository'
    assert len(env['calls']) == 1


@pytest.mark.parametrize('tier,boundary', [(None, None), ('formal', 'formal'), ('dev', None), (None, 'dev_loop')])
def test_formal_and_legacy_tiers_are_never_cached(env, tier, boundary):
    call(env, tier=tier, boundary=boundary); call(env, tier=tier, boundary=boundary)
    assert len(env['calls']) == 2


def test_any_trust_input_change_rechecks(env):
    call(env)
    env['registry'].write_text('{"new": 1}'); call(env)
    env['parent'].write_text('{"p": 2}'); call(env)
    call(env, sha='b' * 64)
    call(env, lock={'x': 2})
    assert len(env['calls']) == 5


def test_local_registry_and_its_locks_are_part_of_the_key(env, monkeypatch):
    d06 = env['tmp'] / 'd06.json'; d06.write_text('{"d": 6}')
    local = env['tmp'] / 'local.json'
    entries = {'official_parent': {'lock_path': str(env['parent'])},
               'chain': {'d05': {'lock_path': str(d06)}, 'd06': {'lock_path': str(d06)}}}
    local.write_text(json.dumps(entries))
    monkeypatch.setattr(identity, '_local_registry_path', lambda: local)
    call(env); call(env)
    d06.write_text('{"d": 7}'); call(env)
    assert len(env['calls']) == 2


def test_dev_local_base_lock_is_part_of_the_key(env):
    base = env['tmp'] / 'base.json'; base.write_text('{"b": 1}')
    lock = {'provenance': {'deployment_kind': 'dev-local', 'base_deployment_lock_path': str(base)}}
    call(env, lock=lock); call(env, lock=lock)
    base.write_text('{"b": 2}'); call(env, lock=lock)
    assert len(env['calls']) == 2


def test_refusals_are_never_cached(env, monkeypatch):
    seen = []
    monkeypatch.setattr(identity, '_approval_source_checked', lambda *a, **k: seen.append(1))
    assert call(env) is None and call(env) is None and len(seen) == 2


def test_permission_change_rechecks(env):
    call(env); env['registry'].chmod(0o666); call(env)
    assert len(env['calls']) == 2


def test_dev_local_snapshot_entries_are_part_of_the_key(env):
    base = env['tmp'] / 'base.json'; base.write_text('{"b": 1}')
    lock = {'provenance': {'deployment_kind': 'dev-local', 'base_deployment_lock_path': str(base)}}
    project = env['tmp'] / 'runtime/project'; project.mkdir(parents=True)
    call(env, lock=lock); call(env, lock=lock)
    (project / 'linked').symlink_to(base); call(env, lock=lock)
    (project / 'linked').unlink(); (project / 'extra.py').write_text(''); call(env, lock=lock)
    assert len(env['calls']) == 3
