"""Engineering smoke policy: apply a scoped Memory hint on the next attempt."""

from robot_sdk import sensors, arm, gripper

gripper.open(settle_steps=1)
guidance = context["attention_input"].get("memory_guidance", {})
if guidance:
    text = list(guidance.values())[0]
    if "cube" in text and "gripper" in text and "lift" in text:
        matches = sensors.find_objects(["cube"])
        if matches:
            cube = matches[0]["position"]
            arm.move_to_position(cube[0], cube[1], cube[2] + 0.12)
            arm.move_to_position(cube[0], cube[1], cube[2] + 0.005)
            gripper.close(settle_steps=30)
            arm.move_to_position(cube[0], cube[1], 1.08)
            gripper.close(settle_steps=10)
