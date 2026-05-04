"""Tiny HTTP client for curobo_service. Used by smoke test + benchmark.

We don't import curobo_client.py from the sim repo because (a) it lives under
an untracked tree, (b) it depends on numpy in a way that's fine but adds a
dependency for what is otherwise a pure-stdlib comparison harness.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlanResult:
    ok: bool
    elapsed_s: float
    raw: dict
    error: str | None = None

    @property
    def trajectory(self) -> list[list[float]] | None:
        return self.raw.get("trajectory") if self.ok else None

    @property
    def num_waypoints(self) -> int:
        traj = self.trajectory
        return len(traj) if traj else 0


@dataclass
class CuroboClient:
    """Lookalike of CuroboHttpClient with timing + structured errors."""
    url: str
    env_id: str = "default"
    timeout: float = 60.0
    _world_pushed: bool = field(default=False)

    def _post(self, path: str, body: dict) -> tuple[dict, float]:
        t0 = time.time()
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read()), time.time() - t0
        except urllib.error.HTTPError as e:
            body_bytes = e.read() if e.fp is not None else b""
            try:
                payload = json.loads(body_bytes)
            except json.JSONDecodeError:
                payload = {"raw_body": body_bytes.decode("utf-8", "replace")}
            raise urllib.error.HTTPError(
                e.url, e.code,
                f"HTTP {e.code} from {path}: {payload}",
                e.hdrs, None,
            )

    def _get(self, path: str) -> dict:
        with urllib.request.urlopen(self.url + path, timeout=2.0) as resp:
            return json.loads(resp.read())

    def health(self) -> dict:
        return self._get("/health")

    def warmup(self) -> dict:
        out, _ = self._post("/warmup", {})
        return out

    def push_world(self, cuboids: list[dict],
                   robot_pos: list[float] | None = None) -> dict:
        body: dict[str, Any] = {"env_id": self.env_id, "cuboids": cuboids}
        if robot_pos is not None:
            body["robot_pos"] = robot_pos
        out, _ = self._post("/world/cuboids", body)
        self._world_pushed = True
        return out

    def plan_pose(self, current_q: list[float],
                  target_pos: list[float],
                  target_quat: list[float] | None = None,
                  mask: str = "whole_body") -> PlanResult:
        body = {
            "env_id": self.env_id,
            "current_q": list(current_q),
            "target_pose": list(target_pos),
            "target_quat": list(target_quat) if target_quat is not None else None,
            "mask": mask,
        }
        try:
            out, dt = self._post("/plan", body)
        except (urllib.error.URLError, ConnectionError) as e:
            return PlanResult(ok=False, elapsed_s=0.0, raw={}, error=str(e))
        ok = out.get("status") == "success"
        return PlanResult(ok=ok, elapsed_s=dt, raw=out,
                          error=None if ok else out.get("status"))

    def plan_joints(self, current_q: list[float],
                    target_q: list[float]) -> PlanResult:
        body = {
            "env_id": self.env_id,
            "current_q": list(current_q),
            "target_qpos": list(target_q),
        }
        try:
            out, dt = self._post("/plan/joint", body)
        except (urllib.error.URLError, ConnectionError) as e:
            return PlanResult(ok=False, elapsed_s=0.0, raw={}, error=str(e))
        ok = out.get("status") == "success"
        return PlanResult(ok=ok, elapsed_s=dt, raw=out,
                          error=None if ok else out.get("status"))


def path_length(traj: list[list[float]]) -> float:
    """Sum of euclidean joint-space deltas across waypoints."""
    if not traj or len(traj) < 2:
        return 0.0
    total = 0.0
    for i in range(1, len(traj)):
        d = sum((traj[i][k] - traj[i-1][k]) ** 2 for k in range(len(traj[i]))) ** 0.5
        total += d
    return total


HOME_QPOS_10D: list[float] = [
    0.0, 0.0, 0.0,                       # base x, y, yaw
    0.0, -0.785, 0.0, -2.356,            # arm joints 1-4
    0.0, 1.571, 0.785,                   # arm joints 5-7
]
"""Standard Franka home pose with mobile base at origin. Matches the lock
defaults in franka_tidyverse.yml."""


GROUND_CUBOID: dict = {
    "name": "ground",
    "center": [0.0, 0.0, -0.51],
    "half_size": [5.0, 5.0, 0.01],
}
"""Minimal world — flat ground 51cm below origin. Curobo needs at least one
collision object. Schema: {name, center [xyz], half_size [hx,hy,hz]} —
this is what CuroboPlanner.set_collision_world() expects (NOT the
{size, pose} shape advertised in the service README, which is outdated)."""
