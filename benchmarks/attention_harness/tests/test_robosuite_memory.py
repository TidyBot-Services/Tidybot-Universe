from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from attention_memory_service import MemoryService
from attention_memory_service.core.models import MemoryRecord, MemoryStatus
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_agent import MemoryAgent
from benchmarks.attention_harness.robosuite_memory.adapter import RobosuiteSimGTBackend
from benchmarks.attention_harness.robosuite_memory.discover_variations import discover_cases
from benchmarks.attention_harness.robosuite_memory.paired_trials import RobosuitePairedTrialExecutor
from benchmarks.attention_harness.robosuite_memory.sim_gt_runner import run_robosuite_sim_gt_episode
from robosuite_sim.client import ClientStep


class FakeClient:
    def __init__(self):
        self.seed = None
        self.camera = "agentview"
        self.success = False
        self.closed = False

    def _observation(self):
        return {"robot0_eef_pos": np.zeros(3), "robot0_joint_pos": np.zeros(7)}

    def reset_attested(self, **request):
        self.seed = request["seed"]
        self.camera = request["camera_name"]
        self.success = False
        self.closed = False
        group = self.seed % 2
        applied = {"scene_id": f"scene-{group}", "object_set_id": f"objects-{group}"}
        if "variation" in request and request["variation"] != applied:
            raise RuntimeError("reset did not realize requested variation")
        return self._observation(), np.full(7, -1.0), np.full(7, 1.0), {
            "task_id": request["task_id"], "control_frame": "robosuite_world",
        }, applied

    def perceive_gt(self, *, target_names=None, camera_names=None):
        if camera_names is not None and camera_names != [self.camera]:
            raise RuntimeError("wrong camera")
        objects = [{"name": "cube", "position": [0.1, 0.2, 0.3]}]
        if target_names is not None:
            objects = [row for row in objects if row["name"] in target_names]
        return {"objects": objects, "cameras": [self.camera], "frame": "robosuite_world"}

    def step(self, action):
        if float(action[-1]) == 1.0:
            self.success = True
        return ClientStep(self._observation(), 0.0, False, {})

    def native_success(self):
        return self.success

    def close_environment(self):
        self.closed = True


def memory_using_policy(sdk, context):
    for item in context["memory_catalog"]:
        context["retrieve_memory"](item["memory_id"])
        sdk.gripper.close()


def _adapter(task, camera="agentview", client=None):
    return RobosuiteSimGTBackend(
        task, camera_name=camera, service_url="http://fake-robosuite",
        client=client or FakeClient(),
    )


def _cases():
    return tuple({
        "seed": seed, "scene_id": f"scene-{seed % 2}",
        "object_set_id": f"objects-{seed % 2}",
        "camera_config_id": f"camera:{'agentview' if seed % 2 == 0 else 'frontview'}",
        "camera_names": ["agentview" if seed % 2 == 0 else "frontview"],
        "task_variant_id": f"task-variant-{seed % 2}",
        "task_prompt": "Lift the cube" if seed % 2 == 0 else "Raise the cube",
    } for seed in range(102, 107))


def test_robosuite_failed_native_evaluator_has_no_authoritative_verdict(tmp_path):
    class EvaluatorFailureClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.evaluations = 0

        def native_success(self):
            self.evaluations += 1
            if self.evaluations == 2:
                raise RuntimeError("evaluator unavailable")
            return super().native_success()

    result = run_robosuite_sim_gt_episode(
        task_id="cube_lift", seed=101, artifact_root=tmp_path,
        adapter=_adapter("cube_lift", client=EvaluatorFailureClient()),
        policy=lambda sdk, context: None, policy_id="evaluator-failure",
        perception_mode="sim_gt", retrieve_memory=False,
    )
    raw = AttentionStore(Path(result["attention_trace"]["store"])).get_raw_trace(
        result["attention_trace"]["raw_trace_id"]
    )
    assert result["status"] == "failed"
    assert raw["outcome"]["evaluator_authoritative"] is False
    assert raw["outcome"]["evaluator_verdict"] is None


def test_robosuite_stale_memory_catalog_does_not_release_guidance(tmp_path):
    memory = MemoryRecord(
        memory_id="memory-1", version=1, source_trace_id="trace-1",
        guidance="approach from above", candidate_repair="adjust grasp",
        applicability={"suite": "robosuite", "task_id": "cube_lift", "perception_mode": "sim_gt"},
        evidence_refs=("evidence-1",), created_at=1.0, status=MemoryStatus.TRUSTED,
    )

    class RevokedGateway:
        calls = 0

        def retrieve(self, context, *, now):
            self.calls += 1
            return [memory] if self.calls == 1 else []

        def provenance(self, memory_id):
            return {"artifact": {"kind": "text_hint"}, "source_kind": "advisor_proxy"}

    gateway = RevokedGateway()
    denied = []

    def policy(sdk, context):
        assert context["memory_catalog"][0]["memory_id"] == memory.memory_id
        with pytest.raises(KeyError, match="no longer trusted"):
            context["retrieve_memory"](memory.memory_id)
        denied.append(True)

    result = run_robosuite_sim_gt_episode(
        task_id="cube_lift", seed=101, artifact_root=tmp_path,
        adapter=_adapter("cube_lift"), policy=policy,
        policy_id="stale-catalog", perception_mode="sim_gt",
        memory_gateway=gateway,
    )
    assert denied and gateway.calls == 2
    assert result["memory_ids"] == []


def test_robosuite_rejected_validation_candidate_does_not_release_guidance(tmp_path):
    store_path = tmp_path / "attention.sqlite3"
    source = run_robosuite_sim_gt_episode(
        task_id="cube_lift", seed=101, artifact_root=tmp_path,
        store_path=store_path, adapter=_adapter("cube_lift"),
        policy=lambda sdk, context: None, policy_id="source",
        perception_mode="sim_gt", retrieve_memory=False,
    )
    memory = MemoryRecord(
        memory_id="candidate-1", version=1, source_trace_id=source["attention_trace"]["advisor_trace_id"],
        guidance="approach from above", candidate_repair="adjust grasp",
        applicability={"suite": "robosuite", "task_id": "cube_lift", "perception_mode": "sim_gt"},
        evidence_refs=("evidence-1",), created_at=1.0,
    )
    AttentionStore(store_path).create_memory(memory)

    class RevokedGateway:
        def provenance(self, memory_id):
            return {"artifact": {"kind": "text_hint"}, "source_kind": "advisor_proxy"}

        def get_memory(self, memory_id):
            return replace(memory, status=MemoryStatus.REJECTED)

    def policy(sdk, context):
        with pytest.raises(KeyError, match="validation candidate is no longer available"):
            context["retrieve_memory"](memory.memory_id)

    result = run_robosuite_sim_gt_episode(
        task_id="cube_lift", seed=101, artifact_root=tmp_path,
        store_path=store_path, adapter=_adapter("cube_lift"), policy=policy,
        policy_id="revoked-candidate", perception_mode="sim_gt",
        validation_memory_id=memory.memory_id, retrieve_memory=False,
        memory_gateway=RevokedGateway(),
    )
    assert result["memory_ids"] == []


def test_robosuite_candidate_pair_promotion_and_scoped_use(tmp_path: Path):
    store_path = tmp_path / "attention_memory.sqlite3"
    service = MemoryService(AttentionStore(store_path), artifact_root=tmp_path)
    source = run_robosuite_sim_gt_episode(
        task_id="cube_lift", seed=101, artifact_root=tmp_path,
        store_path=store_path, adapter=_adapter("cube_lift"),
        policy=memory_using_policy, policy_id="smoke-policy", perception_mode="sim_gt",
        advisor_transport=lambda request: json.dumps({
            "schema_version": "attentionbench.advisor-advice.v1",
            "request_type": "hint", "diagnosis": "No action",
            "guidance": "Close gripper", "caution": "Simulator only", "confidence": 0.8,
        }),
        advisor_sleeper=lambda seconds: None, assistance_credits=1,
        memory_gateway=service,
    )
    assert source["native_success"] is False
    memory_id = source["memory_candidate_id"]
    agent = MemoryAgent(service)
    executor = RobosuitePairedTrialExecutor(
        policy=memory_using_policy, policy_id="smoke-policy",
        artifact_root=tmp_path, store_path=store_path,
        adapter_factory=lambda task, camera: _adapter(task, camera),
        memory_gateway=service,
    )
    report = agent.run_validation(memory_id, executor=executor, cases=_cases())
    assert report["paired_dev_seeds"] == 5
    assert report["control_successes"] == 0
    assert report["treatment_successes"] == 5
    assert report["control_unsafe_attempts"] == 0
    assert report["treatment_unsafe_attempts"] == 0
    for pair in service.list_pairs(memory_id):
        control = Path(pair["control_safety"]["uri"]).parent
        treatment = Path(pair["treatment_safety"]["uri"]).parent
        control_config = json.loads((control / "trial_config.json").read_text())
        treatment_config = json.loads((treatment / "trial_config.json").read_text())
        assert control_config["config_sha256"] == treatment_config["config_sha256"]
        assert control_config["arm"] == "control"
        assert treatment_config["arm"] == "treatment"
    assert agent.request_promotion(memory_id).status.value == "trusted"
    variation = {key: value for key, value in _cases()[0].items() if key != "seed"}
    context = {"suite": "robosuite", "task_id": "cube_lift", "perception_mode": "sim_gt", **variation}
    assert [item.memory_id for item in service.retrieve(context, now=100.0)] == [memory_id]
    assert service.retrieve({**context, "suite": "robocasa"}, now=100.0) == []
    assert service.retrieve({**context, "scene_id": "unvalidated"}, now=100.0) == []
    assert service.retrieve({**context, "camera_names": ["wrong_camera"]}, now=100.0) == []
    assert service.retrieve({**context, "task_prompt": "different task wording"}, now=100.0) == []
    use = run_robosuite_sim_gt_episode(
        task_id="cube_lift", seed=102, artifact_root=tmp_path,
        store_path=store_path, adapter=_adapter("cube_lift", "agentview"),
        policy=memory_using_policy, policy_id="smoke-policy", perception_mode="sim_gt",
        runtime_variation=variation, memory_gateway=service,
    )
    assert use["native_success"] is True
    assert use["memory_ids"] == [memory_id]
    uses = AttentionStore(store_path).list_memory_uses(memory_id)
    assert len(uses) == 1
    assert uses[0]["memory_version"] == 1
    assert uses[0]["outcome"] == "native_success"
    denied = []

    def disable_before_use(sdk, context):
        assert context["memory_catalog"][0]["memory_id"] == memory_id
        service.disable(memory_id, actor="operator-1", reason="regression")
        with pytest.raises(KeyError, match="no longer trusted"):
            context["retrieve_memory"](memory_id)
        denied.append(True)

    revoked = run_robosuite_sim_gt_episode(
        task_id="cube_lift", seed=102, artifact_root=tmp_path,
        store_path=store_path, adapter=_adapter("cube_lift", "agentview"),
        policy=disable_before_use, policy_id="disable-before-use",
        perception_mode="sim_gt", runtime_variation=variation,
        memory_gateway=service,
    )
    assert denied and revoked["memory_ids"] == []
    assert len(AttentionStore(store_path).list_memory_uses(memory_id)) == 1
    assert service.get_memory(memory_id).status.value == "disabled"
    assert service.retrieve(context, now=100.0) == []


def test_robosuite_validation_rejects_unattested_case(tmp_path: Path):
    called = []
    bad = {key: value for key, value in _cases()[0].items() if key != "seed"}
    bad["scene_id"] = "wrong-scene"
    result = run_robosuite_sim_gt_episode(
        task_id="cube_lift", seed=102, artifact_root=tmp_path,
        adapter=_adapter("cube_lift"), policy=lambda sdk, context: called.append(True),
        policy_id="attestation-test", perception_mode="sim_gt",
        validation_variation=bad,
    )
    assert result["status"] == "failed"
    assert "variation" in result["error"]
    assert called == []


def test_robosuite_discovery_checks_realized_variations():
    cases = discover_cases(
        "cube_lift", seeds=(102, 103, 104, 105, 106),
        cameras=("agentview", "frontview"),
        task_prompts=("Lift the cube", "Raise the cube"),
        adapter_factory=lambda task, camera: _adapter(task, camera),
    )
    assert len(cases) == 5
    assert cases[1]["camera_names"] == ["frontview"]
    assert cases[0]["scene_id"] != cases[1]["scene_id"]
