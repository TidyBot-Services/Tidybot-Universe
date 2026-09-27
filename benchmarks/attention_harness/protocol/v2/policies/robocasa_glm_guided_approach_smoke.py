"""Development smoke: act on GLM's boxed_drink target with a bounded step."""

from robot_sdk import sensors, arm

matches = sensors.find_objects(["boxed_drink"])
if len(matches) != 1:
    raise RuntimeError("Expected one visible boxed_drink")

goal = matches[0]["position"]
eef = sensors.get_observation()["robot0_eef_pos"]
step = [max(-0.01, min(0.01, goal[i] - eef[i])) for i in range(3)]
arm.move_delta(step[0], step[1], step[2])
