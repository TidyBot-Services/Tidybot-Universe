"""Explicitly privileged infrastructure probe; never imported by policy code."""

from __future__ import annotations

from typing import Any

from .client import JsonTransport, RobocasaServiceError
from .tasks import get_robocasa_task


class PrivilegedRobocasaProbe:
    """Exercise native predicates by placing the target inside its destination."""

    def __init__(
        self,
        task_id: str,
        *,
        base_url: str,
        transport: JsonTransport,
    ) -> None:
        self.spec = get_robocasa_task(task_id)
        self.base_url = base_url.rstrip("/")
        self._transport = transport

    def force_reference_success(self) -> None:
        evaluation = self._transport(
            "GET", self.base_url + "/task/success", None, 30.0
        )
        debug = evaluation.get("debug")
        if not isinstance(debug, dict):
            raise RobocasaServiceError("privileged evaluator debug is unavailable")
        destination = debug.get(self.spec.destination_debug_key)
        if not isinstance(destination, dict):
            raise RobocasaServiceError("destination debug record is unavailable")
        center = destination.get("shifted") or destination.get("pos")
        if not isinstance(center, list) or len(center) != 3:
            raise RobocasaServiceError("destination center is invalid")
        target = [float(value) for value in center]
        position = destination.get("pos")
        if isinstance(position, list) and len(position) == 3:
            target[2] = float(position[2])
        result = self._transport(
            "POST",
            self.base_url + "/teleport",
            {"object": "obj", "position": target},
            30.0,
        )
        if result.get("status") != "ok":
            raise RobocasaServiceError(f"privileged reference probe failed: {result}")
