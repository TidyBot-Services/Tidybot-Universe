from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from attention_memory_service import MemoryService

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_agent import MemoryAgent
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.robocasa_native.paired_trials import RoboCasaPairedTrialExecutor
from benchmarks.attention_harness.robocasa_native.discover_variations import discover_cases
from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend, SafetyViolation
from benchmarks.attention_harness.robocasa_native.sim_gt_runner import run_robocasa_sim_gt_episode
from benchmarks.attention_harness.tests.test_memory_v2 import _cases


class World:
    def __init__(self):
        self.success = False
        self.seed = None
        self.actions = 0

    def transport(self, method, url, payload, timeout):
        path = "/" + url.split("/", 3)[-1]
        if path == "/task/info":
            # A live service reports the language of its current (possibly
            # previous-seed) episode before the next reset.
            return {"task": "RoboCasa-Pn-P-Counter-To-Sink-v0", "lang": f"place mug in sink: {self.seed}"}
        if path == "/reset":
            self.seed = payload["seed"]
            self.success = False
            result = {"status": "ok"}
            if "variation" in payload:
                result["applied_variation"] = payload["variation"]
            if payload.get("discover_variation") is True:
                result["applied_variation"] = {
                    "scene_id": f"scene-{self.seed % 2}",
                    "object_set_id": f"objects-{self.seed % 2}",
                }
            return result
        if path == "/task/success":
            return {"success": self.success}
        if path == "/perceive":
            return {
                "objects": [{"name": "mug", "x": 0.1, "y": 0.2, "z": 0.3}],
                "cameras": payload.get("camera_names") if payload else [],
                "arm_base": [0, 0, 0], "arm_base_quat": [1, 0, 0, 0],
            }
        raise AssertionError(path)


class Backend:
    control_frame = "arm_base"

    def __init__(self, world):
        self.world = world

    def observe(self):
        return {"robot0_eef_pos": np.zeros(3), "robot0_joint_pos": np.zeros(7)}

    def move_arm_delta(self, *args):
        self.world.actions += 1

    def move_arm_to_position(self, *args, **kwargs):
        self.world.actions += 1

    def set_gripper(self, command, *, settle_steps):
        self.world.actions += 1
        self.world.success = True


def memory_using_policy(sdk, context):
    for item in context["memory_catalog"]:
        context["retrieve_memory"](item["memory_id"])
        sdk.gripper.close()


def test_candidate_to_paired_evidence_to_promotion_smoke(tmp_path: Path):
    world = World()
    store_path = tmp_path / "attention_memory.sqlite3"
    service = MemoryService(AttentionStore(store_path), artifact_root=tmp_path)
    client_factory = lambda task: RobocasaSimClient(task, transport=world.transport)
    source = run_robocasa_sim_gt_episode(
        task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
        store_path=store_path, action_backend=Backend(world),
        policy=memory_using_policy, policy_id="smoke-policy",
        perception_mode="sim_gt", client=client_factory("counter_to_sink"),
        advisor_transport=lambda request: json.dumps({
            "schema_version": "attentionbench.advisor-advice.v1",
            "request_type": "hint", "diagnosis": "No action", "guidance": "Close gripper",
            "caution": "Simulator only", "confidence": 0.8,
        }),
        advisor_sleeper=lambda seconds: None, assistance_credits=1,
        memory_gateway=service,
    )
    assert source["native_success"] is False
    memory_id = source["memory_candidate_id"]
    agent = MemoryAgent(service)
    executor = RoboCasaPairedTrialExecutor(
        policy=memory_using_policy, policy_id="smoke-policy",
        artifact_root=tmp_path, store_path=store_path,
        backend_factory=lambda: Backend(world), client_factory=client_factory,
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
        assert control_config["seed"] == treatment_config["seed"] == pair["seed"]
        assert control_config["arm"] == "control"
        assert treatment_config["arm"] == "treatment"
        assert (control / "result.json").is_file()
        assert (treatment / "result.json").is_file()
        assert (control / "attention_bundle.json").is_file()
        assert (treatment / "attention_bundle.json").is_file()
    assert agent.request_promotion(memory_id).status.value == "trusted"
    variation = {key: value for key, value in _cases()[0].items() if key != "seed"}
    context = {"suite": "robocasa", "task_id": "counter_to_sink", "perception_mode": "sim_gt", **variation}
    assert [item.memory_id for item in service.retrieve(context, now=100.0)] == [memory_id]
    assert service.retrieve({**context, "scene_id": "untested-scene"}, now=100.0) == []
    assert service.disable(memory_id, actor="operator-1", reason="camera mismatch").status.value == "disabled"
    assert service.retrieve(context, now=100.0) == []
    assert service.rollback(memory_id, actor="operator-1", reason="regression").status.value == "rolled_back"
    assert service.retrieve(context, now=100.0) == []
    assert service.verify_package(memory_id)["status"] == "rolled_back"
    lifecycle = [json.loads(line) for line in (service.artifacts.directory(memory_id) / "lifecycle.jsonl").read_text().splitlines()]
    assert [event["actor"] for event in lifecycle if event["status"] in {"disabled", "rolled_back"}] == [
        "operator-1", "operator-1",
    ]


def test_independent_monitor_blocks_unsafe_command_and_records_evidence(tmp_path):
    world = World()
    monitor = SafetyMonitorBackend(Backend(world), max_delta_m=0.1)
    with pytest.raises(SafetyViolation, match="delta_exceeds_limit"):
        monitor.move_arm_delta(0.2, 0, 0, (0, 0, 0))
    assert world.actions == 0
    artifact = json.loads(monitor.write_artifact(tmp_path / "safety.json", attempt_id="a").read_text())
    assert artifact["source"] == "independent_safety_monitor"
    assert artifact["unsafe_attempts"] == 1
    assert artifact["violations"][0]["kind"] == "delta_exceeds_limit"


def test_validation_does_not_run_policy_without_simulator_variation_attestation(tmp_path):
    class UnattestedWorld(World):
        def transport(self, method, url, payload, timeout):
            if url.endswith("/reset"):
                return {"status": "ok"}
            return super().transport(method, url, payload, timeout)

    world = UnattestedWorld()
    called = []
    result = run_robocasa_sim_gt_episode(
        task_id="counter_to_sink", seed=102, artifact_root=tmp_path,
        action_backend=Backend(world), policy=lambda sdk, context: called.append(True),
        policy_id="attestation-test", perception_mode="sim_gt",
        client=RobocasaSimClient("counter_to_sink", transport=world.transport),
        validation_variation={key: value for key, value in _cases()[0].items() if key != "seed"},
    )
    assert result["status"] == "failed"
    assert "did not attest" in result["error"]
    assert called == []


def test_variation_discovery_freezes_realized_ids_and_camera_views():
    world = World()
    cases = discover_cases(
        RobocasaSimClient("counter_to_sink", transport=world.transport),
        seeds=(102, 103, 104, 105, 106),
        camera_configs=(("base_camera",), ("wrist_camera",)),
        task_prompts=("place mug in sink", "move the mug into the sink"),
    )
    assert len(cases) == 5
    assert cases[0]["scene_id"] == "scene-0"
    assert cases[1]["object_set_id"] == "objects-1"
    assert cases[1]["camera_names"] == ["wrist_camera"]
    assert cases[1]["task_prompt"] == "move the mug into the sink"


def test_variation_discovery_rejects_nondeterministic_seed():
    class UnstableWorld(World):
        resets = 0

        def transport(self, method, url, payload, timeout):
            result = super().transport(method, url, payload, timeout)
            if url.endswith("/reset") and payload.get("discover_variation"):
                self.resets += 1
                result["applied_variation"]["scene_id"] += f"-{self.resets}"
            return result

    world = UnstableWorld()
    with pytest.raises(RuntimeError, match="same seed produced different"):
        discover_cases(
            RobocasaSimClient("counter_to_sink", transport=world.transport),
            seeds=(102, 103, 104, 105, 106),
            camera_configs=(("base_camera",), ("wrist_camera",)),
            task_prompts=("place mug in sink", "move the mug into the sink"),
        )
