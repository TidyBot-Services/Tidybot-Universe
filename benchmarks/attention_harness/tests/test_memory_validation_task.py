from __future__ import annotations

import hashlib
import json

from benchmarks.attention_harness.memory_agent import MemoryAgent, TrialEvidence
from benchmarks.attention_harness.memory_service import MemoryService
from benchmarks.attention_harness.memory_validation_task import MemoryValidationTask
from benchmarks.attention_harness.tests.test_memory_v2 import _candidate, _cases, _episode, _safety


def _task(tmp_path, *, success=True):
    shared, _, memory_id, _ = _candidate(tmp_path)
    service = MemoryService(shared)
    source = tmp_path / "attention_run.json"
    artifacts = {}
    for name in ("trace", "safety", "sandbox_receipt", "native_result"):
        file = tmp_path / f"formal-{name}.json"
        file.write_text("{}")
        artifacts[name] = {"uri": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    source.write_text(json.dumps({
        "formal_eligible": False, "runner_boundary": {"mode": "formal"},
        "attempts": [{"attention_trace": {"run_id": "source-run", "attempt_id": "source-attempt"},
                      "native_success": False,
                      "formal_runner_result": {"boundary_checked": True, "formal_eligible": False,
                                               "run_id": "source-run", "attempt_id": "source-attempt",
                                               "native_evaluator": {"evaluated": True, "native_success": False},
                                               "artifacts": artifacts}}],
        "requests": [{"candidate_memory_id": memory_id}],
    }))
    policy = tmp_path / "approved_policy.py"
    policy.write_text("# approved validation callback\n")
    calls = []

    def execute(trial):
        calls.append((trial.seed, trial.treatment))
        result = _episode(
            tmp_path, shared, trial.seed,
            candidate=trial.memory_id if trial.treatment else None,
            variation=True, suppress_success=trial.treatment and not success,
        )
        attempt_id = result["attention_trace"]["attempt_id"]
        safety = _safety(tmp_path / f"safety-{trial.seed}-{trial.treatment}.json", attempt_id)
        return TrialEvidence(attempt_id, safety, f"config-{trial.seed}")

    task = MemoryValidationTask(
        agent=MemoryAgent(service), memory_id=memory_id,
        source_run=source, source_run_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        cases=_cases(), validation_policy=policy,
        approved_policy_sha256=hashlib.sha256(policy.read_bytes()).hexdigest(),
        executor=execute, state_path=tmp_path / "task.json",
    )
    return task, service, calls


def test_validation_task_promotes_once_and_restart_does_not_rerun(tmp_path):
    task, service, calls = _task(tmp_path)
    first = task.run()
    assert first["status"] == "trusted"
    assert len(calls) == 10
    assert len(service.list_pairs(task.memory_id)) == 5
    assert first["reused_existing_pairs"] == []
    assert task.run()["status"] == "trusted"
    assert len(calls) == 10


def test_validation_task_blocks_bad_source_hash_before_trials(tmp_path):
    task, service, calls = _task(tmp_path)
    task.source_run.write_text("{}")
    result = task.run()
    assert result["status"] == "blocked"
    assert "source run SHA-256" in result["blocker"]
    assert calls == []
    assert service.get_memory(task.memory_id).status.value == "candidate"


def test_validation_task_keeps_failed_repair_as_candidate(tmp_path):
    task, service, calls = _task(tmp_path, success=False)
    result = task.run()
    assert result["status"] == "blocked"
    assert len(calls) == 10
    assert result["impact"]["treatment_successes"] == 0
    assert service.get_memory(task.memory_id).status.value == "candidate"
    assert task.run()["status"] == "blocked"
    assert len(calls) == 10
