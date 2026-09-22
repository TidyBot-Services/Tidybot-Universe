from __future__ import annotations

import numpy as np

from benchmarks.attention_harness.robosuite_adapter import StepResult
from benchmarks.attention_harness.robot_sdk import NativeRobotSDK


class FakeAdapter:
    action_shape = (7,)

    def __init__(self) -> None:
        self.position = np.zeros(3, dtype=np.float64)
        self.actions: list[np.ndarray] = []

    def observe(self):
        return {
            "robot0_eef_pos": self.position.copy(),
            "agentview_image": np.zeros((2, 2, 3), dtype=np.uint8),
        }

    def step(self, action):
        action = np.asarray(action, dtype=np.float64)
        self.actions.append(action.copy())
        self.position += action[:3] * 0.05
        return StepResult(self.observe(), 0.0, False, {})


def test_sdk_exposes_only_owned_facade() -> None:
    adapter = FakeAdapter()
    sdk = NativeRobotSDK(adapter)  # type: ignore[arg-type]
    assert set(sdk.describe()) == {"frame", "arm", "gripper", "sensors"}
    assert set(sdk.sensors.get_observation()) == {"robot0_eef_pos", "agentview_image"}


def test_local_osc_move_and_gripper_state() -> None:
    adapter = FakeAdapter()
    sdk = NativeRobotSDK(adapter)  # type: ignore[arg-type]
    sdk.gripper.close(settle_steps=2)
    sdk.arm.move_to_position(0.1, -0.05, 0.025, max_steps=10)
    assert np.linalg.norm(adapter.position - (0.1, -0.05, 0.025)) < 0.004
    assert all(action[-1] == 1.0 for action in adapter.actions)


def test_move_delta_preserves_gripper_command() -> None:
    adapter = FakeAdapter()
    sdk = NativeRobotSDK(adapter)  # type: ignore[arg-type]
    sdk.gripper.open(settle_steps=1)
    sdk.arm.move_delta(0.2, 0.0, 0.0)
    assert adapter.actions[-1].tolist() == [0.2, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0]
