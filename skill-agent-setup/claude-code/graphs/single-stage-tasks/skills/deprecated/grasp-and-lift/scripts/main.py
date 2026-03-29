#!/usr/bin/env python3
"""
grasp-and-lift — top-down grasp primitive using wb.move_to_pose.

Interface
---------
    from main import grasp_and_lift
    success = grasp_and_lift(target_x=0.4, target_y=0.0, target_z=-0.42)

Pipeline
--------
1. Open gripper (pre-grasp clearance)
2. Approach from above: target XY, Z = target_z + approach_clearance,
   top-down orientation via quat=[0, 1, 0, 0] (180° around X → EE points down)
3. Lower to grasp pose: target XYZ, same top-down orientation
4. Close gripper — confirm contact via gripper width > grasp_width_threshold
5. Lift: raise EE by lift_height metres, maintain grip
6. Verify end-effector reached lift height

Uses wb.move_to_pose (collision-free whole-body planner, base + arm).
Prints 'Result: SUCCESS' or 'Result: FAILED – <reason>' to stdout.
Returns True on success, False on failure.
"""

import sys

from robot_sdk import gripper, sensors, wb

# ── Default configuration ──────────────────────────────────────────────────────
APPROACH_CLEARANCE = 0.15   # m — height above grasp point for approach
LIFT_HEIGHT        = 0.20   # m — rise after successful grasp
GRASP_WIDTH_MIN    = 0.010  # m — gripper width below this → empty hand
MOVE_TIMEOUT       = 15     # s — per wb.move_to_pose call
LIFT_Z_TOLERANCE   = 0.10   # m — acceptable undershoot on lift verification

# 180° rotation around X-axis → EE Z-axis points straight down (roll=π)
TOP_DOWN_QUAT = (0, 1, 0, 0)
WB_MASK       = "whole_body"


def _wb_move(x: float, y: float, z: float, label: str, timeout: float) -> None:
    """Move EE to (x, y, z) with top-down orientation via wb.move_to_pose."""
    print(f"  wb.move_to_pose → {label} ({x:.3f}, {y:.3f}, {z:.3f})", flush=True)
    wb.move_to_pose(x=x, y=y, z=z, quat=TOP_DOWN_QUAT, mask=WB_MASK, timeout=timeout)


def _move_or_fail(x: float, y: float, z: float, label: str, timeout: float) -> bool:
    """Call _wb_move and print a FAILED line on exception. Returns False on failure."""
    try:
        _wb_move(x, y, z, label, timeout)
        return True
    except Exception as e:
        print(f"Result: FAILED – {label}_failed: {e}", flush=True)
        return False


def _get_gripper_width_m() -> float:
    """Return current gripper opening in metres (multi-level fallback)."""
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
        pos = state.get("position", 255)   # 0=open, 255=closed
        return (255 - pos) / 255.0 * 0.085
    except Exception:
        return 0.0


def grasp_and_lift(
    target_x: float,
    target_y: float,
    target_z: float,
    approach_clearance: float = APPROACH_CLEARANCE,
    lift_height: float = LIFT_HEIGHT,
    grasp_width_threshold: float = GRASP_WIDTH_MIN,
    move_timeout: float = MOVE_TIMEOUT,
) -> bool:
    """
    Top-down grasp and lift at a known XYZ world-frame position.

    Parameters
    ----------
    target_x, target_y, target_z : float
        Grasp point in metres (world frame).
    approach_clearance : float
        Height above target_z to start descent (default 0.15 m).
    lift_height : float
        Vertical rise after grasping (default 0.20 m).
    grasp_width_threshold : float
        Min gripper width (m) confirming an object was grasped (default 0.01 m).
    move_timeout : float
        Timeout per wb.move_to_pose call in seconds (default 15 s).

    Returns
    -------
    bool
        True on success, False on any failure.
    """
    try:
        print("Step 1: opening gripper", flush=True)
        gripper.open()

        print("Step 2: approaching from above", flush=True)
        if not _move_or_fail(target_x, target_y, target_z + approach_clearance, "approach", move_timeout):
            return False

        print("Step 3: lowering to grasp height", flush=True)
        if not _move_or_fail(target_x, target_y, target_z, "grasp", move_timeout):
            return False

        print("Step 4: closing gripper", flush=True)
        gripper.close()
        width = _get_gripper_width_m()
        print(f"  gripper width after close: {width:.4f} m", flush=True)

        if width <= grasp_width_threshold:
            print(
                f"Result: FAILED – grasp_failed: "
                f"width={width:.4f} ≤ threshold={grasp_width_threshold}",
                flush=True,
            )
            gripper.open()
            return False

        print("Step 5: lifting object", flush=True)
        try:
            _, _, pre_lift_ee_z = sensors.get_ee_position()
        except Exception:
            pre_lift_ee_z = None

        if not _move_or_fail(target_x, target_y, target_z + lift_height, "lift", move_timeout):
            return False

        try:
            _, _, post_lift_ee_z = sensors.get_ee_position()
            if pre_lift_ee_z is not None:
                delta_z = post_lift_ee_z - pre_lift_ee_z
                print(
                    f"  ee_z: {pre_lift_ee_z:.3f} → {post_lift_ee_z:.3f} m "
                    f"(Δ={delta_z:+.3f}, expected ≥{lift_height - LIFT_Z_TOLERANCE:.3f})",
                    flush=True,
                )
                if delta_z < lift_height - LIFT_Z_TOLERANCE:
                    print(
                        f"Result: FAILED – lift_verify_failed: "
                        f"Δz={delta_z:.3f} < {lift_height - LIFT_Z_TOLERANCE:.3f}",
                        flush=True,
                    )
                    return False
        except Exception:
            pass  # trust wb.move_to_pose success

        print("Result: SUCCESS", flush=True)
        return True

    except Exception as e:
        print(f"Result: FAILED – crash: {e}", flush=True)
        return False


if __name__ == "__main__":
    # Usage: python main.py [target_x] [target_y] [target_z]
    tx = float(sys.argv[1]) if len(sys.argv) > 1 else 0.40
    ty = float(sys.argv[2]) if len(sys.argv) > 2 else 0.00
    tz = float(sys.argv[3]) if len(sys.argv) > 3 else -0.42
    ok = grasp_and_lift(tx, ty, tz)
    sys.exit(0 if ok else 1)
