"""Evidence-backed memory lifecycle and applicability-based retrieval."""

from __future__ import annotations

from typing import Any

from .models import MemoryRecord, MemoryStatus, MemoryUseRecord
from .store import AttentionStore, StateConflictError


class MemoryManager:
    def __init__(self, store: AttentionStore) -> None:
        self.store = store

    def add_candidate(self, memory: MemoryRecord) -> MemoryRecord:
        if memory.status is not MemoryStatus.CANDIDATE:
            raise StateConflictError("new memory must start as a candidate")
        return self.store.create_memory(memory)

    def record_validation(
        self,
        memory_id: str,
        *,
        successes: int,
        failures: int,
        minimum_successes: int = 3,
        minimum_confidence: float = 0.75,
    ) -> MemoryRecord:
        if successes < 0 or failures < 0 or successes + failures == 0:
            raise ValueError("validation requires at least one non-negative outcome")
        confidence = successes / (successes + failures)
        accepted = successes >= minimum_successes and confidence >= minimum_confidence
        status = MemoryStatus.VALIDATED if accepted else MemoryStatus.REJECTED
        return self.store.transition_memory(
            memory_id,
            status,
            event_key=f"memory-validation:{memory_id}:{successes}:{failures}",
            confidence=confidence,
            validation_successes=successes,
            validation_failures=failures,
            reason="validation_gate_passed" if accepted else "validation_gate_failed",
        )

    def promote(self, memory_id: str) -> MemoryRecord:
        return self.store.transition_memory(
            memory_id,
            MemoryStatus.TRUSTED,
            event_key=f"memory-promote:{memory_id}",
            reason="validated_memory_promoted",
        )

    def disable(self, memory_id: str, *, reason: str) -> MemoryRecord:
        return self.store.transition_memory(
            memory_id,
            MemoryStatus.DISABLED,
            event_key=f"memory-disable:{memory_id}",
            reason=reason,
        )

    def enable(self, memory_id: str) -> MemoryRecord:
        return self.store.transition_memory(
            memory_id,
            MemoryStatus.TRUSTED,
            event_key=f"memory-enable:{memory_id}",
            reason="human_reenabled",
        )

    def rollback(self, memory_id: str, *, reason: str) -> MemoryRecord:
        return self.store.transition_memory(
            memory_id,
            MemoryStatus.ROLLED_BACK,
            event_key=f"memory-rollback:{memory_id}",
            reason=reason,
        )

    def expire_due(self, *, now: float) -> list[MemoryRecord]:
        expired = []
        for memory in self.store.list_memories():
            if (
                memory.status is MemoryStatus.TRUSTED
                and memory.expires_at is not None
                and memory.expires_at <= now
            ):
                expired.append(
                    self.store.transition_memory(
                        memory.memory_id,
                        MemoryStatus.EXPIRED,
                        event_key=f"memory-expire:{memory.memory_id}",
                        reason="expiry_reached",
                    )
                )
        return expired

    def retrieve(self, context: dict[str, Any], *, now: float) -> list[MemoryRecord]:
        self.expire_due(now=now)
        matches = [
            memory
            for memory in self.store.list_memories()
            if memory.status is MemoryStatus.TRUSTED
            and _matches(memory.applicability, context)
        ]
        return sorted(matches, key=lambda item: (-item.confidence, item.memory_id))

    def record_use(self, use: MemoryUseRecord) -> MemoryUseRecord:
        return self.store.record_memory_use(use)


def _matches(conditions: dict[str, Any], context: dict[str, Any]) -> bool:
    for key, expected in conditions.items():
        if key not in context:
            return False
        actual = context[key]
        if isinstance(expected, list):
            if actual not in expected:
                return False
        elif actual != expected:
            return False
    return True
