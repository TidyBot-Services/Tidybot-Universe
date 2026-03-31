# pick-and-place

## Description
Pick the target object from the counter and place it into the cabinet. Composes the `pick-object` skill for grasping, then navigates to the cabinet and places the object inside. This is the root skill for the `RoboCasa-Pn-P-Counter-To-Cab-v0` task.

## Preconditions (Input State)
- Robot arm is at or near home position
- Gripper is open
- Target object is on the counter
- Cabinet is accessible (may need to be opened)
- Sim is running with `RoboCasa-Pn-P-Counter-To-Cab-v0` task

## Postconditions (Output State)
- Object has been placed inside the cabinet
- Gripper is open (released the object)
- Robot arm retracted from the cabinet
- Task success condition met (`GET /task/success` returns true)

## Success Criteria
- `GET http://localhost:5500/task/success` returns success (ground-truth sim check)
- Object is inside the cabinet (not on counter, not on floor)
- Gripper is open after placement
- No collisions or reflex errors

## Dependencies
- `pick-object` — provides: object grasped and lifted above counter

## Notes
- After pick-object completes, the robot should be holding the object above the counter
- Use `sensors.find_objects()` or scene knowledge to locate the cabinet
- May need to open the cabinet door first if it's closed
- Use `wb.move_to_pose()` to navigate to the cabinet interior
- Lower the object into the cabinet, then `gripper.open()` to release
- Retract arm after placing to avoid collisions with cabinet
- The sim's `_check_success()` is the definitive success test
