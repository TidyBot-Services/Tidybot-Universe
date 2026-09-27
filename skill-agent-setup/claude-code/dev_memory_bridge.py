"""Opt-in, auditable Dev Agent exposure to trusted AttentionBench Memory.

This is not runtime policy retrieval and must not be mixed into a control arm.
The Memory Service owns the grant and the outcome link; graph state is only a
discoverable pointer to that evidence.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Any


_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _client(url: str):
    from attention_memory_service.memory_service_client import MemoryServiceClient

    key = os.environ.get("ATTENTION_MEMORY_SERVICE_KEY", "")
    if not key:
        raise ValueError("ATTENTION_MEMORY_SERVICE_KEY is required for Dev Memory")
    return MemoryServiceClient(url, api_key=key)


def _source_digest(policy_ref: str, repo_root: Path) -> str:
    if not isinstance(policy_ref, str) or not re.fullmatch(
        r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", policy_ref,
    ):
        raise ValueError("Dev Memory requires an importable policy source")
    module = policy_ref.partition(":")[0]
    root = repo_root.resolve()
    source = root.joinpath(*module.split(".")).with_suffix(".py")
    if not source.is_file() or not source.resolve().is_relative_to(root):
        raise ValueError("Dev Memory policy source must be inside Universe")
    return hashlib.sha256(source.read_bytes()).hexdigest()


def prepare_dev_memory(
    *, entry: dict[str, Any], graph_id: str, repo_root: Path,
    memory_service_url: str,
) -> tuple[str, dict[str, Any]]:
    config = entry.get("attentionbench")
    dev = entry.get("dev_memory")
    if not isinstance(config, dict) or not isinstance(dev, dict):
        raise ValueError("Dev Memory requires explicit AttentionBench and dev_memory objects")
    if dev.get("enabled") is not True or config.get("attention_policy") != "trace_aware_full":
        raise ValueError("Dev Memory requires an explicit trace_aware_full treatment")
    context = dev.get("context")
    if not isinstance(context, dict) or context.get("suite") != config.get("suite") or context.get("task_id") != config.get("task"):
        raise ValueError("Dev Memory context disagrees with node task")
    if context.get("perception_mode") != "sim_gt":
        raise ValueError("Dev Memory v1 supports simulator GT context only")
    memory_id = dev.get("memory_id")
    development_id = dev.get("development_id")
    if not all(isinstance(value, str) and value.strip() for value in (memory_id, development_id)):
        raise ValueError("Dev Memory needs explicit memory_id and development_id")
    if not development_id.startswith(f"{graph_id}:{entry['name']}:"):
        raise ValueError("development_id must be namespaced to this graph node")
    digest = _source_digest(config.get("robot_policy"), repo_root)
    store_name = config.get("store_path")
    if not isinstance(store_name, str) or not store_name.strip():
        raise ValueError("Dev Memory requires an explicit shared store_path")
    store_path = Path(store_name)
    if not store_path.is_absolute():
        store_path = repo_root / store_path
    if not store_path.is_file():
        raise ValueError("Dev Memory store_path must exist before guidance exposure")
    from attention_memory_service.identity import store_id

    client = _client(memory_service_url)
    health = client.health()
    if (health.get("schema_version") != "attentionbench.memory-service.v2"
            or "development_use_evidence" not in health.get("capabilities", [])):
        raise RuntimeError("Memory Service lacks development-use evidence capability")
    if client.store_id() != store_id(store_path):
        raise RuntimeError("Dev Memory Service is connected to a different Attention store")
    grant = client.authorize_dev_use(
        memory_id=memory_id, development_id=development_id,
        context=context, source_policy_sha256=digest, now=time.time(),
    )
    if (grant.get("schema_version") != "attentionbench.dev-memory-exposure.v1"
            or grant.get("memory_id") != memory_id
            or grant.get("development_id") != development_id
            or grant.get("source_policy_sha256") != digest
            or not isinstance(grant.get("guidance"), str)
            or not grant["guidance"].strip()
            or not isinstance(grant.get("evidence_refs"), list)):
        raise ValueError("Memory Service returned an invalid Dev exposure")
    pointer = {
        "grant_id": grant["grant_id"], "memory_id": memory_id,
        "memory_version": grant["memory_version"],
        "development_id": development_id,
        "source_policy_sha256": digest,
        "source_trace_id": grant["source_trace_id"],
        "evidence_refs": grant["evidence_refs"],
        "phase": "development_exposure_not_runtime_use",
    }
    guidance = (
        "\n\n## Validated Memory for development (treatment arm only)\n"
        f"Memory {memory_id} v{grant['memory_version']}; grant {grant['grant_id']}; "
        f"raw evidence {', '.join(grant['evidence_refs'])}.\n"
        f"Guidance: {grant['guidance']}\n"
        "This is historical guidance, not task ground truth. Record code changes; "
        "do not claim this exposure caused a successful run.\n"
    )
    return guidance, pointer


def record_dev_memory_result(
    *, pointer: dict[str, Any], run_id: str, artifact: Path,
    policy_ref: str, repo_root: Path, memory_service_url: str,
) -> dict[str, Any]:
    digest = _source_digest(policy_ref, repo_root)
    if not isinstance(pointer.get("grant_id"), str):
        raise ValueError("Dev Memory grant pointer is missing")
    return _client(memory_service_url).record_dev_result(
        grant_id=pointer["grant_id"], run_id=run_id,
        result_policy_sha256=digest,
        artifact_uri=str(artifact.resolve()),
        artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
    )
