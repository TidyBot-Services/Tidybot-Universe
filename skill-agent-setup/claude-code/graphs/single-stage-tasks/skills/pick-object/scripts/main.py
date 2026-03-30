"""
pick-object skill: Detect target object on counter, navigate to it, grasp and lift.

Pipeline:
1. Coarse detection with sensors.find_objects() to get world-frame position
2. Whole-body move to position EE ~15cm above object (top-down orientation)
3. Fine perception from close range for precise position
4. Use wb.move_to_pose to descend to grasp height, then grasp
5. Verify grasp and lift ~15cm
6. Retry with different strategies if grasp fails
"""

import numpy as np
from robot_sdk import arm, gripper, sensors, wb, rewind

# ─── Configuration ───────────────────────────────────────────────────────────
PRE_GRASP_HEIGHT = 0.15      # hover height above object (m)
GRASP_FORCE = 100            # moderate grasp force
MAX_GRASP_ATTEMPTS = 6       # total attempts across strategies
TOPDOWN_QUAT = [0, 1, 0, 0] # wxyz: gripper pointing straight down

# Fixture names to ignore — these are NOT graspable objects
FIXTURE_KEYWORDS = [
    'counter', 'stool', 'wall', 'floor', 'door', 'hinge', 'cabinet',
    'shelf', 'shelves', 'window', 'outlet', 'fridge', 'dishwasher',
    'stack_', 'plant', 'paper_towel', 'utensil_rack', 'utensil_holder',
    'cab_corner', 'knob', 'spout', 'knife_block', 'coffee_machine',
    'distr_', 'inner_box', 'group_0', 'group_base',
]


def is_fixture(name):
    """Check if object name looks like a fixture (not a graspable object)."""
    name_lower = name.lower()
    for kw in FIXTURE_KEYWORDS:
        if kw in name_lower:
            return True
    return False


def find_target_object():
    """Find the nearest graspable object (not a fixture)."""
    objects = sensors.find_objects()
    print(f"[perception] Found {len(objects)} objects total")

    for obj in objects:
        fixture_flag = " [FIXTURE]" if is_fixture(obj['name']) else " [CANDIDATE]"
        print(f"  - {obj['name']} at ({obj['x']:.3f}, {obj['y']:.3f}, {obj['z']:.3f}), "
              f"dist={obj['distance_m']:.2f}m, ctx={obj.get('fixture_context', '?')}, "
              f"size=({obj.get('size_x', 0):.3f}, {obj.get('size_y', 0):.3f}, {obj.get('size_z', 0):.3f})"
              f"{fixture_flag}")

    # Filter out fixtures
    graspable = [o for o in objects if not is_fixture(o['name'])]
    print(f"[perception] Graspable candidates: {len(graspable)}")
    for obj in graspable:
        print(f"  >> {obj['name']} at ({obj['x']:.3f}, {obj['y']:.3f}, {obj['z']:.3f}), "
              f"dist={obj['distance_m']:.2f}m, ctx={obj.get('fixture_context', '?')}")

    if graspable:
        target = graspable[0]
        print(f"[perception] Selected target: {target['name']}")
        return target

    # Fallback: look for 'obj_' or 'object' patterns
    for o in objects:
        if o['name'].startswith('obj_') or o['name'] == 'object':
            print(f"[perception] Fallback target: {o['name']}")
            return o

    if objects:
        print(f"[perception] Last resort target: {objects[0]['name']}")
        return objects[0]

    return None


def compute_grasp_yaw(obj_x, obj_y):
    """Compute yaw from arm base to object."""
    bx, by, _ = sensors.get_base_pose()
    return np.arctan2(obj_y - by, obj_x - bx)


def make_angled45_quat(yaw):
    """Create a 45-degree tilted grasp quaternion (wxyz)."""
    from transforms3d.euler import euler2quat
    return list(euler2quat(0, 3 * np.pi / 4, yaw))


def print_ee_position(label=""):
    """Print current EE position for debugging."""
    pos = sensors.get_ee_position()
    print(f"[debug] EE pos{' (' + label + ')' if label else ''}: ({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f})")
    return pos


def attempt_grasp(obj_x, obj_y, obj_z, strategy, yaw_offset=0.0):
    """
    Attempt a single grasp at the object position with the given strategy.
    Returns True if object is grasped.
    """
    yaw = compute_grasp_yaw(obj_x, obj_y) + yaw_offset
    strategy_name = f"{strategy}(yaw_off={np.degrees(yaw_offset):.0f})"
    print(f"[grasp] Trying {strategy_name}...")

    if strategy == "TopDown":
        quat = TOPDOWN_QUAT
        grasp_x, grasp_y = obj_x, obj_y
        grasp_z = obj_z  # go right to object height
    elif strategy == "Angled45":
        quat = make_angled45_quat(yaw)
        # Offset slightly back along approach direction so fingertips meet object
        grasp_x = obj_x - 0.02 * np.cos(yaw)
        grasp_y = obj_y - 0.02 * np.sin(yaw)
        grasp_z = obj_z + 0.02
    else:
        raise ValueError(f"Unknown strategy: {strategy}")

    pre_x, pre_y = grasp_x, grasp_y
    pre_z = obj_z + PRE_GRASP_HEIGHT

    try:
        # Open gripper
        gripper.open()

        # Move to pre-grasp position using whole-body planner
        print(f"[grasp] Pre-grasp: ({pre_x:.3f}, {pre_y:.3f}, {pre_z:.3f})")
        wb.move_to_pose(x=pre_x, y=pre_y, z=pre_z, quat=quat, mask="whole_body")
        print_ee_position("after pre-grasp")

        # Descend to grasp height using whole-body planner (arm_only since base is positioned)
        print(f"[grasp] Descending to grasp: ({grasp_x:.3f}, {grasp_y:.3f}, {grasp_z:.3f})")
        wb.move_to_pose(x=grasp_x, y=grasp_y, z=grasp_z, quat=quat, mask="arm_only")
        print_ee_position("at grasp height")

        # Grasp: close with full force, then check
        gripper.close(speed=255, force=255)
        gs = gripper.get_state()
        print(f"[grasp] Gripper after close: pos={gs.get('position')}, obj_detected={gs.get('object_detected')}, width_mm={gs.get('position_mm')}")

        # Check if we're actually holding something
        holding = sensors.is_gripper_holding()
        print(f"[grasp] is_gripper_holding: {holding}")

        if holding:
            # Lift the object using whole-body planner
            lift_z = grasp_z + PRE_GRASP_HEIGHT + 0.05
            print(f"[grasp] Lifting to z={lift_z:.3f}")
            wb.move_to_pose(x=grasp_x, y=grasp_y, z=lift_z, quat=quat, mask="arm_only")
            print_ee_position("after lift")

            still_holding = sensors.is_gripper_holding()
            print(f"[grasp] Still holding after lift: {still_holding}")
            return still_holding
        else:
            # Failed - open gripper, retreat upward
            print("[grasp] Missed. Retreating...")
            gripper.open()
            wb.move_to_pose(x=pre_x, y=pre_y, z=pre_z, quat=quat, mask="arm_only")
            return False

    except Exception as e:
        print(f"[grasp] Error during {strategy_name}: {e}")
        # Try to recover
        try:
            gripper.open()
        except Exception:
            pass
        try:
            wb.move_to_pose(x=pre_x, y=pre_y, z=pre_z + 0.05, quat=TOPDOWN_QUAT, mask="arm_only")
        except Exception:
            pass
        return False


def main():
    print("=" * 60)
    print("PICK-OBJECT SKILL")
    print("=" * 60)

    # Initialize gripper
    print("[init] Activating gripper...")
    gripper.activate()
    gripper.open()
    print("[init] Gripper activated and opened")

    # Print initial state
    print_ee_position("initial")
    bx, by, btheta = sensors.get_base_pose()
    print(f"[debug] Base pose: ({bx:.3f}, {by:.3f}, theta={btheta:.3f})")
    gs = gripper.get_state()
    print(f"[debug] Gripper state: pos={gs.get('position')}, activated={gs.get('is_activated')}, obj_detected={gs.get('object_detected')}")

    # ── Step 1: Coarse detection ──────────────────────────────────────────
    print("\n[step 1] Coarse detection...")
    target = find_target_object()
    if target is None:
        print("[ERROR] No objects detected!")
        return

    obj_name = target['name']
    obj_x, obj_y, obj_z = target['x'], target['y'], target['z']
    print(f"\n[step 1] TARGET: {obj_name} at ({obj_x:.3f}, {obj_y:.3f}, {obj_z:.3f})")

    # ── Step 2: Approach — move EE above object ───────────────────────────
    print("\n[step 2] Approaching target (pre-grasp hover)...")
    hover_z = obj_z + PRE_GRASP_HEIGHT
    try:
        wb.move_to_pose(x=obj_x, y=obj_y, z=hover_z, quat=TOPDOWN_QUAT, mask="whole_body")
        print_ee_position("after approach")
    except Exception as e:
        print(f"[step 2] Approach failed: {e}")
        # Try with just arm
        try:
            wb.move_to_pose(x=obj_x, y=obj_y, z=hover_z, quat=TOPDOWN_QUAT, mask="arm_only")
            print_ee_position("after arm_only approach")
        except Exception as e2:
            print(f"[step 2] arm_only also failed: {e2}")

    # ── Step 3: Fine perception ───────────────────────────────────────────
    print("\n[step 3] Fine perception from close range...")
    fine_objects = sensors.find_objects()
    target_fine = None
    for o in fine_objects:
        if o['name'] == obj_name:
            target_fine = o
            break

    if target_fine is not None:
        obj_x, obj_y, obj_z = target_fine['x'], target_fine['y'], target_fine['z']
        print(f"[step 3] Refined: {obj_name} at ({obj_x:.3f}, {obj_y:.3f}, {obj_z:.3f})")
    else:
        graspable_fine = [o for o in fine_objects if not is_fixture(o['name'])]
        if graspable_fine:
            target_fine = graspable_fine[0]
            obj_x, obj_y, obj_z = target_fine['x'], target_fine['y'], target_fine['z']
            obj_name = target_fine['name']
            print(f"[step 3] Switched to: {obj_name} at ({obj_x:.3f}, {obj_y:.3f}, {obj_z:.3f})")
        else:
            print("[step 3] No refined match — using coarse position")

    # ── Step 4 & 5: Grasp attempts ────────────────────────────────────────
    print("\n[step 4] Starting grasp attempts...")

    strategies = [
        ("TopDown", 0.0),
        ("TopDown", np.radians(30)),
        ("TopDown", np.radians(-30)),
        ("Angled45", 0.0),
        ("Angled45", np.radians(30)),
        ("Angled45", np.radians(-30)),
    ]

    for attempt_idx, (strategy, yaw_off) in enumerate(strategies):
        if attempt_idx >= MAX_GRASP_ATTEMPTS:
            break
        print(f"\n--- Attempt {attempt_idx + 1}/{MAX_GRASP_ATTEMPTS} ---")

        # Re-perceive before retries
        if attempt_idx > 0:
            print("[retry] Re-perceiving target...")
            retry_objects = sensors.find_objects()
            for o in retry_objects:
                if o['name'] == obj_name:
                    obj_x, obj_y, obj_z = o['x'], o['y'], o['z']
                    print(f"[retry] Re-found {obj_name} at ({obj_x:.3f}, {obj_y:.3f}, {obj_z:.3f})")
                    break

        success = attempt_grasp(obj_x, obj_y, obj_z, strategy, yaw_off)
        if success:
            ee_pos = sensors.get_ee_position()
            print(f"\n{'=' * 60}")
            print(f"[SUCCESS] Object '{obj_name}' grasped and lifted!")
            print(f"  EE position: ({ee_pos[0]:.3f}, {ee_pos[1]:.3f}, {ee_pos[2]:.3f})")
            print(f"  Gripper holding: {sensors.is_gripper_holding()}")
            print(f"  Strategy: {strategy}, yaw_offset: {np.degrees(yaw_off):.0f}")
            print(f"{'=' * 60}")
            return

    # All attempts failed
    print(f"\n[FAILED] All grasp attempts failed for '{obj_name}'.")
    print("Returning to home position...")
    try:
        wb.go_home()
    except Exception:
        pass


if __name__ == "__main__":
    main()
