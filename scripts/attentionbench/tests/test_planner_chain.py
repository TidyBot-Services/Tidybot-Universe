"""Offline coverage for the portable planner chain; no Conda, GPU or service runs."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.tests.test_local_dev_trust import chain
from curobo_v2 import identity
from scripts.attentionbench import build_d05, build_planner_chain as planner
from scripts.attentionbench.register_local_dev_trust import register
from scripts import build_d06


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    return identity.digest(path)


def test_argument_plumbing_and_dry_run(tmp_path, capsys):
    ws = tmp_path / 'workspace'
    planner.build(SimpleNamespace(workspace=str(ws), project_from=None,
                                  native_assets_from=None, dry_run=True))
    assert not ws.exists()
    assert 'official-content' in capsys.readouterr().out


def test_d05_d06_derive_from_fake_parent(tmp_path, monkeypatch):
    parent = tmp_path / 'parent'
    project = parent / 'runtime/project'
    project.mkdir(parents=True)
    (project / 'adapter.py').write_text('old')
    for name in build_d05.YAMLS:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('root: ' + str(parent) + '\n')
    native = project / 'native-task-source/robocasa_tasks/__init__.py'
    native.parent.mkdir(parents=True)
    native.write_text('native')
    outside = tmp_path / 'outside'; outside.write_text('external')
    parent_lock = dict(schema='attentionbench.curobo-v2.runtime.v1', generation=2,
        library_revision=identity.OFFICIAL_COMMIT, library_base=identity.OFFICIAL_COMMIT,
        project_root=str(project), service_root=str(project / 'curobo_v2/service'),
        assets_root=str(project / 'curobo_v2/service/curobo_service/assets'),
        files={str(path): identity.digest(path) for path in project.rglob('*') if path.is_file()} |
              {str(outside): identity.digest(outside)},
        robot_configs={str(project / name): identity.digest(project / name) for name in build_d05.YAMLS},
        task_sources={str(native.parent.parent): identity.digest(native)},
        provenance='official', official_only=True)
    parent_sha = write(parent / 'runtime-lock.json', parent_lock)
    declared = tmp_path / 'sources.json'
    write(declared, {'SOURCE_FILES': ['adapter.py'], 'NEW_FILES': []})
    monkeypatch.setattr(build_d05, 'SOURCE_LIST', declared)
    monkeypatch.setattr(build_d05.identity, '_official_content', lambda lock: True)
    monkeypatch.setattr(build_d05.subprocess, 'check_output', lambda *a, **kw: b'five')
    repo = tmp_path / 'repo'; repo.mkdir()
    (repo / 'adapter.py').write_text('six')
    d05 = tmp_path / 'd05'
    build_d05.build(d05, parent=parent, repo=repo, source_commit='fake', expected_parent_sha=parent_sha)
    d05_lock = json.loads((d05 / 'runtime-lock.json').read_text())
    assert identity._derived_from_parent(d05_lock, parent_lock)
    monkeypatch.setattr(build_d06, 'source_manifest', lambda repo, old: {'adapter.py': identity.digest(repo / 'adapter.py')})
    d06 = tmp_path / 'd06'
    build_d06.build(d06, parent=d05, repo=repo, expected_parent_sha=identity.digest(d05 / 'runtime-lock.json'),
                    expected_official_sha=parent_sha)
    d06_lock = json.loads((d06 / 'runtime-lock.json').read_text())
    assert identity._derived_from_parent(d06_lock, parent_lock)
    assert d06_lock['provenance']['parent_adapter_lock_sha256'] == identity.digest(d05 / 'runtime-lock.json')


def test_registry_is_readonly_and_loadable(chain):
    registry = chain['registry']
    registry.unlink()
    proof = chain['tmp'] / 'proof.json'
    write(proof, {'passed': True, 'request_set_sha256': 'a' * 64, 'result_sha256': 'b' * 64})
    register(chain['parent'], chain['d05'][0], chain['d06'][0], chain['data']['dev_local_root'], proof)
    assert registry.stat().st_mode & 0o777 == 0o444
    assert identity.load_lock(chain['d06'][0], tier='dev', boundary='dev_loop')[0]['trust_source'] == 'local-dev-registry'


def test_tampered_official_source_names_file(chain):
    parent = json.loads(chain['parent'].read_text())
    chain['source'].write_text('tampered')
    with pytest.raises(planner.StepError, match=r'curobo/__init__\.py') as failure:
        planner.check_parent(parent)
    assert 'official-content check failed' in str(failure.value)


def test_proof_uses_owned_service_boundary(tmp_path, monkeypatch):
    import sys
    import types
    project = tmp_path / 'project'
    config = project / 'config/openarm_fixed.yml'
    config.parent.mkdir(parents=True)
    config.write_text('service:\n  joint_names: [one, two]\nrobot_cfg:\n  kinematics:\n    cspace:\n      joint_names: [one, two]\n      default_joint_position: [0.1, 0.2]\n')
    lock = tmp_path / 'd06/runtime-lock.json'
    write(lock, {'project_root': str(project)})
    calls = []
    class FakeService:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def call(self, path, body):
            calls.append((path, body))
            return {'status': 'success'}
    monkeypatch.setitem(sys.modules, 'curobo_v2.session', types.SimpleNamespace(V2Service=FakeService))
    out = tmp_path / 'proof.json'
    planner.proof(lock, out)
    assert calls[0]['tier'] == 'dev' and calls[0]['boundary'] == 'dev_loop'
    assert [item[0] for item in calls[1:]] == ['/world/cuboids', '/plan/joint', '/plan/joint']
    assert json.loads(out.read_text())['passed'] is True
