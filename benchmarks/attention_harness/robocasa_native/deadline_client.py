"""V2-only deadline wrapper around the frozen RoboCasa simulator client."""

from __future__ import annotations

import math
import time
from typing import Any

from .client import RobocasaSimClient


class DeadlineRobocasaSimClient(RobocasaSimClient):
    def __init__(self, source: RobocasaSimClient, *, deadline: float) -> None:
        if not math.isfinite(deadline):
            raise ValueError("episode deadline must be finite")
        super().__init__(source.spec.task_id, base_url=source.base_url,
                         transport=source._transport)
        self.deadline = deadline

    def _call(
        self, method: str, path: str, payload: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("RoboCasa episode deadline reached before simulator request")
        return super()._call(method, path, payload, timeout=min(timeout, remaining))
