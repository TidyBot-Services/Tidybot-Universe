#!/usr/bin/env python3
"""
pick-object — Detect target object on counter, navigate to it, and grasp it.

Pipeline:
1. sensors.find_objects() → filter fixtures → prefer counter context
2. Select target by name (exact then substring) or nearest graspable object
3. TopDown (3 z-offsets) then Angled45 (3 yaw offsets) grasp strategies
4. On success: lift 0.20 m. On all misses: FAILED.

Usage:
    python main.py [target_name]   # default: pick nearest counter object
"""

import math
import sys
import time

from transforms3d.euler import euler2quat

from robot_sdk import gripper, sensors, wb

APPROACH_CLEARANCE  = 0.15
LIFT_HEIGHT         = 0.20
COUNTER_Z_MIN       = 0.65
COUNTER_Z_MAX       = 1.15
MOVE_TIMEOUT        = 20
GRIPPER_SETTLE_S    = 0.6
GRIPPER_MIN_M       = 0.005
GRIPPER_MAX_M       = 0.083
GRASP_Z_OFFSET      = 0.02   # bias above centroid; top-down grasp contacts best slightly above centre

TOP_DOWN_QUAT      = (0, 1, 0, 0)   # 180° around X → EE Z-axis points straight down
WB_MASK            = "whole_body"

_TOPDOWN_Z_OFFSETS = [0.02, 0.03, 0.01]
_YAW_OFFSETS       = [0.0, math.radians(30), math.radians(-30)]

_FIXTURE_KEYWORDS = (
    "floor", "wall", "ceiling", "counter", "cab_corner", "cabinet",
    "hinge", "door", "shelf", "shelves", "rack", "dishwasher",
    "paper_towel", "spout", "stack_", "utensil", "fridge", "stove",
    "oven", "microwave", "window", "light",
)


def _is_fixture(name: str) -> bool:
    low = name.lower()
    return any(kw in low for kw in _FIXTURE_KEYWORDS)


def _gripper_width_m() -> float:
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


def _has_object() -> tuple:
    time.sleep(GRIPPER_SETTLE_S)
    w = _gripper_width_m()
    return GRIPPER_MIN_M < w < GRIPPER_MAX_M, w


def _wb_move(x: float, y: float, z: float, quat, label: str) -> None:
    print(f"  → {label}: ({x:.3f}, {y:.3f}, {z:.3f})", flush=True)
    wb.move_to_pose(x=x, y=y, z=z, quat=quat, mask=WB_MASK, timeout=MOVE_TIMEOUT)


def _close_and_check(retreat_x: float, retreat_y: float, retreat_z: float, quat) -> bool:
    """Close gripper; if miss, open and retreat to pre-grasp pose. Returns True if grasped."""
    gripper.close()
    grasped, width = _has_object()
    print(f"  gripper width: {width:.4f} m  grasped={grasped}", flush=True)
    if grasped:
        return True
    gripper.open()
    try:
        _wb_move(retreat_x, retreat_y, retreat_z, quat, "retreat")
    except Exception:
        pass
    return False


def _lift(x: float, y: float, from_z: float, quat) -> bool:
    lift_z = from_z + LIFT_HEIGHT
    print(f"Lifting to z={lift_z:.3f}", flush=True)
    try:
        _wb_move(x, y, lift_z, quat, "lift")
        return True
    except Exception as e:
        print(f"Result: FAILED – lift_failed: {e}", flush=True)
        return False


def _find_graspable() -> list:
    raw = sensors.find_objects()
    if not raw:
        return []

    candidates = []
    for o in raw:
        name = o.get("name", "").strip()
        try:
            x, y, z = float(o["x"]), float(o["y"]), float(o["z"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        if not name or _is_fixture(name):
            continue
        candidates.append(o)

    if not candidates:
        return []

    candidates.sort(key=lambda o: o.get("distance_m", 9999))
    ctx_counter  = [o for o in candidates if "counter" in o.get("fixture_context", "").lower()]
    height_match = [o for o in candidates if COUNTER_Z_MIN <= float(o["z"]) <= COUNTER_Z_MAX]
    return ctx_counter or height_match or candidates


def _select_target(objects: list, target_name: str | None):
    if target_name is None:
        return objects[0] if objects else None
    low = target_name.lower()
    for o in objects:
        if o["name"].lower() == low:
            return o
    for o in objects:
        if low in o["name"].lower():
            return o
    return None


def _topdown_attempt(tx: float, ty: float, tz: float, z_offset: float) -> bool:
    gz = tz + z_offset
    try:
        _wb_move(tx, ty, gz + APPROACH_CLEARANCE, TOP_DOWN_QUAT, "topdown-approach")
        _wb_move(tx, ty, gz, TOP_DOWN_QUAT, "topdown-grasp")
    except Exception as e:
        print(f"  topdown move failed: {e}", flush=True)
        return False
    return _close_and_check(tx, ty, gz + APPROACH_CLEARANCE, TOP_DOWN_QUAT)


def _angled45_attempt(tx: float, ty: float, tz: float, yaw: float) -> tuple:
    """Returns (grasped, px, py, pz, quat) so caller can lift from the correct grasp pose."""
    quat = euler2quat(0, 3 * math.pi / 4, yaw, axes="sxyz")
    px = tx - 0.02 * math.cos(yaw)
    py = ty - 0.02 * math.sin(yaw)
    pz = tz + GRASP_Z_OFFSET
    try:
        _wb_move(px, py, pz + APPROACH_CLEARANCE, quat, "angled-approach")
        _wb_move(px, py, pz, quat, "angled-grasp")
    except Exception as e:
        print(f"  angled move failed: {e}", flush=True)
        return False, px, py, pz, quat
    grasped = _close_and_check(px, py, pz + APPROACH_CLEARANCE, quat)
    return grasped, px, py, pz, quat


def pick_object(target_name: str | None = None) -> bool:
    """
    Detect and pick an object from the counter.
    target_name: object name to pick; None = nearest graspable counter object.
    """
    print(f"Scanning scene (target={target_name!r})…", flush=True)
    objects = _find_graspable()
    if not objects:
        print("Result: FAILED – no_objects_detected", flush=True)
        return False

    print(f"  {len(objects)} candidate(s):", flush=True)
    for o in objects[:6]:
        print(
            f"    {o['name']:40s}  ctx={o.get('fixture_context',''):20s}"
            f"  z={o.get('z', 0):.3f}",
            flush=True,
        )

    target = _select_target(objects, target_name)
    if target is None:
        names = [o["name"] for o in objects]
        print(f"Result: FAILED – target_not_found: '{target_name}' not in {names}", flush=True)
        return False

    tx, ty, tz = float(target["x"]), float(target["y"]), float(target["z"])
    print(f"Target '{target['name']}' at ({tx:.3f}, {ty:.3f}, {tz:.3f})", flush=True)

    try:
        base_pose = sensors.get_base_pose()
        bx, by = float(base_pose["x"]), float(base_pose["y"])
    except Exception:
        bx, by = 0.0, 0.0
    approach_yaw = math.atan2(ty - by, tx - bx)

    gripper.open()

    for z_off in _TOPDOWN_Z_OFFSETS:
        print(f"\n[TopDown] z_offset={z_off:+.2f}", flush=True)
        if _topdown_attempt(tx, ty, tz, z_offset=z_off):
            if not _lift(tx, ty, tz + z_off, TOP_DOWN_QUAT):
                return False
            print(f"Result: SUCCESS – picked {target['name']}", flush=True)
            return True

    for yaw_off in _YAW_OFFSETS:
        yaw = approach_yaw + yaw_off
        print(f"\n[Angled45] yaw={math.degrees(yaw):.1f}°", flush=True)
        grasped, px, py, pz, quat = _angled45_attempt(tx, ty, tz, yaw=yaw)
        if grasped:
            if not _lift(px, py, pz, quat):
                return False
            print(f"Result: SUCCESS – picked {target['name']}", flush=True)
            return True

    print(
        f"Result: FAILED – all_attempts_failed: "
        f"{len(_TOPDOWN_Z_OFFSETS)} TopDown + {len(_YAW_OFFSETS)} Angled45",
        flush=True,
    )
    return False


if __name__ == "__main__":
    tname = sys.argv[1] if len(sys.argv) > 1 else None
    ok = pick_object(target_name=tname)
    sys.exit(0 if ok else 1)
