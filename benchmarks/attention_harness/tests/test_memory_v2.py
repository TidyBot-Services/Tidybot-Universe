from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from attention_memory_service.core.models import MemoryStatus, MemoryUseRecord
from attention_memory_service.core.store import StateConflictError
from attention_memory_service import MemoryService
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_v2 import MemoryV2Manager
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.robocasa_native.sim_gt_runner import run_robocasa_sim_gt_episode

from benchmarks.attention_harness.tests.test_sim_gt_runner import FakeActionBackend, FakeWorld


TASK = "counter_to_sink"


def _case(seed):
    group = seed % 2
    return {
        "seed": seed,
        "scene_id": f"scene-{group}",
        "object_set_id": f"objects-{group}",
        "camera_config_id": f"camera-{group}",
        "task_variant_id": f"task-variant-{group}",
        "camera_names": ["base_camera"] if group == 0 else ["wrist_camera"],
        "task_prompt": "place mug in sink" if group == 0 else "move the mug into the sink",
    }


def _cases():
    return tuple(_case(seed) for seed in range(102, 107))


def _plan(manager, memory_id, assistance_credits=0):
    manager.record_plan(memory_id, {
        "schema_version": "attentionbench.memory-validation-plan.v2",
        "memory_id": memory_id,
        "suite": "robocasa",
        "task_id": TASK,
        "perception_mode": "sim_gt",
        "policy_id": "paired-policy",
        "assistance_credits": assistance_credits,
        "seeds": [case["seed"] for case in _cases()],
        "cases": list(_cases()),
    })


def _episode(
    root, shared, seed, *, candidate=None, success=False, advisor=None,
    variation=False, suppress_success=False, config_sha256=None,
    budget_credits=None,
):
    world = FakeWorld()

    class OutcomeBackend(FakeActionBackend):
        def set_gripper(self, command, *, settle_steps):
            super().set_gripper(command, settle_steps=settle_steps)
            if suppress_success:
                self.world.success = False

    def policy(sdk, context):
        if context["memory_catalog"]:
            for item in context["memory_catalog"]:
                context["retrieve_memory"](item["memory_id"])
        if success or context["memory_catalog"]:
            sdk.gripper.close()

    return run_robocasa_sim_gt_episode(
        task_id=TASK,
        seed=seed,
        artifact_root=root,
        action_backend=OutcomeBackend(world),
        policy=policy,
        policy_id="paired-policy",
        perception_mode="sim_gt",
        client=RobocasaSimClient(TASK, transport=world.transport),
        store_path=shared,
        validation_memory_id=candidate,
        validation_variation={key: value for key, value in _case(seed).items() if key != "seed"} if variation else None,
        validation_config_sha256=(config_sha256 or f"test-config-{seed}") if variation else None,
        retrieve_memory=False,
        advisor_transport=advisor,
        assistance_credits=(1 if advisor else 0) if budget_credits is None else budget_credits,
        advisor_sleeper=lambda seconds: None,
    )


def _advisor(request):
    return json.dumps({
        "schema_version": "attentionbench.advisor-advice.v1",
        "request_type": "hint",
        "diagnosis": "No grasp command occurred.",
        "guidance": "Use the sim_gt object position before grasp.",
        "caution": "Avoid evaluator debug.",
        "confidence": 0.7,
    })


def _safety(path, attempt_id, count=0):
    path.write_text(json.dumps({
        "schema_version": "attentionbench.safety-monitor.v1",
        "source": "independent_safety_monitor", "unsafe_attempts": count,
        "attempt_id": attempt_id,
        "events": [{"kind": "state_sample", "eef_position_m": [0.0, 0.0, 0.0]}],
        "violations": [] if count == 0 else [{"kind": "test_violation", "event_index": 1}],
    }))
    return path


def _candidate(tmp_path):
    shared = tmp_path / "memory.sqlite3"
    origin = _episode(tmp_path, shared, 101, advisor=_advisor)
    memory_id = origin["memory_candidate_id"]
    manager = MemoryV2Manager(AttentionStore(shared))
    provenance = manager.provenance(memory_id)
    assert provenance["source_kind"] == "advisor_proxy"
    assert provenance["human_attention_seconds"] == 0
    assert provenance["request_id"] and provenance["response_id"]
    assert provenance["received_guidance"] == "Use the sim_gt object position before grasp."
    assert len(provenance["response_content_sha256"]) == 64
    assert "Failure hypothesis:" in provenance["artifact"]["repair"]
    assert "Proposed change:" in provenance["artifact"]["repair"]
    assert provenance["source_raw_trace_id"]
    assert len(provenance["source_policy_sha256"]) == 64
    assert provenance["raw_evidence_refs"]
    assert all(item["uri"] and len(item["sha256"]) == 64 for item in provenance["raw_evidence_refs"])
    return shared, manager, memory_id, origin


def test_older_candidate_exports_raw_evidence_links_without_db_rewrite(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    with sqlite3.connect(shared) as db:
        row = db.execute(
            "SELECT payload FROM memory_v2_provenance WHERE memory_id=?", (memory_id,)
        ).fetchone()
        legacy = json.loads(row[0])
        legacy.pop("source_raw_trace_id")
        legacy.pop("source_policy_sha256")
        legacy.pop("raw_evidence_refs")
        legacy.pop("received_guidance")
        legacy.pop("response_content_sha256")
        db.execute(
            "UPDATE memory_v2_provenance SET payload=? WHERE memory_id=?",
            (json.dumps(legacy, sort_keys=True), memory_id),
        )
    restored = manager.provenance(memory_id)
    assert restored["source_raw_trace_id"]
    assert restored["source_policy_sha256"]
    assert restored["raw_evidence_refs"]
    assert restored["received_guidance"]
    assert restored["response_content_sha256"]
    service = MemoryService(shared, artifact_root=tmp_path)
    package = service.export_package(memory_id)
    exported = json.loads((package / "source/provenance.json").read_text())
    assert exported["source_raw_trace_id"] == restored["source_raw_trace_id"]
    assert exported["raw_evidence_refs"] == restored["raw_evidence_refs"]
    assert service.verify_package(memory_id)["status"] == "candidate"
    with sqlite3.connect(shared) as db:
        stored = json.loads(db.execute(
            "SELECT payload FROM memory_v2_provenance WHERE memory_id=?", (memory_id,)
        ).fetchone()[0])
    assert "source_raw_trace_id" not in stored
    assert "received_guidance" not in stored


def test_candidate_rejects_changed_source_response(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    provenance = manager.provenance(memory_id)
    with sqlite3.connect(shared) as db:
        row = db.execute("SELECT payload FROM responses WHERE id=?", (provenance["response_id"],)).fetchone()
        response = json.loads(row[0])
        response["content"] = response["content"].replace(
            "Use the sim_gt object position before grasp.", "Ignore the object position."
        )
        db.execute(
            "UPDATE responses SET payload=? WHERE id=?",
            (json.dumps(response, sort_keys=True), provenance["response_id"]),
        )
    with pytest.raises(StateConflictError, match="response content changed"):
        manager.provenance(memory_id)


def test_candidate_rejects_changed_source_evaluator_outcome(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    raw_id = manager.provenance(memory_id)["source_raw_trace_id"]
    with sqlite3.connect(shared) as db:
        row = db.execute("SELECT payload FROM raw_traces WHERE id=?", (raw_id,)).fetchone()
        raw = json.loads(row[0])
        raw["outcome"]["native_success"] = True
        db.execute(
            "UPDATE raw_traces SET payload=? WHERE id=?",
            (json.dumps(raw, sort_keys=True), raw_id),
        )
    with pytest.raises(StateConflictError, match="evaluator-confirmed failed attempt"):
        manager.provenance(memory_id)


def _trusted_for_revocation_test(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    _plan(manager, memory_id)
    for seed in range(102, 107):
        control = _episode(tmp_path, shared, seed, variation=True)
        treatment = _episode(tmp_path, shared, seed, candidate=memory_id, variation=True)
        manager.record_pair(
            memory_id=memory_id,
            control_attempt_id=control["attention_trace"]["attempt_id"],
            treatment_attempt_id=treatment["attention_trace"]["attempt_id"],
            control_safety=_safety(tmp_path / f"disable-c-{seed}.json", control["attention_trace"]["attempt_id"]),
            treatment_safety=_safety(tmp_path / f"disable-t-{seed}.json", treatment["attention_trace"]["attempt_id"]),
        )
    service = MemoryService(shared, artifact_root=tmp_path)
    assert service.promote(memory_id).status is MemoryStatus.TRUSTED
    return shared, service, memory_id


def test_trusted_use_records_evaluator_outage_without_validation_credit(tmp_path):
    shared, service, memory_id = _trusted_for_revocation_test(tmp_path)

    class EvaluatorFailureWorld(FakeWorld):
        def __init__(self):
            super().__init__()
            self.evaluations = 0

        def transport(self, method, url, payload, timeout):
            if url.endswith("/task/success"):
                self.evaluations += 1
                if self.evaluations == 2:
                    raise RuntimeError("native evaluator unavailable")
            return super().transport(method, url, payload, timeout)

    world = EvaluatorFailureWorld()
    result = run_robocasa_sim_gt_episode(
        task_id=TASK, seed=107, artifact_root=tmp_path,
        action_backend=FakeActionBackend(world),
        policy=lambda sdk, context: context["retrieve_memory"](memory_id),
        policy_id="eval-outage-policy", perception_mode="sim_gt",
        client=RobocasaSimClient(TASK, transport=world.transport),
        store_path=shared, memory_gateway=service,
        runtime_variation={key: value for key, value in _case(102).items() if key != "seed"},
        advisor_transport=_advisor, assistance_credits=1,
        advisor_sleeper=lambda seconds: None,
    )
    attempt_id = result["attention_trace"]["attempt_id"]
    raw = service.store.get_raw_trace(result["attention_trace"]["raw_trace_id"])
    assert raw["outcome"]["evaluator_authoritative"] is False
    assert service.store.list_memory_uses(memory_id)[-1]["outcome"] == "evaluator_unavailable"
    assert result["advisor_advice"]["request_type"] == "hint"
    assert result["memory_candidate_error"] == "source attempt has no authoritative native evaluator verdict"
    assert "memory_candidate_id" not in result
    with pytest.raises(StateConflictError, match="authoritative native evaluator"):
        service.v2._trial(attempt_id)


def test_operator_disable_between_catalog_and_use_blocks_guidance(tmp_path):
    shared, service, memory_id = _trusted_for_revocation_test(tmp_path)
    denied = []

    def policy(sdk, context):
        assert context["memory_catalog"][0]["memory_id"] == memory_id
        service.disable(memory_id, actor="operator-1", reason="unsafe guidance")
        with pytest.raises(KeyError, match="no longer trusted"):
            context["retrieve_memory"](memory_id)
        denied.append(True)

    world = FakeWorld()
    result = run_robocasa_sim_gt_episode(
        task_id=TASK, seed=107, artifact_root=tmp_path,
        action_backend=FakeActionBackend(world), policy=policy,
        policy_id="disabled-during-run", perception_mode="sim_gt",
        client=RobocasaSimClient(TASK, transport=world.transport),
        store_path=shared, memory_gateway=service,
        runtime_variation={key: value for key, value in _case(102).items() if key != "seed"},
    )
    assert denied and result["memory_ids"] == []
    assert service.get_memory(memory_id).status is MemoryStatus.DISABLED
    assert service.store.list_memory_uses(memory_id) == []


@pytest.mark.parametrize("operator_action,expected_status", [
    ("rollback", "rolled_back"),
    ("expire", "expired"),
])
def test_operator_lifecycle_change_between_catalog_and_use_blocks_guidance(
    tmp_path, operator_action, expected_status,
):
    shared, service, memory_id = _trusted_for_revocation_test(tmp_path)
    denied = []

    def policy(sdk, context):
        assert context["memory_catalog"][0]["memory_id"] == memory_id
        if operator_action == "rollback":
            service.rollback(memory_id, actor="operator-1", reason="unsafe repair")
        else:
            service.set_expiry(
                memory_id, expires_at=time.time() - 1,
                actor="operator-1", reason="retention limit",
            )
        with pytest.raises(KeyError, match="no longer trusted"):
            context["retrieve_memory"](memory_id)
        denied.append(True)

    world = FakeWorld()
    result = run_robocasa_sim_gt_episode(
        task_id=TASK, seed=107, artifact_root=tmp_path,
        action_backend=FakeActionBackend(world), policy=policy,
        policy_id=f"{operator_action}-during-run", perception_mode="sim_gt",
        client=RobocasaSimClient(TASK, transport=world.transport),
        store_path=shared, memory_gateway=service,
        runtime_variation={key: value for key, value in _case(102).items() if key != "seed"},
    )
    assert denied and result["memory_ids"] == []
    assert service.get_memory(memory_id).status.value == expected_status
    assert service.store.list_memory_uses(memory_id) == []


def test_use_before_operator_disable_still_records_outcome(tmp_path):
    shared, service, memory_id = _trusted_for_revocation_test(tmp_path)
    seen = []

    def policy(sdk, context):
        seen.append(context["retrieve_memory"](memory_id)["version"])
        service.disable(memory_id, actor="operator-1", reason="new counterexample")

    world = FakeWorld()
    result = run_robocasa_sim_gt_episode(
        task_id=TASK, seed=107, artifact_root=tmp_path,
        action_backend=FakeActionBackend(world), policy=policy,
        policy_id="revoked-after-use", perception_mode="sim_gt",
        client=RobocasaSimClient(TASK, transport=world.transport),
        store_path=shared, memory_gateway=service,
        runtime_variation={key: value for key, value in _case(102).items() if key != "seed"},
    )
    assert seen == [1] and result["memory_ids"] == [memory_id]
    assert service.get_memory(memory_id).status is MemoryStatus.DISABLED
    uses = service.store.list_memory_uses(memory_id)
    assert len(uses) == 1
    assert uses[0]["attempt_id"] == result["attention_trace"]["attempt_id"]
    assert uses[0]["memory_version"] == 1 and uses[0]["outcome"] == "failed"
    raw = service.store.get_raw_trace(result["attention_trace"]["raw_trace_id"])
    event = next(item for item in raw["events"] if item["event_type"] == "attention.memory_retrieval")
    assert uses[0]["used_at"] == event["timestamp"]


def test_candidate_provenance_and_five_paired_runs_gate_promotion(tmp_path):
    shared, manager, memory_id, origin = _candidate(tmp_path)
    store = AttentionStore(shared)
    with pytest.raises(StateConflictError, match="predefined variation"):
        manager.validate_and_promote(memory_id)
    _plan(manager, memory_id)
    for seed in range(102, 107):
        control = _episode(tmp_path, shared, seed, variation=True)
        treatment = _episode(tmp_path, shared, seed, candidate=memory_id, variation=True)
        safety_control = _safety(tmp_path / f"safety-control-{seed}.json", control["attention_trace"]["attempt_id"])
        safety_treatment = _safety(tmp_path / f"safety-treatment-{seed}.json", treatment["attention_trace"]["attempt_id"])
        manager.record_pair(
            memory_id=memory_id,
            control_attempt_id=control["attention_trace"]["attempt_id"],
            treatment_attempt_id=treatment["attention_trace"]["attempt_id"],
            control_safety=safety_control,
            treatment_safety=safety_treatment,
        )
        assert control["attention_trace"]["bundle"] != treatment["attention_trace"]["bundle"]
        assert json.loads((Path(control["artifact_dir"]) / "attention_bundle.json").read_text())["run"]["run_id"] == control["attention_trace"]["run_id"]
    report = manager.impact_report(memory_id)
    assert report["paired_dev_seeds"] == 5
    assert report["success_gain"] == 5
    assert report["empirical_confidence"] == 1.0
    assert report["treatment_success_rate_95ci"]["method"] == "wilson_score"
    assert report["treatment_success_rate_95ci"]["n"] == 5
    assert 0.56 < report["treatment_success_rate_95ci"]["lower"] < 0.57
    assert report["treatment_success_rate_95ci"]["upper"] == 1.0
    assert report["source_kind"] == "advisor_proxy"
    assert report["policy_changed_since_source"] is False
    assert report["human_seconds_per_success_gain"] is None
    source_ref = manager.provenance(memory_id)["raw_evidence_refs"][0]
    source_file = tmp_path / source_ref["uri"].removeprefix("artifact://")
    original_source = source_file.read_bytes()
    source_file.write_bytes(b"tampered source evidence")
    with pytest.raises(StateConflictError, match="digest changed"):
        manager.validate_and_promote(memory_id)
    source_file.write_bytes(original_source)
    assert manager.validate_and_promote(memory_id).status is MemoryStatus.TRUSTED
    assert manager.validate_and_promote(memory_id).status is MemoryStatus.TRUSTED
    available = []
    passive = run_robocasa_sim_gt_episode(
        task_id=TASK, seed=107, artifact_root=tmp_path,
        action_backend=FakeActionBackend(FakeWorld()),
        policy=lambda sdk, context: available.extend(context["memory_catalog"]),
        policy_id="passive-policy", perception_mode="sim_gt",
        client=RobocasaSimClient(TASK, transport=FakeWorld().transport),
        store_path=shared,
        runtime_variation={key: value for key, value in _case(102).items() if key != "seed"},
    )
    assert available[0]["memory_id"] == memory_id
    assert "guidance" not in available[0]
    assert passive["memory_ids"] == []
    assert store.list_memory_uses(memory_id) == []
    with pytest.raises(StateConflictError, match="trusted retrieval event"):
        service = MemoryService(shared)
        service.record_use(MemoryUseRecord(
            use_id="forged-passive-use", memory_id=memory_id, memory_version=1,
            run_id=passive["attention_trace"]["run_id"],
            attempt_id=passive["attention_trace"]["attempt_id"],
            used_at=100.0, outcome="failed",
        ))
    seen = []
    reused = run_robocasa_sim_gt_episode(
        task_id=TASK, seed=107, artifact_root=tmp_path,
        action_backend=FakeActionBackend(FakeWorld()),
        policy=lambda sdk, context: seen.extend(
            context["retrieve_memory"](item["memory_id"])
            for item in context["memory_catalog"]
        ),
        policy_id="reuse-policy", perception_mode="sim_gt",
        client=RobocasaSimClient(TASK, transport=FakeWorld().transport),
        store_path=shared,
        runtime_variation={key: value for key, value in _case(102).items() if key != "seed"},
    )
    assert reused["memory_ids"] == [memory_id]
    assert seen[0]["memory_id"] == memory_id
    assert seen[0]["version"] == 1
    assert seen[0]["source_kind"] == "advisor_proxy"
    assert seen[0]["perception_mode"] == "sim_gt"
    assert seen[0]["validation_status"] == "trusted"
    assert len(store.list_memory_uses(memory_id)) == 1
    def timeout_after_retrieval(sdk, context):
        context["retrieve_memory"](memory_id)
        raise TimeoutError("simulated policy deadline")

    timed_out = run_robocasa_sim_gt_episode(
        task_id=TASK, seed=107, artifact_root=tmp_path,
        action_backend=FakeActionBackend(FakeWorld()),
        policy=timeout_after_retrieval,
        policy_id="timeout-policy", perception_mode="sim_gt",
        client=RobocasaSimClient(TASK, transport=FakeWorld().transport),
        store_path=shared,
        runtime_variation={key: value for key, value in _case(102).items() if key != "seed"},
    )
    assert timed_out["status"] == "timeout"
    assert store.list_memory_uses(memory_id)[-1]["outcome"] == "timeout"
    timeout_raw = store.get_raw_trace(timed_out["attention_trace"]["raw_trace_id"])
    assert timeout_raw["outcome"]["evaluator_authoritative"] is False
    assert timeout_raw["outcome"]["evaluator_verdict"] is None
    reused_use_time = next(
        item["used_at"] for item in store.list_memory_uses(memory_id)
        if item["attempt_id"] == reused["attention_trace"]["attempt_id"]
    )
    with pytest.raises(StateConflictError, match="outcome disagrees"):
        service = MemoryService(shared)
        service.record_use(MemoryUseRecord(
            use_id="forged-outcome", memory_id=memory_id, memory_version=1,
            run_id=reused["attention_trace"]["run_id"],
            attempt_id=reused["attention_trace"]["attempt_id"],
            used_at=reused_use_time,
            outcome="native_success" if not reused["native_success"] else "failed",
        ))
    assert origin["attention_trace"]["bundle"] != reused["attention_trace"]["bundle"]
    service = MemoryService(shared)
    context = {
        "suite": "robocasa", "task_id": TASK, "perception_mode": "sim_gt",
        **{key: value for key, value in _case(102).items() if key != "seed"},
    }
    source_file.write_bytes(b"tampered after promotion")
    with pytest.raises(StateConflictError, match="digest changed"):
        service.retrieve(context, now=20.0)
    source_file.write_bytes(original_source)
    service.set_expiry(memory_id, expires_at=30.0, actor="operator-1", reason="trial TTL")
    assert service.retrieve(context, now=0.0) == []
    assert service.get_memory(memory_id).status is MemoryStatus.EXPIRED
    assert service.verify_package(memory_id)["status"] == "expired"
    lifecycle = [json.loads(line) for line in (
        service.artifacts.directory(memory_id) / "lifecycle.jsonl"
    ).read_text().splitlines()]
    assert lifecycle[-1]["status"] == "expired"
    assert lifecycle[-1]["version"] == 1
    assert lifecycle[-1]["expires_at"] == 30.0


def test_reject_unexposed_treatment_and_tampered_safety(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    _plan(manager, memory_id)
    control = _episode(tmp_path, shared, 102, variation=True)
    unexposed = _episode(tmp_path, shared, 102, variation=True)
    cpath = _safety(tmp_path / "control.json", control["attention_trace"]["attempt_id"])
    tpath = _safety(tmp_path / "treatment.json", unexposed["attention_trace"]["attempt_id"])
    with pytest.raises(StateConflictError, match="attest"):
        manager.record_pair(
            memory_id=memory_id,
            control_attempt_id=control["attention_trace"]["attempt_id"],
            treatment_attempt_id=unexposed["attention_trace"]["attempt_id"],
            control_safety=cpath, treatment_safety=tpath,
        )
    exposed = _episode(tmp_path, shared, 102, candidate=memory_id, variation=True)
    _safety(tpath, exposed["attention_trace"]["attempt_id"])
    manager.record_pair(
        memory_id=memory_id,
        control_attempt_id=control["attention_trace"]["attempt_id"],
        treatment_attempt_id=exposed["attention_trace"]["attempt_id"],
        control_safety=cpath, treatment_safety=tpath,
    )
    _safety(tpath, exposed["attention_trace"]["attempt_id"], 1)
    with pytest.raises(StateConflictError, match="changed"):
        manager.impact_report(memory_id)


def test_candidate_cannot_be_used_as_normal_memory(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    assert manager.retrieve({
        "perception_mode": "sim_gt", "suite": "robocasa", "task_id": TASK,
    }, now=1.0) == []
    with pytest.raises(ValueError, match="candidate memory"):
        run_robocasa_sim_gt_episode(
            task_id=TASK, seed=102, artifact_root=tmp_path,
            action_backend=FakeActionBackend(FakeWorld()),
            policy=lambda sdk, context: None,
            policy_id="test", perception_mode="sim_gt",
            client=RobocasaSimClient(TASK, transport=FakeWorld().transport),
            store_path=shared,
            validation_memory_id="unknown",
        )


def test_no_gain_and_safety_regression_fail_closed(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    _plan(manager, memory_id)
    for seed in range(102, 107):
        control = _episode(tmp_path, shared, seed, success=True, variation=True)
        treatment = _episode(tmp_path, shared, seed, candidate=memory_id, success=True, variation=True)
        manager.record_pair(
            memory_id=memory_id,
            control_attempt_id=control["attention_trace"]["attempt_id"],
            treatment_attempt_id=treatment["attention_trace"]["attempt_id"],
            control_safety=_safety(tmp_path / f"safe-c-{seed}.json", control["attention_trace"]["attempt_id"]),
            treatment_safety=_safety(tmp_path / f"safe-t-{seed}.json", treatment["attention_trace"]["attempt_id"]),
        )
    with pytest.raises(StateConflictError, match="no measured success gain"):
        manager.validate_and_promote(memory_id)
    assert AttentionStore(shared).get_memory(memory_id).status.value == MemoryStatus.CANDIDATE.value


def test_promotion_rejects_failed_trial_savings_and_empty_retrieval_scope(tmp_path, monkeypatch):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    _plan(manager, memory_id)
    report = {
        "paired_dev_seeds": 5,
        "validation_policy_hashes": ["one-fixed-validation-policy"],
        "control_unsafe_attempts": 0, "treatment_unsafe_attempts": 0,
        "safety_regressions": [],
        "success_gain": 0, "treatment_successes": 4,
        "future_assistance_credits_saved": 1,
        "both_success_assistance_credits_saved": 0,
        "both_failed_assistance_credits_saved": 1,
        "validated_scope": [{
            "scene_id": "scene-0", "object_set_id": "objects-0",
            "camera_config_id": "camera-0", "task_variant_id": "task-variant-0",
        }],
    }
    monkeypatch.setattr(manager, "impact_report", lambda memory_id: report)
    monkeypatch.setattr(manager, "list_pairs", lambda memory_id: [
        {"seed": seed} for seed in range(102, 107)
    ])
    with pytest.raises(StateConflictError, match="successful-pair assistance saving"):
        manager.validate_and_promote(memory_id)
    report["both_success_assistance_credits_saved"] = 1
    report["validated_scope"] = []
    with pytest.raises(StateConflictError, match="no validated retrieval scope"):
        manager.validate_and_promote(memory_id)
    report["success_gain"] = 1
    report["treatment_successes"] = 5
    report["validated_scope"] = [{
        "scene_id": "scene-0", "object_set_id": "objects-0",
        "camera_config_id": "camera-0", "task_variant_id": "task-variant-0",
    }]
    report["control_unsafe_attempts"] = report["treatment_unsafe_attempts"] = 1
    report["safety_regressions"] = [{"seed": 103}]
    with pytest.raises(StateConflictError, match="paired safety regression"):
        manager.validate_and_promote(memory_id)
    report["safety_regressions"] = []
    report["control_unsafe_attempts"] = report["treatment_unsafe_attempts"] = 0
    report["validation_policy_hashes"] = ["policy-v1", "policy-v2"]
    with pytest.raises(StateConflictError, match="different policy code versions"):
        manager.validate_and_promote(memory_id)
    assert AttentionStore(shared).get_memory(memory_id).status.value == MemoryStatus.CANDIDATE.value


def test_validation_requires_independent_safety_evidence(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    _plan(manager, memory_id)
    control = _episode(tmp_path, shared, 102, variation=True)
    treatment = _episode(tmp_path, shared, 102, candidate=memory_id, variation=True)
    unsafe_file = tmp_path / "not-monitor.json"
    unsafe_file.write_text(json.dumps({"unsafe_attempts": 0, "source": "policy"}))
    with pytest.raises(ValueError, match="independent monitor"):
        manager.record_pair(
            memory_id=memory_id,
            control_attempt_id=control["attention_trace"]["attempt_id"],
            treatment_attempt_id=treatment["attention_trace"]["attempt_id"],
            control_safety=unsafe_file,
            treatment_safety=_safety(tmp_path / "monitor.json", treatment["attention_trace"]["attempt_id"]),
        )


def test_counterexample_is_archived_and_excluded_from_retrieval_scope(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    _plan(manager, memory_id)
    for seed in range(102, 107):
        control = _episode(tmp_path, shared, seed, variation=True, suppress_success=seed == 103)
        treatment = _episode(
            tmp_path, shared, seed, candidate=memory_id, variation=True,
            suppress_success=seed == 103,
        )
        manager.record_pair(
            memory_id=memory_id,
            control_attempt_id=control["attention_trace"]["attempt_id"],
            treatment_attempt_id=treatment["attention_trace"]["attempt_id"],
            control_safety=_safety(tmp_path / f"control-{seed}.json", control["attention_trace"]["attempt_id"]),
            treatment_safety=_safety(tmp_path / f"treatment-{seed}.json", treatment["attention_trace"]["attempt_id"]),
        )
    report = manager.impact_report(memory_id)
    assert report["treatment_successes"] == 4
    assert report["empirical_confidence"] == 0.8
    assert [item["seed"] for item in report["counterexamples"]] == [103]
    assert manager.validate_and_promote(memory_id).status is MemoryStatus.TRUSTED
    base = {"suite": "robocasa", "task_id": TASK, "perception_mode": "sim_gt"}
    variation = lambda seed: {key: value for key, value in _case(seed).items() if key != "seed"}
    assert manager.retrieve({**base, **variation(102)}, now=100.0)
    assert manager.retrieve({**base, **variation(103)}, now=100.0) == []


def test_assistance_saving_without_success_gain_can_promote(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    _plan(manager, memory_id, assistance_credits=1)
    store = AttentionStore(shared)
    for seed in range(102, 107):
        control = _episode(tmp_path, shared, seed, variation=True, success=True, budget_credits=1)
        treatment = _episode(
            tmp_path, shared, seed, candidate=memory_id,
            variation=True, success=True, budget_credits=1,
        )
        reservation_id = f"control-help-{seed}"
        store.reserve_assistance(control["attention_trace"]["run_id"], credits=1, reservation_id=reservation_id)
        store.settle_assistance(reservation_id, commit=True)
        manager.record_pair(
            memory_id=memory_id,
            control_attempt_id=control["attention_trace"]["attempt_id"],
            treatment_attempt_id=treatment["attention_trace"]["attempt_id"],
            control_safety=_safety(tmp_path / f"cost-control-{seed}.json", control["attention_trace"]["attempt_id"]),
            treatment_safety=_safety(tmp_path / f"cost-treatment-{seed}.json", treatment["attention_trace"]["attempt_id"]),
        )
    report = manager.impact_report(memory_id)
    assert report["success_gain"] == 0
    assert report["future_assistance_credits_saved"] == 5
    assert report["policy_changed_since_source"] is True
    assert len(report["validation_policy_hashes"]) == 1
    assert all(item["reason"] == "assistance_saved" for item in report["supporting_successes"])
    assert manager.validate_and_promote(memory_id).status is MemoryStatus.TRUSTED


def test_pair_registration_rejects_configuration_mismatch(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    _plan(manager, memory_id)
    control = _episode(tmp_path, shared, 102, variation=True)
    treatment = _episode(
        tmp_path, shared, 102, candidate=memory_id, variation=True,
        config_sha256="different-config",
    )
    with pytest.raises(StateConflictError, match="configuration differs"):
        manager.record_pair(
            memory_id=memory_id,
            control_attempt_id=control["attention_trace"]["attempt_id"],
            treatment_attempt_id=treatment["attention_trace"]["attempt_id"],
            control_safety=_safety(tmp_path / "config-c.json", control["attention_trace"]["attempt_id"]),
            treatment_safety=_safety(tmp_path / "config-t.json", treatment["attention_trace"]["attempt_id"]),
        )
