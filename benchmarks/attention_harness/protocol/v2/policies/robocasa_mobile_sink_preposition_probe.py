"""Development-only sink transfer with the base positioned before grasping."""

from robot_sdk import sensors, base, arm, gripper

if context["task_id"] != "counter_to_sink":
    raise ValueError("This policy is only for counter_to_sink")

base.move_delta(0.25, -0.20, frame="local")
base.move_delta(0.30, -0.15, frame="local")
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(-0.175, 0.165, 0.0))
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(0.0, 0.0, -0.80))
targets = sensors.find_objects(["boxed_drink"])
if len(targets) != 1:
    raise RuntimeError("The boxed drink is not uniquely visible")
target = targets[0]["position"]
gripper.open()
arm.move_to_position(target[0], target[1], target[2] + 0.16)
arm.move_to_position(target[0], target[1], target[2] - 0.005)
gripper.close()
for lift_step in range(4):
    arm.move_delta(0.0, 0.0, 0.035)
    if sensors.get_observation()["robot0_gripper_width"][0] < 0.005:
        raise RuntimeError("The boxed drink slipped during lift step " + str(lift_step))

landmarks = sensors.find_objects(["spout"])
if len(landmarks) != 1:
    raise RuntimeError("Sink spout is not uniquely visible")
spout = landmarks[0]["position"]
carry_z = target[2] + 0.15
arm.move_to_position(target[0] + 0.08, target[1] + 0.15, carry_z)
if sensors.get_observation()["robot0_gripper_width"][0] < 0.005:
    raise RuntimeError("The boxed drink slipped during the first transfer")
arm.move_to_position(target[0] + 0.15, target[1] + 0.30, carry_z)
if sensors.get_observation()["robot0_gripper_width"][0] < 0.005:
    raise RuntimeError("The boxed drink slipped during the second transfer")
arm.move_to_position(spout[0] - 0.18, spout[1] - 0.19, spout[2] - 0.065)
if sensors.get_observation()["robot0_gripper_width"][0] < 0.005:
    raise RuntimeError("The boxed drink slipped before release")
gripper.open()
