"""Independent parent monitor; normalized composite limits and public proprioception."""

import math
import numpy as np
from ..robocasa_native.safety_monitor import SafetyMonitorBackend


class Safety(SafetyMonitorBackend):
    def __init__(self, backend, limits, interrupt_check=None):
        super().__init__(
            backend,
            max_delta_m=limits["max_arm_delta_m"],
            max_observed_step_m=limits["max_observed_eef_step_m"],
            interrupt_check=interrupt_check,
        )
        self.limits = limits
        self.last_base = None
        self.cap = backend.cap

    def observe(self):
        obs = super().observe()
        base = obs.get("robot0_base_pos")
        if base is not None:
            base = np.asarray(base, dtype=float)
            if base.shape != (3,) or not np.isfinite(base).all():
                self._violate("invalid_base_state")
            if (
                self.last_base is not None
                and np.linalg.norm(base - self.last_base)
                > self.limits["max_observed_base_step_m"]
            ):
                self._violate("observed_base_step_exceeds_limit")
            self.last_base = base.copy()
        return obs

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        if len(rotation_delta) != 3 or any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            or abs(v) > self.limits["max_arm_rotation_rad"]
            for v in rotation_delta
        ):
            self._violate("rotation_command_outside_envelope")
        return super().move_arm_delta(dx, dy, dz, rotation_delta)

    def set_gripper(self, command, *, settle_steps):
        if (
            type(settle_steps) is not int
            or not 1 <= settle_steps <= 10
            or isinstance(command, bool)
        ):
            self._violate("invalid_gripper_settle_steps")
        return super().set_gripper(command, settle_steps=settle_steps)

    def normalized(self, part, values):
        if part not in {"base", "torso"}:
            self._violate("unknown_part")
        indexes = self.backend.cap["part_indexes"][part]
        dim = indexes[1] - indexes[0]
        if (
            not isinstance(values, (list, tuple))
            or len(values) != dim
            or any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or abs(v) > self.limits["max_normalized_command"]
                for v in values
            )
        ):
            self._violate("invalid_normalized_composite_command", part=part)
        return self._execute(
            "normalized_" + part,
            lambda: self.backend.normalized(part, values),
            {
                "values": values,
                "frame": "current_base" if part == "base" else "torso_joint",
            },
        )

    def find_objects(self, *args, **kwargs):
        return self.backend.find_objects(*args, **kwargs)
