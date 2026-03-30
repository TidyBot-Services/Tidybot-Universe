# place-object

## Description
Navigate the robot to the target cabinet, position the arm to place the held object inside, release it, and retract to a safe pose. Assumes the object is already grasped and lifted (postcondition of pick-object).

## Preconditions (Input State)
- Object is grasped by the gripper and lifted ~15cm above the counter surface
- Gripper is holding the object (`sensors.is_gripper_holding()` returns True)
- Robot arm is in a stable pose with the object held clear of obstacles

## Postconditions (Output State)
- Object has been placed inside the cabinet
- Gripper is open (object released)
- Robot arm is retracted to a safe pose away from the cabinet

## Success Criteria
- Object is inside the cabinet (no longer held by gripper)
- `sensors.is_gripper_holding()` returns False after release
- Arm is retracted clear of the cabinet (no collision)

## Dependencies
- None (leaf skill — but expects pick-object's postconditions as input)

## Pipeline
1. **Detect cabinet**: Use `sensors.find_objects()` to locate the target cabinet in world coordinates
2. **Navigate to cabinet**: Use `wb.move_to_pose()` to position the end-effector at the cabinet opening, keeping the object clear of obstacles
3. **Position inside cabinet**: Move the arm into the cabinet interior using `wb.move_to_pose()` or `arm.move_delta()`
4. **Release**: `gripper.open()` to release the object
5. **Retract**: `arm.move_delta(dz=0.10)` then `wb.go_home()` to retract safely

## Notes
- The cabinet may already be open in this task environment — check scene state
- Use `sensors.find_objects()` to detect the cabinet position (look for cabinet/shelf-type objects)
- Be careful with arm positioning inside the cabinet to avoid collisions with shelves/walls
- After releasing, retract upward first before moving away to avoid knocking the placed object
