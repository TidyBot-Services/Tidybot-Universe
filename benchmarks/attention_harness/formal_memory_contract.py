"""Hash-bound, per-run Memory scope and exact-version admission contract."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4


ACCOUNTING = {
    "answered_request_credits": 1,
    "cached_answer_credits": 1,
    "cached_provider_tokens": 0,
    "proxy_latency_seconds": 2,
    "advisor_cache_initial_state": "empty_per_run",
}
CONTEXT_KEYS = {"suite", "task_id", "perception_mode", "scene_id", "object_set_id",
                "camera_config_id", "task_variant_id", "camera_names", "task_prompt"}


def validate_memory_contract(contract: dict[str, Any], *, suite: str, task_id: str,
                             policy_id: str, config: dict[str, Any]) -> None:
    required = {"schema_version", "suite", "task_id", "condition", "visibility",
                "initial_state", "context", "reset_each_run", "automatic_promotion",
                "accounting", "evidence_root"}
    if (not isinstance(contract, dict) or set(contract) != required
            or contract["schema_version"] != "attentionbench.formal-memory-contract.v1"
            or (contract["suite"], contract["task_id"], contract["condition"])
               != (suite, task_id, policy_id)
            or contract["reset_each_run"] is not True
            or contract["automatic_promotion"] is not False
            or contract["accounting"] != ACCOUNTING):
        raise ValueError("Memory contract identity, reset or accounting mismatch")
    state = contract["initial_state"]
    if not isinstance(state, dict) or set(state) != {"kind", "path", "sha256", "versions"}:
        raise ValueError("Memory initial state must bind snapshot and versions")
    if contract["visibility"] == "none":
        if (state != {"kind": "empty", "path": None, "sha256": None, "versions": []}
                or contract["context"] is not None or contract["evidence_root"] is not None):
            raise ValueError("hidden Memory requires an empty per-run initial state")
        return
    if (policy_id != "full_trace_aware_attention_planner"
            or contract["visibility"] != "trusted_exact_versions"
            or state["kind"] != "sqlite_snapshot"
            or not isinstance(state["versions"], list) or not state["versions"]):
        raise ValueError("only full Attention may expose frozen trusted Memory")
    versions = state["versions"]
    if (any(not isinstance(v, dict) or set(v) != {"memory_id", "version"}
            or not isinstance(v["memory_id"], str) or not v["memory_id"]
            or type(v["version"]) is not int or v["version"] < 1 for v in versions)
            or len({v["memory_id"] for v in versions}) != len(versions)):
        raise ValueError("Memory exact-version allowlist invalid")
    snapshot = Path(state["path"] or "/missing")
    if (not snapshot.is_absolute() or not snapshot.is_file()
            or hashlib.sha256(snapshot.read_bytes()).hexdigest() != state["sha256"]):
        raise ValueError("Memory initial snapshot differs from frozen SHA-256")
    with sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Memory initial snapshot integrity failure")
        for version in versions:
            row = connection.execute("SELECT payload FROM memories WHERE id=?",
                                     (version["memory_id"],)).fetchone()
            memory = json.loads(row[0]) if row else {}
            if (memory.get("status") != "trusted"
                    or memory.get("version") != version["version"]):
                raise ValueError("initial Memory is not the frozen trusted version")
        if connection.execute("SELECT COUNT(*) FROM advisor_cache").fetchone()[0]:
            raise ValueError("frozen initial Advisor cache must be empty")
    if not isinstance(contract["evidence_root"], str) or not Path(contract["evidence_root"]).is_dir():
        raise ValueError("Memory provenance evidence root missing")
    context = contract["context"]
    if (not isinstance(context, dict) or set(context) != CONTEXT_KEYS
            or any(context.get(k) != config.get(k) for k in
                   ("suite", "task_id", "perception_mode", "scene_id", "object_set_id"))
            or context["camera_names"] != (config["camera_names"] if suite == "robocasa"
                                          else [config["camera_name"]])
            or (suite == "robocasa" and context["task_prompt"] != config["task_prompt"])
            or any(not isinstance(context[k], str) or not context[k] for k in
                   CONTEXT_KEYS - {"camera_names"})):
        raise ValueError("Memory applicability scope disagrees with simulator config")


def initialize_frozen_memory(contract: dict[str, Any], *, artifact_root: Path):
    """Create a fresh store and writable evidence copy without touching the freeze."""
    from attention_memory_service import MemoryService
    from .core.store import AttentionStore

    root = artifact_root / "memory-initializations" / uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    store_path = root / "attention.sqlite3"
    state = contract["initial_state"]
    evidence = root / "evidence"
    if state["kind"] == "sqlite_snapshot":
        raw = Path(state["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != state["sha256"]:
            raise ValueError("Memory initial snapshot changed during initialization")
        store_path.write_bytes(raw)
        shutil.copytree(contract["evidence_root"], evidence)
    else:
        evidence.mkdir()
    AttentionStore(store_path)
    gateway = FrozenMemoryGateway(MemoryService(store_path, artifact_root=evidence), contract)
    return store_path, evidence, gateway


class FrozenMemoryGateway:
    """Retain Service authority while forbidding promotion and version drift."""

    def __init__(self, service: Any, contract: dict[str, Any]):
        self.service = service
        self.contract = contract
        self.versions = {v["memory_id"]: v["version"]
                         for v in contract["initial_state"]["versions"]}

    def __getattr__(self, name: str) -> Any:
        if name in {"promote", "disable", "rollback", "set_expiry", "authorize_dev_use"}:
            raise PermissionError("Memory lifecycle changes forbidden during primary matrix")
        return getattr(self.service, name)

    def retrieve(self, context: dict[str, Any], *, now: float) -> list[Any]:
        if self.contract["visibility"] == "none":
            return []
        if context != self.contract["context"]:
            raise ValueError("Memory retrieval outside frozen per-run scope")
        result = self.service.retrieve(context, now=now)
        permitted = []
        for item in result:
            if item.memory_id in self.versions:
                if item.version != self.versions[item.memory_id]:
                    raise ValueError("frozen Memory version drift")
                permitted.append(item)
        return permitted

    def authorize_use(self, context: dict[str, Any], *, memory_id: str,
                      attempt_id: str, now: float) -> dict[str, Any]:
        if (context != self.contract["context"] or memory_id not in self.versions
                or self.contract["visibility"] == "none"):
            raise PermissionError("Memory grant outside frozen allowlist/scope")
        grant = self.service.authorize_use(context, memory_id=memory_id,
                                            attempt_id=attempt_id, now=now)
        if (grant.get("version") != self.versions[memory_id]
                or grant.get("attempt_id") != attempt_id or grant.get("memory_id") != memory_id):
            raise ValueError("Service grant differs from frozen exact version/attempt")
        return grant
