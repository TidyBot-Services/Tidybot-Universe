#!/usr/bin/env python3
"""
pick-object — Detect a target object on the counter, navigate to it, and grasp it.

Pipeline
--------
1. Detect scene objects via sensors.find_objects(); filter fixtures
2. Locate the target (default: "obj") at counter height in world frame
3. Open gripper → approach from above → lower to grasp → close → lift
4. Verify grasp by checking gripper width; retry with z-offsets on miss

Usage
-----
    python main.py [target_name]           # default target_name = "obj"

Output (machine-readable, parsed by run_trials.py)
---------------------------------------------------
    Detections: [{"name": "...", "position": [x, y, z]}, ...]
    Target '<name>' at (x.xxx, y.yyy, z.zzz)
    gripper width after close: X.XXXX m
    Result: SUCCESS  |  Result: FAILED – <reason>
"""

import json
import math
import sys
import time

from robot_sdk import gripper, sensors, wb

# Counter height band (world frame, metres)
COUNTER_Z_MIN = 0.65   # below this → probably not on counter
COUNTER_Z_MAX = 1.15   # above this → suspiciously high

APPROACH_CLEARANCE = 0.15   # m above grasp point for pre-grasp approach
LIFT_HEIGHT        = 0.20   # m to raise after successful grasp
MOVE_TIMEOUT       = 20     # s per wb.move_to_pose call
GRIPPER_SETTLE_S   = 0.8    # s to let gripper sensor settle after close
GRIPPER_MIN_M      = 0.005  # below this → empty hand (nothing grasped)
GRIPPER_MAX_M      = 0.083  # above this → gripper still open (grasp missed)

# Biased slightly above centroid — top-down grasp contacts best above the detected centre
GRASP_Z_OFFSETS = [0.02, 0.03, 0.01, 0.04, 0.0, 0.05, -0.01]

# 180° rotation around X → EE Z-axis points straight down
TOP_DOWN_QUAT = (0, 1, 0, 0)
WB_MASK = "whole_body"


def _is_finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _gripper_width_m() -> float:
    """Return current gripper opening in metres (best available source)."""
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
        pos = state.get("position", 255)   # 0=open, 255=closed (Robotiq 2F-85)
        return (255 - pos) / 255.0 * 0.085
    except Exception:
        return 0.0


def _gripper_has_object() -> tuple:
    """Return (has_object: bool, width_m: float)."""
    time.sleep(GRIPPER_SETTLE_S)
    w = _gripper_width_m()
    has_obj = GRIPPER_MIN_M < w < GRIPPER_MAX_M
    return has_obj, w


def _wb_move(x: float, y: float, z: float, label: str) -> None:
    print(f"  → {label}: ({x:.3f}, {y:.3f}, {z:.3f})", flush=True)
    wb.move_to_pose(x=x, y=y, z=z, quat=TOP_DOWN_QUAT, mask=WB_MASK, timeout=MOVE_TIMEOUT)


# ─────────────────────────────────────────────────────────────────────────────

def detect_objects(target_name: str) -> list:
    """Return valid scene detections as [{"name": str, "position": [x,y,z]}, ...].
    Raises RuntimeError if no objects are detected at all.
    """
    raw = sensors.find_objects()
    # Targeted pass to boost recall for the specific target
    targeted = sensors.find_objects(target_names=[target_name, "counter", "cabinet"])

    # Merge, deduplicating by name
    seen = {o["name"] for o in raw}
    merged = list(raw)
    for o in targeted:
        if o["name"] not in seen:
            merged.append(o)
            seen.add(o["name"])

    detections = []
    for o in merged:
        name = o.get("name", "").strip()
        x, y, z = o.get("x"), o.get("y"), o.get("z")
        if not name or not (_is_finite(x) and _is_finite(y) and _is_finite(z)):
            continue
        detections.append({"name": name, "position": [float(x), float(y), float(z)]})

    if not detections:
        raise RuntimeError("no_objects_detected")

    return detections


def find_target(detections: list, target_name: str):
    """Exact match first, then substring. Returns detection dict or None."""
    low = target_name.lower()
    fallback = None
    for d in detections:
        d_low = d["name"].lower()
        if d_low == low:
            return d
        if fallback is None and low in d_low:
            fallback = d
    return fallback


def grasp_and_lift(tx: float, ty: float, tz: float) -> bool:
    """
    Top-down grasp at world-frame (tx, ty, tz) with retry on grasp miss.
    Returns True on success.
    """
    print("Opening gripper", flush=True)
    gripper.open()

    width = 0.0
    for attempt, z_offset in enumerate(GRASP_Z_OFFSETS):
        gz = tz + z_offset
        print(f"Attempt {attempt + 1}: grasp z = {gz:.3f} (offset {z_offset:+.3f})", flush=True)

        # Approach from above
        try:
            _wb_move(tx, ty, gz + APPROACH_CLEARANCE, "approach")
        except Exception as e:
            print(f"  approach failed: {e}", flush=True)
            continue

        # Lower to grasp height
        try:
            _wb_move(tx, ty, gz, "grasp")
        except Exception as e:
            print(f"  lower failed: {e}", flush=True)
            continue

        # Close gripper and check
        print("  closing gripper", flush=True)
        gripper.close()
        has_obj, width = _gripper_has_object()
        print(f"  gripper width after close: {width:.4f} m", flush=True)

        if has_obj:
            break

        print(f"  grasp miss (width={width:.4f}), opening and retrying", flush=True)
        gripper.open()
        # Retreat before retry
        try:
            _wb_move(tx, ty, gz + APPROACH_CLEARANCE, "retreat")
        except Exception:
            pass
    else:
        print(
            f"Result: FAILED – grasp_failed: "
            f"all {len(GRASP_Z_OFFSETS)} attempts missed, last width={width:.4f} m",
            flush=True,
        )
        return False

    # Lift
    lift_z = tz + LIFT_HEIGHT
    print(f"Lifting to z = {lift_z:.3f}", flush=True)
    try:
        _wb_move(tx, ty, lift_z, "lift")
    except Exception as e:
        print(f"Result: FAILED – lift_failed: {e}", flush=True)
        return False

    # Verify EE reached lift height
    try:
        _, _, ee_z = sensors.get_ee_position()
        print(f"  ee_z after lift: {ee_z:.3f} m (target {lift_z:.3f} m)", flush=True)
    except Exception:
        pass

    return True


def pick_object(target_name: str = "obj") -> bool:
    """
    Detect target_name on the counter and pick it up.
    Returns True on success; always prints Result: SUCCESS/FAILED.
    """
    # Detect
    print(f"Detecting scene objects (target='{target_name}')", flush=True)
    try:
        detections = detect_objects(target_name)
    except Exception as e:
        print(f"Result: FAILED – detection_failed: {e}", flush=True)
        return False

    print(f"Detections: {json.dumps(detections)}", flush=True)

    # Locate target
    target = find_target(detections, target_name)
    if target is None:
        names = [d["name"] for d in detections]
        print(f"Result: FAILED – target_not_found: '{target_name}' not in {names}", flush=True)
        return False

    tx, ty, tz = target["position"]
    print(f"Target '{target['name']}' at ({tx:.3f}, {ty:.3f}, {tz:.3f})", flush=True)

    # Validate counter height
    if not (COUNTER_Z_MIN <= tz <= COUNTER_Z_MAX):
        print(
            f"Result: FAILED – not_on_counter: "
            f"z={tz:.3f} outside counter band [{COUNTER_Z_MIN}, {COUNTER_Z_MAX}]",
            flush=True,
        )
        return False

    # Grasp and lift
    ok = grasp_and_lift(tx, ty, tz)
    if not ok:
        return False

    print("Result: SUCCESS", flush=True)
    return True


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "obj"
    ok = pick_object(target)
    sys.exit(0 if ok else 1)
