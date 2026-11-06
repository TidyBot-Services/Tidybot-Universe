"""Local trust accepts only a content-matched, path-bound development chain."""
import ast
import copy
import base64
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from curobo_v2 import identity as auth
from curobo_v2 import official_normalization as normalization


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))
    return auth.digest(path)


@pytest.fixture
def chain(tmp_path, monkeypatch):
    official = tmp_path / 'official'; official.mkdir()
    source = official / 'curobo/__init__.py'; source.parent.mkdir(); source.write_text('official\n')
    prefix = tmp_path / 'prefix'; prefix.mkdir()
    dep = prefix / 'numpy/__init__.py'; dep.parent.mkdir(); dep.write_text('numpy\n')
    host = tmp_path / 'libcuda.so'; host.write_text('cuda')
    libc = tmp_path / 'libc.so.6'; libc.write_text('libc')
    assets = tmp_path / 'assets'; assets.mkdir()
    asset = assets / 'robot.dat'; asset.write_text('robot')
    manifest = tmp_path / 'native.json'
    native = {'files': [{'relative': 'robot.dat', 'sha256': auth.digest(asset)}]}
    write(manifest, native)
    project = tmp_path / 'parent/runtime/project'; project.mkdir(parents=True)
    project_file = project / 'adapter.py'; project_file.write_text('parent')
    outside = {str(p): auth.digest(p) for p in (source, dep, manifest)}
    parent = dict(schema='attentionbench.curobo-v2.runtime.v1', generation=2,
        official_only=True, library_revision=auth.OFFICIAL_COMMIT, library_base=auth.OFFICIAL_COMMIT,
        library_root=str(official), prefix=str(prefix), python=str(prefix / 'bin/python'),
        project_root=str(project), service_root=str(project), assets_root=str(project),
        files={**outside, str(project_file): auth.digest(project_file)},
        dependencies={'numpy': {'path': str(dep), 'sha256': auth.digest(dep)}},
        distributions={'numpy': '1'}, distribution_version='1', module_version='1',
        robot_configs={}, task_sources={}, provenance={},
        native_assets={'manifest': str(manifest), 'manifest_sha256': auth.digest(manifest)},
        native_host_dependencies={str(host): auth.digest(host), str(libc): auth.digest(libc)})
    parent_path = tmp_path / 'parent/runtime-lock.json'; parent_sha = write(parent_path, parent)
    locks = {}
    for name in ('d05', 'd06'):
        path = tmp_path / name / 'runtime-lock.json'
        local_project = path.parent / 'runtime/project'; local_project.mkdir(parents=True)
        local_file = local_project / 'adapter.py'; local_file.write_text(name)
        lock = copy.deepcopy(parent)
        lock.update(project_root=str(local_project), service_root=str(local_project), assets_root=str(local_project),
                    files={**outside, str(local_file): auth.digest(local_file)},
                    provenance={'parent_lock_path': str(parent_path), 'parent_lock_sha256': parent_sha,
                                'adapter_revision': 'deployment-06' if name == 'd06' else 'deployment-05'})
        locks[name] = (path, lock)
    d05_path, d05 = locks['d05']; d05_sha = write(d05_path, d05)
    d06_path, d06 = locks['d06']; d06['provenance']['parent_adapter_lock_sha256'] = d05_sha
    d06['provenance']['parent_adapter_lock_path'] = str(d05_path)
    d06_sha = write(d06_path, d06)
    official_manifests = tmp_path / 'manifests'; official_manifests.mkdir()
    write(official_manifests / 'curobo-78fd485-source.json', {'curobo/__init__.py': auth.digest(source)})
    write(official_manifests / 'r12-distributions.json', {'numpy': '1'})
    write(official_manifests / 'r12-dependencies.json', {'numpy': {'path': 'numpy/__init__.py', 'sha256': auth.digest(dep)}})
    write(official_manifests / 'r12-prefix-files.json', {'numpy/__init__.py': auth.digest(dep)})
    write(official_manifests / 'r12-native-assets.json', {'robot.dat': auth.digest(asset)})
    write(official_manifests / 'allowed-path-files.json', [])
    write(official_manifests / 'author-paths.json', {'prefix': str(prefix), 'library_root': str(official),
                                                    'binary_prefix': str(prefix), 'binary_prefix_slot': 256,
                                                    'script_prefix': str(prefix), 'origin_prefix': str(prefix)})
    write(official_manifests / 'r12-wheel-normalized.json', {})
    write(official_manifests / 'r12-record-normalized.json', {})
    write(official_manifests / 'r12-bin-shebangs.json', {})
    registry = tmp_path / 'local-dev-trust.json'
    data = {'schema': 'attentionbench.curobo-v2.local-dev-trust.v1',
            'official_commit': auth.OFFICIAL_COMMIT,
            'official_parent': {'lock_path': str(parent_path), 'sha256': parent_sha},
            'chain': {'d05': {'lock_path': str(d05_path), 'sha256': d05_sha},
                      'd06': {'lock_path': str(d06_path), 'sha256': d06_sha,
                              'derived_from_sha256': d05_sha, 'profile': 'r3-20261025'}},
            'dev_local_root': str(tmp_path / 'snapshots'),
            'build_proof': {'request_set_sha256': 'a'*64, 'result_sha256': 'b'*64, 'passed': True}}
    write(registry, data); registry.chmod(0o444)
    repo = tmp_path / 'repository.json'; write(repo, {})
    monkeypatch.setattr(auth, 'DEPLOYMENT_REGISTRY', repo)
    monkeypatch.setattr(auth, 'OFFICIAL_MANIFESTS', official_manifests)
    monkeypatch.setattr(auth, '_local_registry_path', lambda: registry)
    monkeypatch.setattr(auth, '_local_registry_digest', None)
    monkeypatch.setattr(auth, '_registry_digest', None)
    return dict(registry=registry, data=data, source=source, host=host,
                d05=(d05_path, d05), d06=(d06_path, d06), parent=parent_path, tmp=tmp_path)


def accepted(chain):
    return auth.load_lock(chain['d06'][0], tier='dev', boundary='dev_loop')


def test_valid_chain_and_author_pin_unchanged(chain):
    lock, sha = accepted(chain)
    assert lock['trust_source'] == 'local-dev-registry'
    assert sha == chain['data']['chain']['d06']['sha256']
    from curobo_v2.execution_settings import R3_20261025, receipt_fields
    labels = receipt_fields(R3_20261025)
    assert labels['trust_source'] == 'local-dev-registry'
    assert labels['formal_eligible'] is False


def test_missing_author_files_use_authenticated_local_chain(chain, monkeypatch):
    from curobo_v2 import execution_settings as es, local_dev_trust
    from openarm365_dev import config
    author05 = chain['tmp'] / 'absent-author-d05.json'
    author06 = chain['tmp'] / 'absent-author-d06.json'
    monkeypatch.setattr(config, 'LOCK', author05)
    monkeypatch.setattr(config, 'LOCK_SHA', 'c' * 64)
    monkeypatch.setattr(es, 'D05_SHA', 'c' * 64)
    monkeypatch.setattr(es, 'D06_LOCK', str(author06))
    write(auth.DEPLOYMENT_REGISTRY, {
        'c' * 64: {'lock_path': str(author05)},
        'd' * 64: {'lock_path': str(author06), 'profile': es.PROFILE,
                   'allowed_tiers': ['dev'], 'derived_from_sha256': 'c' * 64},
    })
    assert local_dev_trust.dev05_pin() == chain['data']['chain']['d05']
    assert local_dev_trust.dev06_pin() == {k: chain['data']['chain']['d06'][k]
                                           for k in ('lock_path', 'sha256')}
    author06.write_text('{}')
    with pytest.raises(auth.IdentityError):
        local_dev_trust.dev06_pin()  # Present but altered author evidence cannot fall back.


def test_builder_verifies_before_writing_readonly_registry(chain):
    from scripts.attentionbench.register_local_dev_trust import register
    path = chain['registry']
    path.unlink()
    proof = chain['tmp'] / 'proof.json'
    write(proof, {'request_set_sha256': 'a'*64, 'result_sha256': 'b'*64, 'passed': False})
    with pytest.raises(ValueError, match='failed local identity'):
        register(chain['parent'], chain['d05'][0], chain['d06'][0], chain['data']['dev_local_root'], proof)
    assert not path.exists()
    write(proof, {'request_set_sha256': 'a'*64, 'result_sha256': 'b'*64, 'passed': True})
    assert register(chain['parent'], chain['d05'][0], chain['d06'][0], chain['data']['dev_local_root'], proof) == path
    assert path.stat().st_mode & 0o777 == 0o444
    assert accepted(chain)[0]['trust_source'] == 'local-dev-registry'


def test_local_dev_snapshot_without_official_manifests_rejected(chain):
    from curobo_v2.dev_local import _key
    base_path, base = chain['d06']
    base_sha = auth.digest(base_path)
    source_manifest = {'adapter.py': 'a'*64}
    key = _key(base_sha, source_manifest)
    out = Path(chain['data']['dev_local_root']) / key
    project = out / 'runtime/project'; project.mkdir(parents=True)
    adapter = project / 'adapter.py'; adapter.write_text('snapshot')
    snapshot = copy.deepcopy(base)
    snapshot['project_root'] = str(project)
    snapshot['service_root'] = str(project)
    snapshot['assets_root'] = str(project)
    snapshot['files'] = {name: sha for name, sha in base['files'].items()
                         if not Path(name).is_relative_to(base['project_root'])}
    snapshot['files'][str(adapter)] = auth.digest(adapter)
    snapshot['provenance'] = {'deployment_kind': 'dev-local', 'formal_eligible': False,
        'snapshot_key': key, 'source_manifest': source_manifest,
        'base_deployment_lock_path': str(base_path), 'base_deployment_lock_sha256': base_sha}
    path = out / 'runtime-lock.json'; write(path, snapshot)
    with pytest.raises(auth.IdentityError, match='Unapproved'):
        auth.load_lock(path, tier='dev', boundary='dev_loop')
    with pytest.raises(auth.IdentityError, match='Unapproved'):
        auth.load_lock(path, tier='formal', boundary='formal')


@pytest.mark.parametrize('tier,boundary', [('formal','formal'), ('dev','formal'), ('dev','dev'), (None,None)])
def test_only_dev_loop(chain, tier, boundary):
    with pytest.raises(auth.IdentityError, match='Unapproved'):
        auth.load_lock(chain['d06'][0], tier=tier, boundary=boundary)


def test_source_mutation_rejected(chain):
    chain['source'].write_text('changed')
    with pytest.raises(auth.IdentityError, match='Unapproved'): accepted(chain)


@pytest.mark.parametrize('change', ['new_package', 'version'])
def test_distributions_rejected(chain, change):
    parent = json.loads(chain['parent'].read_bytes())
    if change == 'new_package': parent['distributions']['other'] = '1'
    else: parent['distributions']['numpy'] = '2'
    assert auth._official_content(parent) is False
    path, lock = chain['d06']
    if change == 'new_package': lock['distributions']['other'] = '1'
    else: lock['distributions']['numpy'] = '2'
    write(path, lock)
    chain['data']['chain']['d06']['sha256'] = auth.digest(path)
    chain['registry'].chmod(0o644); write(chain['registry'], chain['data']); chain['registry'].chmod(0o444)
    with pytest.raises(auth.IdentityError, match='Unapproved'): accepted(chain)


@pytest.mark.parametrize('change', ['writable', 'symlink', 'midprocess'])
def test_registry_integrity(chain, change):
    accepted(chain)
    path = chain['registry']
    if change == 'writable': path.chmod(0o664)
    elif change == 'symlink':
        path.rename(path.with_suffix('.real'))
        path.symlink_to(path.with_suffix('.real'))
    else:
        path.chmod(0o644); chain['data']['created_at'] = 'changed'; write(path, chain['data']); path.chmod(0o444)
    with pytest.raises(auth.IdentityError, match='changed' if change == 'midprocess' else 'Unapproved'):
        accepted(chain)


def test_derived_outside_change_and_unregistered_path(chain):
    path, lock = chain['d06']
    outside = next(iter(lock['files']))
    lock['files'][outside] = '0'*64
    write(path, lock)
    chain['data']['chain']['d06']['sha256'] = auth.digest(path)
    chain['registry'].chmod(0o644); write(chain['registry'], chain['data']); chain['registry'].chmod(0o444)
    with pytest.raises(auth.IdentityError, match='Unapproved'): accepted(chain)
    original = chain['d05'][0]
    moved = chain['tmp'] / 'other.json'; moved.write_bytes(original.read_bytes())
    with pytest.raises(auth.IdentityError, match='Unapproved'):
        auth.load_lock(moved, tier='dev', boundary='dev_loop')


def test_host_dependency_drift_is_recorded_only_for_dev_loop(chain, monkeypatch):
    lock, _ = accepted(chain)
    monkeypatch.setattr(auth.sys, 'prefix', lock['prefix'])
    monkeypatch.setattr(auth.sys, 'base_prefix', lock['prefix'])
    monkeypatch.setattr(auth.sys, 'executable', lock['python'])
    monkeypatch.setattr(auth.importlib.util, 'find_spec', lambda name: type('Spec', (), {'origin': str(Path(lock['library_root']) / 'curobo/__init__.py')})())
    monkeypatch.setattr(auth, '_distributions', lambda: {})
    monkeypatch.setattr(auth, 'verify_files', lambda lock: None)
    monkeypatch.setattr('curobo_v2.host_fingerprint.gate', lambda value: ([], []))
    monkeypatch.setattr('curobo_v2.host_fingerprint.host_fingerprint', lambda: {})
    monkeypatch.setattr(auth.subprocess, 'check_output', lambda *a, **k: ' M source.py\n')
    chain['host'].write_text('changed')
    with pytest.raises(auth.IdentityError, match='Native host dependency changed'):
        auth.verify_runtime(dict(lock))
    with pytest.raises(auth.IdentityError, match='Official solver checkout has local changes'):
        auth.verify_runtime(lock)
    assert auth.last_host_drift == [{'path': str(chain['host']), 'expected': chain['d06'][1]['native_host_dependencies'][str(chain['host'])], 'actual': auth.digest(chain['host'])}]
    chain['host'].unlink()
    with pytest.raises(auth.IdentityError, match='Native host dependency changed'):
        auth.verify_runtime(dict(lock))
    with pytest.raises(auth.IdentityError, match='Official solver checkout has local changes'):
        auth.verify_runtime(lock)
    assert auth.last_host_drift[0]['actual'] == 'missing'


def test_manifest_host_policy_never_grants_record_mode(chain, monkeypatch):
    path, value = chain['d06']
    value = dict(value, host_policy='record')
    raw_sha = write(path, value)
    monkeypatch.setattr(auth, '_approval_source', lambda *a, **k: 'author-registry')
    for tier, boundary, expected in ((None, None, False), ('formal', 'formal', False),
                                     ('dev', 'dev_loop', True), ('dev', 'other', False)):
        loaded, sha = auth.load_lock(path, tier=tier, boundary=boundary)
        assert sha == raw_sha
        assert isinstance(loaded, auth._RecordHostLock) is expected


def test_dev_lock_still_equals_the_file(chain, monkeypatch):
    # evidence.preserve_runtime, execution_settings and session compare the lock to its JSON.
    path, value = chain['d06']
    write(path, value)
    monkeypatch.setattr(auth, '_approval_source', lambda *a, **k: 'author-registry')
    loaded, _ = auth.load_lock(path, tier='dev', boundary='dev_loop')
    assert json.loads(Path(path).read_bytes()) == {k: v for k, v in loaded.items() if k != 'trust_source'}
    assert isinstance(loaded, dict) and json.loads(json.dumps(loaded)) == loaded


def test_formal_config_rejects_local_tag(chain):
    from openarm365_dev_v2.formal_config import reject_dev_local
    lock, _ = accepted(chain)
    with pytest.raises(ValueError, match='local dev registry'):
        reject_dev_local({'trust_source': lock['trust_source']})


def test_formal_runner_rejects_local_tag(chain):
    from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest, run_with_formal_boundary
    policy = chain['tmp'] / 'policy.py'; policy.write_text('pass\n')
    request = FormalRunRequest(suite='robosuite', task_id='task', seed=1,
        policy_code_path=policy, policy_sha256=auth.digest(policy), config_sha256='a'*64,
        artifact_root=chain['tmp'], overall_deadline_seconds=1)
    runner = type('Runner', (), {'suite': 'robosuite', 'execute': lambda self, request: {'trust_source': 'local-dev-registry'}})()
    with pytest.raises(RuntimeError, match='local dev registry'):
        run_with_formal_boundary(request, runner=runner)
    config = chain['tmp'] / 'config.json'; write(config, {'trust_source': 'local-dev-registry'})
    from dataclasses import replace
    tagged_request = replace(request, config_path=config, config_sha256=auth.digest(config))
    with pytest.raises(ValueError, match='local dev registry'):
        run_with_formal_boundary(tagged_request, runner=runner)


def test_real_author_locks_keep_paths_and_digests():
    from curobo_v2.execution_settings import D05_SHA, D06_LOCK
    from curobo_v2.dev_local import DEV_LOCAL_ROOT
    from openarm365_dev.config import LOCK, LOCK_SHA
    if not LOCK.is_file() or not Path(D06_LOCK).is_file():
        pytest.skip('Author d05/d06 locks are absent on this machine')
    five, five_sha = auth.load_lock(LOCK, tier='dev', boundary='dev_loop')
    six, six_sha = auth.load_lock(D06_LOCK, tier='dev', boundary='dev_loop')
    assert five_sha == LOCK_SHA == D05_SHA and six_sha == auth.digest(Path(D06_LOCK))
    assert 'trust_source' not in five and 'trust_source' not in six
    if not DEV_LOCAL_ROOT.is_dir():
        pytest.skip('Author dev-local snapshot root is absent on this machine')
    snapshots = list(DEV_LOCAL_ROOT.glob('*/runtime-lock.json'))
    if not snapshots:
        pytest.skip('Author dev-local lock is absent on this machine')
    for path in snapshots:
        lock, sha = auth.load_lock(path, tier='dev', boundary='dev_loop')
        assert sha == auth.digest(path) and 'trust_source' not in lock


AUTHOR_ENV = Path(json.loads((auth.OFFICIAL_MANIFESTS / 'author-paths.json').read_bytes())['prefix'])
COLLAB_ENV = Path(__file__).resolve().parents[5] / 'diagnostics/collab-repro/l2/ws/envs/curobo-env'
COLLAB_LIBRARY = '/home/collab/aws/repos/curobo'


@pytest.mark.parametrize('name', [
    'lib/python3.11/site-packages/Farama_Notifications-0.0.4.dist-info/RECORD',
    'lib/python3.11/site-packages/numpy-1.26.4.dist-info/RECORD',
    'lib/python3.11/site-packages/charset_normalizer-3.4.6.dist-info/RECORD',
    'lib/python3.11/site-packages/pygments-2.21.0.dist-info/RECORD',
    'lib/python3.11/site-packages/pytest-9.1.1.dist-info/RECORD',
    'lib/python3.11/site-packages/nvidia_curobo-0.8.0.post1.dev43.dist-info/RECORD',
    'lib/python3.11/site-packages/nvidia_curobo-0.8.0.post1.dev43.dist-info/WHEEL',
    'lib/python3.11/site-packages/toppra-0.6.3.dist-info/WHEEL',
    'lib/python3.11/site-packages/__editable___nvidia_curobo_0_8_0_post1_dev43_finder.py',
    'lib/python3.1/site-packages/__editable___nvidia_curobo_0_8_0_post1_dev43_finder.py',
    'lib/python3.11/site-packages/nvidia_curobo-0.8.0.post1.dev43.dist-info/direct_url.json',
])
def test_real_normalized_pair_and_mutations(tmp_path, name):
    if not (AUTHOR_ENV / name).is_file() or not (COLLAB_ENV / name).is_file():
        pytest.skip('read-only real environment pair is absent')
    manifests = auth.OFFICIAL_MANIFESTS
    paths = json.loads((manifests / 'author-paths.json').read_bytes())
    wheels = json.loads((manifests / 'r12-wheel-normalized.json').read_bytes())
    records = json.loads((manifests / 'r12-record-normalized.json').read_bytes())
    shebangs = json.loads((manifests / 'r12-bin-shebangs.json').read_bytes())
    path = tmp_path / name
    path.parent.mkdir(parents=True)
    shutil.copyfile(COLLAB_ENV / name, path)
    if name.endswith('/RECORD'):
        for row in path.read_bytes().splitlines():
            item = row.split(b',', 1)[0].decode()
            full_name = str(Path(name).parent.parent / item)
            if normalization.rule_for(full_name) == 'text-path':
                target = tmp_path / full_name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(COLLAB_ENV / full_name, target)
            if row.startswith(b'../../../bin/'):
                script = row.split(b',', 1)[0].decode().removeprefix('../../../bin/')
                target = tmp_path / 'bin' / script
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(COLLAB_ENV / 'bin' / script, target)
    author_sha = auth.digest(AUTHOR_ENV / name)
    def check():
        return normalization.matches(name, path, auth.digest(path), author_sha,
                                     '/home/collab/aws/envs/curobo-env', COLLAB_LIBRARY,
                                     paths, wheels, records, shebangs)
    assert check() == normalization.rule_for(name)
    original = path.read_bytes()
    if name.endswith('/RECORD'):
        path.write_bytes(original + b'other/file,sha256=abc,3\r\n')
        assert check() is None
        path.write_bytes(original.replace(Path(name).parent.name.encode() + b'/REQUESTED',
                                          b'other-1.0.dist-info/REQUESTED'))
        assert check() is None
        if b'.py,sha256=' in original:
            path.write_bytes(original.replace(b'.py,sha256=', b'.py,sha256=X', 1))
            assert check() is None
        if b'../../../bin/' in original:
            path.write_bytes(original.replace(b'../../../bin/', b'../../../bin/changed-', 1))
            assert check() is None
            row = next(line for line in original.splitlines(keepends=True)
                       if line.startswith(b'../../../bin/'))
            script = row.split(b',', 1)[0].decode().removeprefix('../../../bin/')
            script_path = tmp_path / 'bin' / script
            altered = script_path.read_bytes() + b'\nraise RuntimeError("changed body")\n'
            script_path.write_bytes(altered)
            digest = base64.urlsafe_b64encode(hashlib.sha256(altered).digest()).rstrip(b'=')
            replacement = row.split(b',', 1)[0] + b',sha256=' + digest + b',' + str(len(altered)).encode() + b'\r\n'
            path.write_bytes(original.replace(row, replacement))
            assert check() is None
        text_row = next((row for row in original.splitlines(keepends=True)
                         if normalization.rule_for(str(Path(name).parent.parent /
                             row.split(b',', 1)[0].decode())) == 'text-path'), None)
        if text_row is not None:
            item = text_row.split(b',', 1)[0]
            target = tmp_path / str(Path(name).parent.parent / item.decode())
            altered = target.read_bytes() + b'\nchanged body\n'
            target.write_bytes(altered)
            digest = base64.urlsafe_b64encode(hashlib.sha256(altered).digest()).rstrip(b'=')
            replacement = item + b',sha256=' + digest + b',' + str(len(altered)).encode() + b'\r\n'
            path.write_bytes(original.replace(text_row, replacement))
            assert check() is None
    elif name.endswith('/WHEEL'):
        path.write_bytes(original.replace(b'Root-Is-Purelib: true', b'Root-Is-Purelib: false')
                         if b'Root-Is-Purelib: true' in original
                         else original.replace(b'Root-Is-Purelib: false', b'Root-Is-Purelib: true'))
        assert check() is None
    elif name.endswith('_finder.py'):
        path.write_bytes(original + b'\nraise RuntimeError("extra")\n')
        assert check() is None
    else:
        path.write_bytes(original + b' ')
        assert check() is None


def test_synthetic_conda_binary_slot_and_other_byte(tmp_path):
    paths = json.loads((auth.OFFICIAL_MANIFESTS / 'author-paths.json').read_bytes())
    local = '/home/collab/aws/envs/curobo-env'
    size = paths['binary_prefix_slot']
    author_slot = paths['binary_prefix'].encode() + b'\0' * (size - len(paths['binary_prefix']))
    local_slot = local.encode() + b'\0' * (size - len(local))
    author = b'ELF\0header' + author_slot + b'\0tail'
    path = tmp_path / 'python3.11'
    path.write_bytes(b'ELF\0header' + local_slot + b'\0tail')
    expected = hashlib.sha256(author).hexdigest()
    def check():
        return normalization.matches('bin/python3.11', path, auth.digest(path), expected,
                                     local, COLLAB_LIBRARY, paths, {})
    assert check() == 'binary-prefix'
    path.write_bytes(path.read_bytes().replace(b'tail', b'fail'))
    assert check() is None


def _finder(values):
    return f'MAPPING = {{}}\nNAMESPACES: dict[str, list[str]] = {values!r}\nPATH = 1\n'.encode()


def test_finder_namespaces_return_to_the_author_order_and_nothing_else_changes():
    paths = {'prefix': '/author/env', 'library_root': '/author/lib'}
    order = normalization.AUTHOR_NAMESPACES
    values = lambda names, root: {name: [f'{root}/{name}'] for name in names}
    mapped = lambda data: normalization.normalized_text_path(data, '/local/env', '/local/lib', paths)
    wanted = _finder(values(order, '/author/lib'))
    assert mapped(_finder(values(reversed(order), '/local/lib'))) == wanted
    changed = values(reversed(order), '/local/lib') | {'docs': ['/local/lib/elsewhere']}
    assert mapped(_finder(changed)) != wanted
    assert mapped(_finder(values(order[1:], '/local/lib'))) is None
    assert mapped(_finder(values((*order, 'extra'), '/local/lib'))) is None
    assert mapped(b'{"url": "file:///local/lib"}') == b'{"url": "file:///author/lib"}'


def test_real_finder_from_another_filesystem_order_matches_the_author_hash():
    name = 'lib/python3.11/site-packages/__editable___nvidia_curobo_0_8_0_post1_dev43_finder.py'
    if not (AUTHOR_ENV / name).is_file():
        pytest.skip('read-only author environment is absent')
    manifests = auth.OFFICIAL_MANIFESTS
    paths = json.loads((manifests / 'author-paths.json').read_bytes())
    expected = json.loads((manifests / 'r12-prefix-files.json').read_bytes())[name]
    data = (AUTHOR_ENV / name).read_bytes().replace(paths['library_root'].encode(), b'/local/lib')
    line = normalization.NAMESPACES_LINE.search(data).group(1)
    values = ast.literal_eval(line.decode())
    other = data.replace(line, repr(dict(reversed(values.items()))).encode())
    assert other != data
    assert normalization.normalized(name, other, '/local/env', '/local/lib', paths, {}) == ('text-path', expected)
    bent = other.replace(b'/local/lib/docs', b'/local/lib/docz', 1)
    assert normalization.normalized(name, bent, '/local/env', '/local/lib', paths, {}) != ('text-path', expected)


def test_official_content_binds_normalized_record_to_local_lock(chain):
    parent = json.loads(chain['parent'].read_bytes())
    prefix = Path(parent['prefix'])
    name = 'lib/python3.11/site-packages/example-1.dist-info/RECORD'
    path = prefix / name
    path.parent.mkdir(parents=True)
    author = b'example-1.dist-info/RECORD,,\r\n'
    requested = b'example-1.dist-info/REQUESTED' + normalization.EMPTY_REQUESTED + b'\r\n'
    path.write_bytes(author + requested)
    parent['files'][str(path)] = auth.digest(path)
    manifests = auth.OFFICIAL_MANIFESTS
    inventory = json.loads((manifests / 'r12-prefix-files.json').read_bytes())
    inventory[name] = hashlib.sha256(author).hexdigest()
    write(manifests / 'r12-prefix-files.json', inventory)
    write(manifests / 'r12-record-normalized.json', {name: hashlib.sha256(author).hexdigest()})
    assert auth._official_content(parent) is True
    path.write_bytes(path.read_bytes() + b'extra,,\r\n')
    assert auth._official_content(parent) is False
    path.write_bytes(author + requested)
    parent['files'][str(path)] = '0' * 64
    assert auth._official_content(parent) is False
