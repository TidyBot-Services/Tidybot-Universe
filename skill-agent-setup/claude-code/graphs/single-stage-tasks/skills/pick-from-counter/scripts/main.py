#!/usr/bin/env python3
"""
pick-from-counter — Detect the target object on the counter and grasp it.

Pipeline
--------
1. Call sensors.find_objects() and identify the graspable target on the counter
2. Filter out scene fixtures (walls, floors, counters, cabinets, doors…)
3. Select nearest graspable object at counter height (0.65–1.15 m)
4. Open gripper, approach from above, lower and grasp, lift
5. Retry grasp with z variation if first attempt misses

Prints 'Result: SUCCESS – picked <name>' or 'Result: FAILED – <reason>'.
"""

import math
import sys
import time

from robot_sdk import gripper, sensors, wb

# ── Config ────────────────────────────────────────────────────────────────────
APPROACH_CLEARANCE = 0.15   # m above grasp point for pre-grasp approach
LIFT_HEIGHT        = 0.20   # m to lift after successful grasp
COUNTER_Z_MIN      = 0.65   # m — lower bound for "counter-level" object
COUNTER_Z_MAX      = 1.15   # m — upper bound for "counter-level" object
MOVE_TIMEOUT       = 20     # s per wb.move_to_pose call

TOP_DOWN_QUAT   = (0, 1, 0, 0)
WB_MASK         = "whole_body"

# Gripper width thresholds (metres) for grasp detection
GRIPPER_MIN_M   = 0.005   # below this → gripper fully closed, nothing grasped
GRIPPER_MAX_M   = 0.083   # above this → gripper still open, grasp missed

# Z offsets to try per attempt (relative to detected object z); biased upward
# because top-down grasp contacts best slightly above the detected centroid
GRASP_Z_OFFSETS  = [0.02, 0.03, 0.01, 0.04, 0.0, 0.05, -0.01]
GRIPPER_SETTLE_S = 0.8    # sim gripper sensor has ~0.5–1 s reporting lag

# Name substrings that mark scene fixtures (not graspable objects)
_FIXTURE_KEYWORDS = (
    "floor", "wall", "ceiling",
    "counter", "cab_corner", "cabinet",
    "hinge", "door", "shelf", "shelves",
    "rack", "dishwasher", "paper_towel",
    "spout", "stack_", "utensil",
    "fridge", "stove", "oven", "microwave",
    "window", "light",
)


def _is_fixture(name: str) -> bool:
    """Return True if the object name looks like a scene fixture."""
    low = name.lower()
    return any(kw in low for kw in _FIXTURE_KEYWORDS)


def _find_graspable_objects(raw: list = None) -> list:
    """
    Return graspable candidates from raw sensor data, sorted nearest-first.

    Priority: counter-context > counter height > any non-fixture.
    Pass raw to avoid a second sensors.find_objects() call.
    """
    if raw is None:
        raw = sensors.find_objects()
    if not raw:
        return []

    candidates = []
    for o in raw:
        name = o.get("name", "").strip()
        x, y, z = o.get("x"), o.get("y"), o.get("z")
        try:
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                continue
        except (TypeError, ValueError):
            continue
        if not name or _is_fixture(name):
            continue
        candidates.append(o)

    if not candidates:
        return []

    candidates.sort(key=lambda o: o.get("distance_m", 9999))

    ctx_counter  = [o for o in candidates if "counter" in o.get("fixture_context", "").lower()]
    height_match = [o for o in candidates
                    if COUNTER_Z_MIN <= float(o["z"]) <= COUNTER_Z_MAX]
    return ctx_counter or height_match or candidates


def _wb_move(x: float, y: float, z: float, label: str) -> None:
    print(f"  → {label}: ({x:.3f}, {y:.3f}, {z:.3f})", flush=True)
    wb.move_to_pose(x=x, y=y, z=z, quat=TOP_DOWN_QUAT, mask=WB_MASK, timeout=MOVE_TIMEOUT)


def _gripper_width_m() -> float:
    """Return current gripper opening in metres."""
    try:
        w = sensors.get_gripper_width()
        if w is not None:
            return float(w)
    except Exception:
        pass
    try:
        state = gripper.get_state()
        mm = state.get("position_mm")
        if mm is not None:
            return mm / 1000.0
        pos = state.get("position", 255)
        return (255 - pos) / 255.0 * 0.085
    except Exception:
        return 0.0


def _gripper_has_object() -> tuple:
    """Return (object_in_hand: bool, width_m: float).
    object_detected flag is not reliable in sim — width is the ground truth."""
    width = _gripper_width_m()
    return GRIPPER_MIN_M < width < GRIPPER_MAX_M, width


def _attempt_grasp(tx: float, ty: float, grasp_z: float, attempt: int) -> bool:
    """
    One grasp attempt at (tx, ty, grasp_z). Returns True if object detected in gripper.
    """
    print(f"  Grasp attempt {attempt}: z={grasp_z:.3f}", flush=True)
    gripper.open()
    _wb_move(tx, ty, grasp_z + APPROACH_CLEARANCE, f"approach[{attempt}]")
    _wb_move(tx, ty, grasp_z, f"grasp[{attempt}]")
    gripper.close()
    time.sleep(GRIPPER_SETTLE_S)
    has_obj, width = _gripper_has_object()
    print(f"    width={width:.4f} m  grasped={has_obj}", flush=True)
    if has_obj:
        return True
    gripper.open()
    return False


def pick_from_counter(target_name: str = None) -> bool:
    """
    Detect and pick the target object from the counter.

    Parameters
    ----------
    target_name : str, optional
        Object name to grasp. If None, picks the nearest graspable object.

    Returns
    -------
    bool – True on success.
    """
    try:
        print("Step 1: scanning scene for graspable objects…", flush=True)
        raw = sensors.find_objects()
        if not raw:
            print("Result: FAILED – no_objects_detected", flush=True)
            return False

        objects = _find_graspable_objects(raw)
        if not objects:
            # filter found nothing graspable — fall back to nearest raw object
            print("  Filter found nothing; falling back to nearest raw object…", flush=True)
            objects = sorted(raw, key=lambda o: o.get("distance_m", 9999))

        print(f"  {len(objects)} candidate(s):", flush=True)
        for o in objects:
            print(f"    {o['name']:40s}  fixture={o.get('fixture_context',''):20s}"
                  f"  z={o.get('z', 0):.3f}", flush=True)

        if target_name:
            target = next((o for o in objects if o["name"] == target_name), None)
            if target is None:
                print(f"Result: FAILED – target_not_found: {target_name}", flush=True)
                return False
        else:
            target = objects[0]

        tx, ty, tz = float(target["x"]), float(target["y"]), float(target["z"])
        print(f"  Selected: {target['name']} @ ({tx:.3f}, {ty:.3f}, {tz:.3f})", flush=True)

        final_grasp_z = None
        for attempt, z_offset in enumerate(GRASP_Z_OFFSETS, 1):
            grasp_z = tz + z_offset
            if _attempt_grasp(tx, ty, grasp_z, attempt):
                final_grasp_z = grasp_z
                break

        if final_grasp_z is None:
            print("Result: FAILED – all_grasp_attempts_failed", flush=True)
            return False

        print(f"Step 6: lifting object (from z={final_grasp_z:.3f})", flush=True)
        _wb_move(tx, ty, final_grasp_z + LIFT_HEIGHT, "lift")

        print(f"Result: SUCCESS – picked {target['name']}", flush=True)
        return True

    except Exception as e:
        print(f"Result: FAILED – crash: {e}", flush=True)
        return False


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else None
    ok = pick_from_counter(target_name=target)
    sys.exit(0 if ok else 1)
