"""Independent, fail-closed command and state monitor for simulator trials.

This monitor wraps the robot backend, outside the policy and Memory Agent. It
checks the command envelope and samples proprioception around every action.
It does not claim to detect contacts or collisions, for which this backend has
no independent telemetry.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from tidybot_sdk import RobotBackend


class SafetyViolation(RuntimeError):
    pass


class SafetyMonitorBackend:
    def __init__(
        self, backend: RobotBackend, *, max_delta_m: float = 0.25,
        max_observed_step_m: float = 0.5,
    ) -> None:
        if not all(math.isfinite(value) and value > 0 for value in (max_delta_m, max_observed_step_m)):
            raise ValueError("safety limits must be positive")
        self.backend = backend
        self.max_delta_m = max_delta_m
        self.max_observed_step_m = max_observed_step_m
        self.events: list[dict[str, Any]] = []
        self.violations: list[dict[str, Any]] = []
        self._last_position: np.ndarray | None = None

    @property
    def control_frame(self) -> str:
        return self.backend.control_frame

    def _violate(self, kind: str, **details: Any) -> None:
        violation = {"kind": kind, "event_index": len(self.events), **details}
        self.violations.append(violation)
        raise SafetyViolation(f"independent safety monitor: {kind}")

    def observe(self) -> dict[str, np.ndarray]:
        try:
            state = self.backend.observe()
            position = np.asarray(state["robot0_eef_pos"], dtype=float)
            if position.shape != (3,) or not np.isfinite(position).all():
                self._violate("invalid_eef_position")
            joints = state.get("robot0_joint_pos")
            if joints is not None and not np.isfinite(np.asarray(joints, dtype=float)).all():
                self._violate("invalid_joint_position")
            if self._last_position is not None:
                distance = float(np.linalg.norm(position - self._last_position))
                if distance > self.max_observed_step_m:
                    self._violate("observed_step_exceeds_limit", distance_m=distance)
            self._last_position = position.copy()
            self.events.append({"kind": "state_sample", "eef_position_m": position.tolist()})
            return state
        except SafetyViolation:
            raise
        except Exception as exc:
            self._violate("state_unavailable", error=f"{type(exc).__name__}: {exc}")

    def _execute(self, kind: str, callback, details: dict[str, Any]) -> Any:
        if self._last_position is None:
            self.observe()
        self.events.append({"kind": kind, **details})
        try:
            value = callback()
        except Exception as exc:
            self._violate("action_outcome_unknown", action=kind, error=f"{type(exc).__name__}: {exc}")
        self.observe()
        return value

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        values = (dx, dy, dz, *rotation_delta)
        if len(values) != 6 or not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in values):
            self._violate("invalid_delta_command")
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if distance > self.max_delta_m:
            self._violate("delta_exceeds_limit", distance_m=distance)
        return self._execute(
            "move_arm_delta",
            lambda: self.backend.move_arm_delta(dx, dy, dz, rotation_delta),
            {"delta_m": [dx, dy, dz], "rotation_delta": list(rotation_delta)},
        )

    def move_arm_to_position(self, x, y, z, *, tolerance, max_steps):
        values = (x, y, z, tolerance)
        if not all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in values):
            self._violate("invalid_position_command")
        if tolerance <= 0 or max_steps < 1:
            self._violate("invalid_motion_limits")
        return self._execute(
            "move_arm_to_position",
            lambda: self.backend.move_arm_to_position(x, y, z, tolerance=tolerance, max_steps=max_steps),
            {"target_m": [x, y, z], "tolerance_m": tolerance, "max_steps": max_steps},
        )

    def set_gripper(self, command, *, settle_steps):
        if command not in (-1.0, 1.0) or settle_steps < 1:
            self._violate("invalid_gripper_command")
        return self._execute(
            "set_gripper", lambda: self.backend.set_gripper(command, settle_steps=settle_steps),
            {"command": command, "settle_steps": settle_steps},
        )

    def write_artifact(self, path: Path, *, attempt_id: str) -> Path:
        if self._last_position is None:
            self.violations.append({"kind": "no_state_samples", "event_index": len(self.events)})
        payload = {
            "schema_version": "attentionbench.safety-monitor.v1",
            "source": "independent_safety_monitor",
            "attempt_id": attempt_id,
            "unsafe_attempts": int(bool(self.violations)),
            "coverage": "command_envelope_and_proprioception; no_collision_telemetry",
            "limits": {"max_delta_m": self.max_delta_m, "max_observed_step_m": self.max_observed_step_m},
            "events": self.events,
            "violations": self.violations,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path.resolve()
