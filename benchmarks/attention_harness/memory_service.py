"""Shared, backend-neutral authority for AttentionBench memory.

Agents may propose artifacts and trials, but only this boundary can read the
store, register evidence, or promote a candidate. The in-process interface is
also used by the local HTTP facade; it does not require a daemon for tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .attention_modes import RequestState
from .core.memory import MemoryManager
from .core.models import MemoryRecord, MemoryUseRecord, RequestType
from .core.store import AttentionStore, StateConflictError
from .memory_artifacts import MemoryArtifactStore
from .memory_v2 import MemoryV2Manager


class MemoryService:
    def __init__(self, store: AttentionStore | Path, *, artifact_root: Path | None = None) -> None:
        self.store = store if isinstance(store, AttentionStore) else AttentionStore(store)
        self.v2 = MemoryV2Manager(self.store)
        self.artifacts = MemoryArtifactStore(
            self.store, artifact_root or self.store.path.parent,
        )

    def source_for_agent(self, request_id: str) -> dict[str, Any]:
        """Return an answered hint and bounded public context, never raw evaluator debug."""
        request = self.store.get_request(request_id)
        if request is None or request.request_type is not RequestType.HINT:
            raise StateConflictError("memory source must be a hint request")
        if request.state is not RequestState.ANSWERED or not request.response_id:
            raise StateConflictError("memory source must be answered")
        run = self.store.get_run(request.run_id)
        trace = self.store.get_trace(request.trace_id)
        response = self.store.get_response(request.response_id)
        if run is None or trace is None or response is None:
            raise StateConflictError("memory source lineage is incomplete")
        raw = self.store.get_raw_trace(trace["raw_trace_id"])
        if raw is None:
            raise StateConflictError("memory source raw trace is missing")
        return {
            "request_id": request_id,
            "response_id": request.response_id,
            "responder": response["responder"],
            "response_content": response["content"],
            "response_created_at": response["created_at"],
            "source_trace_id": request.trace_id,
            "suite": run["suite"],
            "task_id": run["task_id"],
            "perception_mode": raw["metadata"].get("runtime", {}).get("perception_mode"),
            "failure": trace["failure"],
        }

    def create_candidate(
        self, *, memory_id: str, request_id: str, guidance: str,
        repair: str, applicability: dict[str, str], created_at: float,
        artifact_kind: str = "text_hint", human_attention_seconds: float = 0.0,
    ) -> MemoryRecord:
        memory = self.v2.create_candidate_from_response(
            memory_id=memory_id, request_id=request_id, guidance=guidance,
            repair=repair, applicability=applicability, created_at=created_at,
            artifact_kind=artifact_kind, human_attention_seconds=human_attention_seconds,
        )
        self.artifacts.publish(memory_id)
        return memory

    def get_memory(self, memory_id: str) -> MemoryRecord:
        memory = self.store.get_memory(memory_id)
        if memory is None:
            raise StateConflictError("unknown memory")
        self.v2.provenance(memory_id)
        return memory

    def provenance(self, memory_id: str) -> dict[str, Any]:
        return self.v2.provenance(memory_id)

    def retrieve(self, context: dict[str, Any], *, now: float) -> list[MemoryRecord]:
        return self.v2.retrieve(context, now=now)

    def record_use(self, use: MemoryUseRecord) -> MemoryUseRecord:
        self.v2.provenance(use.memory_id)
        result = MemoryManager(self.store).record_use(use)
        self.artifacts.publish(use.memory_id)
        return result

    def record_plan(self, memory_id: str, plan: dict[str, Any]) -> dict[str, Any]:
        result = self.v2.record_plan(memory_id, plan)
        self.artifacts.publish(memory_id)
        return result

    def get_plan(self, memory_id: str) -> dict[str, Any] | None:
        return self.v2.get_plan(memory_id)

    def record_pair(
        self, *, memory_id: str, control_attempt_id: str,
        treatment_attempt_id: str, control_safety: Path,
        treatment_safety: Path,
    ) -> dict[str, Any]:
        pair = self.v2.record_pair(
            memory_id=memory_id,
            control_attempt_id=control_attempt_id,
            treatment_attempt_id=treatment_attempt_id,
            control_safety=control_safety,
            treatment_safety=treatment_safety,
        )
        self.artifacts.publish(memory_id)
        return pair

    def impact_report(self, memory_id: str) -> dict[str, Any]:
        return self.v2.impact_report(memory_id)

    def list_pairs(self, memory_id: str) -> list[dict[str, Any]]:
        return self.v2.list_pairs(memory_id)

    def promote(self, memory_id: str) -> MemoryRecord:
        memory = self.v2.validate_and_promote(memory_id)
        self.artifacts.publish(memory_id)
        return memory

    def export_package(self, memory_id: str) -> Path:
        return self.artifacts.publish(memory_id)

    def verify_package(self, memory_id: str) -> dict[str, Any]:
        return self.artifacts.verify(memory_id)
