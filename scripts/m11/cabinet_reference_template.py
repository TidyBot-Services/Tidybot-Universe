"""Restricted Dev Python source template. Oracle constants are diagnostic only."""
SOURCE = '''# Simulator truth constants, diagnostic only. No success claim.
from robot_sdk import sensors, base, $arm_arm, $arm_gripper
calls = [0]
object_name = $object_name
grasp = $grasp
approach = $approach
lift = $lift
front = $front
place = $place
safe_front = $safe_front
grasp_quat = $grasp_quat

def charge():
    assert calls[0] < 320, 'SDK call budget exceeded'
    calls[0] += 1

def observe():
    charge()
    return sensors.get_observation()

def move(target, search=False):
    for k in range(160):
        obs = observe()
        pos = obs['robot0_${arm}_eef_pos']
        delta = [target[j]-pos[j] for j in range(3)]
        distance = sum(v*v for v in delta)**0.5
        if distance < 0.001: return
        scale = min(1.0, 0.029/distance)
        step = [v*scale for v in delta]
        charge()
        if search and distance <= 0.029:
            assert step[2] < 0, 'grasp_search requires final descent'
            $arm_arm.move_delta(step[0], step[1], step[2], grasp_search={'object_name': object_name, 'lift_position': lift, 'place_position': place})
            return
        $arm_arm.move_delta(step[0], step[1], step[2])
    assert False, 'waypoint failed to converge'

def atan_small(t):
    result = 0.0
    power = t
    for k in range(24):
        result += power/(2*k+1) if k % 2 == 0 else -power/(2*k+1)
        power *= t*t
    return result

def atan_positive(t):
    if t > 2.0: return 1.5707963267948966-atan_small(1.0/t)
    if t > 0.5: return 0.7853981633974483+atan_small((t-1.0)/(t+1.0))
    return atan_small(t)

def orient():
    for k in range(60):
        obs = observe()
        x,y,z,w = obs['robot0_${arm}_eef_quat']
        a,b,c,d = grasp_quat
        q = [-d*x+a*w-b*z+c*y, -d*y+a*z+b*w-c*x, -d*z-a*y+b*x+c*w, d*w+a*x+b*y+c*z]
        if q[3] < 0: q = [-v for v in q]
        length = sum(q[j]*q[j] for j in range(3))**0.5
        if length < 0.001: return
        angle = 2.0*atan_positive(length/q[3]) if q[3] > 1e-10 else 3.141592653589793
        rv = [q[j]/length*min(angle, 0.13) for j in range(3)]
        correction = [approach[j]-obs['robot0_${arm}_eef_pos'][j] for j in range(3)]
        assert sum(v*v for v in correction)**0.5 <= 0.05, 'orientation drift'
        charge()
        $arm_arm.move_delta(correction[0],correction[1],correction[2],rv)
    assert False, 'orientation failed to converge'

for step in $base_steps:
    charge()
    base.move_delta(step[0], step[1], step[2])
charge()
objects = sensors.find_objects()
assert sum(1 for obj in objects if obj['name'] == object_name) == 1, 'public target missing'
assert sum(1 for obj in objects if obj['name'] == 'cabinet') == 1, 'public cabinet missing'
charge()
$arm_gripper.open()
move(approach)
orient()
move(grasp, True)
charge()
$arm_gripper.close()
move(lift)
move(safe_front)
move(front)
move(place)
charge()
$arm_gripper.open()
move(front)
move(safe_front)
'''
