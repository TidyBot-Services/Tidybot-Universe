"""Additive RoboCasa v2 base capability; the frozen shared SDK is unchanged."""

from __future__ import annotations

from typing import Any

from tidybot_sdk import Arm, CapabilityNotAvailableError, TidyBotSDK


class _RobocasaArmFacade(Arm):
    def plan_to_position(self, x: float, y: float, z: float) -> dict[str, Any]:
        """Public read-only reachability query for the current simulator pose."""
        callback = getattr(self._backend, "plan_arm_to_position", None)
        if not callable(callback):
            raise CapabilityNotAvailableError("arm planning is unavailable")
        arguments = {"x": x, "y": y, "z": z}
        if self._emitter is None:
            return callback(x, y, z)
        return self._emitter.call(
            source="robot_sdk.arm", event_type="sdk.arm_plan_query",
            operation="plan_to_position", arguments=arguments,
            callback=lambda: callback(x, y, z),
        )


class _BaseFacade:
    def __init__(self, action_backend: Any, emitter: Any) -> None:
        self._action_backend = action_backend
        self._emitter = emitter

    def move_delta(
        self, dx: float, dy: float, dtheta: float = 0.0, *, frame: str = "local"
    ) -> Any:
        if frame not in {"local", "global"}:
            raise ValueError("base frame must be local or global")
        callback = getattr(self._action_backend, "move_base_delta", None)
        if not callable(callback):
            raise CapabilityNotAvailableError("base motion is unavailable")
        arguments = {"dx": dx, "dy": dy, "dtheta": dtheta, "frame": frame}
        if self._emitter is None:
            return callback(dx, dy, dtheta, frame=frame)
        return self._emitter.call(
            source="robot_sdk.base",
            event_type="sdk.base_command",
            operation="move_delta",
            arguments=arguments,
            callback=lambda: callback(dx, dy, dtheta, frame=frame),
        )


class RobocasaMobileSDK(TidyBotSDK):
    """One extra SDK module for mobile-base RoboCasa tasks only."""

    def __init__(self, backend: Any, *, action_backend: Any, event_sink: Any) -> None:
        super().__init__(backend, event_sink=event_sink)
        # Reuse the frozen facade's emitter so IDs and order remain unique
        # across arm, gripper, sensors and the additive base command.
        self.arm = _RobocasaArmFacade(action_backend, self.arm._emitter)
        self.base = _BaseFacade(action_backend, self.arm._emitter)

    def describe(self) -> dict[str, Any]:
        value = super().describe()
        if callable(getattr(self.base._action_backend, "move_base_delta", None)):
            value["base"] = ("move_delta",)
        if callable(getattr(self.arm._backend, "plan_arm_to_position", None)):
            value["arm"] = (*value["arm"], "plan_to_position")
        return value
