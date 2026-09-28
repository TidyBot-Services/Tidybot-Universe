from robot_sdk import sensors, arm, gripper

objs = sensors.find_objects()
cube = next((o for o in objs if o.get('name') == 'cube'), None)
if cube is not None:
    x, y, z = cube['position']
    gripper.open()
    arm.move_to_position(x, y, z + 0.15)
    arm.move_to_position(x, y, z + 0.02)
    gripper.close()
    arm.move_to_position(x, y, z + 0.25)
    final_objs = sensors.find_objects()
    final_cube = next((o for o in final_objs if o.get('name') == 'cube'), None)
    if final_cube is not None:
        print(final_cube['position'])
# Hypothesis: the cube's reported z is its center, so grasping at z + 0.02 may collide with the table if the cube is thin; a slightly higher offset could improve grasp reliability.
