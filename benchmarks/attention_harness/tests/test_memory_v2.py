from __future__ import annotations

import json
from pathlib import Path

import pytest

from attention_memory_service.core.models import MemoryStatus
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
        "source": "independent_safety_monitor", "unsafe_attempts": count,
        "attempt_id": attempt_id,
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
    return shared, manager, memory_id, origin


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
    assert report["source_kind"] == "advisor_proxy"
    assert report["human_seconds_per_success_gain"] is None
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
    assert seen[0]["source_kind"] == "advisor_proxy"
    assert seen[0]["perception_mode"] == "sim_gt"
    assert seen[0]["validation_status"] == "trusted"
    assert len(store.list_memory_uses(memory_id)) == 1
    assert origin["attention_trace"]["bundle"] != reused["attention_trace"]["bundle"]
    service = MemoryService(shared)
    service.set_expiry(memory_id, expires_at=30.0, actor="operator-1", reason="trial TTL")
    context = {
        "suite": "robocasa", "task_id": TASK, "perception_mode": "sim_gt",
        **{key: value for key, value in _case(102).items() if key != "seed"},
    }
    assert service.retrieve(context, now=31.0) == []
    assert service.get_memory(memory_id).status is MemoryStatus.EXPIRED
    assert service.verify_package(memory_id)["status"] == "expired"


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
