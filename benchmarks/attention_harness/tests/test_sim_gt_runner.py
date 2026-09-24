from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from attention_memory_service import MemoryServiceClient

from benchmarks.attention_harness.core.memory import MemoryManager
from benchmarks.attention_harness.core.models import MemoryRecord, MemoryStatus
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.robocasa_native.sim_gt_runner import (
    run_robocasa_sim_gt_episode,
)


class FakeWorld:
    success = False
    calls: list

    def __init__(self):
        self.calls = []

    def transport(self, method, url, payload, timeout):
        path = "/" + url.split("/", 3)[-1]
        self.calls.append((method, path))
        if path == "/task/info":
            return {"task": "RoboCasa-Pn-P-Counter-To-Sink-v0", "lang": "place mug in sink"}
        if path == "/reset":
            self.success = False
            result = {"status": "ok"}
            if "variation" in payload:
                result["applied_variation"] = payload["variation"]
            return result
        if path == "/task/success":
            return {"success": self.success, "debug": {"object_pose": [9, 9, 9]}}
        if path == "/perceive":
            return {
                "objects": [{"name": "mug", "x": 1.0, "y": 2.0, "z": 3.0,
                             "segmentation_id": 7}],
                "cameras": payload.get("camera_names") if payload else [],
                "arm_base": [0.0, 0.0, 0.0],
                "arm_base_quat": [1.0, 0.0, 0.0, 0.0],
            }
        raise AssertionError(path)


class FakeActionBackend:
    control_frame = "arm_base"

    def __init__(self, world):
        self.world = world

    def observe(self):
        return {"robot0_eef_pos": np.zeros(3, dtype=np.float64)}

    def move_arm_delta(self, *args):
        return None

    def move_arm_to_position(self, *args, **kwargs):
        return None

    def set_gripper(self, command, *, settle_steps):
        self.world.success = True


def _run(tmp_path: Path, world, policy, **kwargs):
    return run_robocasa_sim_gt_episode(
        task_id="counter_to_sink",
        seed=101,
        artifact_root=tmp_path,
        action_backend=FakeActionBackend(world),
        policy=policy,
        policy_id="test-policy",
        perception_mode="sim_gt",
        client=RobocasaSimClient("counter_to_sink", transport=world.transport),
        **kwargs,
    )


def test_remote_memory_rejects_wrong_store_before_reset(tmp_path, monkeypatch):
    world = FakeWorld()
    gateway = MemoryServiceClient("http://127.0.0.1:8768", api_key="memory-service-test-key")
    monkeypatch.setattr(gateway, "health", lambda: {
        "status": "ok", "schema_version": "attentionbench.memory-service.v2",
    })
    monkeypatch.setattr(gateway, "store_id", lambda: "other-store")
    with pytest.raises(RuntimeError, match="different Attention store"):
        _run(tmp_path, world, lambda sdk, context: None, memory_gateway=gateway)
    assert ("POST", "/reset") not in world.calls


def test_sim_gt_runner_exposes_only_labelled_sdk_objects_and_persists_trace(tmp_path):
    world = FakeWorld()

    def policy(sdk, context):
        assert context["perception_mode"] == "sim_gt"
        assert context["initial_objects"][0] == {
            "name": "mug", "position": [1.0, 2.0, 3.0],
            "confidence": 1.0, "source": "sim_gt", "frame": "arm_base",
        }
        assert "native_success" not in context
        sdk.gripper.close()

    result = _run(tmp_path, world, policy)
    assert result["native_success"] is True
    assert result["formal_eligible"] is False
    assert result["score_namespace"] == "v2/gt_perception_attentionbench"
    assert world.calls.count(("GET", "/task/success")) == 2
    store = AttentionStore(Path(result["attention_trace"]["store"]))
    raw = store.get_raw_trace(result["attention_trace"]["raw_trace_id"])
    assert raw["metadata"]["runtime"]["perception_mode"] == "sim_gt"
    assert any(
        event["event_type"] == "sdk.perception" and "sim_gt" in json.dumps(event)
        for event in raw["events"]
    )
    assert "object_pose" not in json.dumps(raw)


def test_failed_gt_run_requests_advisor_and_stages_mode_tagged_memory(tmp_path):
    world = FakeWorld()
    advisor_requests = []

    def advisor(request):
        advisor_requests.append(request)
        return json.dumps({
            "schema_version": "attentionbench.advisor-advice.v1",
            "request_type": "hint",
            "diagnosis": "No grasp command occurred.",
            "guidance": "Recheck the sim_gt object position and grasp.",
            "caution": "Do not use evaluator debug.",
            "confidence": 0.7,
        })

    result = _run(
        tmp_path, world, lambda sdk, context: None,
        advisor_transport=advisor,
        assistance_credits=1,
        advisor_sleeper=lambda seconds: None,
    )
    assert result["native_success"] is False
    assert result["no_op_success"] is False
    assert result["memory_candidate_id"].startswith("candidate:")
    store = AttentionStore(Path(result["attention_trace"]["store"]))
    candidate = store.get_memory(result["memory_candidate_id"])
    assert candidate.status is MemoryStatus.CANDIDATE
    assert candidate.applicability["perception_mode"] == "sim_gt"
    assert "GT-perception" in advisor_requests[0]["messages"][0]["content"]


def test_runner_rejects_vision_mode_and_heldout_seed_before_reset(tmp_path):
    import pytest

    world = FakeWorld()
    with pytest.raises(ValueError, match="sim_gt"):
        run_robocasa_sim_gt_episode(
            task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
            action_backend=FakeActionBackend(world), policy=lambda sdk, context: None,
            policy_id="p", perception_mode="vision",
            client=RobocasaSimClient("counter_to_sink", transport=world.transport),
        )
    with pytest.raises(PermissionError, match="held-out"):
        run_robocasa_sim_gt_episode(
            task_id="counter_to_sink", seed=1001, artifact_root=tmp_path,
            action_backend=FakeActionBackend(world), policy=lambda sdk, context: None,
            policy_id="p", perception_mode="sim_gt",
            client=RobocasaSimClient("counter_to_sink", transport=world.transport),
        )
    assert world.calls == []


def test_memory_retrieval_requires_explicit_matching_perception_provenance(tmp_path):
    world = FakeWorld()
    shared = tmp_path / "shared.sqlite3"
    first = _run(tmp_path, world, lambda sdk, context: None, store_path=shared)
    trace_id = first["attention_trace"]["advisor_trace_id"]
    store = AttentionStore(shared)
    evidence = tuple(item["evidence_id"] for item in store.get_trace(trace_id)["evidence"])
    manager = MemoryManager(store)
    for memory_id, applicability in (
        ("untagged", {}),
        ("vision", {"perception_mode": "vision", "suite": "robocasa", "task_id": "counter_to_sink"}),
        ("gt", {"perception_mode": "sim_gt", "suite": "robocasa", "task_id": "counter_to_sink"}),
    ):
        manager.add_candidate(MemoryRecord(
            memory_id=memory_id, version=1, source_trace_id=trace_id,
            guidance=memory_id, candidate_repair=memory_id,
            applicability=applicability, evidence_refs=evidence, created_at=1.0,
        ))
        manager.record_validation(memory_id, successes=3, failures=0)
        manager.promote(memory_id)

    seen = []
    second = _run(
        tmp_path, world,
        lambda sdk, context: seen.extend(context["memory_catalog"]),
        store_path=shared,
    )
    # A legacy v1 trusted label without v2 request/response provenance and
    # paired-trial evidence cannot leak into the v2 policy context.
    assert seen == []
    assert second["memory_ids"] == []
    assert store.list_memory_uses("gt") == []
    assert store.list_memory_uses("untagged") == []
    assert store.list_memory_uses("vision") == []
