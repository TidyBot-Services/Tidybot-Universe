"""Policy for one T1b diagnostic candidate; physical gates remain in executor."""
SOURCE = '''# T1b sweep candidate $candidate_id, seed $seed, diagnostic only.
# Offline complete: $complete. No stable-grasp or physical-success claim.
from robot_sdk import sensors, base, bimanual, $arm_arm, $arm_gripper
calls = [0]
object_name = 'obj'
grasp_quat = $grasp_quat
approach = $pregrasp
grasp = $grasp
lift = $lift
front = $carry
place = $place

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
        step_limit = 0.015 if search else 0.029
        step = [v*min(1.0, step_limit/distance) for v in delta]
        charge()
        if search and distance <= step_limit:
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
    previous = None
    for k in range(60):
        obs = observe()
        current = obs['robot0_${arm}_eef_quat']
        x,y,z,w = current
        a,b,c,d = grasp_quat
        q = [-d*x+a*w-b*z+c*y, -d*y+a*z+b*w-c*x, -d*z-a*y+b*x+c*w, d*w+a*x+b*y+c*z]
        if q[3] < 0: q = [-v for v in q]
        length = sum(q[j]*q[j] for j in range(3))**0.5
        angle = 2.0*atan_positive(length/q[3]) if q[3] > 1e-10 else 3.141592653589793
        if angle <= 0.01: return
        if previous is not None:
            # Unit quaternion distance, invariant to q versus -q.
            gap = min(sum((current[j]-previous[j])**2 for j in range(4)), sum((current[j]+previous[j])**2 for j in range(4)))
            if gap < 0.000000000025: return
        previous = current
        rv = [q[j]/length*min(angle, 0.13) for j in range(3)]
        correction = [approach[j]-obs['robot0_${arm}_eef_pos'][j] for j in range(3)]
        assert sum(v*v for v in correction)**0.5 <= 0.05, 'orientation drift'
        charge()
        $arm_arm.move_delta(correction[0],correction[1],correction[2],rv)
    assert False, 'orientation failed to converge'

def tuck():
    if $tuck_enabled:
        obs = observe()
        joints = obs['robot0_left_joint_pos'] + obs['robot0_right_joint_pos']
        for j in range(7):
            joints[$tuck_index+j] = $tuck_q[j]
        charge()
        bimanual.move_joints(joints)

def move_base(steps):
    for step in steps:
        charge()
        base.move_delta(step[0], step[1], step[2])

charge()
objects = sensors.find_objects()
assert sum(1 for obj in objects if obj['name'] == object_name) == 1, 'public target missing'
assert sum(1 for obj in objects if obj['name'] == 'cabinet') == 1, 'public cabinet missing'
tuck()
move_base($base_before)
charge()
$arm_gripper.open()
move(approach)
orient()
move(grasp, True)
charge()
$arm_gripper.close()
move(lift)
move_base($base_after)
move(front)
move(place)
charge()
$arm_gripper.open()
move(front)
'''
