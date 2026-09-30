"""Public-SDK cube-lift candidate after the OSC step-semantics diagnosis."""

from robot_sdk import sensors, arm, gripper

if context["task_id"] != "cube_lift":
    raise RuntimeError("Unexpected task")

cubes = sensors.find_objects(["cube"])
if len(cubes) != 1 or cubes[0]["source"] != "sim_gt":
    raise RuntimeError("One public sim-GT cube is required")
cube = cubes[0]["position"]
if len(cube) != 3:
    raise RuntimeError("Cube position is invalid")


def approach(x, y, z):
    # move_delta is one OSC tick on this backend. Use its public local
    # controller and divide long translations into short observed targets.
    for _ in range(8):
        current = sensors.get_observation()["robot0_eef_pos"]
        dx, dy, dz = x - current[0], y - current[1], z - current[2]
        distance = (dx * dx + dy * dy + dz * dz) ** 0.5
        if distance < 0.025:
            return
        fraction = min(1.0, 0.18 / distance)
        arm.move_to_position(
            current[0] + dx * fraction,
            current[1] + dy * fraction,
            current[2] + dz * fraction,
            tolerance=0.018,
            max_steps=80,
        )
    raise RuntimeError("Public arm state did not approach the cube")


gripper.open()
approach(cube[0], cube[1], cube[2] + 0.12)
approach(cube[0], cube[1], cube[2] + 0.02)
gripper.close()
approach(cube[0], cube[1], cube[2] + 0.20)
