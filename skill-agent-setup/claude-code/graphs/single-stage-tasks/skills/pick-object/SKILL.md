# pick-object

## Description
Detect the target object on the kitchen counter using YOLO vision, navigate the mobile base to bring the arm within reach, position the end-effector above the object, re-perceive to get a precise position, then descend, grasp, and lift.

## Preconditions (Input State)
- Robot arm is at or near home position
- Gripper is open and activated
- Target object is resting on the kitchen counter and visible to the camera
- Robot is in the kitchen environment (RoboCasa-Pn-P-Counter-To-Cab-v0)

## Postconditions (Output State)
- Object is grasped by the gripper and lifted ~15cm above the counter surface
- Gripper is holding the object (object_detected = True)
- Robot arm is in a stable pose with the object held clear of obstacles

## Success Criteria
- `sensors.is_gripper_holding()` returns True after grasp
- End-effector Z position is at least 15cm above the counter surface
- Object was detected by YOLO with confidence > 0.3 before grasp attempt

## Dependencies
- None (leaf skill)

## Pipeline
1. **Coarse detection**: Use `sensors.find_objects()` to locate the target object in world coordinates
2. **Approach**: Use `wb.move_to_pose()` to move the end-effector above the object (~15cm overhead), gripper open and pointing down
3. **Fine perception**: Pause and re-perceive with `sensors.find_objects()` to get a precise, close-range position estimate
4. **Descend & grasp**: Lower the arm to the object with `arm.move_delta(dz=...)`, then `gripper.grasp()` with moderate force (~100)
5. **Lift**: `arm.move_delta(dz=0.15)` to clear the surface

## Notes
- The two-stage perception (coarse then fine from above) is critical — the close-range view gives much better depth/position accuracy
- Use `sensors.find_objects()` for all perception (not YOLO) — it returns world-frame positions directly
- Use `wb.move_to_pose()` for whole-body motion to reach the object (handles base + arm coordination)
- If grasp fails, retry from step 3 (re-perceive and re-attempt)
