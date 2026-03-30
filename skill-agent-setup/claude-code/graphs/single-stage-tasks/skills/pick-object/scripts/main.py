"""pick-object: Detect target object, navigate to it, grasp and lift."""
import numpy as np
from robot_sdk import arm, base, gripper, sensors, rewind, wb

# ── Step 1: Coarse detection ────────────────────────────────────────
print("=== Step 1: Coarse detection ===")
objects = sensors.find_objects()
target = None
for obj in objects:
    if obj['name'].startswith('obj'):
        target = obj
        break

if target is None:
    print("FAILURE: No target object (name starting with 'obj') found")
    raise SystemExit(1)

print(f"Target: {target['name']} at ({target['x']:.3f}, {target['y']:.3f}, {target['z']:.3f})")
print(f"  size=({target['size_x']:.3f}, {target['size_y']:.3f}, {target['size_z']:.3f})")
print(f"  fixture={target.get('fixture_context', 'unknown')}, dist={target['distance_m']:.3f}m")

obj_x, obj_y, obj_z = target['x'], target['y'], target['z']

# ── Step 2: Move EE above object (pre-grasp) ───────────────────────
print("\n=== Step 2: Navigate to pre-grasp position ===")
# Use top-down orientation: quat=[0, 1, 0, 0] (180° about X, EE Z-axis points down)
pre_grasp_z = obj_z + 0.15
print(f"Pre-grasp target: ({obj_x:.3f}, {obj_y:.3f}, {pre_grasp_z:.3f})")

try:
    wb.move_to_pose(x=obj_x, y=obj_y, z=pre_grasp_z, quat=[0, 1, 0, 0])
    print("Pre-grasp position reached")
except Exception as e:
    print(f"Pre-grasp move failed: {e}, trying with offset")
    # Try slightly higher
    wb.move_to_pose(x=obj_x, y=obj_y, z=pre_grasp_z + 0.05, quat=[0, 1, 0, 0])
    print("Elevated pre-grasp position reached")

# ── Step 3: Fine perception (re-detect from close range) ───────────
print("\n=== Step 3: Fine perception from close range ===")
objects_fine = sensors.find_objects()
target_fine = None
for obj in objects_fine:
    if obj['name'].startswith('obj'):
        target_fine = obj
        break

if target_fine is not None:
    obj_x, obj_y, obj_z = target_fine['x'], target_fine['y'], target_fine['z']
    print(f"Refined position: ({obj_x:.3f}, {obj_y:.3f}, {obj_z:.3f})")
else:
    print("Fine detection missed, using coarse position")

# ── Step 4: Grasp attempt loop ──────────────────────────────────────
print("\n=== Step 4: Grasp attempt ===")

# Compute yaw from arm base to object (world frame)
mocap = sensors.get_mocap_pose()
if mocap is not None:
    arm_base_x, arm_base_y = mocap[0], mocap[1]
else:
    base_pose = sensors.get_base_pose()
    arm_base_x, arm_base_y = base_pose[0], base_pose[1]
yaw = np.arctan2(obj_y - arm_base_y, obj_x - arm_base_x)
print(f"Arm base: ({arm_base_x:.3f}, {arm_base_y:.3f}), yaw to object: {np.degrees(yaw):.1f} deg")

# Try multiple grasp strategies
from transforms3d.euler import euler2quat

grasp_strategies = []
# Strategy 1: Angled45 with various yaw offsets
for yaw_offset in [0, np.radians(30), np.radians(-30)]:
    grasp_yaw = yaw + yaw_offset
    # euler2quat returns (w, x, y, z)
    q = euler2quat(0, 3*np.pi/4, grasp_yaw)
    quat_wxyz = [q[0], q[1], q[2], q[3]]
    gx = obj_x + (-0.02 * np.cos(grasp_yaw))
    gy = obj_y + (-0.02 * np.sin(grasp_yaw))
    grasp_strategies.append(("Angled45", quat_wxyz, gx, gy))

# Strategy 2: TopDown (fallback)
grasp_strategies.append(("TopDown", [0, 1, 0, 0], obj_x, obj_y))

grasped = False
for i, (strategy_name, quat, gx, gy) in enumerate(grasp_strategies):
    if grasped:
        break
    print(f"\nAttempt {i+1}: {strategy_name}")

    try:
        # Open gripper
        gripper.open()

        # Move to pre-grasp (above object)
        pre_z = obj_z + 0.15
        if strategy_name == "Angled45":
            pre_z = obj_z + 0.17
        wb.move_to_pose(x=gx, y=gy, z=pre_z, quat=quat, mask="arm_only")
        print(f"  Pre-grasp reached at z={pre_z:.3f}")

        # Lower to grasp height (center of object)
        grasp_z = obj_z  # object center
        wb.move_to_pose(x=gx, y=gy, z=grasp_z, quat=quat, mask="arm_only")
        print(f"  Lowered to grasp z={grasp_z:.3f}")

        # Grasp
        result = gripper.grasp(force=100)
        print(f"  Grasp result: {result}")

        # Check if holding
        holding = sensors.is_gripper_holding()
        gripper_pos = sensors.get_gripper_position()
        print(f"  Holding: {holding}, gripper_pos: {gripper_pos}")

        if holding or gripper_pos > 5:
            # Lift the object
            print("  Object detected in gripper, lifting...")
            arm.move_delta(dz=0.15)

            # Verify still holding after lift
            holding_after = sensors.is_gripper_holding()
            ee_pos = sensors.get_ee_position()
            print(f"  After lift: holding={holding_after}, ee_z={ee_pos[2]:.3f}")

            if holding_after:
                grasped = True
                print(f"  SUCCESS: Object grasped and lifted with {strategy_name}")
            else:
                print(f"  Object dropped during lift, trying next strategy")
        else:
            print(f"  No object detected, gripper fully closed")
            gripper.open()
    except Exception as e:
        print(f"  Strategy failed with error: {e}")
        # Try to recover
        try:
            if rewind.is_out_of_bounds():
                rewind.rewind_to_safe()
            else:
                arm.move_delta(dz=0.10)
        except Exception:
            pass

# ── Final result ────────────────────────────────────────────────────
if grasped:
    ee_pos = sensors.get_ee_position()
    print(f"\nSUCCESS: Object picked up. EE at ({ee_pos[0]:.3f}, {ee_pos[1]:.3f}, {ee_pos[2]:.3f})")
    print(f"Object lifted ~15cm above counter surface")
else:
    print("\nFAILURE: Could not grasp the object after all attempts")
    raise SystemExit(1)
