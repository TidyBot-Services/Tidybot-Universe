"""Client-side contracts shared by all TidyBot robot environments."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class ActionResult:
    """Public result of one bounded robot action."""

    observation: dict[str, np.ndarray]
    reward: float
    done: bool
    info: dict[str, Any]


class CapabilityNotAvailableError(RuntimeError):
    """Raised when the selected backend does not implement an SDK capability."""


class RobotBackend(Protocol):
    """Client-side backend consumed by :class:`TidyBotSDK`.

    An implementation may call a simulator service over HTTP, communicate with
    hardware services over ZMQ, or run entirely in process. It is deliberately
    not named ``Service``: the implementation is the client/adapter on the SDK
    side of the process boundary, not the server process itself.
    """

    @property
    def control_frame(self) -> str: ...

    def observe(self) -> dict[str, np.ndarray]: ...

    def move_arm_delta(
        self,
        dx: float,
        dy: float,
        dz: float,
        rotation_delta: tuple[float, float, float],
    ) -> Any: ...

    def move_arm_to_position(
        self,
        x: float,
        y: float,
        z: float,
        *,
        tolerance: float,
        max_steps: int,
    ) -> None: ...

    def set_gripper(self, command: float, *, settle_steps: int) -> None: ...


@runtime_checkable
class ObjectPerceptionBackend(Protocol):
    """Optional non-core capability used by ``sensors.find_objects``."""

    def find_objects(
        self,
        target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> list[dict[str, Any]]: ...


def copy_observation(
    observation: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Return an owned copy so policy code cannot mutate backend state."""

    return {key: np.array(value, copy=True) for key, value in observation.items()}
