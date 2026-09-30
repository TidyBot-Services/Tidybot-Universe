"""Proposed public-SDK counter-to-sink base policy; requires operator review."""

from robot_sdk import sensors, base, arm, gripper

if context["task_id"] != "counter_to_sink":
    raise RuntimeError("Unexpected task")

# These names come from the public task instruction, never an evaluator key.
language = context["language"]
target_name = None
for phrase, name in (
    ("boxed drink", "boxed_drink"),
    ("cup", "cup"),
    ("mango", "mango"),
    ("onion", "onion"),
    ("rolling pin", "rolling_pin"),
):
    if "pick the " + phrase + " from the counter" in language:
        target_name = name
if target_name is None:
    raise RuntimeError("Task object is outside the preregistered public vocabulary")

# A small, fixed base approach stays within the independent command envelope.
base.move_delta(0.15, 0.0, frame="local")
targets = sensors.find_objects([target_name])
if len(targets) != 1 or targets[0]["source"] != "sim_gt":
    raise RuntimeError("One public sim-GT target is required")
target = targets[0]["position"]
if len(target) != 3:
    raise RuntimeError("Target position is invalid")

# All arm translations are bounded SDK deltas. Each axis is capped at 0.10 m,
# so every commanded Euclidean translation is below the 0.25 m safety limit.
def approach(x, y, z):
    for _ in range(15):
        current = sensors.get_observation()["robot0_eef_pos"]
        dx, dy, dz = x - current[0], y - current[1], z - current[2]
        if max(abs(dx), abs(dy), abs(dz)) < 0.025:
            return
        arm.move_delta(
            max(-0.10, min(0.10, dx)),
            max(-0.10, min(0.10, dy)),
            max(-0.10, min(0.10, dz)),
        )
    raise RuntimeError("Public arm state did not approach the target")


gripper.open()
approach(target[0], target[1], target[2] + 0.14)
approach(target[0], target[1], target[2] + 0.015)
gripper.close()
approach(target[0], target[1], target[2] + 0.20)

landmarks = sensors.find_objects(["spout"])
if len(landmarks) != 1 or landmarks[0]["source"] != "sim_gt":
    raise RuntimeError("One public sim-GT sink landmark is required")
spout = landmarks[0]["position"]
if len(spout) != 3:
    raise RuntimeError("Sink landmark position is invalid")
approach(spout[0], spout[1], spout[2] + 0.08)
approach(spout[0], spout[1], spout[2] - 0.04)
gripper.open()
