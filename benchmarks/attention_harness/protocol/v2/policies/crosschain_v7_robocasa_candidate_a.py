from robot_sdk import sensors, arm, gripper

objs = sensors.find_objects()
target = None
sink = None
for obj in objs:
    if obj.get('name') == 'boxed_drink':
        target = obj
    if obj.get('name') == 'sink':
        sink = obj
if target is not None:
    tx, ty, tz = target['position']
    gripper.open()
    arm.move_to_position(tx, ty, tz + 0.15)
    arm.move_to_position(tx, ty, tz + 0.02)
    gripper.close()
    arm.move_to_position(tx, ty, tz + 0.2)
    if sink is not None:
        sx, sy, sz = sink['position']
        arm.move_to_position(sx, sy, sz + 0.15)
        arm.move_to_position(sx, sy, sz + 0.05)
        gripper.open()
# Hypothesis: sink may be absent from the public object list, so the drink is grasped and lifted but not placed to avoid inventing a destination pose.
