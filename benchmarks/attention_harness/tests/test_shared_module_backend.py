from __future__ import annotations

import pytest

from tidybot_sdk import (
    CapabilityNotAvailableError,
    ModuleRobotBackend,
    PerceptionModuleRobotBackend,
    TidyBotSDK,
)


class FakeArmModule:
    def __init__(self) -> None:
        self.calls = []

    def move_delta(self, **kwargs):
        self.calls.append(("move_delta", kwargs))

    def move_to_pose(self, **kwargs):
        self.calls.append(("move_to_pose", kwargs))


class FakeSensorModule:
    def get_arm_joints(self):
        return list(range(7))

    def get_ee_position(self):
        return (0.4, 0.0, 0.3)

    def get_gripper_width(self):
        return 0.08

    def find_objects(self, *, target_names, camera_names):
        return [{"name": target_names[0], "camera_names": camera_names}]


class FakeGripperModule:
    def __init__(self) -> None:
        self.calls = []

    def open(self):
        self.calls.append("open")

    def close(self):
        self.calls.append("close")


def make_modules():
    return FakeArmModule(), FakeSensorModule(), FakeGripperModule()


def test_existing_agent_server_modules_fit_shared_sdk() -> None:
    arm, sensors, gripper = make_modules()
    sdk = TidyBotSDK(
        ModuleRobotBackend(arm=arm, sensors=sensors, gripper=gripper)
    )

    observation = sdk.sensors.get_observation()
    assert observation["robot0_joint_pos"].tolist() == list(range(7))
    assert observation["robot0_eef_pos"].tolist() == [0.4, 0.0, 0.3]
    assert sdk.describe()["frame"] == "arm_base"
    assert "find_objects" not in sdk.describe()["sensors"]

    sdk.arm.move_delta(0.1, 0.2, 0.3, (0.4, 0.5, 0.6))
    sdk.arm.move_to_position(0.5, 0.1, 0.2)
    sdk.gripper.open()
    sdk.gripper.close()

    assert arm.calls[0] == (
        "move_delta",
        {
            "dx": 0.1,
            "dy": 0.2,
            "dz": 0.3,
            "droll": 0.4,
            "dpitch": 0.5,
            "dyaw": 0.6,
            "frame": "base",
        },
    )
    assert arm.calls[1] == (
        "move_to_pose",
        {"x": 0.5, "y": 0.1, "z": 0.2},
    )
    assert gripper.calls == ["open", "close"]

    with pytest.raises(CapabilityNotAvailableError, match="find_objects"):
        sdk.sensors.find_objects()


def test_perception_module_backend_enables_find_objects_explicitly() -> None:
    arm, sensors, gripper = make_modules()
    sdk = TidyBotSDK(
        PerceptionModuleRobotBackend(
            arm=arm,
            sensors=sensors,
            gripper=gripper,
            control_frame="robocasa_world",
        )
    )

    assert "find_objects" in sdk.describe()["sensors"]
    assert sdk.sensors.find_objects(["mug_0"], ["base_camera"]) == [
        {"name": "mug_0", "camera_names": ["base_camera"]}
    ]
