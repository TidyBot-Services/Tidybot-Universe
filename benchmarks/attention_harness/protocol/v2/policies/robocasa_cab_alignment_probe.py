"""Development-only cabinet-task open-gripper alignment diagnostic."""

from robot_sdk import sensors, arm, gripper

if context["task_id"] != "counter_to_cab":
    raise ValueError("This policy is only for counter_to_cab")
matches = sensors.find_objects(["condiment_bottle"])
if len(matches) != 1:
    raise RuntimeError("The condiment bottle is not uniquely visible")
target = matches[0]["position"]
gripper.open()
arm.move_to_position(target[0], target[1], target[2] + 0.16)
arm.move_to_position(target[0], target[1], target[2] + 0.02)
sensors.find_objects(["condiment_bottle"])
