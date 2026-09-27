from robot_sdk import sensors, gripper

objects = sensors.find_objects()
gripper.open(settle_steps=1)
