"""Auditable per-memory file packages materialized from the authoritative DB."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .core.store import AttentionStore, StateConflictError
from .memory_v2 import MemoryV2Manager


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _jsonl(items: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
        for item in items
    )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def safe_memory_directory(memory_id: str) -> str:
    """Human hint plus full digest; never use raw IDs as filesystem paths."""
    hint = re.sub(r"[^a-z0-9]+", "-", memory_id.lower()).strip("-")[:32] or "memory"
    return f"{hint}-{_sha256(memory_id.encode('utf-8'))}"


class MemoryArtifactStore:
    def __init__(self, store: AttentionStore, root: Path) -> None:
        self.store = store
        self.v2 = MemoryV2Manager(store)
        self.root = root.resolve() / "memory"

    def directory(self, memory_id: str) -> Path:
        return self.root / safe_memory_directory(memory_id)

    def publish(self, memory_id: str) -> Path:
        """Rewrite the generated view; write the manifest last as a commit marker."""
        memory = self.store.get_memory(memory_id)
        if memory is None:
            raise StateConflictError("unknown memory")
        provenance = self.v2.provenance(memory_id)
        directory = self.directory(memory_id)
        directory.mkdir(parents=True, exist_ok=True)
        if not directory.resolve().is_relative_to(self.root):
            raise StateConflictError("memory package directory escapes artifact root")
        files = self._files(memory_id, memory.artifact(), provenance)
        for relative, content in sorted(files.items()):
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.parent.resolve().is_relative_to(directory.resolve()):
                raise StateConflictError("memory package file escapes its directory")
            self._write_atomic(target, content)
        manifest = {
            "schema_version": "attentionbench.memory-package.v2",
            "memory_id": memory_id,
            "version": memory.version,
            "status": memory.status.value,
            "artifact_kind": provenance["artifact"]["kind"],
            "source_kind": provenance["source_kind"],
            "applicability": memory.applicability,
            "created_at": memory.created_at,
            "core_record_sha256": _sha256(_json(memory.artifact())),
            "files": {name: _sha256(content) for name, content in sorted(files.items())},
        }
        self._write_atomic(directory / "MEMORY.json", _json(manifest))
        return directory

    def verify(self, memory_id: str) -> dict[str, Any]:
        """Check bytes and DB status without treating the file package as truth."""
        directory = self.directory(memory_id)
        manifest = json.loads((directory / "MEMORY.json").read_text(encoding="utf-8"))
        if manifest.get("memory_id") != memory_id:
            raise StateConflictError("memory package identity mismatch")
        memory = self.store.get_memory(memory_id)
        if memory is None or manifest.get("core_record_sha256") != _sha256(_json(memory.artifact())):
            raise StateConflictError("memory package is stale relative to SQLite")
        provenance = self.v2.provenance(memory_id)
        if any((
            manifest.get("schema_version") != "attentionbench.memory-package.v2",
            manifest.get("version") != memory.version,
            manifest.get("status") != memory.status.value,
            manifest.get("artifact_kind") != provenance["artifact"]["kind"],
            manifest.get("source_kind") != provenance["source_kind"],
            manifest.get("applicability") != memory.applicability,
            manifest.get("created_at") != memory.created_at,
        )):
            raise StateConflictError("memory package manifest differs from SQLite")
        expected = self._files(memory_id, memory.artifact(), provenance)
        expected_hashes = {name: _sha256(content) for name, content in sorted(expected.items())}
        if manifest.get("files") != expected_hashes:
            raise StateConflictError("memory package is stale relative to SQLite")
        for relative, digest in expected_hashes.items():
            path = directory / relative
            if not path.resolve().is_relative_to(directory.resolve()):
                raise StateConflictError("memory package contains an unsafe file path")
            try:
                content = path.read_bytes()
            except FileNotFoundError as exc:
                raise StateConflictError(f"memory package is missing {relative}") from exc
            if _sha256(content) != digest:
                raise StateConflictError(f"memory package file hash mismatch: {relative}")
        return manifest

    def _files(
        self, memory_id: str, memory: dict[str, Any], provenance: dict[str, Any]
    ) -> dict[str, bytes]:
        source_attempt = self.store.get_attempt(provenance["source_attempt_id"])
        source = {
            key: value for key, value in provenance.items()
            if key != "artifact"
        }
        source["evidence_ids"] = memory["evidence_refs"]
        source["episode_result_uri"] = None if source_attempt is None else source_attempt["artifact_uri"]
        plan = self.v2.get_plan(memory_id) or {
            "schema_version": "attentionbench.memory-validation-plan.v2",
            "memory_id": memory_id,
            "status": "unplanned",
        }
        pairs = self.v2.list_pairs(memory_id)
        lifecycle = [
            {
                "sequence": event["sequence"],
                "event_key": event["event_key"],
                "event_type": event["event_type"],
                "status": event["payload"].get("memory", {}).get("status"),
            }
            for event in self.store.events()
            if event["entity_type"] == "memory" and event["entity_id"] == memory_id
        ]
        uses = sorted(
            self.store.list_memory_uses(memory_id),
            key=lambda item: (item["used_at"], item["use_id"]),
        )
        files = {
            "knowledge/guidance.md": (memory["guidance"].rstrip() + "\n").encode("utf-8"),
            "knowledge/repair.md": (memory["candidate_repair"].rstrip() + "\n").encode("utf-8"),
            "source/provenance.json": _json(source),
            "validation/plan.json": _json(plan),
            "validation/pairs.jsonl": _jsonl(pairs),
            "validation/impact.json": _json(self.v2.impact_report(memory_id)),
            "lifecycle.jsonl": _jsonl(lifecycle),
            "usage.jsonl": _jsonl(uses),
        }
        return files

    @staticmethod
    def _write_atomic(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".memory-", delete=False) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        os.replace(temporary, path)
