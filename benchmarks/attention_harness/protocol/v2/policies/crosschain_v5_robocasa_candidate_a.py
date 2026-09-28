from robot_sdk import sensors, arm, gripper

gripper.open()

objects = sensors.find_objects()
target = None
for obj in objects:
    name = obj.get('name')
    if name is not None and name != 'counter' and name != 'sink':
        target = obj
        break

if target is not None:
    pos = target.get('position')
    if pos is not None:
        x = pos[0]
        y = pos[1]
        z = pos[2]
        arm.move_to_position(x, y, z + 0.15)
        arm.move_to_position(x, y, z + 0.02)
        gripper.close()
        arm.move_to_position(x, y, z + 0.2)
        arm.move_to_position(x + 0.3, y, z + 0.2)
        gripper.open()
