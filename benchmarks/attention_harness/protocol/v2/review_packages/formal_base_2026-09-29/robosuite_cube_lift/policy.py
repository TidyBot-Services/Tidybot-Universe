"""Proposed public-SDK cube-lift base policy; requires operator review."""

from robot_sdk import sensors, arm, gripper

if context["task_id"] != "cube_lift":
    raise RuntimeError("Unexpected task")

cubes = sensors.find_objects(["cube"])
if len(cubes) != 1 or cubes[0]["source"] != "sim_gt":
    raise RuntimeError("One public sim-GT cube is required")
cube = cubes[0]["position"]
if len(cube) != 3:
    raise RuntimeError("Cube position is invalid")

# Read only public proprioception, and cap every axis at 0.10 m per action.
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
    raise RuntimeError("Public arm state did not approach the cube")


gripper.open()
approach(cube[0], cube[1], cube[2] + 0.12)
approach(cube[0], cube[1], cube[2] + 0.02)
gripper.close()
arm.move_delta(0.0, 0.0, 0.18)
