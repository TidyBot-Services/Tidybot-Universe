from robot_sdk import sensors, arm, gripper

gripper.open()
objs = sensors.find_objects()
target = None
for obj in objs:
    if obj.get('name') == 'yogurt':
        target = obj
if target is not None:
    pos = target.get('position')
    x, y, z = pos[0], pos[1], pos[2]
    arm.move_to_position(x, y, z + 0.15)
    arm.move_to_position(x, y, z + 0.02)
    gripper.close()
    arm.move_to_position(x, y, z + 0.2)
