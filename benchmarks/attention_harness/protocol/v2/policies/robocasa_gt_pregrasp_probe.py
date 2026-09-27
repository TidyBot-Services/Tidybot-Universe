"""Development-only reachability probe for the two RoboCasa GT tasks."""

from robot_sdk import sensors, arm, gripper

target_name = "boxed_drink" if context["task_id"] == "counter_to_sink" else "condiment_bottle"
matches = sensors.find_objects([target_name])
if len(matches) != 1:
    raise RuntimeError("Target is not uniquely visible")
target = matches[0]["position"]
gripper.open()
arm.move_to_position(target[0], target[1], target[2] + 0.16)
