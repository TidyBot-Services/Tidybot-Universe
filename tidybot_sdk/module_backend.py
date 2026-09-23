"""Adapter for the existing agent_server arm / sensors / gripper modules."""

from __future__ import annotations

from typing import Any

import numpy as np


class ModuleRobotBackend:
    """Expose existing TidyBot module objects through the shared backend API.

    This adapter deliberately uses duck typing so the shared SDK does not import
    agent_server, a hardware driver, or a simulator. Existing RoboCasa and real
    deployments can inject their current ``ArmAPI``, ``SensorAPI``, and
    ``GripperAPI`` instances without moving those services into this package.
    """

    def __init__(
        self,
        *,
        arm: Any,
        sensors: Any,
        gripper: Any,
        control_frame: str = "arm_base",
    ) -> None:
        self._arm = arm
        self._sensors = sensors
        self._gripper = gripper
        self._control_frame = control_frame

    @property
    def control_frame(self) -> str:
        return self._control_frame

    def observe(self) -> dict[str, np.ndarray]:
        get_observation = getattr(self._sensors, "get_observation", None)
        if callable(get_observation):
            return {
                key: np.array(value, copy=True)
                for key, value in get_observation().items()
            }

        observation = {
            "robot0_joint_pos": np.asarray(
                self._sensors.get_arm_joints(), dtype=np.float64
            ),
            "robot0_eef_pos": np.asarray(
                self._sensors.get_ee_position(), dtype=np.float64
            ),
        }
        get_width = getattr(self._sensors, "get_gripper_width", None)
        if callable(get_width):
            width = get_width()
            if width is not None:
                observation["robot0_gripper_width"] = np.asarray(
                    [width], dtype=np.float64
                )
        return observation

    def move_arm_delta(
        self,
        dx: float,
        dy: float,
        dz: float,
        rotation_delta: tuple[float, float, float],
    ) -> Any:
        droll, dpitch, dyaw = rotation_delta
        return self._arm.move_delta(
            dx=dx,
            dy=dy,
            dz=dz,
            droll=droll,
            dpitch=dpitch,
            dyaw=dyaw,
            frame="base",
        )

    def move_arm_to_position(
        self,
        x: float,
        y: float,
        z: float,
        *,
        tolerance: float,
        max_steps: int,
    ) -> None:
        # Existing ArmAPI is blocking and owns its convergence criteria. The
        # shared arguments remain part of the portable call shape even though
        # this adapter cannot override those legacy controller settings yet.
        del tolerance, max_steps
        self._arm.move_to_pose(x=x, y=y, z=z)

    def set_gripper(self, command: float, *, settle_steps: int) -> None:
        if command not in (-1.0, 1.0):
            raise ValueError("gripper command must be -1.0 (open) or 1.0 (close)")
        if settle_steps < 1:
            raise ValueError("settle_steps must be positive")
        if command < 0.0:
            self._gripper.open()
        else:
            self._gripper.close()


class PerceptionModuleRobotBackend(ModuleRobotBackend):
    """Module adapter that explicitly enables ``sensors.find_objects``."""

    def find_objects(
        self,
        target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        find_objects = getattr(self._sensors, "find_objects", None)
        if not callable(find_objects):
            raise RuntimeError("injected sensor module has no find_objects capability")
        return list(
            find_objects(target_names=target_names, camera_names=camera_names)
        )
