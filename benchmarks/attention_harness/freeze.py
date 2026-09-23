"""Create and verify the deterministic AttentionBench v1 freeze manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .parity import load_dataset, passed


REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = Path("benchmarks/attention_harness/protocol/v1/protocol.json")
FREEZE_INPUTS = (
    PROTOCOL,
    Path("benchmarks/attention_harness/protocol/v1/seeds.json"),
    Path("benchmarks/attention_harness/protocol/v1/evidence/legacy_probes.json"),
    Path("benchmarks/attention_harness/protocol/v1/evidence/native_probes.json"),
    Path("benchmarks/attention_harness/protocol/v1/evidence/parity_report.json"),
    Path("benchmarks/attention_harness/artifacts.py"),
    Path("benchmarks/attention_harness/parity.py"),
    Path("benchmarks/attention_harness/freeze.py"),
    Path("benchmarks/attention_harness/migration/export_evaluator_probes.py"),
    Path("benchmarks/attention_harness/run_d6_parity.sh"),
    Path("benchmarks/attention_harness/task_registry.py"),
    Path("benchmarks/attention_harness/seed_guard.py"),
    Path("benchmarks/attention_harness/robosuite_adapter.py"),
    Path("benchmarks/attention_harness/robot_sdk.py"),
    Path("benchmarks/attention_harness/sandbox.py"),
    Path("benchmarks/attention_harness/sandbox_worker.py"),
    Path("benchmarks/attention_harness/model_protocol.py"),
    Path("benchmarks/attention_harness/parcc_client.py"),
    Path("benchmarks/attention_harness/parcc_runner.py"),
    Path("benchmarks/attention_harness/reference_policy.py"),
    Path("benchmarks/attention_harness/runner.py"),
    Path("benchmarks/attention_harness/service_process.py"),
    Path("benchmarks/attention_harness/requirements-robosuite.txt"),
    Path("robosuite_sim/backend.py"),
    Path("robosuite_sim/client.py"),
    Path("robosuite_sim/codec.py"),
    Path("robosuite_sim/server.py"),
    Path("robosuite_sim/tasks.py"),
    Path("robosuite_sim/reference_policy.py"),
    Path("robosuite_sim/requirements.txt"),
)


class FreezeError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FreezeError(f"{path} must contain a JSON object")
    return value


def _canonical_digest(files: dict[str, str]) -> str:
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_manifest(*, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    protocol = _read_json(repo_root / PROTOCOL)
    parity_path = repo_root / "benchmarks/attention_harness/protocol/v1/evidence/parity_report.json"
    parity = _read_json(parity_path)
    if not passed(parity):
        raise FreezeError("D6 evaluator parity gate has not passed")
    files: dict[str, str] = {}
    for relative in FREEZE_INPUTS:
        path = repo_root / relative
        if not path.is_file():
            raise FreezeError(f"freeze input is missing: {relative}")
        files[relative.as_posix()] = sha256_file(path)
    heldout_ready = bool(
        protocol.get("heldout_ready")
        and protocol.get("formal_model_policies", {}).get("heldout_eligible")
        and protocol.get("formal_model_policies", {}).get("status") == "frozen"
    )
    return {
        "schema_version": "attentionbench.freeze-manifest.v1",
        "protocol_id": protocol["protocol_id"],
        "freeze_scope": "Robosuite harness contract and privileged reference policy",
        "file_sha256": files,
        "freeze_digest_sha256": _canonical_digest(files),
        "d6_parity": {
            "agreement": parity["agreement"],
            "compared": parity["compared"],
            "predicate_source_match": parity["predicate_source_match"],
            "version_match": parity["version_match"],
        },
        "formal_model_policies": protocol["formal_model_policies"],
        "heldout_ready": heldout_ready,
    }


def verify_manifest(
    manifest_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
    require_heldout_ready: bool = False,
) -> dict[str, Any]:
    manifest = _read_json(manifest_path)
    files = manifest.get("file_sha256")
    if not isinstance(files, dict) or not files:
        raise FreezeError("manifest contains no frozen files")
    mismatches = []
    for relative, expected in sorted(files.items()):
        path = repo_root / relative
        actual = sha256_file(path) if path.is_file() else None
        if actual != expected:
            mismatches.append({"path": relative, "expected": expected, "actual": actual})
    digest_matches = manifest.get("freeze_digest_sha256") == _canonical_digest(files)
    if mismatches or not digest_matches:
        raise FreezeError(
            json.dumps(
                {"file_mismatches": mismatches, "manifest_digest_matches": digest_matches},
                sort_keys=True,
            )
        )
    if require_heldout_ready and not manifest.get("heldout_ready"):
        raise FreezeError(
            "held-out run denied: formal task-solving model policies are not frozen"
        )
    return {
        "verified": True,
        "files": len(files),
        "freeze_digest_sha256": manifest["freeze_digest_sha256"],
        "heldout_ready": bool(manifest.get("heldout_ready")),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "benchmarks/attention_harness/protocol/v1/freeze_manifest.json",
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument(
        "manifest",
        type=Path,
        nargs="?",
        default=REPO_ROOT / "benchmarks/attention_harness/protocol/v1/freeze_manifest.json",
    )
    verify.add_argument("--require-heldout-ready", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "create":
        manifest = create_manifest()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    result = verify_manifest(
        args.manifest,
        require_heldout_ready=args.require_heldout_ready,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FreezeError as exc:
        print(f"freeze error: {exc}")
        raise SystemExit(2) from None
