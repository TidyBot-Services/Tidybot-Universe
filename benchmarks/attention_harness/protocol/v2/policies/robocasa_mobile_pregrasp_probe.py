"""Development-only mobile-base reachability check for RoboCasa."""

from robot_sdk import sensors, base, arm, gripper

target_name = "boxed_drink" if context["task_id"] == "counter_to_sink" else "condiment_bottle"
before = sensors.find_objects([target_name])
if len(before) != 1:
    raise RuntimeError("Target is not uniquely visible before base movement")

base.move_delta(0.2, -0.2, frame="local")

after = sensors.find_objects([target_name])
if len(after) != 1:
    raise RuntimeError("Target is not uniquely visible after base movement")
target = after[0]["position"]
gripper.open()
arm.move_to_position(target[0], target[1], target[2] + 0.16)
