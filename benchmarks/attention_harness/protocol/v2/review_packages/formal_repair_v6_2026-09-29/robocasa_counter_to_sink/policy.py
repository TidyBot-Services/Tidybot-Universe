"""Public-SDK counter-to-sink repair candidate with base alignment."""

from robot_sdk import sensors, base, arm, gripper

if context["task_id"] != "counter_to_sink":
    raise RuntimeError("Unexpected task")

language = context["language"]
prefix = "pick the "
suffix = " from the counter"
if language[:len(prefix)] != prefix:
    raise RuntimeError("The public task instruction has an unexpected grammar")
end = -1
for index in range(len(prefix), len(language) - len(suffix) + 1):
    if language[index:index + len(suffix)] == suffix:
        end = index
        break
if end <= len(prefix):
    raise RuntimeError("The public task instruction has no target")
target_name = ""
for character in language[len(prefix):end]:
    target_name += "_" if character == " " else character


def one_object(name, allow_missing=False):
    found = sensors.find_objects([name])
    if allow_missing and not found:
        return None
    if len(found) != 1 or found[0]["source"] != "sim_gt":
        raise RuntimeError("One public sim-GT object is required")
    position = found[0]["position"]
    if len(position) != 3:
        raise RuntimeError("Public object position is invalid")
    return position


# Move the base toward the public target before any arm reach. All base
# commands are below the independent 0.30 m per-axis envelope.
initial = one_object(target_name)
lateral = 1.0 if initial[1] > 0.0 else -1.0
for _ in range(2):
    base.move_delta(0.125, lateral * 0.10, frame="local")
for _ in range(2):
    base.move_delta(0.15, lateral * 0.075, frame="local")

target = one_object(target_name)
# Keep the target near the arm center before the Cartesian approach.
# A wide lateral approach can plateau; every base step remains inside
# the independent command envelope and is followed by public reobservation.
for _ in range(6):
    if abs(target[1]) <= 0.20:
        break
    base.move_delta(0.0, max(-0.08, min(0.08, target[1])), frame="local")
    target = one_object(target_name)
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(-0.175, 0.165, 0.0))
arm.move_delta(0.0, 0.0, 0.0, rotation_delta=(0.0, 0.0, -0.80))
gripper.open()
arm.move_to_position(target[0], target[1], target[2] + 0.16)
arm.move_to_position(target[0], target[1], target[2] + 0.025)
gripper.close()
# The repeated small post-grasp deltas had a nonconvergent action in the
# engineering smoke. Use one bounded-height public SDK target instead.
arm.move_to_position(target[0], target[1], target[2] + 0.15)

lifted = one_object(target_name, allow_missing=True)
if lifted is None:
    # /perceive is based on camera segmentation. Occlusion after grasp is
    # valid missing public evidence, so end without another robot command.
    pass
elif lifted[2] < target[2] + 0.025:
    # A missed grasp is a native task failure, not an uncertain arm action.
    # Stop motion cleanly so the independent evaluator can record that failure.
    gripper.open()
else:
    # Reobserve the sink after each base shift. Keep the carried target above
    # the counter and stop before release if public lift evidence disappears.
    lost = False
    for _ in range(24):
        spout = one_object("spout", allow_missing=True)
        if spout is None:
            lost = True
            break
        eef = sensors.get_observation()["robot0_eef_pos"]
        dx = spout[0] - 0.20 - eef[0]
        dy = spout[1] - 0.09 - eef[1]
        # The final few centimeters of forward base travel can be blocked by
        # the sink fixture. Use the same release goal with a bounded 8 cm
        # forward window, while preserving the tighter lateral window.
        if abs(dx) < 0.08 and abs(dy) < 0.03:
            break
        # The diagonal carry command timed out after partial motion in the
        # stopped chain. Clear the counter laterally before moving forward;
        # every command remains inside the same independent Safety envelope.
        if abs(dy) >= 0.03:
            base.move_delta(0.0, max(-0.08, min(0.08, dy)), frame="local")
        else:
            base.move_delta(max(-0.08, min(0.08, dx)), 0.0, frame="local")
        carried = one_object(target_name, allow_missing=True)
        if carried is None or carried[2] < target[2] + 0.025:
            lost = True
            break

    if not lost:
        spout = one_object("spout", allow_missing=True)
        if spout is not None:
            eef = sensors.get_observation()["robot0_eef_pos"]
            release = (spout[0] - 0.20, spout[1] - 0.09, spout[2] - 0.065)
            # A 0.11 m arm translation stalled at the sink in the stopped chain.
            # Keep the same release target, but use bounded base alignment.  If
            # alignment is incomplete, leave the object in hand for native failure.
            if (abs(release[0] - eef[0]) < 0.08 and
                    abs(release[1] - eef[1]) < 0.03 and
                    abs(release[2] - eef[2]) < 0.06):
                gripper.open()
