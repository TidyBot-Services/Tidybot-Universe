"""Frozen public SDK callback: use the Advisor repair only when Memory is exposed."""

def policy(sdk, context):
    catalog = context["memory_catalog"]
    sdk.gripper.open(settle_steps=1)
    if not catalog:
        return
    if len(catalog) != 1:
        raise RuntimeError("expected exactly one validation Memory")
    item = context["retrieve_memory"](catalog[0]["memory_id"])
    guidance = item["guidance"].lower()
    if "cube" not in guidance or "gripper" not in guidance or "lift" not in guidance:
        raise RuntimeError("retrieved guidance does not describe the expected lift repair")
    cube = next(row["position"] for row in sdk.sensors.find_objects(["cube"])
                if row["name"] == "cube")
    sdk.sensors.get_observation()
    sdk.arm.move_to_position(cube[0], cube[1], cube[2] + 0.12)
    sdk.arm.move_to_position(cube[0], cube[1], cube[2] + 0.005)
    sdk.gripper.close(settle_steps=30)
    sdk.arm.move_to_position(cube[0], cube[1], 1.08)
    sdk.gripper.close(settle_steps=10)
