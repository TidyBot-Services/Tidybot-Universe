# pick-object

## Description
Detect the target object on the counter using `sensors.find_objects()`, navigate the base within arm reach using `wb.move_to_pose()`, then grasp the object with the gripper. The skill should handle approach, close-range re-detection, descent, and grasp verification.

## Preconditions (Input State)
- Robot arm is at or near home position
- Gripper is open
- Target object is resting on the counter and visible to cameras
- Sim is running with `RoboCasa-Pn-P-Counter-To-Cab-v0` task

## Postconditions (Output State)
- Object is grasped by the gripper (gripper closed around object, `object_detected=True` or gripper width between ~5-70mm)
- Object is lifted ~10-15cm above the counter surface
- Robot is stationary and holding the object

## Success Criteria
- Gripper width > 5mm and < 75mm (object held, not empty close)
- Object is no longer resting on the counter (lifted)
- No collisions or reflex errors during execution

## Dependencies
- None (leaf skill)

## Notes
- Use `sensors.find_objects()` to get world-frame object positions
- In RoboCasa, the target object name starts with `"obj"` but NOT `"object"` — filter carefully (e.g. match `obj_` or exact `obj`)
- Use `wb.move_to_pose()` for coordinated base+arm movement to reach the object
- **Two-phase perception**: detect from afar for rough position, navigate closer, re-detect for precise position before grasping
- Account for EE-to-fingertip offset when computing grasp height (EE frame is at wrist, not fingertips — may need to lower ~3-5cm below object center)
- Use `gripper.grasp(force=100)` and verify with `sensors.get_gripper_position()` or `sensors.is_gripper_holding()`
- Gripper commands now block until settled — close/grasp will wait for physics to finish
- Try multiple grasp orientations if the first attempt fails (angled, top-down)
- Lift after grasping to confirm object is held
