from robot_sdk import sensors, left_arm, left_gripper
observation = sensors.get_observation()
objects = sensors.find_objects()
left_gripper.open()
left_arm.move_delta(0.0, 0.0, 0.01)
