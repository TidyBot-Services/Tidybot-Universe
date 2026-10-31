"""Fail closed on runtime, source, dependency, or service identity mismatch."""
from __future__ import annotations

import hashlib
import hmac
import importlib
import importlib.abc
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from . import official_normalization


class IdentityError(RuntimeError):
    """The requested planner is not the locked, isolated V2 runtime."""


_service_context = None
OFFICIAL_COMMIT = '78fd485fa82d9b9a063fb4985e371814587e666a'
TRUSTED_LOCKS = {'207d7e82a716cd8dd71a1f6ba56fe3843edf365a913065ab937a24238128399c': OFFICIAL_COMMIT}
DEPLOYMENT_REGISTRY = Path(__file__).resolve().with_name('trusted_deployments.json')
OFFICIAL_PARENT_LOCK = Path('/home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/outputs/revisions/r12/runtime-lock.json')
_registry_digest = None
_local_registry_digest = None
OFFICIAL_MANIFESTS = Path(__file__).resolve().with_name('official')


def _local_registry_path():
    try:
        from benchmarks.attention_harness.workspace_paths import get
        return get('planner.local_dev_trust')
    except (ImportError, OSError, ValueError, RuntimeError, KeyError):
        return None


def _local_registry():
    """Read a protected local policy; absence is not an approval."""
    global _local_registry_digest
    path = _local_registry_path()
    if path is None or not path.is_absolute():
        return None
    try:
        if (any(part.is_symlink() for part in (path, *path.parents))
                or not path.is_file()):
            return None
        stat = path.stat()
        raw = path.read_bytes()
        current = hashlib.sha256(raw).hexdigest()
        if _local_registry_digest is not None and current != _local_registry_digest:
            raise IdentityError('Local dev trust policy changed during this process')
        if stat.st_uid != os.getuid() or stat.st_mode & 0o022:
            return None
        value = json.loads(raw)
        if (not isinstance(value, dict)
                or value.get('schema') != 'attentionbench.curobo-v2.local-dev-trust.v1'
                or value.get('official_commit') != OFFICIAL_COMMIT):
            return None
        _local_registry_digest = current
        return value
    except (OSError, ValueError, TypeError):
        return None


def _local_entry(registry, *keys):
    value = registry
    for key in keys:
        value = value[key]
    path = Path(value['lock_path'])
    if (not path.is_absolute() or any(p.is_symlink() for p in (path, *path.parents))
            or len(value['sha256']) != 64 or any(c not in '0123456789abcdef' for c in value['sha256'])):
        raise ValueError('Invalid local lock entry')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise ValueError('Local lock digest mismatch')
    return json.loads(raw), value['sha256'], path


def _portable_files(lock, root):
    root = Path(root)
    if not root.is_absolute() or not isinstance(lock['files'], dict):
        raise ValueError('Invalid portable file root')
    result = {}
    for name, sha in lock['files'].items():
        path = Path(name)
        if path.is_relative_to(root):
            relative = path.relative_to(root)
            if (not relative.parts or '..' in relative.parts or not isinstance(sha, str)):
                raise ValueError('Invalid portable file entry')
            result[str(relative)] = sha
    return result


def _official_content(parent):
    if (parent.get('official_only') is not True
            or parent.get('library_revision') != OFFICIAL_COMMIT
            or parent.get('library_base') != OFFICIAL_COMMIT):
        return False
    def manifest(name):
        return json.loads((OFFICIAL_MANIFESTS / name).read_bytes())
    source = _portable_files(parent, parent['library_root'])
    if source != manifest('curobo-78fd485-source.json'):
        return False
    if any(digest(Path(parent['library_root']) / name) != expected for name, expected in source.items()):
        return False
    if parent['distributions'] != manifest('r12-distributions.json'):
        return False
    prefix = Path(parent['prefix'])
    dependencies = {name: {'path': str(Path(item['path']).relative_to(prefix)),
                           'sha256': item['sha256']}
                    for name, item in parent['dependencies'].items()}
    if manifest('allowed-path-files.json') != []:
        return False
    author_paths = manifest('author-paths.json')
    wheel_hashes = manifest('r12-wheel-normalized.json')
    record_hashes = manifest('r12-record-normalized.json')
    author_shebangs = manifest('r12-bin-shebangs.json')
    expected_prefix = manifest('r12-prefix-files.json')
    if (not isinstance(author_paths, dict)
            or set(author_paths) != {'prefix', 'library_root', 'binary_prefix', 'binary_prefix_slot', 'script_prefix', 'origin_prefix'}
            or any(not isinstance(author_paths[key], str) or not author_paths[key].startswith('/')
                   for key in ('prefix', 'library_root', 'binary_prefix', 'script_prefix', 'origin_prefix'))
            or type(author_paths['binary_prefix_slot']) is not int
            or not isinstance(wheel_hashes, dict)
            or set(wheel_hashes) != {name for name in expected_prefix if name.endswith('/WHEEL')}
            or set(record_hashes) != {name for name in expected_prefix if name.endswith('/RECORD')}
            or not isinstance(author_shebangs, dict)
            or any(not isinstance(key, str) or '/' in key or not isinstance(value, str)
                   or value not in ('#!' + author_paths['script_prefix'] + '/bin/python',
                                    '#!' + author_paths['script_prefix'] + '/bin/python3.11')
                   for key, value in author_shebangs.items())
            or any(not isinstance(value, str) or len(value) != 64
                   or any(c not in '0123456789abcdef' for c in value)
                   for value in (*wheel_hashes.values(), *record_hashes.values()))):
        return False
    def portable_match(name, local_sha, expected_sha):
        return official_normalization.matches(name, prefix / name, local_sha, expected_sha,
                                              str(prefix), parent['library_root'], author_paths,
                                              wheel_hashes, record_hashes, author_shebangs) is not None
    expected_deps = manifest('r12-dependencies.json')
    if (set(dependencies) != set(expected_deps)
            or any(actual['path'] != expected_deps[name]['path']
                   or (actual['sha256'] != expected_deps[name]['sha256']
                       and not portable_match(actual['path'], actual['sha256'], expected_deps[name]['sha256']))
                   for name, actual in dependencies.items())):
        return False
    actual_prefix = _portable_files(parent, prefix)
    if (set(actual_prefix) != set(expected_prefix)
            or any(actual_prefix[name] != expected_prefix[name]
                   and not portable_match(name, actual_prefix[name], expected_prefix[name])
                   for name in actual_prefix)):
        return False
    native = parent['native_assets']
    path = Path(native['manifest'])
    if (not path.is_absolute() or digest(path) != native['manifest_sha256']):
        return False
    entries = json.loads(path.read_bytes())['files']
    assets = {item['relative']: item['sha256'] for item in entries}
    expected = manifest('r12-native-assets.json')
    if expected.get('format') == 'canonical-relative-path-sha256-v1':
        encoded = json.dumps(assets, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
        if len(assets) != expected['count'] or hashlib.sha256(encoded).hexdigest() != expected['sha256']:
            return False
    elif assets != expected:
        return False
    host = parent['native_host_dependencies']
    if (not isinstance(host, dict) or not host
            or not any('libcuda' in name for name in host)
            or not any('libc.so.6' in name for name in host)):
        return False
    return True


def _derived_from_parent(lock, parent):
    mutable = {'project_root','service_root','assets_root','files','robot_configs','task_sources','provenance'}
    if set(lock) != set(parent) or any(lock[k] != parent[k] for k in parent.keys()-mutable):
        return False
    outside = {k:v for k,v in parent['files'].items() if not Path(k).is_relative_to(parent['project_root'])}
    actual = {k:v for k,v in lock['files'].items() if not Path(k).is_relative_to(lock['project_root'])}
    return actual == outside


def _locally_trusted_dev(lock, lock_sha, path, tier, boundary):
    if tier != 'dev' or boundary != 'dev_loop':
        return False
    registry = _local_registry()
    if registry is None:
        return False
    return _validate_local_chain(registry, lock, lock_sha, path)


def _validate_local_chain(registry, lock, lock_sha, path):
    """Shared fail-closed verification for the reader and registry builder."""
    try:
        proof = registry['build_proof']
        if (proof['passed'] is not True or any(not isinstance(proof[k], str) or len(proof[k]) != 64
                or any(c not in '0123456789abcdef' for c in proof[k])
                for k in ('request_set_sha256', 'result_sha256'))):
            return False
        parent, parent_sha, parent_path = _local_entry(registry, 'official_parent')
        if not _official_content(parent):
            return False
        d05, d05_sha, d05_path = _local_entry(registry, 'chain', 'd05')
        d06, d06_sha, d06_path = _local_entry(registry, 'chain', 'd06')
        if (registry['chain']['d06']['derived_from_sha256'] != d05_sha
                or registry['chain']['d06']['profile'] != 'r3-20261025'
                or not _derived_from_parent(d05, parent)
                or not _derived_from_parent(d06, parent)
                or d05.get('provenance', {}).get('adapter_revision') != 'deployment-05'
                or d06.get('provenance', {}).get('adapter_revision') != 'deployment-06'
                or d05.get('provenance', {}).get('parent_lock_path') != str(parent_path)
                or d06.get('provenance', {}).get('parent_lock_path') != str(parent_path)
                or d05.get('provenance', {}).get('parent_lock_sha256') != parent_sha
                or d06.get('provenance', {}).get('parent_lock_sha256') != parent_sha
                or d06.get('provenance', {}).get('parent_adapter_lock_path') != str(d05_path)
                or d06.get('provenance', {}).get('parent_adapter_lock_sha256') != d05_sha):
            return False
        candidate = (d05, d05_sha, d05_path) if lock_sha == d05_sha else (d06, d06_sha, d06_path)
        return (lock_sha == candidate[1] and str(path) == str(candidate[2])
                and lock == candidate[0])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def _registered_deployment(lock, lock_sha, path, *, tier=None, boundary=None):
    global _registry_digest
    if lock_sha in TRUSTED_LOCKS:
        return True
    try:
        raw = DEPLOYMENT_REGISTRY.read_bytes()
        registry_sha = hashlib.sha256(raw).hexdigest()
        if _registry_digest is not None and registry_sha != _registry_digest:
            raise IdentityError('Deployment trust policy changed during this process')
        entries = json.loads(raw)
        entry = entries.get(lock_sha)
        if (not isinstance(entry, dict) or entry.get('lock_path') != str(Path(path).resolve())
                or entry.get('official_commit') != OFFICIAL_COMMIT
                or entry.get('parent_sha256') not in TRUSTED_LOCKS):
            return False
        if 'allowed_tiers' in entry:
            allowed = entry['allowed_tiers']
            boundary_tier = 'dev' if boundary == 'dev_loop' else boundary
            if (not isinstance(allowed, list) or not allowed
                    or any(type(item) is not str for item in allowed)
                    or tier not in allowed or boundary_tier not in allowed
                    or not isinstance(tier, str) or not isinstance(boundary, str)
                    or 'formal' in tier.lower() or 'formal' in boundary.lower()):
                return False
        parent_raw = OFFICIAL_PARENT_LOCK.read_bytes()
        if hashlib.sha256(parent_raw).hexdigest() != entry['parent_sha256']:
            raise IdentityError('Official parent evidence changed')
        parent = json.loads(parent_raw)
        # Only adapter/config relocation may differ. Official content, Python,
        # dependencies, native assets and external source locks remain exact.
        mutable = {'project_root','service_root','assets_root','files','robot_configs','task_sources','provenance'}
        if set(lock) != set(parent) or any(lock[k] != parent[k] for k in parent.keys()-mutable):
            raise IdentityError('Derived adapter deployment changes official source/environment/native identity')
        outside = {k:v for k,v in parent['files'].items() if not Path(k).is_relative_to(parent['project_root'])}
        actual = {k:v for k,v in lock['files'].items() if not Path(k).is_relative_to(lock['project_root'])}
        if actual != outside:
            raise IdentityError('Derived deployment changes files outside its adapter tree')
        _registry_digest = registry_sha
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def approved_deployment(lock, lock_sha, path, *, tier=None, boundary=None):
    return _approval_source(lock, lock_sha, path, tier=tier, boundary=boundary) is not None


def _approval_source(lock, lock_sha, path, *, tier=None, boundary=None):
    if _registered_deployment(lock, lock_sha, path, tier=tier, boundary=boundary):
        return 'repository'
    if tier == 'dev' and boundary == 'dev_loop':
        from .dev_local import is_dev_local, verify_snapshot
        if is_dev_local(lock):
            if verify_snapshot(path, lock, lock_sha):
                base_sha = lock['provenance']['base_deployment_lock_sha256']
                return ('repository' if base_sha in json.loads(DEPLOYMENT_REGISTRY.read_bytes())
                        else 'local-dev-registry')
            return None
        if _locally_trusted_dev(lock, lock_sha, path, tier, boundary):
            return 'local-dev-registry'
    return None


def authorize_service_import(lock: dict, robot_config: str) -> None:
    global _service_context
    verify_runtime(lock)
    path = Path(robot_config)
    if (not path.is_absolute() or str(path.resolve()) != robot_config
            or lock["robot_configs"].get(robot_config) != digest(path)):
        raise IdentityError("Planner import requires an explicit locked robot configuration")
    _service_context = (lock, robot_config)


def require_service_context() -> tuple[dict, str]:
    if _service_context is None:
        raise IdentityError("Direct planner startup forbidden; use the owned V2Service boundary")
    return _service_context


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def load_lock(path: str | Path | None = None, *, tier=None, boundary=None) -> tuple[dict, str]:
    configured = path or os.environ.get("CUROBO_V2_LOCK")
    if not configured or not Path(configured).is_absolute():
        raise IdentityError("CUROBO_V2_LOCK must explicitly name an absolute locked manifest")
    try:
        raw = Path(configured).read_bytes()
        lock = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise IdentityError("cuRoboV2 lock is missing or invalid") from exc
    if lock.get("schema") != "attentionbench.curobo-v2.runtime.v1" or lock.get("generation") != 2:
        raise IdentityError("Manifest does not identify cuRoboV2")
    required = {"prefix", "python", "library_root", "library_revision", "library_base",
                "distribution_version", "module_version", "files", "dependencies",
                "distributions", "robot_configs", "service_root", "project_root"}
    if not required <= lock.keys() or not lock["files"] or not lock["dependencies"]:
        raise IdentityError("cuRoboV2 lock has missing configuration")
    for key in ("prefix", "python", "library_root", "service_root", "project_root"):
        if not Path(lock[key]).is_absolute():
            raise IdentityError("Lock paths must be absolute: " + key)
    lock_sha = hashlib.sha256(raw).hexdigest()
    source = _approval_source(lock, lock_sha, configured, tier=tier, boundary=boundary)
    if (source is None
            or lock.get('official_only') is not True
            or lock['library_revision'] != OFFICIAL_COMMIT or lock['library_base'] != OFFICIAL_COMMIT):
        raise IdentityError('Unapproved V2 manifest; a version string or regenerated local hash cannot establish official identity')
    if source == 'local-dev-registry':
        lock = dict(lock, trust_source=source)
    return lock, lock_sha


def verify_files(lock: dict) -> None:
    if _registry_digest is not None and digest(DEPLOYMENT_REGISTRY) != _registry_digest:
        raise IdentityError('Deployment trust policy changed during execution')
    project = Path(lock['project_root'])
    for path in project.rglob('*'):
        if path.is_symlink() or path.is_file() and str(path) not in lock['files']:
            raise IdentityError('Unlisted or linked file in locked service/config tree: '+str(path))
    for name, expected in lock["files"].items():
        p = Path(name)
        if not p.is_absolute() or not p.is_file() or digest(p) != expected:
            raise IdentityError("Locked source or dependency changed: " + name)


def verify_client_imports(lock: dict) -> None:
    """A remote client may have no curobo package, but may never expose V1."""
    library = Path(lock["library_root"]).resolve()
    for name in ("curobo", "nvidia-curobo"):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        if name == "curobo" or version != lock["distribution_version"]:
            raise IdentityError("Planning client has an installed legacy cuRobo distribution: " + name)
    spec = importlib.util.find_spec("curobo")
    if spec is not None and (not spec.origin or Path(spec.origin).resolve() != library / "curobo/__init__.py"):
        raise IdentityError("Planning client exposes a legacy or unapproved curobo import path")
    for name, module in list(sys.modules.items()):
        if name == "curobo" or name.startswith("curobo."):
            path = getattr(module, "__file__", None)
            if (name.startswith(("curobo.wrap", "curobo.cuda_robot_model"))
                    or path and not Path(path).resolve().is_relative_to(library)):
                raise IdentityError("Planning client has loaded cuRoboV1: " + name)


def _distributions() -> dict[str, str]:
    result = {}
    for d in importlib.metadata.distributions():
        name = d.metadata["Name"].lower().replace("_", "-")
        if name in result:
            raise IdentityError("Duplicate installed distribution: " + name)
        result[name] = d.version
    return result


def verify_runtime(lock: dict, *, full: bool = True, owner: bool = False) -> dict:
    prefix = Path(lock["prefix"]).resolve()
    library = Path(lock["library_root"]).resolve()
    if (Path(sys.prefix).resolve() != prefix or Path(sys.base_prefix).resolve() != prefix
            or Path(sys.executable).resolve() != Path(lock["python"]).resolve()):
        raise IdentityError("Use the explicitly locked independent cuRoboV2 Python prefix")
    spec = importlib.util.find_spec("curobo")
    if spec is None or not spec.origin or Path(spec.origin).resolve() != library / "curobo/__init__.py":
        raise IdentityError("Actual curobo import path differs from locked V2 source")
    # V2 still reports 0.8.x in this pinned upstream tree. A >=2 version check
    # would reject the actual V2; authenticate distribution, commit, API and path.
    if "curobo" in _distributions():
        raise IdentityError("Legacy curobo distribution is installed")
    for name, module in list(sys.modules.items()):
        if name == "curobo" or name.startswith("curobo."):
            path = getattr(module, "__file__", None)
            if path and not Path(path).resolve().is_relative_to(library):
                raise IdentityError("Loaded curobo module outside locked V2: " + name)
            if path and str(Path(path).resolve()) not in lock["files"]:
                raise IdentityError("Loaded curobo module is absent from source lock: " + name)
            if name.startswith(("curobo.wrap", "curobo.cuda_robot_model")):
                raise IdentityError("Legacy cuRoboV1 API loaded: " + name)
    if (lock.get("official_only") is not True or lock["library_revision"] != OFFICIAL_COMMIT
            or lock["library_base"] != OFFICIAL_COMMIT):
        raise IdentityError("Only an exact unmodified official commit is allowed")
    if full:
        verify_files(lock)
        for path,expected in lock.get('native_host_dependencies',{}).items():
            if digest(Path(path)) != expected:
                raise IdentityError('Native host dependency changed: '+path)
        status = subprocess.check_output(["git","status","--porcelain","--untracked-files=no"],cwd=library,text=True)
        if status.strip():
            raise IdentityError("Official solver checkout has local changes")
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=library, text=True).strip()
        if revision != lock["library_revision"]:
            raise IdentityError("Actual cuRoboV2 commit differs from lock")
        subprocess.run(["git", "merge-base", "--is-ancestor", lock["library_base"], revision],
                       cwd=library, check=True, capture_output=True)
        if _distributions() != lock["distributions"]:
            raise IdentityError("Installed dependencies differ from V2 lock")
    allowed = [prefix, library, Path(lock["project_root"]).resolve()]
    if owner:
        allowed.extend(Path(p).resolve() for p in lock.get("task_sources", {}))
    for value in sys.path:
        if not value or value.startswith("__editable__"):
            continue
        if not any(Path(value).resolve().is_relative_to(p) for p in allowed):
            raise IdentityError("External Python search path: " + value)
    modules = {}
    for name, expected in lock["dependencies"].items():
        module = importlib.import_module(name)
        actual = Path(module.__file__).resolve()
        if (str(actual) != expected["path"] or not actual.is_relative_to(prefix)
                or digest(actual) != expected["sha256"]):
            raise IdentityError("Actual dependency import changed: " + name)
        modules[name] = str(actual)
    for name, module in list(sys.modules.items()):
        if any(name == dep or name.startswith(dep + ".") for dep in lock["dependencies"]):
            path = getattr(module, "__file__", None)
            # PyTorch registers these virtual namespaces with placeholder
            # filenames. Their concrete implementation modules are checked below.
            virtual = {"torch.ops": ("_ops.py", "torch._ops"),
                       "torch.classes": ("_classes.py", "torch._classes")}
            if (name in virtual and (path, type(module).__module__) == virtual[name]
                    and getattr(module, "__spec__", None) is None):
                continue
            if path and not Path(path).resolve().is_relative_to(prefix):
                raise IdentityError("Loaded dependency outside independent prefix: " + name)
    curobo = importlib.import_module("curobo")
    if curobo.__version__ != lock["module_version"]:
        raise IdentityError("Actual curobo version differs from V2 lock")
    if importlib.metadata.version("nvidia-curobo") != lock["distribution_version"]:
        raise IdentityError("V2 distribution version differs from lock")
    for name in ("curobo.motion_planner", "curobo.scene", "curobo.types"):
        api = importlib.util.find_spec(name)
        if api is None or not api.origin or not Path(api.origin).resolve().is_relative_to(library):
            raise IdentityError("Required V2 API missing or shadowed: " + name)
    return {"generation": 2, "prefix": str(prefix), "python": str(Path(sys.executable).resolve()),
            "library_path": str(Path(curobo.__file__).resolve()), "module_version": curobo.__version__,
            "library_revision": lock["library_revision"], "dependencies": modules}


class _RejectLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path=None, target=None):
        if fullname.startswith(("curobo.wrap", "curobo.cuda_robot_model")):
            raise IdentityError("cuRoboV1 import is forbidden: " + fullname)
        return None


def install_import_guard() -> None:
    if not any(isinstance(f, _RejectLegacy) for f in sys.meta_path):
        sys.meta_path.insert(0, _RejectLegacy())


def sign(token: str, identity: dict, challenge: str) -> str:
    return hmac.new(token.encode(), canonical({"identity": identity, "challenge": challenge}),
                    hashlib.sha256).hexdigest()


def verify_service(payload: dict, *, lock: dict, lock_sha: str, nonce: str,
                   pid: int, token: str, challenge: str, robot_config: str,
                   test_response_fault: str = 'none',test_gpu_activity: bool = False) -> dict:
    identity = payload.get("identity", {})
    expected_runtime = {"generation": 2, "prefix": str(Path(lock["prefix"]).resolve()),
                        "python": str(Path(lock["python"]).resolve()),
                        "library_path": str(Path(lock["library_root"]).resolve() / "curobo/__init__.py"),
                        "module_version": lock["module_version"], "library_revision": lock["library_revision"],
                        "dependencies": {k: v["path"] for k, v in lock["dependencies"].items()}}
    if (identity.get("runtime") != expected_runtime or identity.get("lock_sha256") != lock_sha
            or identity.get('test_response_fault') != test_response_fault
            or identity.get('test_gpu_activity') is not test_gpu_activity
            or identity.get("kernel_backend") != "cuda_core"
            or identity.get("nonce") != nonce or identity.get("pid") != pid
            or identity.get("service_root") != lock["service_root"]
            or identity.get("robot_config") != robot_config
            or identity.get("robot_config_sha256") != lock["robot_configs"].get(robot_config)
            or not isinstance(payload.get("signature"), str)
            or not hmac.compare_digest(payload["signature"], sign(token, identity, challenge))):
        raise IdentityError("Planner service identity mismatch; no planning or fallback permitted")
    return identity
