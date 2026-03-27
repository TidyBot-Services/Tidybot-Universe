#!/usr/bin/env python3
"""
place-in-cabinet — Placement primitive.

Assumes the robot arm is already holding an object (gripper closed, contact
confirmed).  Finds the target cabinet in the scene, plans a two-phase approach
(above → lower-in), releases the object, and retracts.

Pipeline
--------
1. sensors.find_objects() → locate cabinet, derive interior XYZ position.
2. Move EE above cabinet interior: (place_x, place_y, place_z + clearance),
   straight-down orientation (roll=π).
3. Lower EE to placement height: (place_x, place_y, place_z).
4. Open gripper; confirm width ≥ open_width_threshold.
5. Retract: raise EE back to approach clearance height.

Output (parsed by tests/run_trials.py)
---------------------------------------
  Cabinet position: (X.XXX, Y.YYY, Z.ZZZ)
  gripper width after open: X.XXXX m
  Result: SUCCESS  |  Result: FAILED – <reason>

Usage
-----
    from main import place_in_cabinet
    success = place_in_cabinet()
"""

import math
import sys

from robot_sdk import arm, gripper, sensors, rewind, wb

# ---------------------------------------------------------------------------
# Default configuration (matches SKILL.md and test constants)
# ---------------------------------------------------------------------------
APPROACH_CLEARANCE   = 0.15    # m — height above placement point for approach
OPEN_WIDTH_THRESHOLD = 0.030   # m — min gripper width after open → object released
MOVE_TIMEOUT         = 10      # s — per arm.move_to_pose call

# roll=π → end-effector Z-axis points straight down (top-down orientation)
STRAIGHT_DOWN_ROLL = math.pi


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _move(x: float, y: float, z: float, label: str, timeout: float = MOVE_TIMEOUT) -> None:
    """
    Move EE to world-frame (x, y, z) with top-down orientation (blocking).
    Uses wb.move_to_pose (whole-body planner) so the base moves if needed.
    Falls back to arm.move_to_pose for small arm-only adjustments.
    """
    print(f"  wb.move_to_pose → {label} ({x:.3f}, {y:.3f}, {z:.3f})", flush=True)
    wb.move_to_pose(x=x, y=y, z=z)


def _gripper_width_m() -> float:
    """
    Return current gripper opening in metres (best-effort).

    Preference order:
      1. sensors.get_gripper_width()  — calibrated, metres or None
      2. gripper.get_state()["position_mm"] / 1000
      3. Linear interpolation from raw position (0=open≈85 mm, 255=closed)
    """
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
    except Exception:
        pass

    try:
        state = gripper.get_state()
        pos = state.get("position", 255)   # 0 = open, 255 = closed
        return (255 - pos) / 255.0 * 0.085
    except Exception:
        return 0.0


def _extract_position(detection: dict):
    """
    Return (x, y, z) floats from a sensors.find_objects() detection dict.

    Handles both:
    • SDK canonical format: separate "x", "y", "z" keys
    • Wrapped format (detect-scene-objects output): "position": [x, y, z]
    Raises ValueError if coordinates are missing or non-finite.
    """
    # Prefer "position" list if present and valid
    pos = detection.get("position")
    if pos and len(pos) >= 3:
        x, y, z = float(pos[0]), float(pos[1]), float(pos[2])
    else:
        # Fall back to individual x/y/z keys (raw SDK format)
        try:
            x = float(detection["x"])
            y = float(detection["y"])
            z = float(detection["z"])
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"missing coordinate keys in detection {detection!r}: {e}")

    if not all(math.isfinite(v) for v in (x, y, z)):
        raise ValueError(f"non-finite coordinate(s): ({x}, {y}, {z})")

    return x, y, z


# ---------------------------------------------------------------------------
# Public skill interface
# ---------------------------------------------------------------------------

def place_in_cabinet(
    approach_clearance: float = APPROACH_CLEARANCE,
    open_width_threshold: float = OPEN_WIDTH_THRESHOLD,
    move_timeout: float = MOVE_TIMEOUT,
) -> bool:
    """
    Place the currently held object into a cabinet detected in the scene.

    Pre-condition: gripper is closed and holding an object.

    Parameters
    ----------
    approach_clearance : float
        Metres above placement point for the pre-place approach (default 0.15 m).
    open_width_threshold : float
        Minimum gripper width (m) after opening that confirms object release
        (default 0.030 m).
    move_timeout : float
        Timeout per arm.move_to_pose call in seconds (default 10 s).

    Returns
    -------
    bool
        True on success, False on any failure.
        Always prints "Result: SUCCESS" or "Result: FAILED – <tag>: <detail>".
    """
    try:
        # ── Step 1: detect cabinet ────────────────────────────────────────────
        print("Step 1: detecting cabinet with sensors.find_objects()", flush=True)
        try:
            detections = sensors.find_objects()
            # Targeted pass to boost cabinet recall using multiple possible names
            targeted = sensors.find_objects(
                target_names=["cabinet", "inner_box", "cab", "shelf", "shelves"]
            )
            seen = {d.get("name") for d in detections}
            for d in targeted:
                if d.get("name") not in seen:
                    detections.append(d)
                    seen.add(d.get("name"))

            print(f"  find_objects returned {len(detections)} detection(s)", flush=True)
            for d in detections:
                pos_repr = d.get("position") or [d.get("x","?"), d.get("y","?"), d.get("z","?")]
                print(f"    • {d.get('name', '?')} @ {pos_repr}", flush=True)
        except Exception as e:
            print(f"Result: FAILED – crash: find_objects() raised: {e}", flush=True)
            return False

        # ── Step 2: locate cabinet interior in detections ─────────────────────
        # Priority order for cabinet interior candidates:
        #   1. "inner_box"    – explicit interior marker used by some sim scenes
        #   2. "cabinet"      – canonical cabinet name
        #   3. "cab_"         – prefix used by RoboCasa cabinet fixture groups
        #   4. "shelf"/"shelves" – open-shelf fixtures

        CABINET_KEYWORDS = ["inner_box", "cabinet", "cab_", "shelf", "shelves"]

        cabinet = None
        for kw in CABINET_KEYWORDS:
            for d in detections:
                name_lower = d.get("name", "").lower()
                if kw in name_lower:
                    cabinet = d
                    break
            if cabinet is not None:
                break

        if cabinet is None:
            names = [d.get("name", "?") for d in detections]
            print(
                f"Result: FAILED – cabinet_not_found: "
                f"no cabinet-like detection in {names}",
                flush=True,
            )
            return False

        # ── Step 3: extract and validate cabinet interior position ─────────────
        try:
            place_x, place_y, place_z = _extract_position(cabinet)
        except ValueError as e:
            print(
                f"Result: FAILED – no_interior_position: "
                f"invalid cabinet position: {e}",
                flush=True,
            )
            return False

        print(f"  cabinet interior target: ({place_x:.3f}, {place_y:.3f}, {place_z:.3f})",
              flush=True)

        # ── Step 4: approach — move above cabinet interior ─────────────────────
        print("Step 2: approaching above cabinet interior", flush=True)
        try:
            _move(place_x, place_y, place_z + approach_clearance,
                  "place-approach", move_timeout)
        except Exception as e:
            print(f"Result: FAILED – approach_failed: {e}", flush=True)
            return False

        # ── Step 5: lower into cabinet ─────────────────────────────────────────
        print("Step 3: lowering into cabinet", flush=True)
        try:
            _move(place_x, place_y, place_z, "place-lower", move_timeout)
        except Exception as e:
            print(f"Result: FAILED – lower_failed: {e}", flush=True)
            return False

        # ── Step 6: open gripper to release object ─────────────────────────────
        print("Step 4: opening gripper to release object", flush=True)
        try:
            gripper.open()
        except Exception as e:
            print(f"Result: FAILED – release_failed: gripper.open() raised: {e}", flush=True)
            return False

        post_width = _gripper_width_m()
        print(f"  gripper width after open: {post_width:.4f} m", flush=True)

        if post_width < open_width_threshold:
            print(
                f"Result: FAILED – release_failed: "
                f"gripper width {post_width:.4f} < threshold {open_width_threshold} "
                f"(object may still be gripped)",
                flush=True,
            )
            return False

        # ── Step 7: retract arm clear of cabinet ───────────────────────────────
        print("Step 5: retracting arm", flush=True)
        try:
            _move(place_x, place_y, place_z + approach_clearance,
                  "retract", move_timeout)
        except Exception as e:
            print(f"Result: FAILED – retract_failed: {e}", flush=True)
            return False

        print("Result: SUCCESS", flush=True)
        return True

    except Exception as e:
        print(f"Result: FAILED – crash: {e}", flush=True)
        return False


# ---------------------------------------------------------------------------
# CLI entry-point — submitted directly via POST /code/execute
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    ok = place_in_cabinet()
    sys.exit(0 if ok else 1)
