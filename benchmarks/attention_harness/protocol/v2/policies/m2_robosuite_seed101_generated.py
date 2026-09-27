from robot_sdk import sensors, arm, gripper

objs = sensors.find_objects()
cube = None
for o in objs:
    if o.get('name') == 'cube':
        cube = o
        break

if cube is not None:
    x, y, z = cube['position']
    gripper.open()
    arm.move_to_position(x, y, z + 0.15)
    arm.move_to_position(x, y, z + 0.02)
    gripper.close()
    arm.move_to_position(x, y, z + 0.25)
