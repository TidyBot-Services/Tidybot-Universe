"""
grasp-and-lift — top-down grasp primitive using wb.move_to_pose.

Interface
---------
    from main import grasp_and_lift
    success = grasp_and_lift(target_x=0.4, target_y=0.0, target_z=-0.42)

Pipeline
--------
1. Open gripper (pre-grasp clearance)
2. Approach from above: target XY, Z = grasp_z + approach_clearance,
   top-down orientation via quat=[0, 1, 0, 0] (w=0, x=1, y=0, z=0 → 180° around X)
3. Lower to grasp pose: target XYZ, same top-down orientation
4. Close gripper — confirm contact via gripper width > grasp_width_threshold
5. Lift: raise EE by lift_height metres, maintain grip
6. Verify end-effector reached lift height

Uses wb.move_to_pose (collision-free whole-body planner, base + arm).
Prints 'Result: SUCCESS' or 'Result: FAILED – <reason>' to stdout.
Returns True on success, False on failure.
"""

import sys

from robot_sdk import gripper, sensors, rewind, wb

# ── Default configuration ──────────────────────────────────────────────────────
APPROACH_CLEARANCE   = 0.15    # m — height above grasp point for approach
LIFT_HEIGHT          = 0.20    # m — rise after successful grasp
GRASP_WIDTH_MIN      = 0.010   # m — gripper width below this → empty hand
MOVE_TIMEOUT         = 15      # s — per wb.move_to_pose call (planner needs more time)
LIFT_Z_TOLERANCE     = 0.05    # m — acceptable undershoot on lift verification

# Top-down gripper orientation: quat [w, x, y, z] = [0, 1, 0, 0]
# Represents 180° rotation around X-axis → EE Z-axis points straight down
TOP_DOWN_QUAT        = [0, 1, 0, 0]

# Allow whole-body motion so the base can reposition to reach the target
WB_MASK              = "whole_body"


# ── Internal helpers ───────────────────────────────────────────────────────────

def _wb_move(x: float, y: float, z: float, label: str, timeout: float) -> None:
    """Move EE to (x, y, z) using wb.move_to_pose with top-down orientation."""
    print(f"  wb.move_to_pose → {label} ({x:.3f}, {y:.3f}, {z:.3f})", flush=True)
    wb.move_to_pose(
        x=x, y=y, z=z,
        quat=TOP_DOWN_QUAT,
        mask=WB_MASK,
        timeout=timeout,
    )


def _get_gripper_width_m() -> float:
    """
    Return the current gripper opening in metres.

    Preference order:
      1. sensors.get_gripper_width()  — calibrated, returns metres or None
      2. gripper.get_state()["position_mm"] / 1000
      3. Linear interpolation from raw position (0=open≈85 mm, 255=closed)
    """
    # 1. Calibrated sensor reading
    try:
        w = sensors.get_gripper_width()
        if w is not None:
            return w
    except Exception:
        pass

    # 2. position_mm from gripper state
    try:
        state = gripper.get_state()
        mm = state.get("position_mm")
        if mm is not None:
            return mm / 1000.0
    except Exception:
        pass

    # 3. Raw position fallback (Robotiq 2F-85: 85 mm stroke)
    try:
        state = gripper.get_state()
        pos = state.get("position", 255)        # 0 = open, 255 = closed
        return (255 - pos) / 255.0 * 0.085
    except Exception:
        return 0.0


# ── Public skill interface ─────────────────────────────────────────────────────

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
    Top-down grasp and lift at a known XYZ position in the world frame.

    Uses wb.move_to_pose for collision-free whole-body motion planning.
    The end-effector is always oriented top-down (quat=[0,1,0,0]).

    Parameters
    ----------
    target_x, target_y, target_z : float
        Grasp point in metres (world / robot base frame).
    approach_clearance : float
        Distance above grasp_z to start the descent (default 0.15 m).
    lift_height : float
        Distance to rise after grasping (default 0.20 m).
    grasp_width_threshold : float
        Minimum gripper width (m) that confirms an object was grasped (default 0.01 m).
    move_timeout : float
        Timeout per wb.move_to_pose call in seconds (default 15 s).

    Returns
    -------
    bool
        True on success, False on any failure.
    """
    try:
        # ── Step 1: open gripper ──────────────────────────────────────────────
        print("Step 1: opening gripper", flush=True)
        gripper.open()

        # ── Step 2: approach from above ───────────────────────────────────────
        print("Step 2: approaching from above", flush=True)
        approach_z = target_z + approach_clearance
        try:
            _wb_move(target_x, target_y, approach_z, "approach", move_timeout)
        except Exception as e:
            print(f"Result: FAILED – approach_failed: {e}", flush=True)
            return False

        # ── Step 3: lower to grasp height ─────────────────────────────────────
        print("Step 3: lowering to grasp height", flush=True)
        try:
            _wb_move(target_x, target_y, target_z, "grasp", move_timeout)
        except Exception as e:
            print(f"Result: FAILED – lower_failed: {e}", flush=True)
            return False

        # ── Step 4: close gripper and confirm contact ─────────────────────────
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

        # ── Step 5: lift ──────────────────────────────────────────────────────
        print("Step 5: lifting object", flush=True)
        lift_z = target_z + lift_height
        try:
            _wb_move(target_x, target_y, lift_z, "lift", move_timeout)
        except Exception as e:
            print(f"Result: FAILED – lift_failed: {e}", flush=True)
            return False

        # ── Verify arm reached lift height ────────────────────────────────────
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
            # Sensor read failure — trust the wb.move_to_pose success
            pass

        print("Result: SUCCESS", flush=True)
        return True

    except Exception as e:
        print(f"Result: FAILED – crash: {e}", flush=True)
        return False


# ── CLI entry-point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Usage: python main.py [target_x] [target_y] [target_z]
    tx = float(sys.argv[1]) if len(sys.argv) > 1 else 0.40
    ty = float(sys.argv[2]) if len(sys.argv) > 2 else 0.00
    tz = float(sys.argv[3]) if len(sys.argv) > 3 else -0.42
    ok = grasp_and_lift(tx, ty, tz)
    sys.exit(0 if ok else 1)
