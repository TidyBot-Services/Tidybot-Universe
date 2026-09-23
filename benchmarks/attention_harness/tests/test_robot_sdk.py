from __future__ import annotations

import numpy as np

from benchmarks.attention_harness.robosuite_adapter import StepResult
from benchmarks.attention_harness.robot_sdk import NativeRobotSDK


class FakeAdapter:
    control_frame = "test_world"
    action_shape = (7,)

    def __init__(self) -> None:
        self.position = np.zeros(3, dtype=np.float64)
        self.actions: list[np.ndarray] = []

    def observe(self):
        return {
            "robot0_eef_pos": self.position.copy(),
            "agentview_image": np.zeros((2, 2, 3), dtype=np.uint8),
            "agentview_depth": np.full((2, 2, 1), 2.0, dtype=np.float32),
            "agentview_intrinsics": np.array(
                [[2.0, 0.0, 1.0], [0.0, 2.0, 1.0], [0.0, 0.0, 1.0]]
            ),
            "agentview_pose_mat": np.array(
                [
                    [1.0, 0.0, 0.0, 10.0],
                    [0.0, 1.0, 0.0, 20.0],
                    [0.0, 0.0, 1.0, 30.0],
                    [0.0, 0.0, 0.0, 1.0],
                ]
            ),
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
    assert sdk.describe()["frame"] == "test_world"
    assert set(sdk.describe()["sensors"]) == {"get_observation", "pixel_to_world"}
    assert set(sdk.sensors.get_observation()) == {
        "robot0_eef_pos",
        "agentview_image",
        "agentview_depth",
        "agentview_intrinsics",
        "agentview_pose_mat",
    }


def test_pixel_to_world_uses_only_public_rgbd_calibration() -> None:
    sdk = NativeRobotSDK(FakeAdapter())  # type: ignore[arg-type]
    assert sdk.sensors.pixel_to_world(1, 1) == (10.0, 20.0, 32.0)
    assert sdk.sensors.pixel_to_world(0, 0, depth_meters=2.0) == (9.0, 19.0, 32.0)


def test_pixel_to_world_rejects_invalid_pixels() -> None:
    sdk = NativeRobotSDK(FakeAdapter())  # type: ignore[arg-type]
    with np.testing.assert_raises(ValueError):
        sdk.sensors.pixel_to_world(2, 0)


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
