from pathlib import Path

import pytest

from benchmarks.attention_harness.core.memory import MemoryManager
from benchmarks.attention_harness.core.models import MemoryRecord, MemoryStatus, MemoryUseRecord
from benchmarks.attention_harness.core.store import StateConflictError
from benchmarks.attention_harness.tests.test_attention_store import populated_store


def candidate(*, expires_at=100.0):
    return MemoryRecord(
        memory_id="memory-1",
        version=1,
        source_trace_id="trace-1",
        guidance="approach the cube from above",
        candidate_repair="reduce lateral offset before closing the gripper",
        applicability={"suite": "robosuite", "failure_stage": ["grasp", "lift"]},
        evidence_refs=("artifact://run-1/frame-17", "artifact://run-1/trace-1"),
        created_at=10.0,
        expires_at=expires_at,
    )


def test_candidate_validation_retrieval_use_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "attention.sqlite3"
    store, _ = populated_store(path)
    manager = MemoryManager(store)
    manager.add_candidate(candidate())
    validated = manager.record_validation("memory-1", successes=4, failures=1)
    assert validated.status is MemoryStatus.VALIDATED
    trusted = manager.promote("memory-1")
    assert trusted.status is MemoryStatus.TRUSTED
    assert manager.retrieve(
        {"suite": "robosuite", "failure_stage": "grasp"}, now=20.0
    ) == [trusted]
    manager.record_use(
        MemoryUseRecord(
            "use-1", "memory-1", 1, "run-1", "attempt-1", 21.0, "success",
            ("artifact://run-1/result",),
        )
    )
    restarted = MemoryManager(type(store)(path))
    assert restarted.store.get_memory("memory-1").status is MemoryStatus.TRUSTED
    assert restarted.store.list_memory_uses("memory-1")[0]["outcome"] == "success"


def test_reject_disable_enable_rollback_and_expiry(tmp_path: Path) -> None:
    store, _ = populated_store(tmp_path / "attention.sqlite3")
    manager = MemoryManager(store)
    manager.add_candidate(candidate())
    assert manager.record_validation("memory-1", successes=1, failures=3).status is MemoryStatus.REJECTED
    with pytest.raises(StateConflictError, match="illegal"):
        manager.promote("memory-1")

    second = candidate(expires_at=30.0)
    second = MemoryRecord(**{**second.__dict__, "memory_id": "memory-2"})
    manager.add_candidate(second)
    manager.record_validation("memory-2", successes=3, failures=0)
    manager.promote("memory-2")
    manager.disable("memory-2", reason="operator found camera mismatch")
    assert manager.retrieve({"suite": "robosuite", "failure_stage": "grasp"}, now=20.0) == []
    manager.enable("memory-2")
    assert manager.expire_due(now=31.0)[0].status is MemoryStatus.EXPIRED

    third = MemoryRecord(**{**candidate().__dict__, "memory_id": "memory-3"})
    manager.add_candidate(third)
    manager.record_validation("memory-3", successes=4, failures=0)
    manager.promote("memory-3")
    assert manager.rollback("memory-3", reason="regression").status is MemoryStatus.ROLLED_BACK


def test_only_trusted_memory_can_be_used(tmp_path: Path) -> None:
    store, _ = populated_store(tmp_path / "attention.sqlite3")
    manager = MemoryManager(store)
    manager.add_candidate(candidate())
    with pytest.raises(StateConflictError, match="trusted"):
        manager.record_use(MemoryUseRecord("use-1", "memory-1", 1, "run-1", "attempt-1", 20.0, "failure"))
