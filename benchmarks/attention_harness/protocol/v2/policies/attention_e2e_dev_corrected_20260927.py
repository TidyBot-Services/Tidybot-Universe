from robot_sdk import sensors, arm, gripper

objs = sensors.find_objects()
cube = None
for obj in objs:
    if obj.get('name') == 'cube':
        cube = obj
        break

if cube is not None:
    pos = cube.get('position')
    x, y, z = pos[0], pos[1], pos[2]
    gripper.open()
    arm.move_to_position(x, y, z + 0.15)
    arm.move_to_position(x, y, z + 0.02)
    gripper.close()
    arm.move_to_position(x, y, z + 0.25)
