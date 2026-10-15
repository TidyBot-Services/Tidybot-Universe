"""Pure official source/runtime/asset verifier, independent of all executors."""
from pathlib import Path
import hashlib,subprocess,importlib.metadata
COMMITS={"robocasa":"456174f62b89b8fca99eaaf33949c29fec9cfc2a","robosuite":"5ce6643f3092639d08f7b0f90ed1c6a84f50552c"}
VERSIONS={"robocasa":"1.0.1","robosuite":"1.5.2"}
INSTALLED_ASSET_MANIFEST_SHA="6ba5a9d08f036b3a729eb3bc769f7b8ab2714ec1de94c5a9b72dbc1915199a01"
import json
class OfficialEnvironmentIdentity:
    def __init__(self,roots,manifest):
        self.roots={k:Path(v).resolve() for k,v in roots.items()};self.manifest=Path(manifest)
    def verify_identity(self):
        identity = {}
        for name, expected in COMMITS.items():
            root = self.roots[name]
            actual = subprocess.check_output(
                ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
            ).strip()
            dirty = subprocess.check_output(
                ["git", "-C", str(root), "diff", "--name-only", "HEAD"], text=True
            ).strip()
            if actual != expected or dirty:
                raise RuntimeError(
                    "official source identity/cleanliness mismatch: " + name
                )
            untracked = subprocess.check_output(
                ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
                text=True,
            ).splitlines()
            if any(
                not filename.startswith("robocasa/models/assets/")
                for filename in untracked
            ):
                raise RuntimeError("unexpected untracked official source")
            version = importlib.metadata.version(name)
            if version != VERSIONS[name]:
                raise RuntimeError("official package version mismatch: " + name)
            identity[name] = {"commit": actual, "root": str(root), "version": version}
        import robocasa, robosuite

        for name, module in [("robocasa", robocasa), ("robosuite", robosuite)]:
            if not Path(module.__file__).resolve().is_relative_to(self.roots[name]):
                raise RuntimeError("imported wrong official checkout")
        assets = self.roots["robocasa"] / "robocasa/models/assets"
        manifest = json.loads(self.manifest.read_bytes())
        lock_path = self.manifest.with_name("assets-installation-lock.json")
        if not lock_path.is_file():
            raise RuntimeError("official missing-dependency installation lock required")
        lock = json.loads(lock_path.read_bytes())
        registry = assets / "box_links/box_links_assets.json"
        installed_path = self.manifest.with_name("assets-installed-manifest.json")
        if (
            lock["official_commit"] != COMMITS["robocasa"]
            or lock["base_manifest_sha256"]
            != hashlib.sha256(self.manifest.read_bytes()).hexdigest()
            or lock["asset_registry_sha256"]
            != hashlib.sha256(registry.read_bytes()).hexdigest()
            or lock["installed_manifest_sha256"]
            != hashlib.sha256(installed_path.read_bytes()).hexdigest()
        ):
            raise RuntimeError("official asset installation identity mismatch")
        if lock["installed_manifest_sha256"] != INSTALLED_ASSET_MANIFEST_SHA:
            raise RuntimeError("unreviewed installed asset manifest")
        installed = json.loads(installed_path.read_bytes())
        if any(installed.get(name) != sha for name, sha in manifest.items()):
            raise RuntimeError("asset installation changed frozen baseline")
        manifest = installed
        actual_files = {
            str(p.relative_to(assets))
            for p in assets.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }
        if actual_files != set(manifest):
            raise RuntimeError("official asset file set differs from installed lock")
        for name, sha in manifest.items():
            p = assets / name
            if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
                raise RuntimeError("asset mismatch: " + name)
        identity["assets"] = {
            "manifest_sha256": hashlib.sha256(self.manifest.read_bytes()).hexdigest(),
            "files_verified": len(manifest),
            "root": str(assets),
            "installed_manifest_sha256": lock["installed_manifest_sha256"],
            "installation_lock_sha256": hashlib.sha256(
                lock_path.read_bytes()
            ).hexdigest(),
            "packs": lock["packs"],
        }
        identity["runtime"] = {
            k: importlib.metadata.version(k)
            for k in ("numpy", "numba", "scipy", "mujoco")
        }
        expected_runtime = {
            "numpy": "2.2.5",
            "numba": "0.61.2",
            "scipy": "1.15.3",
            "mujoco": "3.3.1",
        }
        if identity["runtime"] != expected_runtime:
            raise RuntimeError("frozen runtime version mismatch")
        identity["service"] = {
            "version": importlib.metadata.version("robocasa365_sim"),
            "source_sha256": {
                p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(Path(__file__).parent.glob("*.py"))
            },
        }
        return identity

