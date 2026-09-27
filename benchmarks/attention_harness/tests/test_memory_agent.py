from __future__ import annotations

import json
from pathlib import Path

import pytest

from attention_memory_service.core.models import MemoryStatus
from attention_memory_service.core.store import StateConflictError
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_agent import MemoryAgent, TrialEvidence
from benchmarks.attention_harness.memory_service import MemoryService
from benchmarks.attention_harness.tests.test_memory_v2 import _candidate, _episode, _cases, _safety


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
    plan = agent.plan_validation(memory_id, cases=_cases())
    assert [pair[0].seed for pair in plan] == [102, 103, 104, 105, 106]
    assert all(not control.treatment and treatment.treatment for control, treatment in plan)
    assert all(control.policy_id == treatment.policy_id for control, treatment in plan)
    with pytest.raises(PermissionError, match="held-out"):
        agent.plan_validation(memory_id, cases=({**_cases()[0], "seed": 1001}, *_cases()[1:]))
    with pytest.raises(StateConflictError, match="predefined variation"):
        agent.request_promotion(memory_id)

    def executor(trial):
        result = _episode(
            tmp_path, shared, trial.seed,
            candidate=trial.memory_id if trial.treatment else None,
            variation=True,
        )
        attempt_id = result["attention_trace"]["attempt_id"]
        path = _safety(tmp_path / f"monitor-{trial.seed}-{trial.treatment}.json", attempt_id)
        return TrialEvidence(attempt_id, path, "synthetic-config")

    report = agent.run_validation(memory_id, executor=executor, cases=_cases())
    assert report["success_gain"] == 5
    assert agent.run_validation(
        memory_id,
        executor=lambda trial: (_ for _ in ()).throw(AssertionError("reran completed seed")),
        cases=_cases(),
    )["paired_dev_seeds"] == 5
    assert agent.request_promotion(memory_id).status is MemoryStatus.TRUSTED
    assert AttentionStore(shared).get_memory(memory_id).status.value == MemoryStatus.TRUSTED.value
    package = service.artifacts.directory(memory_id)
    assert service.verify_package(memory_id)["status"] == "trusted"
    lifecycle = [json.loads(line) for line in (package / "lifecycle.jsonl").read_text().splitlines()]
    assert lifecycle[-1]["status"] == "trusted"
    assert len((package / "validation/pairs.jsonl").read_text().splitlines()) == 5
    assert origin["memory_candidate_id"] == memory_id


def test_preflight_checks_candidate_without_freezing_plan_or_running_trials(tmp_path):
    shared, _, memory_id, _ = _candidate(tmp_path)
    service = MemoryService(shared)
    agent = MemoryAgent(service)
    source_sha = service.provenance(memory_id)["source_policy_sha256"]
    preview = agent.preflight_validation(
        memory_id, cases=_cases(), validation_policy_sha256=source_sha,
    )
    assert preview["source_evidence_verified"] is True
    assert preview["source_policy_sha256"] == source_sha
    assert preview["policy_changed_since_source"] is False
    assert preview["remaining_seeds"] == [102, 103, 104, 105, 106]
    assert preview["completed_seeds"] == []
    assert preview["frozen_plan"] is False
    assert preview["simulator_executed"] is False
    assert service.get_plan(memory_id) is None
    assert service.list_pairs(memory_id) == []
    with pytest.raises(ValueError, match="SHA-256"):
        agent.preflight_validation(memory_id, cases=_cases(), validation_policy_sha256="not-a-digest")
    inconsistent = ({**_cases()[0], "camera_names": ["other_camera"]}, *_cases()[1:])
    with pytest.raises(ValueError, match="one-to-one"):
        agent.preflight_validation(memory_id, cases=inconsistent)

    agent.plan_validation(memory_id, cases=_cases())
    assert agent.preflight_validation(memory_id, cases=_cases())["frozen_plan"] is True
    changed = tuple(
        {**case, "task_prompt": "changed prompt"} if case["task_variant_id"] == "task-variant-0" else case
        for case in _cases()
    )
    with pytest.raises(ValueError, match="differ from the frozen"):
        agent.preflight_validation(memory_id, cases=changed)


def test_preflight_rejects_changed_raw_source_evidence(tmp_path):
    shared, _, memory_id, _ = _candidate(tmp_path)
    service = MemoryService(shared)
    source_ref = service.provenance(memory_id)["raw_evidence_refs"][0]
    source_file = tmp_path / Path(source_ref["uri"].removeprefix("artifact://"))
    source_file.write_bytes(b"changed source bytes")
    with pytest.raises(StateConflictError, match="digest changed"):
        MemoryAgent(service).preflight_validation(memory_id, cases=_cases())
    with pytest.raises(StateConflictError, match="digest changed"):
        MemoryAgent(service).plan_validation(memory_id, cases=_cases())
    assert service.get_plan(memory_id) is None


def test_agent_cannot_promote_when_executor_omits_safety(tmp_path):
    shared, _, memory_id, _ = _candidate(tmp_path)
    agent = MemoryAgent(MemoryService(shared))

    def executor(trial):
        result = _episode(
            tmp_path, shared, trial.seed,
            candidate=trial.memory_id if trial.treatment else None,
            variation=True,
        )
        return TrialEvidence(
            result["attention_trace"]["attempt_id"],
            tmp_path / "missing-safety.json",
            "synthetic-config",
        )

    with pytest.raises(FileNotFoundError):
        agent.run_validation(memory_id, executor=executor, cases=_cases())
    with pytest.raises(StateConflictError, match="predefined variation"):
        agent.request_promotion(memory_id)
    assert AttentionStore(shared).get_memory(memory_id).status.value == MemoryStatus.CANDIDATE.value


def test_agent_rejects_mismatched_trial_config_before_pair_registration(tmp_path):
    shared, _, memory_id, _ = _candidate(tmp_path)
    service = MemoryService(shared)
    agent = MemoryAgent(service)

    def executor(trial):
        result = _episode(
            tmp_path, shared, trial.seed,
            candidate=trial.memory_id if trial.treatment else None,
            variation=True,
        )
        path = tmp_path / f"config-monitor-{trial.seed}-{trial.treatment}.json"
        path.write_text(json.dumps({
            "source": "independent_safety_monitor",
            "attempt_id": result["attention_trace"]["attempt_id"],
            "unsafe_attempts": 0,
        }))
        return TrialEvidence(
            result["attention_trace"]["attempt_id"], path,
            "different-config" if trial.treatment else "expected-config",
        )

    with pytest.raises(ValueError, match="different configurations"):
        agent.run_validation(memory_id, executor=executor, cases=_cases())
    assert service.list_pairs(memory_id) == []
