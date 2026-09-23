"""Backend-neutral contract consumed by the public TidyBot robot SDK.

Simulator and hardware adapters may implement this protocol.  The SDK must not
know which concrete service is behind it; it only consumes public observations
and bounded robot actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

import numpy as np


@dataclass(frozen=True)
class ServiceStep:
    observation: dict[str, np.ndarray]
    reward: float
    done: bool
    info: dict[str, Any]


class RobotService(Protocol):
    """Minimum service surface required by ``NativeRobotSDK``."""

    @property
    def control_frame(self) -> str: ...

    @property
    def action_shape(self) -> tuple[int, ...]: ...

    def observe(self) -> dict[str, np.ndarray]: ...

    def step(self, action: np.ndarray | list[float]) -> ServiceStep: ...


def copy_observation(
    observation: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Return an owned copy so policy code cannot mutate adapter state."""

    return {key: np.array(value, copy=True) for key, value in observation.items()}
