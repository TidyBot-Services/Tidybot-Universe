#!/usr/bin/env python3
"""
pick-object — Detect the target object ('obj') on the counter, then pick it up.

Pipeline
--------
1. detect_scene_objects()  — locate 'obj' and fixtures; get world-frame XYZ
2. Validate 'obj' z-position is within the expected counter-height band
3. grasp_and_lift(tx, ty, tz) — approach from above, lower, grasp, lift

Output (parsed by tests/run_trials.py)
--------------------------------------
  Detections: [{"name": "...", "position": [x, y, z]}, ...]
  gripper width after close: X.XXXX m
  ee_z after lift: X.XXX m (target X.XXX m)
  Result: SUCCESS  |  Result: FAILED – <reason>
"""

import json
import math
import sys

from robot_sdk import arm, gripper, sensors

# ---------------------------------------------------------------------------
# Counter-height band — object must be here to be considered "on the counter"
# ---------------------------------------------------------------------------
Z_COUNTER_MIN = -0.60   # m  (below this → not on counter)
Z_COUNTER_MAX = -0.25   # m  (above this → suspiciously high)

# ---------------------------------------------------------------------------
# Grasp / lift parameters — must match grasp-and-lift/scripts/main.py defaults
# ---------------------------------------------------------------------------
APPROACH_CLEARANCE  = 0.15   # m — height above grasp point for pre-grasp approach
LIFT_HEIGHT         = 0.20   # m — rise after successful grasp
GRASP_WIDTH_MIN     = 0.010  # m — gripper width below this means empty hand
MOVE_TIMEOUT        = 10     # s — per arm.move_to_pose call
LIFT_Z_TOLERANCE    = 0.05   # m — acceptable undershoot in lift verification

# roll = π → EE Z-axis points straight down (top-down grasp orientation)
STRAIGHT_DOWN_ROLL = math.pi


# ---------------------------------------------------------------------------
# detect_scene_objects — inlined from detect-scene-objects/scripts/main.py
# ---------------------------------------------------------------------------

def _is_finite(v):
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def detect_scene_objects():
    """
    Scan the scene and return a list of dicts:
        [{"name": str, "position": [x, y, z]}, ...]

    Raises RuntimeError with a descriptive tag on failure.
    """
    # Broad scan — everything the sensor pipeline can see
    raw = sensors.find_objects()

    # Targeted pass to boost recall for the key names
    targeted = sensors.find_objects(target_names=["obj", "cabinet", "counter"])

    # Merge, deduplicating on name
    seen = {o["name"] for o in raw}
    merged = list(raw)
    for o in targeted:
        if o["name"] not in seen:
            merged.append(o)
            seen.add(o["name"])

    # Build structured detection list, dropping invalid entries
    detections = []
    for o in merged:
        name = o.get("name", "")
        x = o.get("x", float("nan"))
        y = o.get("y", float("nan"))
        z = o.get("z", float("nan"))

        if not (isinstance(name, str) and name.strip()):
            print(f"  [warn] skipping detection with invalid name: {o!r}", flush=True)
            continue
        if not (_is_finite(x) and _is_finite(y) and _is_finite(z)):
            print(f"  [warn] skipping '{name}' — non-finite position ({x}, {y}, {z})", flush=True)
            continue

        detections.append({"name": name, "position": [float(x), float(y), float(z)]})

    if not detections:
        raise RuntimeError("no_objects_detected: find_objects returned an empty list")

    names_lower = [d["name"].lower() for d in detections]

    if not any("obj" in n for n in names_lower):
        raise RuntimeError(
            f"obj_not_found: 'obj' absent from scene detections {[d['name'] for d in detections]}"
        )
    if not any("cabinet" in n or "counter" in n for n in names_lower):
        raise RuntimeError(
            f"fixtures_not_found: counter/cabinet not detected in "
            f"{[d['name'] for d in detections]}"
        )

    return detections


# ---------------------------------------------------------------------------
# grasp_and_lift — inlined from grasp-and-lift/scripts/main.py
# ---------------------------------------------------------------------------

def _move(x: float, y: float, z: float, label: str) -> None:
    """Move EE to (x, y, z) with a straight-down orientation."""
    print(f"  move_to_pose → {label} ({x:.3f}, {y:.3f}, {z:.3f})", flush=True)
    arm.move_to_pose(
        x=x, y=y, z=z,
        roll=STRAIGHT_DOWN_ROLL, pitch=0.0, yaw=0.0,
        timeout=MOVE_TIMEOUT,
    )


def _get_gripper_width_m() -> float:
    """Return current gripper opening in metres (best available source)."""
    # 1. Calibrated sensor reading
    try:
        w = sensors.get_gripper_width()
        if w is not None:
            return w
    except Exception:
        pass

    # 2. position_mm from gripper state
    try:
        mm = gripper.get_state().get("position_mm")
        if mm is not None:
            return mm / 1000.0
    except Exception:
        pass

    # 3. Raw position fallback (Robotiq 2F-85: 85 mm stroke, 0=open, 255=closed)
    try:
        pos = gripper.get_state().get("position", 255)
        return (255 - pos) / 255.0 * 0.085
    except Exception:
        return 0.0


def grasp_and_lift(target_x: float, target_y: float, target_z: float) -> bool:
    """
    Top-down grasp and lift at a known XYZ position in the robot base frame.
    Returns True on success, False on any failure.
    Prints 'Result: SUCCESS' or 'Result: FAILED – <reason>' to stdout.
    """
    # Step 1: open gripper
    print("Step 1: opening gripper", flush=True)
    gripper.open()

    # Step 2: approach from above
    print("Step 2: approaching from above", flush=True)
    try:
        _move(target_x, target_y, target_z + APPROACH_CLEARANCE, "approach")
    except Exception as e:
        print(f"Result: FAILED – approach_failed: {e}", flush=True)
        return False

    # Step 3: lower to grasp height
    print("Step 3: lowering to grasp height", flush=True)
    try:
        _move(target_x, target_y, target_z, "grasp")
    except Exception as e:
        print(f"Result: FAILED – lower_failed: {e}", flush=True)
        return False

    # Step 4: close gripper and confirm contact
    print("Step 4: closing gripper", flush=True)
    gripper.close()

    width = _get_gripper_width_m()
    print(f"  gripper width after close: {width:.4f} m", flush=True)

    if width <= GRASP_WIDTH_MIN:
        print(
            f"Result: FAILED – grasp_failed: "
            f"width={width:.4f} ≤ threshold={GRASP_WIDTH_MIN}",
            flush=True,
        )
        gripper.open()
        return False

    # Step 5: lift
    print("Step 5: lifting object", flush=True)
    lift_z = target_z + LIFT_HEIGHT
    try:
        _move(target_x, target_y, lift_z, "lift")
    except Exception as e:
        print(f"Result: FAILED – lift_failed: {e}", flush=True)
        return False

    # Verify EE reached lift height
    try:
        ee_x, ee_y, ee_z = sensors.get_ee_position()
        print(f"  ee_z after lift: {ee_z:.3f} m (target {lift_z:.3f} m)", flush=True)
        if ee_z < lift_z - LIFT_Z_TOLERANCE:
            print(
                f"Result: FAILED – lift_failed: "
                f"ee_z={ee_z:.3f} < expected={lift_z:.3f}",
                flush=True,
            )
            return False
    except Exception:
        pass  # trust move_to_pose success if sensor read fails

    return True


# ---------------------------------------------------------------------------
# pick_object — composed entry point
# ---------------------------------------------------------------------------

def pick_object() -> bool:
    """
    Detect 'obj' on the counter and pick it up.
    Returns True on success, False on failure.
    Always prints 'Result: SUCCESS' or 'Result: FAILED – <reason>'.
    """
    try:
        # ── Step 1: detect scene objects ─────────────────────────────────────
        print("Step 1: detecting scene objects", flush=True)
        try:
            detections = detect_scene_objects()
        except Exception as e:
            print(f"Result: FAILED – detection_failed: {e}", flush=True)
            return False

        # Machine-readable line — parsed by run_trials.py
        print(f"Detections: {json.dumps(detections)}", flush=True)

        # ── Step 2: locate 'obj' ─────────────────────────────────────────────
        obj_det = next(
            (d for d in detections if "obj" in d["name"].lower()), None
        )
        if obj_det is None:
            print("Result: FAILED – obj_not_found: 'obj' not in detections", flush=True)
            return False

        tx, ty, tz = obj_det["position"]
        print(f"Target 'obj' at ({tx:.3f}, {ty:.3f}, {tz:.3f})", flush=True)

        # ── Step 3: verify counter height ────────────────────────────────────
        if not (Z_COUNTER_MIN <= tz <= Z_COUNTER_MAX):
            print(
                f"Result: FAILED – obj_not_on_counter: "
                f"obj z={tz:.3f} outside expected counter band "
                f"[{Z_COUNTER_MIN}, {Z_COUNTER_MAX}]",
                flush=True,
            )
            return False

        # ── Step 4: grasp and lift ────────────────────────────────────────────
        print("Step 2: grasping and lifting 'obj'", flush=True)
        ok = grasp_and_lift(tx, ty, tz)
        if not ok:
            return False

        print("Result: SUCCESS", flush=True)
        return True

    except Exception as e:
        print(f"Result: FAILED – crash: {e}", flush=True)
        return False


# ---------------------------------------------------------------------------
# Entry point — submitted directly via POST /code/execute
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ok = pick_object()
    sys.exit(0 if ok else 1)
