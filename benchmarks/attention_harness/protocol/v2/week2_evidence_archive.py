"""Build or verify a relocatable SHA-256 Week 2 raw evidence archive.

Verification uses only Python's standard library and does not need the source
checkouts or the original absolute paths embedded in historical raw receipts.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(root: Path, prefix: str):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield f"{prefix}/{path.relative_to(root).as_posix()}", path.read_bytes()


def _add(stream: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    stream.addfile(info, io.BytesIO(data))


def build(args: argparse.Namespace) -> None:
    sources = (("graph_live", args.graph_root),
               ("prior_auto_smoke", args.prior_root),
               ("memory_effect", args.memory_root))
    entries: dict[str, dict[str, int | str]] = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(args.output, "w:gz") as stream:
        for prefix, root in sources:
            for name, data in _files(root.resolve(), prefix):
                _add(stream, name, data)
                entries[name] = {"sha256": digest(data), "size": len(data)}
        for name, path in (("logs/clean_install_final.log", args.clean_install_log),
                           ("tools/week2_evidence_archive.py", Path(__file__))):
            data = path.read_bytes()
            _add(stream, name, data)
            entries[name] = {"sha256": digest(data), "size": len(data)}
        manifest = {
            "schema_version": "attentionbench.week2-relocatable-archive.v1",
            "formal_eligible": False,
            "file_count": len(entries),
            "files": entries,
            "subjects": {
                "graph_audit": "graph_live/graph_live_audit.json",
                "robosuite_graph": "graph_live/graph-robosuite-restart/graph.json",
                "robocasa_graph": "graph_live/graph-robocasa/graph.json",
                "restart_checkpoint": "graph_live/restart_checkpoint.json",
                "repeated_action": "graph_live/repeated-action-clean/smoke_receipt.json",
                "memory_effect_index": "memory_effect/archive_manifest.json",
                "depth_failure_safety": "prior_auto_smoke/clean-checkout/attention-robosuite-cube_lift-seed101-1cde894ef24b/attempts/cube_lift-seed101-formal-1790481804027982384/safety.json",
            },
        }
        _add(stream, "archive_manifest.json", (
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        ).encode("utf-8"))
    print(json.dumps({"archive": str(args.output), "sha256": digest(args.output.read_bytes()),
                      "file_count": len(entries)}, ensure_ascii=False))


def verify(path: Path) -> dict:
    with tarfile.open(path, "r:gz") as stream:
        members = stream.getmembers()
        names = [member.name for member in members]
        if len(names) != len(set(names)) or any(
            member.name.startswith("/") or ".." in Path(member.name).parts or not member.isfile()
            for member in members
        ):
            raise ValueError("archive contains unsafe or duplicate members")
        payloads = {member.name: stream.extractfile(member).read() for member in members}
    manifest = json.loads(payloads.pop("archive_manifest.json"))
    if (manifest.get("schema_version") != "attentionbench.week2-relocatable-archive.v1"
            or manifest.get("formal_eligible") is not False
            or set(payloads) != set(manifest["files"])
            or len(payloads) != manifest["file_count"]):
        raise ValueError("archive manifest or member set differs")
    for name, record in manifest["files"].items():
        if digest(payloads[name]) != record["sha256"] or len(payloads[name]) != record["size"]:
            raise ValueError(f"SHA-256 or size mismatch: {name}")
    if any(name not in payloads for name in manifest["subjects"].values()):
        raise ValueError("archive subject is missing")
    return {"archive_sha256": digest(path.read_bytes()),
            "file_count": len(payloads), "subjects": manifest["subjects"],
            "formal_eligible": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    builder = sub.add_parser("build")
    builder.add_argument("--graph-root", type=Path, required=True)
    builder.add_argument("--prior-root", type=Path, required=True)
    builder.add_argument("--memory-root", type=Path, required=True)
    builder.add_argument("--clean-install-log", type=Path, required=True)
    builder.add_argument("--output", type=Path, required=True)
    checker = sub.add_parser("verify")
    checker.add_argument("archive", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        build(args)
    else:
        print(json.dumps(verify(args.archive), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
