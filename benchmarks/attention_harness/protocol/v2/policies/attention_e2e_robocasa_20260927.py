from robot_sdk import sensors, arm, gripper

def strategy():
    objs = sensors.find_objects(['boxed_drink'])
    if not objs:
        return
    target = None
    for o in objs:
        if o.get('name') == 'boxed_drink':
            target = o
            break
    if target is None:
        return
    pos = target.get('position')
    if pos is None:
        return
    x, y, z = pos[0], pos[1], pos[2]
    gripper.open()
    arm.move_to_position(x, y, z + 0.15)
    arm.move_to_position(x, y, z + 0.02)
    gripper.close()
    arm.move_to_position(x, y, z + 0.25)
