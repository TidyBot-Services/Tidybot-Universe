"""Explicit, mode-bound object perception for the v2 shared SDK.

This additive module leaves the frozen v1 SDK implementation untouched.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Protocol

from .contracts import RobotBackend


class PerceptionMode(str, Enum):
    SIM_GT = "sim_gt"
    VISION = "vision"


class ObjectProvider(Protocol):
    def find_objects(
        self,
        target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...


class ModeBoundPerceptionBackend:
    """Add one immutable perception provider to an existing action backend.

    No automatic fallback exists. The selected provider must already return
    positions in the wrapped backend's control frame; unexpected payload
    fields are stripped before they reach policy code.
    """

    def __init__(
        self,
        action_backend: RobotBackend,
        *,
        mode: PerceptionMode | str,
        target: str,
        provider: ObjectProvider,
    ) -> None:
        self._mode = PerceptionMode(mode)
        if target not in {"robocasa_sim", "robosuite_sim", "real_robot"}:
            raise ValueError(f"unsupported execution target: {target}")
        if target == "real_robot" and self._mode is PerceptionMode.SIM_GT:
            raise ValueError("sim_gt perception is forbidden on a real robot")
        self._target = target
        self._action_backend = action_backend
        self._provider = provider

    @property
    def perception_mode(self) -> PerceptionMode:
        return self._mode

    @property
    def execution_target(self) -> str:
        return self._target

    @property
    def control_frame(self) -> str:
        return self._action_backend.control_frame

    def observe(self):
        return self._action_backend.observe()

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        return self._action_backend.move_arm_delta(dx, dy, dz, rotation_delta)

    def move_arm_to_position(self, x, y, z, *, tolerance, max_steps):
        return self._action_backend.move_arm_to_position(
            x, y, z, tolerance=tolerance, max_steps=max_steps
        )

    def set_gripper(self, command, *, settle_steps):
        return self._action_backend.set_gripper(command, settle_steps=settle_steps)

    def find_objects(
        self,
        target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        rows = self._provider.find_objects(
            target_names=target_names, camera_names=camera_names
        )
        if not isinstance(rows, list):
            raise TypeError("perception provider must return a list")
        output = []
        for row in rows:
            if not isinstance(row, dict):
                raise TypeError("perception object must be a mapping")
            if row.get("source") != self._mode.value:
                raise ValueError("perception source does not match the selected mode")
            if row.get("frame") != self.control_frame:
                raise ValueError("perception position is not in the SDK control frame")
            name = row.get("name")
            position = row.get("position")
            confidence = row.get("confidence")
            if not isinstance(name, str) or not name:
                raise ValueError("perception object needs a nonempty name")
            if not isinstance(position, (list, tuple)) or len(position) != 3:
                raise ValueError("perception object needs a 3D position")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in position
            ):
                raise ValueError("perception position must contain finite numbers")
            if (
                isinstance(confidence, bool)
                or not isinstance(confidence, (int, float))
                or not math.isfinite(confidence)
                or not 0.0 <= confidence <= 1.0
            ):
                raise ValueError("perception confidence must be in [0, 1]")
            output.append(
                {
                    "name": name,
                    "position": [float(value) for value in position],
                    "confidence": float(confidence),
                    "source": self._mode.value,
                    "frame": self.control_frame,
                }
            )
        return output
