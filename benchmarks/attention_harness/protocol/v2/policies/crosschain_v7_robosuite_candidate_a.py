from robot_sdk import sensors, arm, gripper

objs = sensors.find_objects()
cube = None
for obj in objs:
    if obj.get('name') == 'cube':
        cube = obj
        break
if cube is not None:
    pos = cube.get('position')
    x = pos[0]
    y = pos[1]
    z = pos[2]
    gripper.open()
    arm.move_to_position(x, y, z + 0.1)
    arm.move_to_position(x, y, z + 0.01)
    gripper.close()
    arm.move_to_position(x, y, z + 0.2)
    sensors.find_objects()
# Hypothesis: cube z offset for a safe grasp may differ; z + 0.01 assumes near-top surface contact.
