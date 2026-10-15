"""Per-env world state.

Each env_id (sim instance / hardware target) keeps its own list of
collision cuboids. Plan calls reference an env_id; we restore that
world before delegating to the planner.

For Phase 1 we keep a single CuroboPlanner instance and re-call
set_collision_world() on switch — simple but pays the GPU cost each
swap. If profiling shows this matters, Phase 2 should hold N motion-gen
configs (one per env_id) and pick the right one per request.
"""

from copy import deepcopy
import hashlib
import json
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

import numpy as np


@dataclass
class WorldState:
    cuboids: list[dict] = field(default_factory=list)
    robot_pos: Optional[np.ndarray] = None  # base x,y for arm-only frame
    set_at: float = 0.0
    revision: int = 0
    sha256: str = ""


class WorldStore:
    """Thread-safe per-env world store."""

    def __init__(self, max_envs: int):
        self._envs: dict[str, WorldState] = {}
        self._max = max_envs
        self._lock = Lock()
        self._active_env: Optional[str] = None  # which env's world is loaded into cuRobo
        self._revision = 0

    def check_capacity(self,env_id):
        with self._lock:
            if env_id not in self._envs and len(self._envs)>=self._max:
                raise ValueError("Maximum environments reached")

    def set(self, env_id: str, cuboids: list[dict],
            robot_pos: Optional[list[float]] = None) -> None:
        import time
        with self._lock:
            if env_id not in self._envs and len(self._envs) >= self._max:
                raise RuntimeError(
                    f"max envs ({self._max}) reached; bump CUROBO_MAX_ENVS")
            self._revision += 1
            self._envs[env_id] = WorldState(
                cuboids=deepcopy(cuboids),
                robot_pos=np.asarray(robot_pos) if robot_pos is not None else None,
                set_at=time.time(),
                revision=self._revision,
                sha256=hashlib.sha256(json.dumps({"cuboids": cuboids, "robot_pos": robot_pos},
                    sort_keys=True, allow_nan=False).encode()).hexdigest(),
            )

    def get(self, env_id: str) -> Optional[WorldState]:
        with self._lock:
            return self._envs.get(env_id)

    def list_envs(self) -> list[str]:
        with self._lock:
            return sorted(self._envs.keys())

    def mark_active(self, env_id: str) -> None:
        with self._lock:
            self._active_env = env_id

    def active(self) -> Optional[str]:
        with self._lock:
            return self._active_env
