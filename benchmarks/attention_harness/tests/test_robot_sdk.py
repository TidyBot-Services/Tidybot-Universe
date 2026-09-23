from __future__ import annotations

import numpy as np

from benchmarks.attention_harness.robosuite_adapter import StepResult
from tidybot_sdk import TidyBotSDK


class FakeAdapter:
    control_frame = "test_world"
    action_shape = (7,)

    def __init__(self) -> None:
        self.position = np.zeros(3, dtype=np.float64)
        self.actions: list[np.ndarray] = []
        self.gripper_command = -1.0

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

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        action = np.zeros(self.action_shape, dtype=np.float64)
        action[:3] = (dx, dy, dz)
        action[3:6] = rotation_delta
        action[-1] = self.gripper_command
        return self.step(action)

    def move_arm_to_position(self, x, y, z, *, tolerance, max_steps):
        target = np.asarray((x, y, z), dtype=np.float64)
        for _ in range(max_steps):
            error = target - self.position
            if np.linalg.norm(error) < tolerance:
                return
            delta = np.clip(error / 0.05, -1.0, 1.0)
            self.move_arm_delta(
                float(delta[0]),
                float(delta[1]),
                float(delta[2]),
                (0.0, 0.0, 0.0),
            )
        raise TimeoutError

    def set_gripper(self, command, *, settle_steps):
        if settle_steps < 1:
            raise ValueError
        self.gripper_command = command
        for _ in range(settle_steps):
            action = np.zeros(self.action_shape, dtype=np.float64)
            action[-1] = command
            self.step(action)


def test_sdk_exposes_only_owned_facade() -> None:
    adapter = FakeAdapter()
    sdk = TidyBotSDK(adapter)  # type: ignore[arg-type]
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
    sdk = TidyBotSDK(FakeAdapter())  # type: ignore[arg-type]
    assert sdk.sensors.pixel_to_world(1, 1) == (10.0, 20.0, 32.0)
    assert sdk.sensors.pixel_to_world(0, 0, depth_meters=2.0) == (9.0, 19.0, 32.0)


def test_pixel_to_world_rejects_invalid_pixels() -> None:
    sdk = TidyBotSDK(FakeAdapter())  # type: ignore[arg-type]
    with np.testing.assert_raises(ValueError):
        sdk.sensors.pixel_to_world(2, 0)


def test_local_osc_move_and_gripper_state() -> None:
    adapter = FakeAdapter()
    sdk = TidyBotSDK(adapter)  # type: ignore[arg-type]
    sdk.gripper.close(settle_steps=2)
    sdk.arm.move_to_position(0.1, -0.05, 0.025, max_steps=10)
    assert np.linalg.norm(adapter.position - (0.1, -0.05, 0.025)) < 0.004
    assert all(action[-1] == 1.0 for action in adapter.actions)


def test_move_delta_preserves_gripper_command() -> None:
    adapter = FakeAdapter()
    sdk = TidyBotSDK(adapter)  # type: ignore[arg-type]
    sdk.gripper.open(settle_steps=1)
    sdk.arm.move_delta(0.2, 0.0, 0.0)
    assert adapter.actions[-1].tolist() == [0.2, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0]


def test_sdk_emits_backend_neutral_semantic_trace() -> None:
    adapter = FakeAdapter()
    events = []
    ticks = iter((10.0, 10.1, 10.2, 10.3, 10.5, 10.6, 10.7))
    sdk = TidyBotSDK(
        adapter,  # type: ignore[arg-type]
        event_sink=events.append,
        clock=lambda: next(ticks),
    )

    sdk.sensors.get_observation()
    sdk.gripper.close(settle_steps=1)
    sdk.arm.move_delta(0.1, 0.0, 0.0)

    assert [event["operation"] for event in events] == [
        "get_observation",
        "close",
        "move_delta",
    ]
    assert [event["sequence"] for event in events] == [0, 1, 2]
    assert events[0]["result"]["agentview_image"] == {
        "shape": [2, 2, 3],
        "dtype": "uint8",
    }
    assert all(event["status"] == "completed" for event in events)


def test_sdk_trace_records_error_before_reraising() -> None:
    events = []
    sdk = TidyBotSDK(FakeAdapter(), event_sink=events.append)  # type: ignore[arg-type]

    with np.testing.assert_raises(ValueError):
        sdk.sensors.pixel_to_world(50, 50)

    assert events[-1]["operation"] == "pixel_to_world"
    assert events[-1]["status"] == "failed"
    assert events[-1]["error"]["type"] == "ValueError"
