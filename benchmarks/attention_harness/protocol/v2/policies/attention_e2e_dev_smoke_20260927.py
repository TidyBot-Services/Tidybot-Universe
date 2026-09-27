from robot_sdk import sensors, arm, gripper

objects = sensors.find_objects()
cube = None
for obj in objects:
    if obj.get('name') == 'cube':
        cube = obj
        break

if cube is not None:
    x, y, z = cube.get('position')
    gripper.open()
    arm.move_to_position(x, y, z + 0.1)
    arm.move_to_position(x, y, z)
    gripper.close()
    arm.move_to_position(x, y, z + 0.2)
