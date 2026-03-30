"""
pick-object skill: Detect target object on counter, navigate to it, and grasp it.

Pipeline:
1. Coarse detection via sensors.find_objects() — find "obj" target
2. Navigate + position arm overhead via wb.move_to_pose()
3. Fine perception — re-detect for precise position
4. Descend, grasp, and lift
5. Retry with different grasp strategies if needed
"""
import numpy as np
from transforms3d.euler import euler2quat
from robot_sdk import arm, base, gripper, sensors, wb, rewind


# ── Helpers ──────────────────────────────────────────────────────────────────

def find_target_object():
    """Find the target object (name starts with 'obj') from scene objects."""
    objects = sensors.find_objects()
    targets = [o for o in objects if o["name"].startswith("obj")]
    if not targets:
        print("WARNING: No target object found")
        return None
    # Pick the one closest to the robot
    target = min(targets, key=lambda o: o.get("distance_m", float("inf")))
    return target


def make_grasp_quat(strategy, yaw):
    """Return quaternion [w,x,y,z] for a given grasp strategy and yaw."""
    if strategy == "angled45":
        # 135° pitch = 3π/4, tilted approach
        q = euler2quat(0, 3 * np.pi / 4, yaw, axes="sxyz")
    elif strategy == "topdown":
        # Pure top-down: 180° about X then rotate yaw about Z
        q = euler2quat(0, np.pi, yaw, axes="sxyz")
    else:
        raise ValueError(f"Unknown strategy: {strategy}")
    return list(q)  # already [w,x,y,z] from transforms3d


def grasp_offset(strategy, yaw):
    """XYZ offset from object center to pre-grasp position."""
    if strategy == "angled45":
        return np.array([-0.02 * np.cos(yaw), -0.02 * np.sin(yaw), 0.02])
    else:  # topdown
        return np.array([0.0, 0.0, 0.0])


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    # Ensure gripper is open and ready
    gripper.open()
    print("Gripper opened")

    # Step 1: Coarse detection
    target = find_target_object()
    if target is None:
        print("FAILURE: Target object not found in scene")
        return
    obj_pos = np.array([target["x"], target["y"], target["z"]])
    print(f"Step 1 — Coarse detection: {target['name']} at ({obj_pos[0]:.3f}, {obj_pos[1]:.3f}, {obj_pos[2]:.3f}), "
          f"fixture={target.get('fixture_context', 'N/A')}")

    # Step 2: Move arm overhead (15cm above object)
    overhead_z = obj_pos[2] + 0.15
    print(f"Step 2 — Moving to overhead position: ({obj_pos[0]:.3f}, {obj_pos[1]:.3f}, {overhead_z:.3f})")
    # Use top-down quat for approach
    bpose = sensors.get_base_pose()
    base_xy = np.array([bpose[0], bpose[1]])
    approach_yaw = np.arctan2(obj_pos[1] - base_xy[1], obj_pos[0] - base_xy[0])
    overhead_quat = make_grasp_quat("topdown", approach_yaw)
    try:
        wb.move_to_pose(x=obj_pos[0], y=obj_pos[1], z=overhead_z, quat=overhead_quat)
        print("  Overhead position reached")
    except Exception as e:
        print(f"  WARNING: wb.move_to_pose overhead failed: {e}")
        # Try without quat constraint
        try:
            wb.move_to_pose(x=obj_pos[0], y=obj_pos[1], z=overhead_z)
            print("  Overhead position reached (no quat constraint)")
        except Exception as e2:
            print(f"  ERROR: Could not reach overhead: {e2}")
            return

    # Step 3: Fine perception — re-detect for precise position
    target_fine = find_target_object()
    if target_fine is not None:
        obj_pos = np.array([target_fine["x"], target_fine["y"], target_fine["z"]])
        print(f"Step 3 — Fine detection: {target_fine['name']} at ({obj_pos[0]:.3f}, {obj_pos[1]:.3f}, {obj_pos[2]:.3f})")
    else:
        print("Step 3 — Fine detection failed, using coarse position")

    # Recompute yaw after base may have moved
    bpose = sensors.get_base_pose()
    base_xy = np.array([bpose[0], bpose[1]])
    approach_yaw = np.arctan2(obj_pos[1] - base_xy[1], obj_pos[0] - base_xy[0])

    # Step 4: Attempt grasp with multiple strategies and yaw offsets
    strategies = ["angled45", "topdown"]
    yaw_offsets = [0, np.radians(30), np.radians(-30)]
    grasp_success = False

    for strategy in strategies:
        if grasp_success:
            break
        for yaw_off in yaw_offsets:
            if grasp_success:
                break
            grasp_yaw = approach_yaw + yaw_off
            quat = make_grasp_quat(strategy, grasp_yaw)
            offset = grasp_offset(strategy, grasp_yaw)
            grasp_pos = obj_pos + offset

            print(f"Step 4 — Grasp attempt: strategy={strategy}, yaw_offset={np.degrees(yaw_off):.0f}°")

            try:
                # Move to pre-grasp (above grasp point)
                pre_z = grasp_pos[2] + 0.15
                gripper.open()
                wb.move_to_pose(x=float(grasp_pos[0]), y=float(grasp_pos[1]), z=float(pre_z), quat=quat)
                print(f"  Pre-grasp reached at z={pre_z:.3f}")

                # Descend to grasp height
                # Apply z-offset to account for EE frame being at wrist, not fingertips
                # The Panda gripper + Robotiq adds ~10-12cm from EE frame to fingertip
                # We want fingertips at object center, so lower EE by ~5cm below object center
                grasp_z = grasp_pos[2] - 0.04
                wb.move_to_pose(x=float(grasp_pos[0]), y=float(grasp_pos[1]), z=float(grasp_z), quat=quat)
                print(f"  Descended to grasp height z={grasp_z:.3f} (obj_z={grasp_pos[2]:.3f})")

                # Additional lower via arm delta for fine adjustment
                arm.move_delta(dz=-0.02, frame="base")
                ee_now = sensors.get_ee_position()
                print(f"  Fine-lowered to EE z={ee_now[2]:.3f}")

                # Close gripper
                holding = gripper.grasp()
                gstate = gripper.get_state()
                gwidth = gstate.get("position_mm", 0)
                print(f"  Grasp result: holding={holding}, gripper_width={gwidth:.1f}mm")

                if holding or gwidth > 5:
                    # Lift well above counter
                    lift_z = obj_pos[2] + 0.20
                    arm.move_delta(dz=0.15, frame="base")
                    print(f"  Lifted via arm delta")
                    print(f"  Lifted to z={lift_z:.3f}")

                    # Verify holding
                    is_holding = sensors.is_gripper_holding()
                    gstate2 = gripper.get_state()
                    print(f"  Holding check: is_gripper_holding={is_holding}, width={gstate2.get('position_mm', 0):.1f}mm")

                    if is_holding or gstate2.get("position_mm", 0) > 5:
                        grasp_success = True
                        print(f"  GRASP SUCCESS with {strategy}, yaw_offset={np.degrees(yaw_off):.0f}°")
                    else:
                        print(f"  Object dropped after lift, trying next strategy")
                        gripper.open()
                else:
                    print(f"  Gripper closed empty (width={gwidth:.1f}mm), trying next")
                    gripper.open()

            except Exception as e:
                print(f"  Attempt failed with error: {e}")
                # Try to recover
                try:
                    if rewind.is_out_of_bounds():
                        rewind.rewind_to_safe()
                        print("  Rewound to safe position")
                    gripper.open()
                except:
                    pass

    # Final result
    if grasp_success:
        ee_pos = sensors.get_ee_position()
        print(f"\nSUCCESS: Object grasped and lifted. EE at z={ee_pos[2]:.3f}")
    else:
        print(f"\nFAILURE: Could not grasp object after all attempts")


if __name__ == "__main__":
    main()
