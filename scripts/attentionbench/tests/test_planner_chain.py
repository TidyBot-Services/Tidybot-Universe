"""Offline coverage for the portable planner chain; no Conda, GPU or service runs."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.tests.test_local_dev_trust import chain
from curobo_v2 import identity
from scripts.attentionbench import build_d05, build_planner_chain as planner
from scripts.attentionbench.report_official_diff import file_differences
from scripts.attentionbench.extract_official_content import clone_origin
from scripts.attentionbench.register_local_dev_trust import register
from scripts import build_d06


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    return identity.digest(path)


def test_clone_origin_requires_one_conda_clone(tmp_path):
    history = tmp_path / 'history'
    history.write_text('==> 2026-10-11 <==\n# cmd: /opt/conda/bin/conda create --prefix /tmp/env --clone /opt/source --offline\n')
    assert clone_origin(history) == '/opt/source'
    history.write_text('# cmd: /opt/conda/bin/conda create --prefix /tmp/env --offline\n')
    with pytest.raises(ValueError, match='exactly one'):
        clone_origin(history)
    history.write_text('# cmd: /opt/conda/bin/conda create --clone /opt/one\n'
                       '# cmd: /opt/conda/bin/conda create --clone /opt/two\n')
    with pytest.raises(ValueError, match='exactly one'):
        clone_origin(history)


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


def test_registry_records_gpu_and_driver(chain, monkeypatch):
    import scripts.attentionbench.register_local_dev_trust as trust
    chain['registry'].unlink()
    proof = chain['tmp'] / 'proof.json'
    write(proof, {'passed': True, 'request_set_sha256': 'a' * 64, 'result_sha256': 'b' * 64})
    monkeypatch.setattr(trust, 'detect_gpu', lambda: ('NVIDIA Test GPU', '580.178.04'))
    trust.register(chain['parent'], chain['d05'][0], chain['d06'][0], chain['data']['dev_local_root'], proof)
    machine = json.loads(chain['registry'].read_bytes())['machine']
    assert (machine['gpu'], machine['driver']) == ('NVIDIA Test GPU', '580.178.04')


def test_detect_gpu_names_missing_tool(monkeypatch):
    import scripts.attentionbench.register_local_dev_trust as trust
    def missing(*a, **k): raise FileNotFoundError('nvidia-smi')
    monkeypatch.setattr(trust.subprocess, 'run', missing)
    assert trust.detect_gpu() == ('unavailable: FileNotFoundError',) * 2


def test_tampered_official_source_names_file(chain):
    parent = json.loads(chain['parent'].read_text())
    chain['source'].write_text('tampered')
    with pytest.raises(planner.StepError, match=r'curobo/__init__\.py') as failure:
        planner.check_parent(parent)
    assert 'official-content check failed' in str(failure.value)


def test_proof_runs_isolated_built_python_without_approval(tmp_path, monkeypatch):
    calls = []
    out = tmp_path / 'proof.json'
    requests = tmp_path / 'requests.json'
    requests.write_text('{}')
    def fake_run(argv, **kwargs):
        calls.append(argv)
        write(out, {'passed': True, 'request_set_sha256': identity.digest(requests),
                    'result_sha256': 'b' * 64})
        return SimpleNamespace(returncode=0, stderr='', stdout='')
    monkeypatch.setattr(planner.subprocess, 'run', fake_run)
    planner.proof(tmp_path / 'env/bin/python', tmp_path / 'repos/curobo',
                  tmp_path / 'd06/runtime-lock.json', out, requests)
    argv = calls[0]
    assert argv[1:3] == ['-B', '-I']
    assert argv[3].endswith('/planner_proof.py')
    assert '--lock' in argv and '--library' in argv
    body = Path(argv[3]).read_text().split('def run(')[1]
    assert 'V2Service(' not in body
    assert 'identity.load_lock(' not in body


def test_proof_rejects_wrong_request_hash(tmp_path, monkeypatch):
    out = tmp_path / 'proof.json'
    def fake_run(argv, **kwargs):
        write(out, {'passed': True, 'request_set_sha256': 'a' * 64,
                    'result_sha256': 'b' * 64})
        return SimpleNamespace(returncode=0, stderr='', stdout='')
    monkeypatch.setattr(planner.subprocess, 'run', fake_run)
    with pytest.raises(ValueError, match='different request set'):
        planner.proof('python', 'curobo', 'lock', out)


def test_official_diff_reports_exact_bytes_and_workspace_path(tmp_path):
    root = tmp_path / 'prefix'
    root.mkdir()
    (root / 'direct_url.json').write_text(str(tmp_path) + '/repos/curobo')
    (root / 'stable.txt').write_text('stable')
    expected = {'direct_url.json': '0' * 64, 'stable.txt': identity.digest(root / 'stable.txt'),
                'missing.txt': '1' * 64}
    rows = file_differences('prefix', root, expected, expected, str(tmp_path).encode())
    assert [(row['file'], row['contains_workspace_path']) for row in rows] == [
        ('direct_url.json', True), ('missing.txt', False)]
    assert rows[0]['actual_sha256'] == identity.digest(root / 'direct_url.json')


def test_default_project_assembly_uses_pinned_sources(tmp_path, monkeypatch):
    names = ['config/openarm_fixed.yml', 'config/openarm-model/robot.urdf',
             'curobo_v2/__init__.py',
             'curobo_v2/service/curobo_service/assets/franka_tidyverse.yml',
             'native-task-source/robocasa_tasks/__init__.py',
             'native-task-source/robocasa_tasks/.git/HEAD']
    inventory = tmp_path / 'inventory.json'; write(inventory, names)
    omissions = tmp_path / 'omissions.json'; write(omissions, {names[-1]: 'Git administrative data'})
    monkeypatch.setattr(planner, 'PROJECT_FILES', inventory)
    monkeypatch.setattr(planner, 'PROJECT_OMISSIONS', omissions)
    native = tmp_path / 'native'; native.mkdir()
    (native / '__init__.py').write_text('pinned source')
    def fake_check_output(argv, **kwargs):
        return planner.NATIVE_COMMIT + '\n' if 'rev-parse' in argv else ''
    monkeypatch.setattr(planner.subprocess, 'check_output', fake_check_output)
    library = tmp_path / 'curobo'; library.mkdir()
    project = tmp_path / 'deployment/runtime/project'
    planner.project_snapshot(planner.REPO, project, library=library, native_source=native)
    assert (project / names[4]).read_text() == 'pinned source'
    assert not (project / names[-1]).exists()
    assert str(project) in (project / 'config/openarm_fixed.yml').read_text()
    assert (project / 'config/openarm-model/robot.urdf').read_bytes() == (
        planner.REPO / 'openarm_phase0_recheck/model/compat_v6/robot.urdf').read_bytes()
