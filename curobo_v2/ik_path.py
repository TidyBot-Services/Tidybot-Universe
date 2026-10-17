"""Stationary-endpoint joint path for the opt-in IK planner."""
from __future__ import annotations

import math
import numpy as np


def ranked_ik_candidates(current, joint_names, active_joints, solutions, successes):
    """Return (ranked indices, L2 distances in IK order, max active changes)."""
    current = np.asarray(current, dtype=float)
    values = np.asarray(solutions, dtype=float)
    success = np.asarray(successes, dtype=bool).reshape(-1)
    if values.ndim != 2 or values.shape[0] != len(success) or values.shape[1] != len(joint_names):
        raise ValueError('Invalid IK candidate dimensions')
    active = [joint_names.index(name) for name in active_joints]
    delta = values[:, active] - current[active]
    distances = [float(np.linalg.norm(row)) if np.isfinite(values[i]).all() else None
                 for i, row in enumerate(delta)]
    changes = [float(np.max(np.abs(row))) if distance is not None else None
               for row, distance in zip(delta, distances)]
    order = sorted((i for i, distance in enumerate(distances)
                    if bool(success[i]) and distance is not None),
                   key=lambda i: (distances[i], changes[i], i))
    return order, distances, changes


def quintic_path(q0, q1, *, dt, minimum_duration, speed, acceleration, jerk):
    q0 = np.asarray(q0, dtype=float)
    q1 = np.asarray(q1, dtype=float)
    speed = np.asarray(speed, dtype=float)
    if (q0.ndim != 1 or q1.shape != q0.shape or speed.shape != q0.shape
            or not all(np.isfinite(x).all() for x in (q0, q1, speed))
            or np.any(speed <= 0) or not all(math.isfinite(x) and x > 0 for x in
                                           (dt, minimum_duration, acceleration, jerk))):
        raise ValueError("Invalid IK path positions or motion bounds")
    delta = q1 - q0
    distance = np.abs(delta)
    # s(u)=10u^3-15u^4+6u^5. Global absolute derivative peaks are exact.
    required = max(minimum_duration,
                   float(np.max(15 * distance / (8 * speed))),
                   math.sqrt(float(np.max((10 / math.sqrt(3)) * distance / acceleration))),
                   float(np.max(60 * distance / jerk)) ** (1 / 3))
    intervals = max(1, math.ceil(required / dt - 1e-12))
    if intervals * dt < required * (1 - 1e-12):
        intervals += 1
    duration = intervals * dt
    times = np.arange(intervals + 1, dtype=float) * dt
    u = times / duration
    s = 10*u**3 - 15*u**4 + 6*u**5
    v = (30*u**2 - 60*u**3 + 30*u**4) / duration
    a = (60*u - 180*u**2 + 120*u**3) / duration**2
    j = (60 - 360*u + 360*u**2) / duration**3
    q = q0 + s[:, None] * delta
    derivatives = {"velocity": v[:, None]*delta,
                   "acceleration": a[:, None]*delta,
                   "jerk": j[:, None]*delta}
    q[0], q[-1] = q0, q1
    for key in ("velocity", "acceleration"):
        derivatives[key][[0, -1]] = 0.
    times[-1] = duration
    return q, times, derivatives
