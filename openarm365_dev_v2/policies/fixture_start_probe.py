from robot_sdk import sensors, bimanual

observation = sensors.get_observation()
start = list(observation['robot0_left_joint_pos']) + list(observation['robot0_right_joint_pos'])
target = list(start)
target[0] = target[0] + 0.05
target[7] = target[7] + 0.05
try:
    print(bimanual.move_joints(target))
except RuntimeError as error:
    print(str(error))
try:
    print(bimanual.move_joints(start))
except RuntimeError as error:
    print(str(error))
