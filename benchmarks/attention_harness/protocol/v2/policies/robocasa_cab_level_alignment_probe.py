"""Development-only cabinet grasp alignment with a leveled wrist."""

from robot_sdk import sensors, base, arm, gripper

if context["task_id"] != "counter_to_cab":
    raise ValueError("This policy is only for counter_to_cab")
base.move_delta(0.3, 0.0, frame="local")
matches = sensors.find_objects(["condiment_bottle"])
if len(matches) != 1:
    raise RuntimeError("The condiment bottle is not uniquely visible")
target = matches[0]["position"]
gripper.open()
arm.move_to_position(target[0], target[1], target[2] + 0.16)
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(0.0, 0.75, 0.0))
arm.move_delta(0.0, 0.0, -0.14)
sensors.find_objects(["condiment_bottle"])
