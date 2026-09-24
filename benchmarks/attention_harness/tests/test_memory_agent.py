from __future__ import annotations

import json

import pytest

from attention_memory_service.core.models import MemoryStatus
from attention_memory_service.core.store import StateConflictError
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_agent import MemoryAgent, TrialEvidence
from benchmarks.attention_harness.memory_service import MemoryService
from benchmarks.attention_harness.tests.test_memory_v2 import _candidate, _episode


def test_memory_agent_plans_and_orchestrates_evidence_gated_validation(tmp_path):
    shared, _, memory_id, origin = _candidate(tmp_path)
    service = MemoryService(shared)
    agent = MemoryAgent(service)
    source = service.source_for_agent(
        service.provenance(memory_id)["request_id"]
    )
    assert source["perception_mode"] == "sim_gt"
    assert "object_pose" not in json.dumps(source)
    assert agent.ingest_answered_hint(source["request_id"], memory_id=memory_id).memory_id == memory_id
    plan = agent.plan_validation(memory_id)
    assert [pair[0].seed for pair in plan] == [102, 103, 104, 105, 106]
    assert all(not control.treatment and treatment.treatment for control, treatment in plan)
    assert all(control.policy_id == treatment.policy_id for control, treatment in plan)
    with pytest.raises(PermissionError, match="held-out"):
        agent.plan_validation(memory_id, seeds=(1001, 102, 103, 104, 105))
    with pytest.raises(StateConflictError, match="five"):
        agent.request_promotion(memory_id)

    def executor(trial):
        result = _episode(
            tmp_path, shared, trial.seed,
            candidate=trial.memory_id if trial.treatment else None,
        )
        attempt_id = result["attention_trace"]["attempt_id"]
        path = tmp_path / f"monitor-{trial.seed}-{trial.treatment}.json"
        path.write_text(json.dumps({
            "source": "independent_safety_monitor",
            "attempt_id": attempt_id,
            "unsafe_attempts": 0,
        }))
        return TrialEvidence(attempt_id, path)

    report = agent.run_validation(memory_id, executor=executor)
    assert report["success_gain"] == 5
    assert agent.run_validation(
        memory_id,
        executor=lambda trial: (_ for _ in ()).throw(AssertionError("reran completed seed")),
    )["paired_dev_seeds"] == 5
    assert agent.request_promotion(memory_id).status is MemoryStatus.TRUSTED
    assert AttentionStore(shared).get_memory(memory_id).status.value == MemoryStatus.TRUSTED.value
    package = service.artifacts.directory(memory_id)
    assert service.verify_package(memory_id)["status"] == "trusted"
    lifecycle = [json.loads(line) for line in (package / "lifecycle.jsonl").read_text().splitlines()]
    assert lifecycle[-1]["status"] == "trusted"
    assert len((package / "validation/pairs.jsonl").read_text().splitlines()) == 5
    assert origin["memory_candidate_id"] == memory_id


def test_agent_cannot_promote_when_executor_omits_safety(tmp_path):
    shared, _, memory_id, _ = _candidate(tmp_path)
    agent = MemoryAgent(MemoryService(shared))

    def executor(trial):
        result = _episode(
            tmp_path, shared, trial.seed,
            candidate=trial.memory_id if trial.treatment else None,
        )
        return TrialEvidence(
            result["attention_trace"]["attempt_id"],
            tmp_path / "missing-safety.json",
        )

    with pytest.raises(FileNotFoundError):
        agent.run_validation(memory_id, executor=executor)
    with pytest.raises(StateConflictError, match="five"):
        agent.request_promotion(memory_id)
    assert AttentionStore(shared).get_memory(memory_id).status.value == MemoryStatus.CANDIDATE.value
