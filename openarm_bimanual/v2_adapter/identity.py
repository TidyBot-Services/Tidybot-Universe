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
import threading
from typing import Any


class IdentityError(RuntimeError):
    """The requested planner is not the locked, isolated V2 runtime."""


_service_context = None
OFFICIAL_COMMIT = '78fd485fa82d9b9a063fb4985e371814587e666a'
TRUSTED_LOCKS = {'207d7e82a716cd8dd71a1f6ba56fe3843edf365a913065ab937a24238128399c': OFFICIAL_COMMIT}
DEPLOYMENT_REGISTRY = Path('/home/truares/Documents/Codex/2026-10-12/curobov2-openarm-ui-ui-runner-sdk/outputs/deployment-13/trusted_deployments.json')
OFFICIAL_PARENT_LOCK = Path('/home/truares/Documents/Codex/2026-10-11/goal-curobov2-nvidia-service-1-service/outputs/revisions/r12/runtime-lock.json')
_registry_digest = None


def approved_deployment(lock, lock_sha, path):
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
        resources=entry.get('native_resource_files',{})
        if (not isinstance(resources,dict) or any(not Path(k).is_absolute()or not isinstance(v,str)or len(v)!=64 for k,v in resources.items())
                or set(resources)&set(outside) or actual != dict(outside,**resources)):
            raise IdentityError('Derived deployment changes undeclared files outside its adapter tree')
        _registry_digest = registry_sha
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


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


def load_lock(path: str | Path | None = None) -> tuple[dict, str]:
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
    if (not approved_deployment(lock, lock_sha, configured)
            or lock.get('official_only') is not True
            or lock['library_revision'] != OFFICIAL_COMMIT or lock['library_base'] != OFFICIAL_COMMIT):
        raise IdentityError('Unapproved V2 manifest; a version string or regenerated local hash cannot establish official identity')
    return lock, lock_sha


_verification_mutex = threading.RLock()

def verify_files(lock: dict) -> None:
    # Rehash every file on every verification. Timestamp caching is unsafe on
    # filesystems where same-size writes can share a coarse ctime. The native
    # source watch runs in its own owned process to retain the freshness bound.
    import stat
    with _verification_mutex:
        if _registry_digest is not None and digest(DEPLOYMENT_REGISTRY) != _registry_digest:
            raise IdentityError('Deployment trust policy changed during execution')
        for directory, dirs, files in os.walk(lock['project_root'], followlinks=False):
            for child in dirs + files:
                name = os.path.join(directory, child)
                if os.path.islink(name) or child in files and name not in lock['files']:
                    raise IdentityError('Unlisted or linked file in locked service/config tree: '+name)
        for name, expected in lock['files'].items():
            try:
                before = os.stat(name, follow_symlinks=False)
                signature = (before.st_dev, before.st_ino, before.st_size,
                             before.st_mtime_ns, before.st_ctime_ns, before.st_mode)
                if not os.path.isabs(name) or not stat.S_ISREG(before.st_mode):
                    raise IdentityError('Locked path is not a regular absolute file: '+name)
                with open(name, 'rb') as stream:
                    hasher=hashlib.sha256()
                    for block in iter(lambda:stream.read(1024*1024),b''):hasher.update(block)
                    actual=hasher.hexdigest()
                    after = os.fstat(stream.fileno())
                final = (after.st_dev, after.st_ino, after.st_size,
                         after.st_mtime_ns, after.st_ctime_ns, after.st_mode)
                if actual != expected or final != signature:
                    raise IdentityError('Locked source or dependency changed: '+name)
            except OSError as exc:
                raise IdentityError('Locked source or dependency unavailable: '+name) from exc


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
    try:legacy=importlib.metadata.distribution('curobo')
    except importlib.metadata.PackageNotFoundError:legacy=None
    if legacy is not None:
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
