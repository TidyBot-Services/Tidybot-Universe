"""Content-addressed, dev-only adapter snapshots over the registered deployment."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from . import identity

DEV_LOCAL_ROOT = Path('/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/dev-local')
RELOCATED_YAMLS = ('config/openarm_fixed.yml', 'curobo_v2/service/curobo_service/assets/franka_tidyverse.yml')
CONTROL = 'curobo_v2/trusted_deployments.json'
MUTABLE = {'project_root','service_root','assets_root','files','robot_configs','task_sources','provenance'}
LOCAL_PATH_FILES = ('benchmarks/attention_harness/workspace_paths.py',
                    'benchmarks/attention_harness/legacy_paths.py')
LOCAL_PACKAGE_FILES = {
    'benchmarks/__init__.py': b'"""Benchmark package for the local planner snapshot."""\n',
    'benchmarks/attention_harness/__init__.py': b'"""Workspace path resolver for the local planner snapshot."""\n',
}
sha = identity.digest


def _snapshot_root():
    """Keep the author's registered snapshot path; use local root for local d06."""
    # A repository-registered deployment needs no collaborator policy. This
    # also keeps isolated owner copies independent of the settings module.
    if identity._local_registry() is None:
        return DEV_LOCAL_ROOT
    from .local_dev_trust import dev06_pin
    from .execution_settings import D06_LOCK
    try:
        pin = dev06_pin()
    except ValueError as exc:
        raise ValueError('Base must be a registered deployment') from exc
    if pin['lock_path'] == D06_LOCK:
        return DEV_LOCAL_ROOT
    registry = identity._local_registry()
    if registry is None:
        raise ValueError('Local snapshot root unavailable')
    root = Path(registry['dev_local_root'])
    if not root.is_absolute() or not _unlinked(root):
        raise ValueError('Unsafe local snapshot root')
    return root


def require_local_workspace(lock):
    """Give local snapshots an actionable error before identity's fail-closed check."""
    if not is_dev_local(lock):
        return
    base_sha = lock['provenance']['base_deployment_lock_sha256']
    if base_sha in json.loads(identity.DEPLOYMENT_REGISTRY.read_bytes()):
        return
    from benchmarks.attention_harness.workspace_paths import get, WorkspacePathError
    try:
        get('planner.local_dev_trust')
    except WorkspacePathError as exc:
        raise ValueError('Local planner snapshot requires ATTENTIONBENCH_WORKSPACE and paths.json: '
                         + str(exc)) from exc


def source_names(repo):
    """Tracked project files plus new runtime Python and focused regression files.

    Untracked output/evidence/secrets are never swept into a deployment.
    """
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=repo).decode().split('\0')
    new = subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard', '-z'], cwd=repo).decode().split('\0')
    names = set(filter(None, tracked))
    names.update(n for n in new if n.endswith('.py') and
                 (n.startswith('curobo_v2/') or n.startswith('benchmarks/attention_harness/tests/')))
    return names - {CONTROL, *RELOCATED_YAMLS, 'memory-m3/keys.env'}


def checked_source(repo, name):
    path = repo / name
    if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink() or not path.resolve().is_relative_to(repo.resolve()):
        raise ValueError('Unsafe source manifest path: ' + name)
    if not path.is_file():
        raise ValueError('Missing source manifest file: ' + name)
    return path


def source_manifest(repo, old_project):
    names = source_names(repo)
    # Match every repository counterpart in the parent's snapshot, as d05 did.
    # Admit new runtime Python modules and regression tests explicitly; never
    # collect unrelated new protocol evidence, output trees or symlinks.
    selected = []
    for name in sorted(names):
        parent = old_project / name
        new_runtime_source = name.endswith('.py') and (
            name.startswith('curobo_v2/') or name.startswith('benchmarks/attention_harness/tests/'))
        if not parent.is_file() and not new_runtime_source:
            continue
        source = checked_source(repo, name)
        if not parent.exists() or sha(source) != sha(parent):
            selected.append(name)
    return {name: sha(checked_source(repo, name)) for name in selected}


def snapshot_manifest(repo, old_project, registered_base):
    manifest = source_manifest(repo, old_project)
    if not registered_base:
        manifest.update({name: sha(checked_source(repo, name)) for name in LOCAL_PATH_FILES})
        manifest.update({name: hashlib.sha256(raw).hexdigest()
                         for name, raw in LOCAL_PACKAGE_FILES.items()})
    return manifest


def is_dev_local(lock):
    provenance = lock.get('provenance', {})
    return isinstance(provenance, dict) and provenance.get('deployment_kind') == 'dev-local'


def _unlinked(path):
    path = Path(path)
    return not any(p.is_symlink() for p in (path, *path.parents))


def _files(root):
    paths = sorted(root.rglob('*'))
    if not _unlinked(root) or any(p.is_symlink() for p in paths):
        raise ValueError('Symlinks prohibited in frozen runtime')
    return [p for p in paths if p.is_file()]


def _key(base_sha, manifest):
    return hashlib.sha256(identity.canonical(dict(base_lock_sha256=base_sha, source_manifest=manifest))).hexdigest()[:20]


def verify_snapshot(lock_path, lock, lock_sha):
    try:
        p = lock['provenance']
        key = _key(p['base_deployment_lock_sha256'], p['source_manifest'])
        path = Path(lock_path)
        root = _snapshot_root()
        out = root / key
        if (not is_dev_local(lock) or p['formal_eligible'] is not False
                or p['snapshot_key'] != key or not _unlinked(path)
                or path.resolve() != (out / 'runtime-lock.json').resolve()
                or sha(path) != lock_sha or lock['project_root'] != str(out / 'runtime/project')):
            return False
        base_path = Path(p['base_deployment_lock_path'])
        raw = base_path.read_bytes()
        base = json.loads(raw)
        base_sha = hashlib.sha256(raw).hexdigest()
        if (base_sha != p['base_deployment_lock_sha256'] or is_dev_local(base)
                or base_sha in identity.TRUSTED_LOCKS
                or not (identity._registered_deployment(base, base_sha, base_path, tier='dev', boundary='dev_loop')
                        or identity._locally_trusted_dev(base, base_sha, base_path, 'dev', 'dev_loop'))):
            return False
        if set(lock) != set(base) or any(lock[k] != base[k] for k in base.keys()-MUTABLE):
            return False
        outside = lambda v: {k:h for k,h in v['files'].items() if not Path(k).is_relative_to(v['project_root'])}
        if outside(lock) != outside(base):
            return False
        if (lock.get('official_only') is not True
                or lock['library_revision'] != identity.OFFICIAL_COMMIT
                or lock['library_base'] != identity.OFFICIAL_COMMIT):
            return False
        _files(out)
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')


def _link(source, dest):
    if source.stat().st_mode & 0o777 != 0o444:
        shutil.copy2(source, dest)
        return
    try:
        os.link(source, dest)
    except OSError:
        shutil.copy2(source, dest)


def materialize(source_root):
    from .execution_settings import D06_LOCK
    repo = Path(source_root)
    if not repo.is_absolute() or not repo.is_dir() or not _unlinked(repo):
        raise ValueError('source_root must be an absolute unlinked directory')
    repo = repo.resolve()
    from .local_dev_trust import dev06_pin
    try:
        pin = dev06_pin()
    except ValueError as exc:
        raise ValueError('Base must be a registered deployment') from exc
    base_path = Path(pin['lock_path'])
    base, base_sha = identity.load_lock(base_path, tier='dev', boundary='dev_loop')
    base = {k: v for k, v in base.items() if k != 'trust_source'}
    registered_base = identity._registered_deployment(base, base_sha, base_path, tier='dev', boundary='dev_loop')
    if (is_dev_local(base) or base_sha in identity.TRUSTED_LOCKS
            or not (registered_base or identity._locally_trusted_dev(
                json.loads(base_path.read_bytes()), base_sha, base_path, 'dev', 'dev_loop'))):
        raise ValueError('Base must be a registered deployment')
    parent, old_project = base_path.parent, Path(base['project_root'])
    if old_project != parent / 'runtime/project':
        raise ValueError('Unexpected base project root')
    inventory = {str(p):sha(p) for p in _files(old_project)}
    if inventory != {p:h for p,h in base['files'].items() if Path(p).is_relative_to(old_project)}:
        raise ValueError('Base project inventory or digest differs from lock')
    manifest = snapshot_manifest(repo, old_project, registered_base)
    for name in manifest:
        if name not in LOCAL_PACKAGE_FILES and not _unlinked(repo / name):
            raise ValueError('Unsafe source manifest path: ' + name)
    key = _key(base_sha, manifest)
    root = _snapshot_root()
    out = root / key
    root.mkdir(parents=True, exist_ok=True)
    if not _unlinked(root):
        raise ValueError('Symlinks prohibited in snapshot root')
    guard_path = root / ('.lock-' + key)
    if guard_path.is_symlink():raise ValueError('Linked snapshot guard')
    with guard_path.open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        if out.exists() or out.is_symlink():
            lock, lock_sha = identity.load_lock(out / 'runtime-lock.json', tier='dev', boundary='dev_loop')
            identity.verify_files(lock)
            if json.loads((out / 'source-manifest.json').read_bytes()) != manifest:
                raise ValueError('Snapshot source manifest changed')
            receipt = json.loads((out / 'dev-local.json').read_bytes())
            if receipt != lock['provenance']:
                raise ValueError('Snapshot receipt changed')
        else:
            temp = root / ('.tmp-' + key + '-' + str(os.getpid()))
            temp.mkdir()
            try:
                project = temp / 'runtime/project'
                project.mkdir(parents=True)
                for name in inventory:
                    src = Path(name); dest = project / src.relative_to(old_project)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    _link(src, dest)
                for name, expected in manifest.items():
                    raw = (LOCAL_PACKAGE_FILES[name] if name in LOCAL_PACKAGE_FILES
                           else checked_source(repo, name).read_bytes())
                    if hashlib.sha256(raw).hexdigest() != expected:
                        raise ValueError('Source changed during build: ' + name)
                    dest = project / name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if dest.exists():dest.unlink()
                    dest.write_bytes(raw)
                for name in RELOCATED_YAMLS:
                    raw = (old_project / name).read_bytes()
                    if str(parent).encode() not in raw:
                        raise ValueError('Expected base path missing from relocated YAML: ' + name)
                    dest = project / name
                    dest.unlink()
                    dest.write_bytes(raw.replace(str(parent).encode(), str(out).encode()))
                if snapshot_manifest(repo, old_project, registered_base) != manifest:
                    raise ValueError('Source changed during build')
                if {str(p):sha(p) for p in _files(old_project)} != inventory:
                    raise ValueError('Base changed during build')
                lock = json.loads(json.dumps(base))
                for field in ('project_root', 'service_root', 'assets_root'):
                    lock[field] = lock[field].replace(str(parent), str(out))
                hashes = {str(out / p.relative_to(temp)):sha(p) for p in _files(project)}
                outside = {p:h for p,h in base['files'].items() if not Path(p).is_relative_to(old_project)}
                lock['files'] = dict(sorted((outside | hashes).items()))
                relocated = lambda p: p.replace(str(parent), str(out))
                local = lambda p: Path(p.replace(str(parent), str(temp)))
                lock['robot_configs'] = {relocated(p):sha(local(p)) for p in base['robot_configs']}
                lock['task_sources'] = {relocated(p):sha(local(p) / 'robocasa_tasks/__init__.py') for p in base['task_sources']}
                receipt = dict(deployment_kind='dev-local', adapter_revision='dev-local', formal_eligible=False,
                    base_deployment_lock_path=str(base_path), base_deployment_lock_sha256=base_sha,
                    **{k:base['provenance'][k] for k in ('parent_lock_sha256','parent_lock_path','official_provenance')},
                    source_root=str(repo), source_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
                    source_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True).strip()),
                    source_manifest=manifest, relocated_project_files=list(RELOCATED_YAMLS),
                    changed_project_files=sorted(set(manifest) | set(RELOCATED_YAMLS)), snapshot_key=key)
                lock['provenance'] = receipt
                _write(temp / 'source-manifest.json', manifest)
                _write(temp / 'dev-local.json', receipt)
                _write(temp / 'runtime-lock.json', lock)
                for p in [*temp.rglob('*'), temp]:
                    p.chmod(0o444 if p.is_file() else 0o555)
                temp.rename(out)
            except BaseException:
                if temp.exists():
                    for p in [temp, *temp.rglob('*')]:
                        if p.is_dir():p.chmod(0o755)
                    shutil.rmtree(temp)
                raise
            lock, lock_sha = identity.load_lock(out / 'runtime-lock.json', tier='dev', boundary='dev_loop')
            identity.verify_files(lock)
        return dict(lock_path=str(out / 'runtime-lock.json'), sha256=lock_sha, snapshot_key=key,
                    source_root=receipt['source_root'], source_head=receipt['source_head'],
                    source_dirty=receipt['source_dirty'], changed_files=receipt['changed_project_files'])
