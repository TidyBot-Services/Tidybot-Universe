"""Development-only grasp probe with a fixed flange-to-finger correction."""

from robot_sdk import sensors, base, arm, gripper

target_name = "boxed_drink" if context["task_id"] == "counter_to_sink" else "condiment_bottle"
base.move_delta(0.2, -0.2, frame="local")
matches = sensors.find_objects([target_name])
if len(matches) != 1:
    raise RuntimeError("Target is not uniquely visible")
target = matches[0]["position"]
gripper.open()
arm.move_to_position(target[0] - 0.07, target[1] + 0.05, target[2] + 0.16)
arm.move_to_position(target[0] - 0.07, target[1] + 0.05, target[2] + 0.045)
gripper.close()
arm.move_delta(0.0, 0.0, 0.14)
sensors.find_objects([target_name])
