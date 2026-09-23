"""Backend-neutral TidyBot SDK boundary backed by a Robot Service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .service_contract import RobotService, ServiceStep


@dataclass
class _ControlState:
    gripper: float = -1.0


class Sensors:
    def __init__(self, service: RobotService) -> None:
        self._service = service

    def get_observation(self) -> dict[str, np.ndarray]:
        """Return camera and robot proprioception, never object oracle state."""
        return self._service.observe()

    def pixel_to_world(
        self,
        u: float,
        v: float,
        *,
        camera: str = "agentview",
        depth_meters: float | None = None,
    ) -> tuple[float, float, float]:
        """Deproject one RGB-D pixel into the service's robot control frame.

        This helper consumes only public depth and camera calibration. It never
        reads simulator segmentation, object names, object poses, or evaluator
        state, so the same operation can be implemented by another simulator or
        a calibrated physical camera service.
        """

        observation = self._service.observe()
        depth_key = f"{camera}_depth"
        intrinsics_key = f"{camera}_intrinsics"
        pose_key = f"{camera}_pose_mat"
        missing = [
            key
            for key in (depth_key, intrinsics_key, pose_key)
            if key not in observation
        ]
        if missing:
            raise RuntimeError(
                f"camera {camera!r} does not provide RGB-D calibration: {missing}"
            )

        depth = np.asarray(observation[depth_key], dtype=np.float64)
        if depth.ndim == 3 and depth.shape[-1] == 1:
            depth = depth[..., 0]
        if depth.ndim != 2:
            raise RuntimeError(f"{depth_key} must have shape (H, W) or (H, W, 1)")

        column = int(round(float(u)))
        row = int(round(float(v)))
        height, width = depth.shape
        if not (0 <= column < width and 0 <= row < height):
            raise ValueError(
                f"pixel ({u}, {v}) is outside camera image {width}x{height}"
            )
        z = float(depth[row, column] if depth_meters is None else depth_meters)
        if not np.isfinite(z) or z <= 0.0:
            raise ValueError(f"pixel ({u}, {v}) has invalid metric depth {z!r}")

        intrinsics = np.asarray(observation[intrinsics_key], dtype=np.float64)
        camera_to_control = np.asarray(observation[pose_key], dtype=np.float64)
        if intrinsics.shape != (3, 3):
            raise RuntimeError(f"{intrinsics_key} must have shape (3, 3)")
        if camera_to_control.shape != (4, 4):
            raise RuntimeError(f"{pose_key} must have shape (4, 4)")

        fx, fy = float(intrinsics[0, 0]), float(intrinsics[1, 1])
        cx, cy = float(intrinsics[0, 2]), float(intrinsics[1, 2])
        if fx <= 0.0 or fy <= 0.0:
            raise RuntimeError("camera focal lengths must be positive")
        point_camera = np.array(
            [((float(u) - cx) * z / fx), ((float(v) - cy) * z / fy), z, 1.0],
            dtype=np.float64,
        )
        point_control = camera_to_control @ point_camera
        return tuple(float(value) for value in point_control[:3])


class Arm:
    def __init__(self, service: RobotService, state: _ControlState) -> None:
        self._service = service
        self._state = state

    def move_delta(
        self,
        dx: float,
        dy: float,
        dz: float,
        rotation_delta: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> ServiceStep:
        action = np.zeros(self._service.action_shape, dtype=np.float64)
        action[:3] = (dx, dy, dz)
        action[3:6] = rotation_delta
        action[-1] = self._state.gripper
        return self._service.step(action)

    def move_to_position(
        self,
        x: float,
        y: float,
        z: float,
        *,
        tolerance: float = 0.004,
        max_steps: int = 100,
    ) -> None:
        """Move in the active service's control frame using its local controller."""
        target = np.asarray((x, y, z), dtype=np.float64)
        for _ in range(max_steps):
            observation = self._service.observe()
            current = np.asarray(observation["robot0_eef_pos"], dtype=np.float64)
            error = target - current
            if np.linalg.norm(error) < tolerance:
                return
            action = np.zeros(self._service.action_shape, dtype=np.float64)
            action[:3] = np.clip(error / 0.05, -1.0, 1.0)
            action[-1] = self._state.gripper
            self._service.step(action)
        raise TimeoutError(f"arm did not reach target within {max_steps} steps")


class Gripper:
    def __init__(self, service: RobotService, state: _ControlState) -> None:
        self._service = service
        self._state = state

    def open(self, *, settle_steps: int = 10) -> None:
        self._apply(-1.0, settle_steps)

    def close(self, *, settle_steps: int = 10) -> None:
        self._apply(1.0, settle_steps)

    def _apply(self, command: float, settle_steps: int) -> None:
        if settle_steps < 1:
            raise ValueError("settle_steps must be positive")
        self._state.gripper = command
        for _ in range(settle_steps):
            action = np.zeros(self._service.action_shape, dtype=np.float64)
            action[-1] = command
            self._service.step(action)


class NativeRobotSDK:
    """Stable public facade for sandboxed policy code."""

    def __init__(self, service: RobotService) -> None:
        self._service = service
        state = _ControlState()
        self.sensors = Sensors(service)
        self.arm = Arm(service, state)
        self.gripper = Gripper(service, state)

    def describe(self) -> dict[str, Any]:
        return {
            "frame": self._service.control_frame,
            "arm": ("move_delta", "move_to_position"),
            "gripper": ("open", "close"),
            "sensors": ("get_observation", "pixel_to_world"),
        }
