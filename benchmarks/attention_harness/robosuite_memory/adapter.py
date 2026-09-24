"""V2-only extension of the frozen Robosuite adapter."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from tidybot_sdk import copy_observation

from ..robosuite_adapter import RobosuiteRobotBackend
from .client import RobosuiteSimGTClient


class RobosuiteSimGTBackend(RobosuiteRobotBackend):
    def __init__(self, task_id: str, *, camera_name: str = "agentview", **kwargs: Any) -> None:
        if not camera_name:
            raise ValueError("camera_name must be nonempty")
        service_url = kwargs.get("service_url")
        if kwargs.get("client") is None:
            import os
            kwargs["client"] = RobosuiteSimGTClient(
                service_url or os.environ.get("TIDYBOT_ROBOSUITE_URL", "http://127.0.0.1:8082")
            )
        super().__init__(task_id, **kwargs)
        self.camera_name = camera_name

    def reset_attested(
        self, seed: int, *, variation: dict[str, str] | None = None,
    ) -> tuple[dict[str, np.ndarray], dict[str, str]]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed must be an integer")
        observation, low, high, metadata, applied = self._client.reset_attested(
            task_id=self.spec.task_id, seed=seed, horizon=self.horizon,
            camera=self.camera, camera_name=self.camera_name,
            camera_height=self.camera_height, camera_width=self.camera_width,
            **({"variation": variation} if variation is not None else {"discover_variation": True}),
        )
        if variation is not None and applied != variation:
            raise RuntimeError("Robosuite service attested a different variation")
        self._seed = seed
        self._started_at = time.monotonic()
        self._last_observation = observation
        self._action_low = np.asarray(low, dtype=np.float64)
        self._action_high = np.asarray(high, dtype=np.float64)
        self._metadata = dict(metadata)
        self._sdk_gripper_command = -1.0
        self.trace.clear()
        return copy_observation(observation), applied

    def perceive_gt(
        self, *, target_names: list[str] | None = None,
        camera_names: list[str] | None = None,
    ) -> dict[str, Any]:
        return self._client.perceive_gt(target_names=target_names, camera_names=camera_names)
