from robot_sdk import sensors, arm, gripper

gripper.open()
objs = sensors.find_objects()
target = None
for obj in objs:
    if obj.get('name') == 'yogurt':
        target = obj
if target is None:
    for obj in objs:
        if obj.get('name') == 'boxed_drink':
            target = obj
if target is not None:
    x, y, z = target.get('position')
    arm.move_to_position(x, y, z + 0.15)
    arm.move_to_position(x, y, z + 0.02)
    gripper.close()
    arm.move_to_position(x, y, z + 0.25)
