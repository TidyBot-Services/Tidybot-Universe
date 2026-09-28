from robot_sdk import sensors, arm, gripper

objects = sensors.find_objects()
cube = None
for obj in objects:
    if obj.get('name') == 'cube':
        cube = obj
        break
if cube is not None:
    pos = cube.get('position')
    x, y, z = pos[0], pos[1], pos[2]
    gripper.open()
    arm.move_to_position(x, y, z + 0.1)
    arm.move_to_position(x, y, z + 0.02)
    gripper.close()
    arm.move_to_position(x, y, z + 0.2)
    sensors.find_objects()
