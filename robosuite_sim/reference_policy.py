"""Server-side privileged diagnostic policy; object state never crosses HTTP."""

from __future__ import annotations

import time
from typing import Protocol

import numpy as np


class ReferenceBackend(Protocol):
    spec: object
    action_spec: tuple[np.ndarray, np.ndarray]

    def reference_observation(self): ...
    def step(self, action: np.ndarray): ...


class EpisodeTimeout(TimeoutError):
    pass


def _check_deadline(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise EpisodeTimeout("reference policy exceeded episode timeout")


def _move_to(backend, target, gripper, deadline, max_steps=100, tolerance=0.004):
    shape = backend.action_spec[0].shape
    for step in range(max_steps):
        _check_deadline(deadline)
        raw = backend.reference_observation()
        eef = np.asarray(raw["robot0_eef_pos"], dtype=np.float64)
        action = np.zeros(shape, dtype=np.float64)
        action[:3] = np.clip((target - eef) / 0.05, -1.0, 1.0)
        action[-1] = gripper
        backend.step(action)
        if step > 8 and np.linalg.norm(target - eef) < tolerance:
            return


def _hold(backend, gripper, steps, deadline):
    shape = backend.action_spec[0].shape
    for _ in range(steps):
        _check_deadline(deadline)
        action = np.zeros(shape, dtype=np.float64)
        action[-1] = gripper
        backend.step(action)


def run_reference_policy(backend, timeout_seconds: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    raw = backend.reference_observation()
    task_id = backend.spec.task_id

    if task_id == "cube_lift":
        cube = np.asarray(raw["cube_pos"], dtype=np.float64)
        _move_to(backend, cube + (0.0, 0.0, 0.12), -1.0, deadline)
        _move_to(backend, cube + (0.0, 0.0, 0.005), -1.0, deadline)
        _hold(backend, 1.0, 30, deadline)
        _move_to(backend, np.array((cube[0], cube[1], 1.08)), 1.0, deadline)
        _hold(backend, 1.0, 10, deadline)
        return

    if task_id == "cube_stack":
        cube_a = np.asarray(raw["cubeA_pos"], dtype=np.float64)
        cube_b = np.asarray(raw["cubeB_pos"], dtype=np.float64)
        _move_to(backend, cube_a + (0.0, 0.0, 0.12), -1.0, deadline)
        _move_to(backend, cube_a + (0.0, 0.0, 0.005), -1.0, deadline)
        _hold(backend, 1.0, 30, deadline)
        _move_to(backend, np.array((cube_a[0], cube_a[1], 1.08)), 1.0, deadline)
        _move_to(backend, np.array((cube_b[0], cube_b[1], 1.08)), 1.0, deadline)
        _move_to(backend, cube_b + (0.0, 0.0, 0.055), 1.0, deadline)
        _hold(backend, -1.0, 25, deadline)
        _move_to(backend, cube_b + (0.0, 0.0, 0.18), -1.0, deadline)
        _hold(backend, -1.0, 10, deadline)
        return

    raise ValueError(f"no reference policy for {task_id}")
