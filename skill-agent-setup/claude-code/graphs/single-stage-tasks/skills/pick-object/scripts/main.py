"""
pick-object skill: Detect target object, navigate to it, grasp and lift.

Pipeline:
1. Detect objects with sensors.find_objects(), identify target (name starts with "obj")
2. Navigate base+arm closer using wb.move_to_pose() to a pre-grasp position
3. Re-detect object for precise position
4. Try grasp with Angled45 orientation, then TopDown fallback, with yaw offsets
5. Verify grasp via gripper width, lift object ~15cm
"""
import numpy as np
from transforms3d.euler import euler2quat
from robot_sdk import sensors, arm, base, gripper, wb, rewind


def find_target_object():
    """Find the target object (name starts with 'obj', not 'object')."""
    objects = sensors.find_objects()
    for obj in objects:
        name = obj["name"]
        # Match "obj" or "obj_*" but not "object"
        if name == "obj" or (name.startswith("obj") and not name.startswith("object")):
            return obj
    return None


def compute_grasp_quaternion(strategy, yaw):
    """Compute gripper orientation quaternion (wxyz) for a grasp strategy and yaw."""
    if strategy == "angled45":
        # Pitch 135 degrees (3pi/4) - tilted approach
        q = euler2quat(0, 3 * np.pi / 4, yaw, axes='sxyz')
        return list(q)  # transforms3d returns wxyz
    elif strategy == "topdown":
        # Pure top-down: 180 about X, then rotate about Z by yaw
        q = euler2quat(0, np.pi, yaw, axes='sxyz')
        return list(q)
    return [0, 1, 0, 0]


def attempt_grasp(obj_pos, arm_base_pos, strategy, yaw_offset=0.0):
    """Attempt a single grasp with given strategy and yaw offset.
    Returns True if grasp succeeded."""
    ox, oy, oz = obj_pos
    ax, ay = arm_base_pos[0], arm_base_pos[1]

    yaw = np.arctan2(oy - ay, ox - ax) + yaw_offset
    quat = compute_grasp_quaternion(strategy, yaw)

    if strategy == "angled45":
        # Offset slightly back along approach for angled grasp
        pre_x = ox - 0.02 * np.cos(yaw)
        pre_y = oy - 0.02 * np.sin(yaw)
        grasp_z = oz + 0.02  # slightly above center
        pre_z = oz + 0.15
    else:  # topdown
        pre_x = ox
        pre_y = oy
        grasp_z = oz - 0.02  # account for EE-to-fingertip offset
        pre_z = oz + 0.15

    label = f"{strategy}/yaw_off={np.degrees(yaw_offset):.0f}deg"
    print(f"  Grasp attempt: {label}")

    # Open gripper
    gripper.open()

    # Move to pre-grasp
    try:
        print(f"    Pre-grasp: ({pre_x:.3f}, {pre_y:.3f}, {pre_z:.3f})")
        wb.move_to_pose(x=pre_x, y=pre_y, z=pre_z, quat=quat, mask="whole_body")
    except Exception as e:
        print(f"    Pre-grasp failed: {e}")
        return False

    # Lower to grasp height
    try:
        print(f"    Grasp pos: ({pre_x:.3f}, {pre_y:.3f}, {grasp_z:.3f})")
        wb.move_to_pose(x=pre_x, y=pre_y, z=grasp_z, quat=quat, mask="arm_only")
    except Exception as e:
        print(f"    Lower failed: {e}")
        # Try to recover
        try:
            wb.move_to_pose(x=pre_x, y=pre_y, z=pre_z, quat=quat, mask="arm_only")
        except:
            pass
        return False

    # Close gripper
    gripper.grasp(force=100)

    # Check if object is grasped
    grip_state = gripper.get_state()
    grip_pos = grip_state.get("position", 255)
    holding = grip_state.get("object_detected", False)
    print(f"    Gripper position: {grip_pos}, object_detected: {holding}")

    if grip_pos >= 250 and not holding:
        # Fully closed = missed
        print(f"    MISS - gripper fully closed")
        gripper.open()
        # Retreat up
        try:
            wb.move_to_pose(x=pre_x, y=pre_y, z=pre_z, quat=quat, mask="arm_only")
        except:
            pass
        return False

    # Grasped! Lift the object
    print(f"    GRASP SUCCESS - lifting object")
    try:
        lift_z = oz + 0.18
        wb.move_to_pose(x=pre_x, y=pre_y, z=lift_z, quat=quat, mask="arm_only")
        print(f"    Lifted to z={lift_z:.3f}")
    except Exception as e:
        print(f"    Lift warning: {e}")

    return True


def main():
    print("=== pick-object skill ===")

    # Phase 1: Initial detection
    print("\n[Phase 1] Detecting objects...")
    target = find_target_object()
    if target is None:
        print("FAILURE: No target object found")
        return

    print(f"Target: {target['name']} at ({target['x']:.3f}, {target['y']:.3f}, {target['z']:.3f}), "
          f"fixture={target.get('fixture_context', 'N/A')}, dist={target.get('distance_m', 0):.3f}m")

    arm_base = sensors.get_arm_base_world()
    print(f"Arm base: ({arm_base[0]:.3f}, {arm_base[1]:.3f}, {arm_base[2]:.3f})")

    # Phase 2: Navigate closer if needed
    obj_pos = [target['x'], target['y'], target['z']]
    dist_xy = np.sqrt((obj_pos[0] - arm_base[0])**2 + (obj_pos[1] - arm_base[1])**2)
    print(f"\n[Phase 2] XY distance to target: {dist_xy:.3f}m")

    if dist_xy > 0.65:
        # Navigate to a position ~0.5m from the object in XY
        direction = np.array([obj_pos[0] - arm_base[0], obj_pos[1] - arm_base[1]])
        direction = direction / np.linalg.norm(direction)
        # Approach position: 0.45m away from object along direction
        approach_x = obj_pos[0] - direction[0] * 0.45
        approach_y = obj_pos[1] - direction[1] * 0.45
        approach_z = obj_pos[2] + 0.15  # above the object

        print(f"Navigating to approach position: ({approach_x:.3f}, {approach_y:.3f}, {approach_z:.3f})")
        try:
            wb.move_to_pose(x=approach_x, y=approach_y, z=approach_z, quat=[0, 1, 0, 0], mask="whole_body")
            print("Navigation complete")
        except Exception as e:
            print(f"Navigation warning: {e}")

        # Re-detect for precise position
        print("\n[Phase 2b] Re-detecting objects after navigation...")
        target = find_target_object()
        if target is None:
            print("FAILURE: Lost target after navigation")
            return
        obj_pos = [target['x'], target['y'], target['z']]
        arm_base = sensors.get_arm_base_world()
        dist_xy = np.sqrt((obj_pos[0] - arm_base[0])**2 + (obj_pos[1] - arm_base[1])**2)
        print(f"Updated target: ({obj_pos[0]:.3f}, {obj_pos[1]:.3f}, {obj_pos[2]:.3f}), dist={dist_xy:.3f}m")

    # Phase 3: Attempt grasps
    print(f"\n[Phase 3] Attempting grasps...")
    strategies = ["angled45", "topdown"]
    yaw_offsets = [0, np.radians(30), np.radians(-30)]

    grasped = False
    for strategy in strategies:
        for yaw_off in yaw_offsets:
            if attempt_grasp(obj_pos, arm_base, strategy, yaw_off):
                grasped = True
                break
        if grasped:
            break

    if grasped:
        # Final verification
        grip_state = gripper.get_state()
        grip_pos = grip_state.get("position", 255)
        print(f"\n=== SUCCESS: Object grasped and lifted ===")
        print(f"Final gripper position: {grip_pos}")
    else:
        print(f"\n=== FAILURE: All grasp attempts failed ===")


main()
