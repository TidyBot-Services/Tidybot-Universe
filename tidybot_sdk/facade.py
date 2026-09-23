"""Backend-neutral implementation of the shared TidyBot SDK facade."""

from __future__ import annotations

from typing import Any

import numpy as np

from .contracts import (
    CapabilityNotAvailableError,
    ObjectPerceptionBackend,
    RobotBackend,
)


class Sensors:
    def __init__(self, backend: RobotBackend) -> None:
        self._backend = backend

    def get_observation(self) -> dict[str, np.ndarray]:
        """Return public camera and robot state, never object oracle state."""

        return self._backend.observe()

    def find_objects(
        self,
        target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Use an explicitly provided perception capability when available.

        The shared SDK does not synthesize this from simulator state. RoboCasa
        may delegate to its existing perception service; Robosuite must provide
        a future RGB-D perception backend before this method becomes available.
        """

        if not isinstance(self._backend, ObjectPerceptionBackend):
            raise CapabilityNotAvailableError(
                "find_objects is not available on the selected robot backend"
            )
        return self._backend.find_objects(target_names, camera_names)

    def pixel_to_world(
        self,
        u: float,
        v: float,
        *,
        camera: str = "agentview",
        depth_meters: float | None = None,
    ) -> tuple[float, float, float]:
        """Deproject an RGB-D pixel into the backend's control frame.

        The calculation consumes only public metric depth and camera
        calibration. It does not read simulator segmentation, object poses, or
        evaluator state and is therefore portable to calibrated hardware.
        """

        observation = self._backend.observe()
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
    def __init__(self, backend: RobotBackend) -> None:
        self._backend = backend

    def move_delta(
        self,
        dx: float,
        dy: float,
        dz: float,
        rotation_delta: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> Any:
        return self._backend.move_arm_delta(dx, dy, dz, rotation_delta)

    def move_to_position(
        self,
        x: float,
        y: float,
        z: float,
        *,
        tolerance: float = 0.004,
        max_steps: int = 100,
    ) -> None:
        """Move in the backend control frame using its local controller."""

        self._backend.move_arm_to_position(
            x,
            y,
            z,
            tolerance=tolerance,
            max_steps=max_steps,
        )


class Gripper:
    def __init__(self, backend: RobotBackend) -> None:
        self._backend = backend

    def open(self, *, settle_steps: int = 10) -> None:
        self._backend.set_gripper(-1.0, settle_steps=settle_steps)

    def close(self, *, settle_steps: int = 10) -> None:
        self._backend.set_gripper(1.0, settle_steps=settle_steps)


class TidyBotSDK:
    """Shared SDK facade; environment-specific behavior lives in its backend."""

    def __init__(self, backend: RobotBackend) -> None:
        self._backend = backend
        self.sensors = Sensors(backend)
        self.arm = Arm(backend)
        self.gripper = Gripper(backend)

    def describe(self) -> dict[str, Any]:
        sensor_methods = ["get_observation", "pixel_to_world"]
        if isinstance(self._backend, ObjectPerceptionBackend):
            sensor_methods.append("find_objects")
        return {
            "frame": self._backend.control_frame,
            "arm": ("move_delta", "move_to_position"),
            "gripper": ("open", "close"),
            "sensors": tuple(sensor_methods),
        }
