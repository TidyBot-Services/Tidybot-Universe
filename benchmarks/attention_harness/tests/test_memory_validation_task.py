from __future__ import annotations

import hashlib
import json

import pytest

from benchmarks.attention_harness.memory_agent import MemoryAgent, TrialEvidence
from benchmarks.attention_harness.memory_service import MemoryService
from benchmarks.attention_harness.memory_validation_task import MemoryValidationTask
from benchmarks.attention_harness.tests.test_memory_v2 import _candidate, _cases, _episode, _safety


def _task(tmp_path, *, success=True, robocasa_receipts=False):
    shared, _, memory_id, _ = _candidate(tmp_path)
    service = MemoryService(shared)
    provenance = service.provenance(memory_id)
    source = tmp_path / "attention_run.json"
    artifacts = {}
    for name in ("trace", "safety", "sandbox_receipt", "native_result"):
        file = tmp_path / f"formal-{name}.json"
        file.write_text("{}")
        artifacts[name] = {"uri": str(file), "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    source.write_text(json.dumps({
        "formal_eligible": False, "runner_boundary": {"mode": "formal"},
        "attempts": [{"attention_trace": {"run_id": provenance["source_run_id"],
                                           "attempt_id": provenance["source_attempt_id"]},
                      "native_success": False,
                      "formal_runner_result": {"boundary_checked": True, "formal_eligible": False,
                                               "run_id": provenance["source_run_id"],
                                               "attempt_id": provenance["source_attempt_id"],
                                               "native_evaluator": {"evaluated": True, "native_success": False},
                                               "artifacts": artifacts}}],
        "requests": [{"candidate_memory_id": memory_id,
                      "request_id": provenance["request_id"]}],
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
        if robocasa_receipts:
            root = tmp_path / f"arm-{trial.seed}-{trial.treatment}"
            root.mkdir()
            safety = _safety(root / "safety_monitor.json", attempt_id)
            for name in ("result.json", "trace.jsonl", "attention_bundle.json", "trial_config.json"):
                (root / name).write_text("{}\n")
            stop = root / "service-stop.json"
            stop.write_text('{"services": {"agent": {"process_group_gone": true}, "simulator": {"process_group_gone": true}}}\n')
            return TrialEvidence(attempt_id, safety, f"config-{trial.seed}", stop)
        safety = _safety(tmp_path / f"safety-{trial.seed}-{trial.treatment}.json", attempt_id)
        return TrialEvidence(attempt_id, safety, f"config-{trial.seed}")

    task = MemoryValidationTask(
        agent=MemoryAgent(service), memory_id=memory_id,
        source_run=source, source_run_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        cases=_cases(), validation_policy=policy,
        approved_policy_sha256=hashlib.sha256(policy.read_bytes()).hexdigest(),
        executor=execute, state_path=tmp_path / "task.json",
        approval={"suite": "robocasa", "formal_eligible": False} if robocasa_receipts else None,
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


def test_validation_task_accepts_new_candidate_id_for_same_verified_request(tmp_path):
    task, service, calls = _task(tmp_path)
    source = json.loads(task.source_run.read_text())
    source["requests"][0]["candidate_memory_id"] = "older-candidate-from-same-request"
    task.source_run.write_text(json.dumps(source))
    task.source_run_sha256 = hashlib.sha256(task.source_run.read_bytes()).hexdigest()
    state = task.run()
    assert state["status"] == "trusted"
    assert len(calls) == 10
    assert service.get_memory(task.memory_id).status.value == "trusted"


def test_validation_task_keeps_failed_repair_as_candidate(tmp_path):
    task, service, calls = _task(tmp_path, success=False)
    result = task.run()
    assert result["status"] == "blocked"
    assert len(calls) == 10
    assert result["impact"]["treatment_successes"] == 0
    assert service.get_memory(task.memory_id).status.value == "candidate"
    assert task.run()["status"] == "blocked"
    assert len(calls) == 10


def test_blocked_restart_checks_frozen_policy_without_replaying_arms(tmp_path):
    task, _, calls = _task(tmp_path, success=False)
    assert task.run()["status"] == "blocked"
    assert len(calls) == 10
    task.validation_policy.write_text("# changed after approval\n")
    with pytest.raises(ValueError, match="approved validation policy SHA-256 changed"):
        task.run()
    assert len(calls) == 10


def test_blocked_restart_checks_formal_source_artifact_without_replaying_arms(tmp_path):
    task, _, calls = _task(tmp_path, success=False)
    assert task.run()["status"] == "blocked"
    assert len(calls) == 10
    (tmp_path / "formal-trace.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="formal source trace SHA-256 changed"):
        task.run()
    assert len(calls) == 10


def test_validation_task_refuses_to_replay_uncertain_arm(tmp_path):
    task, service, calls = _task(tmp_path)
    original = task.executor

    def lost_receipt(trial):
        original(trial)
        raise RuntimeError("worker exited after action")

    task.executor = lost_receipt
    first = task.run()
    assert first["status"] == "blocked"
    assert first["inflight"] == {"seed": task.cases[0]["seed"], "arm": "control"}
    assert len(calls) == 1
    assert task.run()["status"] == "blocked"
    assert len(calls) == 1
    assert service.get_memory(task.memory_id).status.value == "candidate"


def test_robocasa_receipts_verify_artifacts_on_restart(tmp_path):
    task, _, calls = _task(tmp_path, robocasa_receipts=True)
    state = task.run()
    assert state["status"] == "trusted"
    assert len(calls) == 10
    assert "service_stop" in state["arms"][str(task.cases[0]["seed"])]["control"]
    assert task.run()["status"] == "trusted"
    assert len(calls) == 10
    first = state["arms"][str(task.cases[0]["seed"])]["control"]
    (tmp_path / f"arm-{task.cases[0]['seed']}-False" / "result.json").write_text('{"changed":true}\n')
    try:
        task.run()
    except ValueError as exc:
        assert "saved trial result.json SHA-256 changed" in str(exc)
    else:
        raise AssertionError("changed arm result was accepted")
