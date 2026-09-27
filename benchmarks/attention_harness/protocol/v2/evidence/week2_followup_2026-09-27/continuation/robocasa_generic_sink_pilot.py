"""Development-only public SDK repair for the counter-to-sink candidate."""

from robot_sdk import sensors, base, arm, gripper

if context["task_id"] != "counter_to_sink":
    raise ValueError("This policy is only for counter_to_sink")

# Keep each base request within the Agent Server's action deadline.
for _ in range(2):
    base.move_delta(0.125, -0.10, frame="local")
for _ in range(2):
    base.move_delta(0.15, -0.075, frame="local")
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(-0.175, 0.165, 0.0))
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(0.0, 0.0, -0.80))
target_name = None
for phrase, name in (("boxed drink", "boxed_drink"), ("cup", "cup"),
                     ("mango", "mango"), ("onion", "onion"),
                     ("rolling pin", "rolling_pin")):
    if phrase in context["language"]:
        target_name = name
if target_name is None:
    raise RuntimeError("The task prompt is outside approved development variants")
targets = sensors.find_objects([target_name])
if len(targets) != 1:
    raise RuntimeError("The target object is not uniquely visible")
target = targets[0]["position"]
gripper.open()
arm.move_to_position(target[0], target[1], target[2] + 0.16)
arm.move_to_position(target[0], target[1], target[2] - 0.005)
gripper.close()
for lift_step in range(4):
    arm.move_delta(0.0, 0.0, 0.035)
    if sensors.get_observation()["robot0_gripper_width"][0] < 0.005:
        raise RuntimeError("The target object slipped during lift step " + str(lift_step))

landmarks = sensors.find_objects(["spout"])
if len(landmarks) != 1:
    raise RuntimeError("Sink spout is not uniquely visible")
spout = landmarks[0]["position"]
carry_z = target[2] + 0.15
arm.move_to_position(target[0] + 0.08, target[1] + 0.15, carry_z)
if sensors.get_observation()["robot0_gripper_width"][0] < 0.005:
    raise RuntimeError("The target object slipped during the first transfer")
arm.move_to_position(target[0] + 0.15, target[1] + 0.30, carry_z)
if sensors.get_observation()["robot0_gripper_width"][0] < 0.005:
    raise RuntimeError("The target object slipped during the second transfer")
arm.move_to_position(spout[0] - 0.18, spout[1] - 0.19, spout[2] - 0.065)
if sensors.get_observation()["robot0_gripper_width"][0] < 0.005:
    raise RuntimeError("The target object slipped before release")
gripper.open()
