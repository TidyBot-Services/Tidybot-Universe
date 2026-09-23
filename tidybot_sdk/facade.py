"""Backend-neutral implementation of the shared TidyBot SDK facade."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from .contracts import (
    CapabilityNotAvailableError,
    ObjectPerceptionBackend,
    RobotBackend,
)


class Sensors:
    def __init__(
        self, backend: RobotBackend, emitter: _SDKTraceEmitter | None = None
    ) -> None:
        self._backend = backend
        self._emitter = emitter

    def get_observation(self) -> dict[str, np.ndarray]:
        """Return public camera and robot state, never object oracle state."""

        return _trace_call(
            self._emitter,
            source="robot_sdk.sensors",
            event_type="sdk.sensor_read",
            operation="get_observation",
            arguments={},
            callback=self._backend.observe,
        )

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

        def execute() -> list[dict[str, Any]]:
            if not isinstance(self._backend, ObjectPerceptionBackend):
                raise CapabilityNotAvailableError(
                    "find_objects is not available on the selected robot backend"
                )
            return self._backend.find_objects(target_names, camera_names)

        return _trace_call(
            self._emitter,
            source="robot_sdk.sensors",
            event_type="sdk.perception",
            operation="find_objects",
            arguments={"target_names": target_names, "camera_names": camera_names},
            callback=execute,
        )

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

        return _trace_call(
            self._emitter,
            source="robot_sdk.sensors",
            event_type="sdk.frame_transform",
            operation="pixel_to_world",
            arguments={
                "u": u,
                "v": v,
                "camera": camera,
                "depth_meters": depth_meters,
            },
            callback=lambda: self._pixel_to_world(
                u,
                v,
                camera=camera,
                depth_meters=depth_meters,
            ),
        )

    def _pixel_to_world(
        self,
        u: float,
        v: float,
        *,
        camera: str,
        depth_meters: float | None,
    ) -> tuple[float, float, float]:
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
    def __init__(
        self, backend: RobotBackend, emitter: _SDKTraceEmitter | None = None
    ) -> None:
        self._backend = backend
        self._emitter = emitter

    def move_delta(
        self,
        dx: float,
        dy: float,
        dz: float,
        rotation_delta: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> Any:
        return _trace_call(
            self._emitter,
            source="robot_sdk.arm",
            event_type="sdk.arm_command",
            operation="move_delta",
            arguments={
                "dx": dx,
                "dy": dy,
                "dz": dz,
                "rotation_delta": rotation_delta,
            },
            callback=lambda: self._backend.move_arm_delta(dx, dy, dz, rotation_delta),
        )

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

        _trace_call(
            self._emitter,
            source="robot_sdk.arm",
            event_type="sdk.arm_command",
            operation="move_to_position",
            arguments={
                "x": x,
                "y": y,
                "z": z,
                "tolerance": tolerance,
                "max_steps": max_steps,
            },
            callback=lambda: self._backend.move_arm_to_position(
                x,
                y,
                z,
                tolerance=tolerance,
                max_steps=max_steps,
            ),
        )


class Gripper:
    def __init__(
        self, backend: RobotBackend, emitter: _SDKTraceEmitter | None = None
    ) -> None:
        self._backend = backend
        self._emitter = emitter

    def open(self, *, settle_steps: int = 10) -> None:
        _trace_call(
            self._emitter,
            source="robot_sdk.gripper",
            event_type="sdk.gripper_command",
            operation="open",
            arguments={"settle_steps": settle_steps},
            callback=lambda: self._backend.set_gripper(
                -1.0, settle_steps=settle_steps
            ),
        )

    def close(self, *, settle_steps: int = 10) -> None:
        _trace_call(
            self._emitter,
            source="robot_sdk.gripper",
            event_type="sdk.gripper_command",
            operation="close",
            arguments={"settle_steps": settle_steps},
            callback=lambda: self._backend.set_gripper(
                1.0, settle_steps=settle_steps
            ),
        )


class TidyBotSDK:
    """Shared SDK facade; environment-specific behavior lives in its backend."""

    def __init__(
        self,
        backend: RobotBackend,
        *,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        emitter = _SDKTraceEmitter(event_sink, clock=clock) if event_sink else None
        self.sensors = Sensors(backend, emitter)
        self.arm = Arm(backend, emitter)
        self.gripper = Gripper(backend, emitter)

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


class _SDKTraceEmitter:
    """Small dependency-free hook used by every service backend."""

    def __init__(
        self,
        sink: Callable[[dict[str, Any]], None],
        *,
        clock: Callable[[], float],
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._started_at = clock()
        self._sequence = 0

    def call(
        self,
        *,
        source: str,
        event_type: str,
        operation: str,
        arguments: Mapping[str, Any],
        callback: Callable[[], Any],
    ) -> Any:
        started = self._clock()
        try:
            result = callback()
        except BaseException as exc:
            self._emit(
                source=source,
                event_type=event_type,
                operation=operation,
                status="failed",
                started=started,
                arguments=arguments,
                result={},
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise
        self._emit(
            source=source,
            event_type=event_type,
            operation=operation,
            status="completed",
            started=started,
            arguments=arguments,
            result=_summarize(result),
            error=None,
        )
        return result

    def _emit(
        self,
        *,
        source: str,
        event_type: str,
        operation: str,
        status: str,
        started: float,
        arguments: Mapping[str, Any],
        result: Any,
        error: dict[str, str] | None,
    ) -> None:
        finished = self._clock()
        sequence = self._sequence
        self._sequence += 1
        self._sink(
            {
                "schema_version": "attentionbench.sdk-event.v1",
                "event_id": f"sdk-{sequence}",
                "sequence": sequence,
                "timestamp": round(finished - self._started_at, 6),
                "source": source,
                "event_type": event_type,
                "operation": operation,
                "status": status,
                "duration_ms": round(max(0.0, finished - started) * 1000.0, 3),
                "arguments": _summarize(dict(arguments)),
                "result": result,
                "error": error,
            }
        )


def _trace_call(
    emitter: _SDKTraceEmitter | None,
    *,
    source: str,
    event_type: str,
    operation: str,
    arguments: Mapping[str, Any],
    callback: Callable[[], Any],
) -> Any:
    if emitter is None:
        return callback()
    return emitter.call(
        source=source,
        event_type=event_type,
        operation=operation,
        arguments=arguments,
        callback=callback,
    )


def _summarize(value: Any, *, depth: int = 0) -> Any:
    """Bound trace size while retaining shapes and simple public values."""

    if depth > 3:
        return {"type": type(value).__name__}
    if isinstance(value, np.ndarray):
        return {"shape": list(value.shape), "dtype": str(value.dtype)}
    if isinstance(value, Mapping):
        return {
            str(key): _summarize(nested, depth=depth + 1)
            for key, nested in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (tuple, list)):
        if len(value) > 32:
            return {"type": type(value).__name__, "count": len(value)}
        return [_summarize(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"type": type(value).__name__}
