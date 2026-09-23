"""AttentionHarness adapter that talks only to the robosuite_sim service."""

from __future__ import annotations

import hashlib
import os
import time
from typing import Any, Mapping, Protocol

import numpy as np

from robosuite_sim.client import ClientStep, ReferenceResult, RobosuiteSimClient

from .service_contract import ServiceStep, copy_observation
from .task_registry import get_task

# Compatibility name for existing policies and tests. New code should use the
# backend-neutral ServiceStep contract.
StepResult = ServiceStep


class SimulatorClient(Protocol):
    def reset(self, **request: Any): ...
    def attach(self): ...
    def step(self, action: np.ndarray) -> ClientStep: ...
    def observe(self) -> dict[str, np.ndarray]: ...
    def native_success(self) -> bool: ...
    def metadata(self) -> dict[str, Any]: ...
    def run_reference(self, timeout_seconds: float) -> ReferenceResult: ...
    def close_environment(self) -> None: ...


class RobosuiteAdapter:
    """Own benchmark semantics while delegating simulation to its peer service."""

    def __init__(
        self,
        task_id: str,
        *,
        service_url: str | None = None,
        horizon: int = 500,
        camera: bool = True,
        camera_height: int = 256,
        camera_width: int = 256,
        client: SimulatorClient | None = None,
    ) -> None:
        self.spec = get_task(task_id)
        self.service_url = service_url or os.environ.get(
            "TIDYBOT_ROBOSUITE_URL", "http://127.0.0.1:8082"
        )
        self.horizon = horizon
        self.camera = camera
        self.camera_height = camera_height
        self.camera_width = camera_width
        self._client = client or RobosuiteSimClient(self.service_url)
        self._seed: int | None = None
        self._started_at: float | None = None
        self._last_observation: dict[str, np.ndarray] | None = None
        self._action_low: np.ndarray | None = None
        self._action_high: np.ndarray | None = None
        self._metadata: dict[str, Any] = {}
        self.trace: list[dict[str, Any]] = []

    @property
    def action_shape(self) -> tuple[int, ...]:
        if self._action_low is None:
            raise RuntimeError("reset must be called before reading action_shape")
        return tuple(self._action_low.shape)

    @property
    def control_frame(self) -> str:
        return str(self._metadata.get("control_frame", "robosuite_world"))

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "adapter": f"{type(self).__module__}.{type(self).__name__}",
            "service_url": self.service_url,
            **self._metadata,
        }

    def reset(self, seed: int) -> dict[str, np.ndarray]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed must be an integer")
        observation, low, high, metadata = self._client.reset(
            task_id=self.spec.task_id,
            seed=seed,
            horizon=self.horizon,
            camera=self.camera,
            camera_height=self.camera_height,
            camera_width=self.camera_width,
        )
        self._seed = seed
        self._started_at = time.monotonic()
        self._last_observation = observation
        self._action_low = np.asarray(low, dtype=np.float64)
        self._action_high = np.asarray(high, dtype=np.float64)
        self._metadata = dict(metadata)
        self.trace.clear()
        return copy_observation(observation)

    def attach(self) -> dict[str, np.ndarray]:
        """Attach a sandbox process to the service's active episode."""
        observation, low, high, metadata = self._client.attach()
        if metadata.get("task_id") not in (None, self.spec.task_id):
            raise RuntimeError(
                f"active service task {metadata.get('task_id')!r} does not match "
                f"requested task {self.spec.task_id!r}"
            )
        self._seed = metadata.get("seed")
        self._started_at = time.monotonic()
        self._last_observation = observation
        self._action_low = np.asarray(low, dtype=np.float64)
        self._action_high = np.asarray(high, dtype=np.float64)
        self._metadata = dict(metadata)
        self.trace.clear()
        return copy_observation(observation)

    def step(self, action: np.ndarray | list[float]) -> StepResult:
        if self._action_low is None or self._action_high is None:
            raise RuntimeError("reset must be called before step")
        array = np.asarray(action, dtype=np.float64)
        if array.shape != self._action_low.shape:
            raise ValueError(f"action shape {array.shape} does not match {self._action_low.shape}")
        if not np.isfinite(array).all():
            raise ValueError("action must contain only finite values")
        clipped = np.clip(array, self._action_low, self._action_high)
        result = self._client.step(clipped)
        self._last_observation = result.observation
        public = copy_observation(result.observation)
        self.trace.append(
            {
                "step": len(self.trace),
                "action": clipped.tolist(),
                "reward": result.reward,
                "done": result.done,
                "observation_sha256": observation_fingerprint(public),
            }
        )
        return ServiceStep(public, result.reward, result.done, dict(result.info))

    def observe(self) -> dict[str, np.ndarray]:
        if self._last_observation is None:
            raise RuntimeError("reset must be called before observe")
        return copy_observation(self._last_observation)

    def refresh(self) -> dict[str, np.ndarray]:
        """Fetch state changed by another client, such as a sandbox worker."""
        observation = self._client.observe()
        self._last_observation = observation
        return copy_observation(observation)

    def native_success(self) -> bool:
        return self._client.native_success()

    def run_reference_policy(self, timeout_seconds: float = 60.0) -> None:
        result = self._client.run_reference(timeout_seconds)
        self._last_observation = result.observation
        self.trace = list(result.trace)

    def elapsed_seconds(self) -> float:
        return 0.0 if self._started_at is None else time.monotonic() - self._started_at

    def no_op_action(self) -> np.ndarray:
        action = np.zeros(self.action_shape, dtype=np.float64)
        action[-1] = -1.0
        return action

    def close(self) -> None:
        self._client.close_environment()

def observation_fingerprint(observation: Mapping[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for key in sorted(observation):
        value = np.ascontiguousarray(observation[key])
        digest.update(key.encode("utf-8"))
        digest.update(str(value.dtype).encode("ascii"))
        digest.update(repr(value.shape).encode("ascii"))
        digest.update(value.tobytes())
    return digest.hexdigest()
