"""Development-only RoboCasa repair; all observations use the public SDK."""

from robot_sdk import sensors, base, arm, gripper

if context["task_id"] != "counter_to_sink":
    raise ValueError("This repair is limited to counter_to_sink")

sensors.get_observation()
catalog = context["memory_catalog"]
if catalog:
    memory = context["retrieve_memory"](
        "candidate:m5:robocasa-counter-to-sink-public-sdk-v2"
    )
    if memory["version"] != 1 or "find_objects" not in memory["guidance"]:
        raise RuntimeError("The candidate guidance or version changed")

    target_name = None
    for phrase, name in (("boxed drink", "boxed_drink"), ("cup", "cup"),
                         ("mango", "mango"), ("onion", "onion"),
                         ("rolling pin", "rolling_pin")):
        if phrase in context["language"]:
            target_name = name
    if target_name is None:
        raise RuntimeError("The task prompt is outside the frozen variants")

    # The rolling pin path caused an unknown action outcome in the prior
    # evidence. Abstain before motion until a safe path has been demonstrated.
    if target_name != "rolling_pin":
        initial = sensors.find_objects([target_name])
        if len(initial) != 1:
            raise RuntimeError("The target is not uniquely visible before base motion")
        initial_y = initial[0]["position"][1]
        lateral = 1.0 if initial_y > 0.0 else -1.0
        for _ in range(2):
            base.move_delta(0.125, lateral * 0.10, frame="local")
        for _ in range(2):
            base.move_delta(0.15, lateral * 0.075, frame="local")
        arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(-0.175, 0.165, 0.0))
        arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(0.0, 0.0, -0.80))

        targets = sensors.find_objects([target_name])
        if len(targets) != 1:
            raise RuntimeError("The target is not uniquely visible after base motion")
        target = targets[0]["position"]
        gripper.open()
        arm.move_to_position(target[0], target[1], target[2] + 0.16)
        arm.move_to_position(target[0], target[1], target[2] - 0.005)
        gripper.close()
        for _ in range(4):
            arm.move_delta(0.0, 0.0, 0.035)

        lifted = sensors.find_objects([target_name])
        if target_name == "cup" and len(lifted) == 1 and lifted[0]["position"][2] < target[2] + 0.025:
            # The old cup grasp closed without lifting it. Retry once at a
            # higher grasp point, then rely on a fresh public observation.
            gripper.open()
            arm.move_to_position(target[0], target[1], target[2] + 0.025)
            gripper.close()
            for _ in range(4):
                arm.move_delta(0.0, 0.0, 0.035)
            lifted = sensors.find_objects([target_name])
        if len(lifted) != 1 or lifted[0]["position"][2] < target[2] + 0.025:
            raise RuntimeError("The public target observation does not show a lift")

        landmarks = sensors.find_objects(["spout"])
        if len(landmarks) != 1:
            raise RuntimeError("The sink landmark is not uniquely visible")
        spout = landmarks[0]["position"]
        carry_z = target[2] + 0.15
        arm.move_to_position(target[0] + 0.08, target[1] + 0.15, carry_z)
        arm.move_to_position(target[0] + 0.15, target[1] + 0.30, carry_z)
        release_z = spout[2] - (0.11 if target_name == "mango" else 0.065)
        arm.move_to_position(spout[0] - 0.18, spout[1] - 0.19, release_z)
        gripper.open()
