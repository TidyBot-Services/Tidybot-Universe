"""A local snapshot validates the real official parent using its own manifests."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from curobo_v2 import dev_local, identity


ROOT = Path(__file__).resolve().parents[3]
# Pinned from git-tracked bytes at 9670474. A checkout edit must fail this test.
OFFICIAL_SHA256 = {
    'allowed-path-files.json': '37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570',
    'author-paths.json': '1061e9422447c9bda64bd3a34a170cad0b43aa6d862ee2e272e6d5372e5f65f0',
    'curobo-78fd485-source.json': 'c51e0a8ddf6411fd91f29e9e94349019ddf2628d488fec828fe47af9acdc0d31',
    'r12-bin-shebangs.json': 'e0bc57aebbdf8f6efe02cec2ee7088424a7bc8946f80586b09bc0cc9541ecf2c',
    'r12-dependencies.json': '850989ffda615c03b9a37e72f91fce2da8a813b745198e40071282648976b003',
    'r12-distributions.json': 'ab977f23fb852932ef8778c9cd43c723db50e1e068e46d8f26b79cbf0ff11d81',
    'r12-native-assets.json': '3fcfaccf0202412818407b8506f453c270c3a13a99664d655e3df36f20a76b02',
    'r12-prefix-files.json': '74e48a915b9f9b046988ec7b273aec2578ddae99106d3425dce954473c8d3dae',
    'r12-record-normalized.json': '2500657f579c01e43e4387cc9f3d5fb086fbadf44602e59ec884d6e8e7a322c8',
    'r12-wheel-normalized.json': 'ff74e669935c56e78ebefdd68f2c1c5ec80c06809ad2951aeaefc3bbd0ff23ee',
}


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    return identity.digest(path)


def test_official_manifest_inventory_and_git_pins():
    tracked = subprocess.check_output(
        ['git', 'ls-files', '-z', 'curobo_v2/official/*.json'], cwd=ROOT
    ).decode().strip('\0').split('\0')
    expected = {f'curobo_v2/official/{name}' for name in OFFICIAL_SHA256}
    assert set(tracked) == expected == set(dev_local.LOCAL_OFFICIAL_FILES)
    for name, pinned in OFFICIAL_SHA256.items():
        relative = f'curobo_v2/official/{name}'
        tracked_bytes = subprocess.check_output(['git', 'show', f'HEAD:{relative}'], cwd=ROOT)
        assert hashlib.sha256(tracked_bytes).hexdigest() == pinned
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == pinned


def test_local_package_files_are_fixed_and_import_free():
    # A version with imports would pull the robosuite adapter into the planner env.
    assert {name: hashlib.sha256(raw).hexdigest() for name, raw in dev_local.LOCAL_PACKAGE_FILES.items()} == {
        'benchmarks/__init__.py': 'bf013cbf63fb0b6654952c9c7e7283455986ee38bc62cd7c16de58f3ebd1915c',
        'benchmarks/attention_harness/__init__.py': '47bbde31ba3dd33d8df722fef5a2fd6b12e00aa911eb0406b2fa650463cea57c',
    }


@pytest.fixture
def real_local_snapshot(tmp_path, monkeypatch):
    parent_path = identity.OFFICIAL_PARENT_LOCK
    if not parent_path.is_file():
        pytest.skip('real official parent lock is unavailable')
    parent = json.loads(parent_path.read_bytes())
    assert identity._official_content(parent)
    parent_sha = identity.digest(parent_path)
    outside = {name: sha for name, sha in parent['files'].items()
               if not Path(name).is_relative_to(parent['project_root'])}

    source = tmp_path / 'source'
    source.mkdir()
    runtime_modules = ('__init__.py', 'identity.py', 'official_normalization.py',
                       'dev_local.py', 'local_dev_trust.py', 'execution_settings.py',
                       'fixture_interaction.py', 'worker.py')
    for name in runtime_modules:
        dest = source / 'curobo_v2' / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'curobo_v2' / name, dest)
    for name in (*dev_local.LOCAL_PATH_FILES, *dev_local.LOCAL_OFFICIAL_FILES):
        dest = source / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
    subprocess.run(['git', 'init', '-q', str(source)], check=True)
    subprocess.run(['git', 'add', '.'], cwd=source, check=True)
    subprocess.run(['git', '-c', 'user.name=Snapshot Test', '-c',
                    'user.email=snapshot@example.invalid', 'commit', '-qm', 'fixture'],
                   cwd=source, check=True)

    def deployment(name, revision, d05_sha=None):
        base_dir = tmp_path / name
        project = base_dir / 'runtime/project'
        project.mkdir(parents=True)
        for module in runtime_modules:
            dest = project / 'curobo_v2' / module
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / 'curobo_v2' / module, dest)
        (project / 'curobo_v2/trusted_deployments.json').write_text('{}\n')
        for yaml in dev_local.RELOCATED_YAMLS:
            dest = project / yaml
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(str(base_dir) + '/asset\n')
        lock = copy.deepcopy(parent)
        lock.update(project_root=str(project), service_root=str(project), assets_root=str(project))
        lock['files'] = outside | {str(p): identity.digest(p) for p in project.rglob('*') if p.is_file()}
        provenance = dict(adapter_revision=revision, parent_lock_path=str(parent_path),
                          parent_lock_sha256=parent_sha, official_provenance={})
        if d05_sha:
            provenance.update(parent_adapter_lock_path=str(tmp_path / 'd05/runtime-lock.json'),
                              parent_adapter_lock_sha256=d05_sha)
        lock['provenance'] = provenance
        path = base_dir / 'runtime-lock.json'
        sha = _write(path, lock)
        return path, lock, sha

    d05_path, _, d05_sha = deployment('d05', 'deployment-05')
    d06_path, d06, d06_sha = deployment('d06', 'deployment-06', d05_sha)
    registry = tmp_path / 'local-dev-trust.json'
    data = dict(schema='attentionbench.curobo-v2.local-dev-trust.v1',
                official_commit=identity.OFFICIAL_COMMIT,
                official_parent=dict(lock_path=str(parent_path), sha256=parent_sha),
                chain=dict(d05=dict(lock_path=str(d05_path), sha256=d05_sha),
                           d06=dict(lock_path=str(d06_path), sha256=d06_sha,
                                    derived_from_sha256=d05_sha, profile='r3-20261025')),
                dev_local_root=str(tmp_path / 'snapshots'),
                build_proof=dict(passed=True, request_set_sha256='a'*64,
                                 result_sha256='b'*64))
    _write(registry, data)
    registry.chmod(0o444)
    workspace = tmp_path / 'workspace'
    _write(workspace / 'paths.json', dict(schema='attentionbench.paths.v1',
                                         paths={'planner.local_dev_trust': str(registry)}))
    monkeypatch.setenv('ATTENTIONBENCH_WORKSPACE', str(workspace))
    from benchmarks.attention_harness import workspace_paths
    workspace_paths.clear_cache()
    monkeypatch.setattr(identity, '_local_registry_digest', None)
    monkeypatch.setattr(identity, '_registry_digest', None)
    monkeypatch.setattr(identity, 'DEPLOYMENT_REGISTRY', tmp_path / 'empty-registry.json')
    _write(identity.DEPLOYMENT_REGISTRY, {})
    monkeypatch.setattr(__import__('curobo_v2.execution_settings', fromlist=['D06_LOCK']),
                        'D06_LOCK', str(tmp_path / 'absent-author-d06.json'))
    assert identity._validate_local_chain(data, d06, d06_sha, d06_path)
    result = dev_local.materialize(source)
    lock_path = Path(result['lock_path'])
    lock = json.loads(lock_path.read_bytes())
    project = Path(lock['project_root'])
    assert project / 'curobo_v2/official' == lock_path.parent / 'runtime/project/curobo_v2/official'
    yield registry, d06_path, d06_sha, lock_path, lock, project, workspace, source
    workspace_paths.clear_cache()


PROBE = '''import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from curobo_v2 import identity, dev_local
registry = identity._local_registry()
base = Path(sys.argv[2]); base_lock = json.loads(base.read_bytes()); base_sha = identity.digest(base)
chain = identity._validate_local_chain(registry, base_lock, base_sha, base)
p = Path(sys.argv[3]); lock = json.loads(p.read_bytes()); sha = identity.digest(p)
snapshot = dev_local.verify_snapshot(p, lock, sha)
try:
    accepted, _ = identity.load_lock(p, tier='dev', boundary='dev_loop')
    dev = accepted.get('trust_source')
except identity.IdentityError:
    dev = 'REJECT'
try:
    identity.load_lock(p, tier='formal', boundary='formal')
    formal = 'ACCEPT'
except identity.IdentityError:
    formal = 'REJECT'
print(json.dumps(dict(chain=chain, snapshot=snapshot, dev=dev, formal=formal)))
'''


def _probe(fixture):
    registry, d06_path, _, lock_path, _, project, workspace, _ = fixture
    env = os.environ.copy()
    env['ATTENTIONBENCH_WORKSPACE'] = str(workspace)
    proc = subprocess.run([sys.executable, '-B', '-I', '-c', PROBE, str(project),
                           str(d06_path), str(lock_path)], cwd=project, env=env,
                          text=True, capture_output=True)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_snapshot_real_official_chain_and_tampering(real_local_snapshot):
    fixture = real_local_snapshot
    registry, d06_path, d06_sha, lock_path, lock, project, _, source = fixture
    assert _probe(fixture) == dict(chain=True, snapshot=True,
                                   dev='local-dev-registry', formal='REJECT')
    base_project = d06_path.parent / 'runtime/project'
    gained = {str(p.relative_to(project)) for p in project.rglob('*') if p.is_file()} - {
        str(p.relative_to(base_project)) for p in base_project.rglob('*') if p.is_file()}
    assert gained == (set(dev_local.LOCAL_PATH_FILES) | set(dev_local.LOCAL_PACKAGE_FILES)
                      | set(dev_local.LOCAL_OFFICIAL_FILES))
    manifest = lock['provenance']['source_manifest']
    assert {name for name in manifest if name.startswith('curobo_v2/official/')} == set(dev_local.LOCAL_OFFICIAL_FILES)
    assert {name for name in manifest if name.startswith('curobo_v2/official/')} == {
        f'curobo_v2/official/{name}' for name in OFFICIAL_SHA256}
    assert all(lock['files'][str(project / name)] == manifest[name] == OFFICIAL_SHA256[Path(name).name]
               for name in dev_local.LOCAL_OFFICIAL_FILES)
    assert identity._validate_local_chain(json.loads(registry.read_bytes()),
                                          json.loads(d06_path.read_bytes()), d06_sha, d06_path)
    worker = [sys.executable, '-B', '-I', str(project / 'curobo_v2/worker.py'),
              '--lock', str(lock_path), '--robot-config', '/unused/robot.yml',
              '--socket-fd', '0', '--evidence', str(project.parent / 'evidence'),
              '--tier', 'dev', '--boundary', 'dev_loop']
    env = os.environ.copy()
    env.pop('ATTENTIONBENCH_WORKSPACE', None)
    env['CUROBO_V2_OWNER_PID'] = str(os.getpid())
    absent = subprocess.run(worker, cwd=project, env=env, text=True, capture_output=True)
    assert absent.returncode != 0 and 'ATTENTIONBENCH_WORKSPACE and paths.json' in absent.stderr
    env['ATTENTIONBENCH_WORKSPACE'] = str(source.parent / 'missing-workspace')
    Path(env['ATTENTIONBENCH_WORKSPACE']).mkdir()
    unresolved = subprocess.run(worker, cwd=project, env=env, text=True, capture_output=True)
    assert unresolved.returncode != 0 and 'paths.json' in unresolved.stderr
    resolver = source / dev_local.LOCAL_PATH_FILES[0]
    resolver.write_bytes(resolver.read_bytes() + b'\n# changed resolver\n')
    assert dev_local.materialize(source)['snapshot_key'] != lock['provenance']['snapshot_key']
    official = project / 'curobo_v2/official'
    for name in OFFICIAL_SHA256:
        path = official / name
        original = path.read_bytes()
        path.chmod(0o644)
        path.write_bytes(b'!' + original[1:])
        assert _probe(fixture) == dict(chain=False, snapshot=False, dev='REJECT', formal='REJECT')
        path.write_bytes(original)
        path.chmod(0o444)
    whitespace = official / 'allowed-path-files.json'
    original = whitespace.read_bytes()
    whitespace.chmod(0o644)
    whitespace.write_bytes(original + b' ')
    assert _probe(fixture) == dict(chain=True, snapshot=False, dev='REJECT', formal='REJECT')
    whitespace.write_bytes(original)
    whitespace.chmod(0o444)
    missing = official / next(iter(OFFICIAL_SHA256))
    original = missing.read_bytes()
    official.chmod(0o755)
    missing.unlink()
    missing_result = _probe(fixture)
    assert missing_result['snapshot'] is False and missing_result['dev'] == 'REJECT'
    missing.write_bytes(original)
    (official / 'extra.json').write_text('{}')
    extra_result = _probe(fixture)
    assert extra_result['snapshot'] is False and extra_result['dev'] == 'REJECT'
