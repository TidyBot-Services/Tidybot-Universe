"""TidyBot-owned SDK boundary backed by Robosuite's local OSC controller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from .robosuite_adapter import RobosuiteAdapter, StepResult


@dataclass
class _ControlState:
    gripper: float = -1.0


class Sensors:
    def __init__(self, adapter: RobosuiteAdapter) -> None:
        self._adapter = adapter

    def get_observation(self) -> dict[str, np.ndarray]:
        """Return camera and robot proprioception, never object oracle state."""
        return self._adapter.observe()


class Arm:
    def __init__(self, adapter: RobosuiteAdapter, state: _ControlState) -> None:
        self._adapter = adapter
        self._state = state

    def move_delta(
        self,
        dx: float,
        dy: float,
        dz: float,
        rotation_delta: tuple[float, float, float] = (0.0, 0.0, 0.0),
    ) -> StepResult:
        action = np.zeros(self._adapter.action_shape, dtype=np.float64)
        action[:3] = (dx, dy, dz)
        action[3:6] = rotation_delta
        action[-1] = self._state.gripper
        return self._adapter.step(action)

    def move_to_position(
        self,
        x: float,
        y: float,
        z: float,
        *,
        tolerance: float = 0.004,
        max_steps: int = 100,
    ) -> None:
        """Move in Robosuite world coordinates using only the local OSC loop."""
        target = np.asarray((x, y, z), dtype=np.float64)
        for _ in range(max_steps):
            observation = self._adapter.observe()
            current = np.asarray(observation["robot0_eef_pos"], dtype=np.float64)
            error = target - current
            if np.linalg.norm(error) < tolerance:
                return
            action = np.zeros(self._adapter.action_shape, dtype=np.float64)
            action[:3] = np.clip(error / 0.05, -1.0, 1.0)
            action[-1] = self._state.gripper
            self._adapter.step(action)
        raise TimeoutError(f"arm did not reach target within {max_steps} steps")


class Gripper:
    def __init__(self, adapter: RobosuiteAdapter, state: _ControlState) -> None:
        self._adapter = adapter
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
            action = np.zeros(self._adapter.action_shape, dtype=np.float64)
            action[-1] = command
            self._adapter.step(action)


class NativeRobotSDK:
    """Stable public facade for code executed by the future model runner."""

    def __init__(self, adapter: RobosuiteAdapter) -> None:
        state = _ControlState()
        self.sensors = Sensors(adapter)
        self.arm = Arm(adapter, state)
        self.gripper = Gripper(adapter, state)

    def describe(self) -> dict[str, Any]:
        return {
            "frame": "robosuite_world",
            "arm": ("move_delta", "move_to_position"),
            "gripper": ("open", "close"),
            "sensors": ("get_observation",),
        }
