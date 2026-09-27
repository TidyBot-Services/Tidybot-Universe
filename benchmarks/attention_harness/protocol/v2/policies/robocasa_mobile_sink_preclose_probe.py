"""Development-only sink geometry probe before the gripper closes."""

from robot_sdk import sensors, base, arm, gripper

if context["task_id"] != "counter_to_sink":
    raise ValueError("This probe is only for counter_to_sink")

base.move_delta(0.25, -0.20, frame="local")
base.move_delta(0.30, -0.15, frame="local")
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(-0.175, 0.165, 0.0))
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(0.0, 0.0, -0.80))
matches = sensors.find_objects(["boxed_drink"])
if len(matches) != 1:
    raise RuntimeError("The boxed drink is not uniquely visible")
target = matches[0]["position"]
gripper.open()
arm.move_to_position(target[0], target[1], target[2] + 0.16)
arm.move_to_position(target[0], target[1], target[2] + 0.02)
sensors.get_observation()
