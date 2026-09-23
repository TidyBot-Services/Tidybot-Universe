from __future__ import annotations

import pytest

from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.robocasa_native.gt_perception import RobocasaGTPerception
from tidybot_sdk import TidyBotSDK
from tidybot_sdk.perception import ModeBoundPerceptionBackend, PerceptionMode


class ActionBackend:
    control_frame = "arm_base"

    def observe(self):
        return {}

    def move_arm_delta(self, *args):
        return None

    def move_arm_to_position(self, *args, **kwargs):
        return None

    def set_gripper(self, *args, **kwargs):
        return None


class Provider:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def find_objects(self, *, target_names=None, camera_names=None):
        self.calls += 1
        return self.rows


def test_explicit_mode_and_identical_sdk_object_shape() -> None:
    for mode, target in (("sim_gt", "robocasa_sim"), ("vision", "real_robot")):
        provider = Provider(
            [{"name": "mug", "position": [0.1, 0.2, 0.3],
              "confidence": 0.8, "source": mode, "frame": "arm_base",
              "simulator_debug": "must not escape"}]
        )
        sdk = TidyBotSDK(
            ModeBoundPerceptionBackend(
                ActionBackend(), mode=mode, target=target, provider=provider
            )
        )
        assert sdk.sensors.find_objects() == [
            {"name": "mug", "position": [0.1, 0.2, 0.3],
             "confidence": 0.8, "source": mode, "frame": "arm_base"}
        ]
        assert provider.calls == 1
        assert "find_objects" in sdk.describe()["sensors"]


def test_real_robot_rejects_gt_and_vision_cannot_fallback() -> None:
    with pytest.raises(ValueError, match="forbidden"):
        ModeBoundPerceptionBackend(
            ActionBackend(), mode=PerceptionMode.SIM_GT,
            target="real_robot", provider=Provider([])
        )
    provider = Provider(
        [{"name": "mug", "position": [1, 2, 3], "confidence": 1.0,
          "source": "sim_gt", "frame": "arm_base"}]
    )
    sdk = TidyBotSDK(
        ModeBoundPerceptionBackend(
            ActionBackend(), mode="vision", target="real_robot", provider=provider
        )
    )
    with pytest.raises(ValueError, match="source"):
        sdk.sensors.find_objects()
    assert provider.calls == 1


def test_gt_provider_transforms_world_to_control_frame_and_strips_debug() -> None:
    calls = []

    def transport(method, url, payload, timeout):
        calls.append((method, url, payload))
        if url.endswith("/perceive"):
            return {
                "objects": [{"name": "mug", "x": 1.0, "y": 3.0, "z": 3.0,
                             "fixture_context": "counter", "segmentation_id": 17}],
                "arm_base": [1.0, 2.0, 3.0],
                "arm_base_quat": [1.0, 0.0, 0.0, 0.0],
                "evaluator_debug": "must not escape",
            }
        raise AssertionError(url)

    client = RobocasaSimClient("counter_to_sink", transport=transport)
    sdk = TidyBotSDK(
        ModeBoundPerceptionBackend(
            ActionBackend(), mode="sim_gt", target="robocasa_sim",
            provider=RobocasaGTPerception(client)
        )
    )
    assert sdk.sensors.find_objects(["mug"], ["base_camera"]) == [
        {"name": "mug", "position": [0.0, 1.0, 0.0],
         "confidence": 1.0, "source": "sim_gt", "frame": "arm_base"}
    ]
    assert calls[0][2] == {"target_names": ["mug"], "camera_names": ["base_camera"]}
