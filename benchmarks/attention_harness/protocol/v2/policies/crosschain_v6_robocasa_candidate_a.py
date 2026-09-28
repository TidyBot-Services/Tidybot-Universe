from robot_sdk import sensors, arm, gripper

gripper.open()

target = None
sink = None
for obj in sensors.find_objects():
    name = obj.get('name')
    pos = obj.get('position')
    if name == 'sink':
        sink = pos
    elif name == 'yogurt':
        target = pos

if target is not None and sink is not None:
    arm.move_to_position(target[0], target[1], target[2] + 0.15)
    arm.move_to_position(target[0], target[1], target[2] + 0.02)
    gripper.close()
    arm.move_to_position(target[0], target[1], target[2] + 0.25)
    arm.move_to_position(sink[0], sink[1], sink[2] + 0.25)
    arm.move_to_position(sink[0], sink[1], sink[2] + 0.05)
    gripper.open()
# Hypothesis: if the target is not named 'yogurt' in this scene, no motion occurs; a broader name check would be needed.
