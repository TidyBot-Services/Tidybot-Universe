# pick-and-place

## Description
Pick the target object from the counter (using the pick-object skill) and place it into the open cabinet. This is the root skill for the RoboCasa-Pn-P-Counter-To-Cab-v0 task — success is determined by the sim's `_check_success()`.

## Preconditions (Input State)
- Robot arm is at or near home position
- Gripper is open and activated
- Target object is on the kitchen counter
- Cabinet is accessible (may need to be opened or is already open)
- Robot is in the kitchen environment (RoboCasa-Pn-P-Counter-To-Cab-v0)

## Postconditions (Output State)
- Target object has been placed inside the cabinet
- Gripper is open (object released)
- Robot arm is retracted to a safe pose away from the cabinet

## Success Criteria
- Sim task success: `GET http://localhost:5500/task/success` returns success
- Object is no longer on the counter
- Object is inside the cabinet

## Dependencies
- `pick-object`: Provides the grasp-and-lift behavior (object held in gripper, lifted above counter)

## Notes
- After pick-object completes, the object is held ~15cm above the counter
- Need to detect the cabinet location (use `sensors.find_objects()` or `yolo.segment_camera_3d()` to find the cabinet)
- Navigate to the cabinet using `wb.move_to_pose()`, position arm inside cabinet, then `gripper.open()` to release
- May need to open the cabinet door/drawer first if it's closed — check scene state
- After placing, retract the arm to avoid collisions with cabinet
- This is the root skill — tested automatically via sim `_check_success()`
