"""Scripted seed 101–103 grasp-chain probe; real execution is explicit."""
from __future__ import annotations
import argparse, hashlib, json, math, os, time
from pathlib import Path
from openarm365_dev.policy_validation import validate_generated_policy

STAGES=('base','open','pregrasp','descend','close','lift','held')
ORIENTED_STAGES=('base','open','orient','pregrasp','descend','close','lift','held')

def parse_quat(value):
    parts=tuple(float(x) for x in value.split(','))
    if len(parts)!=4 or not all(math.isfinite(x) for x in parts) or abs(sum(x*x for x in parts)-1)>1e-3:
        raise ValueError('grasp quaternion requires unit x,y,z,w')
    norm=math.sqrt(sum(x*x for x in parts))
    return tuple(x/norm for x in parts)

def theta_quat(theta_deg):
    if not math.isfinite(theta_deg):raise ValueError('theta must be finite')
    half=math.radians((90-theta_deg)/2)
    return (math.sin(half),math.cos(half),0.,0.)

def grasp_offset_xy(quat,offset_mm):
    from scipy.spatial.transform import Rotation
    if not math.isfinite(offset_mm):raise ValueError('grasp offset must be finite')
    axis=Rotation.from_quat(quat).apply([1.,0.,0.])[:2]
    norm=math.hypot(*axis)
    if norm<1e-9:raise ValueError('grasp long axis has no horizontal projection')
    return tuple(float(v/norm*offset_mm/1000) for v in axis)

def variant_policy_source(source,quat,*,offset_mm=0.,z_offset_mm=0.,lift_step_m=.025,lift_height_m=.10):
    """Change only requested policy literals; the zero-default path is byte-identical."""
    if not all(math.isfinite(v) for v in (offset_mm,z_offset_mm,lift_step_m,lift_height_m)):
        raise ValueError('variant numbers must be finite')
    if not 0<lift_step_m<=.05 or not 0<lift_height_m<=.5:
        raise ValueError('lift step must be in (0,.05] and height in (0,.5]')
    def replace_once(old,new):
        nonlocal source
        if source.count(old)!=1:raise ValueError(f'expected one policy literal: {old}')
        source=source.replace(old,new)
    if offset_mm:
        dx,dy=grasp_offset_xy(quat,offset_mm)
        replace_once('pre = [oracle_center[0], oracle_center[1], oracle_z[1]+0.10]',
            f'pre = [oracle_center[0]+{dx!r}, oracle_center[1]+{dy!r}, oracle_z[1]+0.10]')
    if z_offset_mm:
        replace_once('grasp_z = (oracle_z[0]+oracle_z[1])/2',
            f'grasp_z = (oracle_z[0]+oracle_z[1])/2+{z_offset_mm/1000!r}')
    if lift_step_m!=.025 or lift_height_m!=.10:
        n=math.ceil(lift_height_m/lift_step_m)
        steps=[lift_step_m]*(n-1)+[lift_height_m-lift_step_m*(n-1)]
        replace_once('for k in range(4):\n    left_arm.move_delta(0.0,0.0,0.025)',
            f'for lift_delta_z in {steps!r}:\n    left_arm.move_delta(0.0,0.0,lift_delta_z)')
    validate_generated_policy(source)
    return source

def valid_rotation_step(value):
    step=float(value)
    if not math.isfinite(step) or not 0 < step <= .5:
        raise ValueError('rotation step must be finite and in (0, 0.5] rad')
    return step

def rotation_step_divisor(rotation_step):
    return rotation_step if rotation_step==.45 else .9*rotation_step

def rotation_vector_steps(rv,rotation_step=.45):
    rotation_step=valid_rotation_step(rotation_step)
    n=math.floor(max(abs(float(v)) for v in rv)/rotation_step_divisor(rotation_step))+1
    return [tuple(float(v/n) for v in rv)]*n
def rotation_steps(current_xyzw,target_xyzw,rotation_step=.45):
    from scipy.spatial.transform import Rotation
    delta=(Rotation.from_quat(target_xyzw)*Rotation.from_quat(current_xyzw).inv()).as_rotvec()
    return rotation_vector_steps(delta,rotation_step)

def approach_steps(current_pos,target_pos,current_xyzw,target_xyzw,sequence,rotation_step=.45):
    """Ideal open-loop step sizes; generated policies remeasure after every step."""
    from scipy.spatial.transform import Rotation
    rotation_step=valid_rotation_step(rotation_step)
    distance=math.dist(current_pos,target_pos)
    rv=(Rotation.from_quat(target_xyzw)*Rotation.from_quat(current_xyzw).inv()).as_rotvec()
    if sequence=='translate-first':
        n=math.floor(distance/.045)+1 if distance else 0
        translation=[(tuple((target_pos[j]-current_pos[j])/n for j in range(3)),(0.,0.,0.))]*n if n else []
        turns=len(rotation_vector_steps(rv,rotation_step))
        return translation+[((0.,0.,0.),tuple(float(v/turns) for v in rv))]*turns
    if sequence=='combined':
        n=max(1,math.ceil(distance/.045),math.ceil(max(abs(float(v)) for v in rv)/rotation_step_divisor(rotation_step)))
        return [(tuple((target_pos[j]-current_pos[j])/n for j in range(3)),
                 tuple(float(v/n) for v in rv))]*n
    raise ValueError('approach_steps requires translate-first or combined')

def convergent_rotation_lines(step_literal,*,correct_drift=False):
    """Non-default policy loop; every extra move still checks the SDK budget."""
    lines=[f'turns = int(max(abs(v) for v in rv)/(0.9*{step_literal}))+1',
           "assert calls+2*turns <= 75, 'SDK call budget exceeded'",
           'for k in range(turns+3):',
           "    rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
           '    if max(abs(v) for v in rv) < 0.01: break',
           '    remaining = max(1, turns-k)',
           f'    step = [max(-{step_literal}, min({step_literal}, v/remaining)) for v in rv]',
           f"    assert max(abs(v) for v in step) <= {step_literal}, 'rotation coordinate bound'"]
    if correct_drift:
        lines += ["    correction = [pre[j]-obs_open['robot0_left_eef_pos'][j] for j in range(3)]",
                  "    assert sum(v*v for v in correction)**0.5 < 0.05, 'rotation drift correction bound'"]
    lines += ["    assert calls+2 <= 75, 'SDK call budget exceeded'",
              ('    left_arm.move_delta(correction[0],correction[1],correction[2],step)' if correct_drift
               else '    left_arm.move_delta(0.0,0.0,0.0,step)'),
              '    calls += 1', '    obs_open = sensors.get_observation()', '    calls += 1',
              "rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
              'if max(abs(v) for v in rv) > 0.02:',
              '    for correction_k in range(3):',
              f'        step = [max(-{step_literal}, min({step_literal}, v)) for v in rv]',
              f"        assert max(abs(v) for v in step) <= {step_literal}, 'rotation coordinate bound'"]
    if correct_drift:
        lines += ["        correction = [pre[j]-obs_open['robot0_left_eef_pos'][j] for j in range(3)]",
                  "        assert sum(v*v for v in correction)**0.5 < 0.05, 'rotation drift correction bound'"]
    lines += ["        assert calls+2 <= 75, 'SDK call budget exceeded'",
              ('        left_arm.move_delta(correction[0],correction[1],correction[2],step)' if correct_drift
               else '        left_arm.move_delta(0.0,0.0,0.0,step)'),
              '        calls += 1', '        obs_open = sensors.get_observation()', '        calls += 1',
              "        rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
              '        if max(abs(v) for v in rv) < 0.01: break',
              'if max(abs(v) for v in rv) > 0.02:',
              "    print('rotation residual:', rv)",
              "    raise AssertionError('rotation did not converge')"]
    return lines

def sequenced_policy_source(shift,quat,oracle=None,*,sequence,rotation_step=.45):
    if sequence not in ('translate-first','combined'):raise ValueError('unknown sequence')
    rotation_step=valid_rotation_step(rotation_step)
    step_literal='0.45' if rotation_step==.45 else repr(rotation_step)
    rotation_bound='0.5' if rotation_step==.45 else step_literal
    old=oriented_policy_source(shift,quat,oracle,rotation_step=rotation_step)
    prefix=old.split("rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)\n",1)[0]
    suffix="grasp_z = (oracle_z[0]+oracle_z[1])/2\n"+old.split("grasp_z = (oracle_z[0]+oracle_z[1])/2\n",1)[1]
    lines=[]
    if not oracle:
        lines += ['objects = sensors.find_objects()', 'calls += 1',
                  'target = None', 'for item in objects:',
                  "    if item['name'] == 'obj': target = item['position']",
                  "assert target is not None, 'obj missing from public find_objects'",
                  'oracle_center = target', 'oracle_z = [target[2],target[2]]']
    lines += ['pre = [oracle_center[0], oracle_center[1], oracle_z[1]+0.10]']
    if sequence=='translate-first':
        lines += ["eef = obs_open['robot0_left_eef_pos']",
                  'distance = sum((pre[j]-eef[j])**2 for j in range(3))**0.5',
                  'n = int(distance/0.045)+1 if distance else 0',
                  "assert calls+2*n <= 75, 'SDK call budget exceeded'",
                  'for k in range(n):',
                  "    eef = obs_open['robot0_left_eef_pos']",
                  '    step = [(pre[j]-eef[j])/(n-k) for j in range(3)]',
                  "    assert sum(v*v for v in step)**0.5 < 0.05, 'pregrasp step bound'",
                  '    left_arm.move_delta(step[0],step[1],step[2])', '    calls += 1',
                  '    obs_open = sensors.get_observation()', '    calls += 1',
                  "rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)"]
        if rotation_step!=.45:
            lines += convergent_rotation_lines(step_literal,correct_drift=True)
        else:
            lines += [f'turns = int(max(abs(v) for v in rv)/{step_literal})+1',
                  "assert calls+2*turns <= 75, 'SDK call budget exceeded'",
                  'for k in range(turns):',
                  "    rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
                  '    step = [v/(turns-k) for v in rv]',
                  '    correction = [pre[j]-obs_open[\'robot0_left_eef_pos\'][j] for j in range(3)]',
                  f"    assert max(abs(v) for v in step) <= {rotation_bound}, 'rotation coordinate bound'",
                  "    assert sum(v*v for v in correction)**0.5 < 0.05, 'rotation drift correction bound'",
                  '    left_arm.move_delta(correction[0],correction[1],correction[2],step)',
                  '    calls += 1', '    obs_open = sensors.get_observation()', '    calls += 1']
    else:
        nondefault=rotation_step!=.45
        turn_divisor=step_literal if not nondefault else f'(0.9*{step_literal})'
        lines += ["eef = obs_open['robot0_left_eef_pos']",
                  'distance = sum((pre[j]-eef[j])**2 for j in range(3))**0.5',
                  "rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
                  'travel = distance/0.045',
                  f'turn = max(abs(v) for v in rv)/{turn_divisor}',
                  'travel_n = int(travel) if travel == int(travel) else int(travel)+1',
                  'turn_n = int(turn) if turn == int(turn) else int(turn)+1',
                  'n = max(1,travel_n,turn_n)',
                  "assert calls+2*n <= 75, 'SDK call budget exceeded'",
                  'for k in range(n+3):' if nondefault else 'for k in range(n):',
                  "    eef = obs_open['robot0_left_eef_pos']",
                  ('    step = [(pre[j]-eef[j])/max(1,n-k) for j in range(3)]' if nondefault
                   else '    step = [(pre[j]-eef[j])/(n-k) for j in range(3)]'),
                  "    rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
                  (f'    turn_step = [max(-{step_literal}, min({step_literal}, v/max(1,n-k))) for v in rv]' if nondefault
                   else '    turn_step = [v/(n-k) for v in rv]')]
        if nondefault:
            lines += ["    if k >= n and max(abs(v) for v in rv) < 0.01: break"]
        lines += [
                  "    assert sum(v*v for v in step)**0.5 < 0.05, 'combined translation bound'",
                  f"    assert max(abs(v) for v in turn_step) <= {step_literal}, 'combined rotation bound'"]
        if nondefault:
            lines += ["    assert calls+2 <= 75, 'SDK call budget exceeded'"]
        lines += [
                  '    left_arm.move_delta(step[0],step[1],step[2],turn_step)',
                  '    calls += 1', '    obs_open = sensors.get_observation()', '    calls += 1']
        if nondefault:
            lines += ["rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
                      'if max(abs(v) for v in rv) > 0.02:',
                      '    for correction_k in range(3):',
                      f'        turn_step = [max(-{step_literal}, min({step_literal}, v)) for v in rv]',
                      f"        assert max(abs(v) for v in turn_step) <= {step_literal}, 'combined rotation bound'",
                      "        step = [pre[j]-obs_open['robot0_left_eef_pos'][j] for j in range(3)]",
                      "        assert sum(v*v for v in step)**0.5 < 0.05, 'combined translation bound'",
                      "        assert calls+2 <= 75, 'SDK call budget exceeded'",
                      '        left_arm.move_delta(step[0],step[1],step[2],turn_step)',
                      '        calls += 1', '        obs_open = sensors.get_observation()', '        calls += 1',
                      "        rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
                      '        if max(abs(v) for v in rv) < 0.01: break',
                      'if max(abs(v) for v in rv) > 0.02:',
                      "    print('rotation residual:', rv)",
                      "    raise AssertionError('rotation did not converge')"]
    lines += ['obs_pre = obs_open',"eef = obs_pre['robot0_left_eef_pos']"]
    source=prefix+'\n'.join(lines)+'\n'+suffix
    validate_generated_policy(source)
    return source

def oriented_policy_source(shift,quat,oracle=None,*,rotation_step=.45):
    """Generated SDK policy; oracle is read once by the host at reset."""
    rotation_step=valid_rotation_step(rotation_step)
    step_literal='0.45' if rotation_step==.45 else repr(rotation_step)
    rotation_bound='0.5' if rotation_step==.45 else step_literal
    moves=base_steps(shift)
    src=['from robot_sdk import sensors, base, left_arm, left_gripper',
         'calls = 0', f'grasp_quat = {[float(v) for v in quat]!r}']
    if oracle:
        src += [f'oracle_center = {oracle["position_world_m"]!r}',
                f'oracle_z = {oracle["z_range_world_m"]!r}',
                '# 使用仿真真值,仅诊断']
    src += [
         'def atan_small(t):',
         '    result = 0.0', '    power = t', '    for k in range(24):',
         '        result += power / (2*k+1) if k % 2 == 0 else -power / (2*k+1)',
         '        power *= t*t', '    return result',
         'def atan_positive(t):',
         '    if t > 2.0: return 1.5707963267948966 - atan_small(1.0/t)',
         '    if t > 0.5: return 0.7853981633974483 + atan_small((t-1.0)/(t+1.0))',
         '    return atan_small(t)',
         'def remaining_rotvec(current, target):',
         '    x,y,z,w = current', '    a,b,c,d = target',
         '    q = [-d*x+a*w-b*z+c*y, -d*y+a*z+b*w-c*x, -d*z-a*y+b*x+c*w, d*w+a*x+b*y+c*z]',
         '    if q[3] < 0: q = [-v for v in q]',
         '    length = (q[0]*q[0]+q[1]*q[1]+q[2]*q[2])**0.5',
         '    if length < 1e-10: return [0.0,0.0,0.0]',
         '    angle = 2.0*atan_positive(length/q[3]) if q[3] > 1e-10 else 3.141592653589793',
         '    return [angle*q[j]/length for j in range(3)]']
    for dx,dy,yaw in moves:src += [f'base.move_delta({dx!r}, {dy!r}, {yaw!r})','calls += 1']
    src += ['obs_base = sensors.get_observation()', 'calls += 1',
            'left_gripper.open()', 'calls += 1',
            'obs_open = sensors.get_observation()', 'calls += 1',
            "rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)"]
    if rotation_step!=.45:
        src += convergent_rotation_lines(step_literal)
        src += ['obs_orient = obs_open']
    else:
        src += [f'turns = int(max(abs(v) for v in rv)/{step_literal})+1',
            "assert calls + 2*turns <= 75, 'SDK call budget exceeded'",
            'for k in range(turns):',
            "    rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
            '    step = [v/(turns-k) for v in rv]',
            f"    assert max(abs(v) for v in step) <= {rotation_bound}, 'rotation coordinate bound'",
            '    left_arm.move_delta(0.0,0.0,0.0,step)',
            '    calls += 1',
            '    obs_open = sensors.get_observation()', '    calls += 1',
            'obs_orient = obs_open']
    if not oracle:
        src += ['objects = sensors.find_objects()', 'calls += 1',
                'target = None', 'for item in objects:',
                "    if item['name'] == 'obj': target = item['position']",
                "assert target is not None, 'obj missing from public find_objects'",
                'oracle_center = target', 'oracle_z = [target[2],target[2]]']
    src += ['pre = [oracle_center[0], oracle_center[1], oracle_z[1]+0.10]',
            "eef = obs_orient['robot0_left_eef_pos']",
            'delta = [pre[j]-eef[j] for j in range(3)]',
            'distance = sum(v*v for v in delta)**0.5',
            'n = int(distance/0.045)+1 if distance else 0',
            "assert calls+2*n <= 75, 'SDK call budget exceeded'",
            'for k in range(n):',
            "    eef = obs_orient['robot0_left_eef_pos']",
            '    delta = [pre[j]-eef[j] for j in range(3)]',
            '    step = [v/(n-k) for v in delta]',
            "    assert sum(v*v for v in step)**0.5 < 0.05, 'pregrasp step bound'",
            '    left_arm.move_delta(step[0],step[1],step[2])', '    calls += 1',
            '    obs_orient = sensors.get_observation()', '    calls += 1',
            'obs_pre = obs_orient',
            "eef = obs_pre['robot0_left_eef_pos']",
            'grasp_z = (oracle_z[0]+oracle_z[1])/2',
            'delta_z = grasp_z-eef[2]',
            'n = int(abs(delta_z)/0.029)+1 if delta_z else 0',
            "assert calls+2*n <= 75, 'SDK call budget exceeded'",
            'for k in range(n):',
            "    eef = obs_pre['robot0_left_eef_pos']",
            '    step_z = (grasp_z-eef[2])/(n-k)',
            "    assert abs(step_z) <= 0.03, 'descent step bound'",
            '    left_arm.move_delta(0.0,0.0,step_z)', '    calls += 1',
            '    obs_pre = sensors.get_observation()', '    calls += 1',
            'obs_down = obs_pre',
            'left_gripper.close()', 'calls += 1',
            'obs_close = sensors.get_observation()', 'calls += 1',
            'objects_before_lift = sensors.find_objects()', 'calls += 1',
            'obj_before = None', 'for item in objects_before_lift:',
            "    if item['name'] == 'obj': obj_before = item['position']",
            "assert obj_before is not None, 'obj missing before lift'",
            'for k in range(4):',
            '    left_arm.move_delta(0.0,0.0,0.025)', '    calls += 1',
            'obs_lift = sensors.get_observation()', 'calls += 1',
            'objects_after_lift = sensors.find_objects()', 'calls += 1',
            'obj_after = None', 'for item in objects_after_lift:',
            "    if item['name'] == 'obj': obj_after = item['position']",
            "assert obj_after is not None, 'obj missing after lift'",
            "assert obj_after[2]-obj_before[2] >= 0.05, 'object rose less than 5cm'"]
    source='\n'.join(src)+'\n';validate_generated_policy(source);return source

def base_steps(shift):
    dx,dy,yaw=shift
    if not all(math.isfinite(v) for v in shift):raise ValueError('base shift must be finite')
    n=max(1,math.ceil(math.hypot(dx,dy)/.20),math.ceil(abs(yaw)/.30))
    while True:
        step_yaw=yaw/n
        a=sum(math.cos(k*step_yaw) for k in range(n))
        b=sum(math.sin(k*step_yaw) for k in range(n))
        den=a*a+b*b
        if den<1e-12:raise ValueError('base shift has no bounded repeated-step decomposition')
        sx=(a*dx+b*dy)/den;sy=(a*dy-b*dx)/den
        if math.hypot(sx,sy)<=.20+1e-12:return [(sx,sy,step_yaw)]*n
        n+=1
        if n>100:raise ValueError('base shift requires more than 100 SDK calls')

def policy_source(shift,tilt=False):
    moves=base_steps(shift)
    src=['from robot_sdk import sensors, base, left_arm, left_gripper',
         'calls = 0']
    if tilt:
        # Reset mount yaw is pi on registered seeds: robot +y maps to world -y.
        # Two world-frame rotations respect the 0.5 rad SDK coordinate bound;
        # both plan and ik_path modes accept them (plan may hit the 8 s deadline).
        src += ['left_arm.move_delta(0, 0, 0, [0, -0.39269908169872414, 0])',
                'calls += 1',
                'left_arm.move_delta(0, 0, 0, [0, -0.39269908169872414, 0])',
                'calls += 1']
    for dx,dy,yaw in moves:
        src += [f'base.move_delta({dx!r}, {dy!r}, {yaw!r})','calls += 1']
    src += ['obs_base = sensors.get_observation()', 'calls += 1',
            'left_gripper.open()', 'calls += 1',
            'obs_open = sensors.get_observation()', 'calls += 1',
            'objects = sensors.find_objects()', 'calls += 1',
            "target = None", "for item in objects:",
            "    if item['name'] == 'obj': target = item['position']",
            "assert target is not None, 'obj missing from public find_objects'",
            "eef = obs_open['robot0_left_eef_pos']",
            'pre = [target[0], target[1], target[2] + 0.10]',
            'delta = [pre[j] - eef[j] for j in range(3)]']
    def segment():
        return ['distance = sum(v*v for v in delta) ** 0.5',
                'n = int(distance / 0.045) + 1 if distance else 0',
                "assert calls + n <= 75, 'SDK call budget exceeded'",
                'for k in range(n):',
                '    left_arm.move_delta(delta[0]/n, delta[1]/n, delta[2]/n)',
                '    calls += 1']
    src += segment()+['obs_pre = sensors.get_observation()', 'calls += 1',
                       "eef = obs_pre['robot0_left_eef_pos']",
                       'delta = [target[j] - eef[j] for j in range(3)]']
    src += segment()+['obs_down = sensors.get_observation()', 'calls += 1',
                       'left_gripper.close()', 'calls += 1',
                       'obs_close = sensors.get_observation()', 'calls += 1',
                       'objects_before_lift = sensors.find_objects()', 'calls += 1',
                       'obj_before = None', 'for item in objects_before_lift:',
                       "    if item['name'] == 'obj': obj_before = item['position']",
                       "assert obj_before is not None, 'obj missing before lift'",
                       'delta = [0.0, 0.0, 0.10]']
    src += segment()+['obs_lift = sensors.get_observation()', 'calls += 1',
                       'objects_after_lift = sensors.find_objects()', 'calls += 1',
                       'obj_after = None', 'for item in objects_after_lift:',
                       "    if item['name'] == 'obj': obj_after = item['position']",
                       "assert obj_after is not None, 'obj missing after lift'",
                       "assert obj_after[2] - obj_before[2] >= 0.05, 'object rose less than 5cm'"]
    source='\n'.join(src)+'\n';validate_generated_policy(source);return source

def _json(path):return json.loads(path.read_text()) if path and path.is_file() else {}

def grip_axis_horizontal_angle(quat):
    if quat is None:return None
    from scipy.spatial.transform import Rotation
    axis=Rotation.from_quat(quat).apply([0.,1.,0.])
    return math.degrees(math.asin(min(1.,abs(float(axis[2])))))

def slip_metrics(samples):
    """Samples are named checkpoints with object and EEF world positions."""
    rows=[];baseline=None;maximum=0.
    for sample in samples:
        obj=sample.get('object_position');eef=sample.get('eef_position')
        if obj is None or eef is None:continue
        relative=[1000*(float(obj[j])-float(eef[j])) for j in range(3)]
        if baseline is None:baseline=relative
        slip=math.dist(relative,baseline);maximum=max(maximum,slip)
        rows.append(dict(checkpoint=sample['checkpoint'],relative_obj_eef_mm=relative,slip_from_close_mm=slip))
    return rows,maximum

def held_physics_samples(episode):
    if not episode:return []
    journal=episode/'native-actions.jsonl';physics=episode/'native-physics.jsonl'
    if not journal.is_file() or not physics.is_file():return []
    actions=[json.loads(line) for line in journal.read_text().splitlines()]
    close=next((i for i,row in enumerate(actions) if row.get('event')=='gripper_intent' and row.get('command')==1.),None)
    if close is None:return []
    lift_ids=[row['request_id'] for row in actions[close+1:] if row.get('event')=='intent']
    if not lift_ids:return []
    last={}
    for line in physics.read_text().splitlines():
        row=json.loads(line);rid=row.get('request_id')
        if rid in lift_ids and row.get('held_trace'):last[rid]=row
    samples=[]
    for index,rid in enumerate(lift_ids,1):
        row=last.get(rid)
        if row is None:continue
        trace=next((v for v in row['held_trace'] if v.get('object_body')=='obj'),row['held_trace'][0])
        tcp=row['tcp']['left'];relative=[v/1000 for v in trace['rel_pos_tcp_mm']]
        obj=[sum(tcp[j][k]*relative[k] for k in range(3))+tcp[j][3] for j in range(3)]
        samples.append(dict(checkpoint=f'lift_{index}',object_position=obj,
                            eef_position=[tcp[j][3] for j in range(3)]))
    return samples

def summarize(root,seed,shift,exempt,tilt,elapsed,error=None,*,grasp_quat=None,oracle=None,sequence='rotate-first'):
    feedback=next(root.glob('artifacts/**/cells/*/v1/public-feedback.json'),None)
    events=_json(feedback).get('action_trace',[]) if feedback else []
    episode=next(root.glob('artifacts/**/cells/*/v1/executions/openarm365-dev-*'),None)
    if episode and (episode/'trace.json').is_file():
        # The public feedback keeps only the last 40 calls. Apply the same
        # public projection to the full trace for longer seed 103 approaches.
        from benchmarks.attention_harness.dev_loop import event_projection
        events=[row for raw in _json(episode/'trace.json').get('sdk_events',[])
            if (row:=event_projection(raw)) is not None]
    certs=[]
    if episode:
        for path in sorted((episode/'planner').glob('continuous-*.json')):
            c=_json(path).get('certificate') or {}
            certs.append(dict(file=path.name,queries=c.get('queries'),elapsed_seconds=c.get('elapsed_seconds'),
                result=c.get('result'),failure_reason=(c.get('failure') or {}).get('reason')))
    initial=_json(episode/'initial_public_observation.json') if episode else {}
    final=_json(episode/'final_public_observation.json') if episode else {}
    stage_names=(('base','open','pregrasp','orient','descend','close','lift','held') if sequence=='translate-first'
                 else ('base','open','pregrasp','descend','close','lift','held') if sequence=='combined'
                 else ORIENTED_STAGES if grasp_quat is not None else STAGES)
    stages=[];boundary_ops={'base':'get_observation','open':'get_observation','orient':'get_observation','pregrasp':'get_observation',
        'descend':'get_observation','close':'get_observation','lift':'get_observation','held':'find_objects'}
    stage_idx=0;last=0;first_failure=None;last_object=initial.get('perception',{}).get('values',{}).get('obj_pos')
    last_eef=initial.get('robot',{}).get('robot0_left_eef_pos');last_gripper=initial.get('robot',{}).get('robot0_left_gripper_qpos')
    object_before_lift=None;object_after_lift=None;cert_cursor=0
    for i,event in enumerate(events):
        if event.get('operation')=='find_objects' and event.get('status')=='completed':
            seen=next((x.get('position') for x in event.get('objects',[]) if x.get('name')=='obj'),None)
            if seen is not None:
                last_object=seen
                if stage_idx<=stage_names.index('lift'):object_before_lift=seen
                else:object_after_lift=seen
        if event.get('status')!='completed' and first_failure is None:
            first_failure={'operation':event.get('operation'),'reason':event.get('error')}
        if stage_idx>=len(stage_names):break
        name=stage_names[stage_idx]
        if event.get('operation')==boundary_ops[name] and event.get('status')=='completed':
            segment=events[last:i+1];obs=event.get('observation') or {}
            if isinstance(obs,dict) and 'robot' in obs:obs=obs['robot']
            if grasp_quat is not None and name in ('pregrasp','descend') and isinstance(obs,dict):
                observed_pos=obs.get('robot0_left_eef_pos')
                target_pos=(oracle['position_world_m'] if oracle else last_object)
                target_z=((oracle['z_range_world_m'][1]+.10) if name=='pregrasp' and oracle
                          else (target_pos[2]+.10 if name=='pregrasp' and target_pos else None))
                if name=='descend':
                    target_z=(sum(oracle['z_range_world_m'])/2 if oracle else
                              target_pos[2] if target_pos else None)
                if observed_pos is None or target_z is None or abs(observed_pos[2]-target_z)>.006:
                    continue
                if sequence!='rotate-first' and name=='pregrasp' and target_pos is not None:
                    if math.dist(observed_pos[:2],target_pos[:2])>.006:continue
                    if sequence=='combined':
                        from scipy.spatial.transform import Rotation
                        observed_q=obs.get('robot0_left_eef_quat')
                        if observed_q is None or (Rotation.from_quat(grasp_quat)*Rotation.from_quat(observed_q).inv()).magnitude()>.03:continue
            if name=='orient' and grasp_quat is not None:
                q=obs.get('robot0_left_eef_quat') if isinstance(obs,dict) else None
                if q is None:continue
                from scipy.spatial.transform import Rotation
                remaining=(Rotation.from_quat(grasp_quat)*Rotation.from_quat(q).inv()).magnitude()
                if remaining>.03:continue
            if isinstance(obs,dict):
                last_eef=obs.get('robot0_left_eef_pos',last_eef)
                last_gripper=obs.get('robot0_left_gripper_qpos',last_gripper)
            arm_moves=sum(x.get('operation')=='move_delta' and x.get('source')=='left_arm' for x in segment)
            stage_certs=certs[cert_cursor:cert_cursor+arm_moves];cert_cursor+=arm_moves
            stage=dict(stage=name,public_status='completed',sdk_calls=len(segment),
                certification_count=arm_moves,
                certification_elapsed_seconds=sum(float(x.get('elapsed_seconds') or 0) for x in stage_certs),
                eef_position=last_eef,gripper_qpos=last_gripper,
                object_position=last_object)
            if grasp_quat is not None:
                stage['grip_axis_horizontal_angle_deg']=grip_axis_horizontal_angle(obs.get('robot0_left_eef_quat'))
                stage['finger_openings_m']=last_gripper
            stages.append(stage)
            last=i+1;stage_idx+=1
    public_error=_json(feedback).get('public_error') if feedback else None
    if stage_idx<len(stage_names):
        stages.append(dict(stage=stage_names[stage_idx],public_status='failed',first_failure=first_failure or error or public_error))
    before=initial.get('perception',{}).get('values',{}).get('obj_pos')
    after=final.get('perception',{}).get('values',{}).get('obj_pos')
    object_lift_cm=(100*(after[2]-before[2]) if before is not None and after is not None else None)
    lifted=bool(object_lift_cm is not None and object_lift_cm>=5)
    if not lifted and stages and stages[-1]['stage']=='held' and stages[-1]['public_status']=='completed':
        stages[-1].update(public_status='failed',first_failure=public_error or 'object rose less than 5cm')
        first_failure=first_failure or {'operation':'held','reason':stages[-1]['first_failure']}
    result=dict(seed=seed,base_shift=shift,exempt=exempt,tilt=tilt,wall_seconds=elapsed,
        stages=stages,certificates=certs,first_failure=first_failure or error or public_error,
        initial_eef=initial.get('robot',{}).get('robot0_left_eef_pos'),final_eef=final.get('robot',{}).get('robot0_left_eef_pos'),
        initial_object=before,final_object=after,object_before_lift=object_before_lift,object_after_lift=object_after_lift,
        initial_gripper=initial.get('robot',{}).get('robot0_left_gripper_qpos'),
        final_gripper=final.get('robot',{}).get('robot0_left_gripper_qpos'),object_lifted_5cm=lifted,
        execution_dir=str(episode) if episode else None)
    abort_reason=error or public_error or (first_failure.get('reason') if isinstance(first_failure,dict) else first_failure)
    if isinstance(abort_reason,dict):abort_reason=abort_reason.get('message',str(abort_reason))
    result['object_lift_cm']=object_lift_cm
    result['aborted_by']=str(abort_reason) if abort_reason else None
    result['monitor_aborted']=bool(abort_reason and ('Measured ' in str(abort_reason) or 'Unexpected physical' in str(abort_reason)))
    close_stage=next((s for s in stages if s['stage']=='close' and s.get('public_status')=='completed'),None)
    samples=[]
    if close_stage and object_before_lift is not None:
        samples.append(dict(checkpoint='close',object_position=object_before_lift,eef_position=close_stage['eef_position']))
        samples.append(dict(checkpoint='hold_0',object_position=object_before_lift,eef_position=close_stage['eef_position']))
    samples.extend(held_physics_samples(episode))
    result['slip_trajectory'],result['max_slip_mm']=slip_metrics(samples)
    if not result['slip_trajectory']:result['max_slip_mm']=None
    result['finger_openings_after_close_m']=close_stage.get('gripper_qpos') if close_stage else None
    if grasp_quat is not None:
        result.update(grasp_quat_world_xyzw=grasp_quat,oracle=oracle,sequence=sequence,
                      diagnostic_truth_notice='使用仿真真值,仅诊断' if oracle else None)
        result['steps']=arm_step_rows(events,certs,initial)
    return result

def arm_step_rows(events,certs,initial):
    """Pair public arm calls with observations and ordered planner certificates."""
    from scipy.spatial.transform import Rotation
    observed=initial.get('robot',{})
    rows=[];closed=False
    for event in events:
        op=event.get('operation')
        if op=='close':closed=True
        if op=='get_observation' and event.get('status')=='completed':
            observed=event.get('observation') or observed
            if 'robot' in observed:observed=observed['robot']
        if op!='move_delta' or event.get('source')!='left_arm':continue
        args=event.get('arguments') or {}
        delta=[float(args.get(key,0) or 0) for key in ('dx','dy','dz')]
        turn=[float(x) for x in (args.get('rotation_delta') or [0,0,0])]
        pos=observed.get('robot0_left_eef_pos') if isinstance(observed,dict) else None
        quat=observed.get('robot0_left_eef_quat') if isinstance(observed,dict) else None
        target_pos=[float(pos[j])+delta[j] for j in range(3)] if pos is not None else None
        target_quat=([float(v) for v in (Rotation.from_rotvec(turn)*Rotation.from_quat(quat)).as_quat()]
                     if quat is not None else None)
        cert=certs[len(rows)] if len(rows)<len(certs) else {}
        reason=(event.get('error') or cert.get('failure_reason')) if event.get('status')!='completed' else None
        rows.append(dict(step=len(rows)+1,phase='lift' if closed else 'approach',
                         translation_delta_m=delta,rotation_delta_rad=turn,
                         public_status=event.get('status'),failure_reason=reason,
                         certificate_queries=cert.get('queries'),
                         certificate_elapsed_seconds=cert.get('elapsed_seconds'),
                         target_eef_position_world_m=target_pos,
                         target_eef_quat_world_xyzw=target_quat))
    return rows

def markdown(row):
    lines=[f"# Seed {row['seed']} 抓取链路探针",'',
           f"豁免：`{row['exempt']}`；底座位移：`{row['base_shift']}`；倾斜：`{row['tilt']}`。",'',
           '| 阶段 | 公开结果 | SDK 次数 | 认证动作数 | 认证耗时(s) | 末端世界坐标 | 物体世界坐标 | 夹爪开度 | 首个失败 |',
           '| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |']
    oriented='grasp_quat_world_xyzw' in row
    if oriented:
        lines[2]='使用仿真真值,仅诊断。'+lines[2]
        lines[4]='| 阶段 | 公开结果 | SDK 次数 | 认证动作数 | 认证耗时(s) | 末端世界坐标 | 物体世界坐标 | 夹爪开度 | 闭合轴与水平面夹角 ° | 两指开度 m | 首个失败 |'
        lines[5]='| --- | --- | ---: | ---: | ---: | --- | --- | --- | ---: | --- | --- |'
    for s in row['stages']:
        val=lambda key:json.dumps(s.get(key),ensure_ascii=False) if s.get(key) is not None else '—'
        extra=f" {val('grip_axis_horizontal_angle_deg')} | {val('finger_openings_m')} |" if oriented else ''
        lines.append(f"| {s['stage']} | {s['public_status']} | {s.get('sdk_calls','—')} | {s.get('certification_count','—')} | {s.get('certification_elapsed_seconds','—')} | {val('eef_position')} | {val('object_position')} | {val('gripper_qpos')} |{extra} {val('first_failure')} |")
    lines += ['',f"物体上升至少 5 cm：`{row['object_lifted_5cm']}`。",'',
              f"最终物体上升 cm：`{row.get('object_lift_cm')}`；监控中止：`{row.get('monitor_aborted')}`；原因：`{row.get('aborted_by')}`。",'',
              f"最大滑动 mm：`{row.get('max_slip_mm')}`；位移轨迹：`{json.dumps(row.get('slip_trajectory'),ensure_ascii=False)}`。",'',
              f"首个失败：`{json.dumps(row['first_failure'],ensure_ascii=False)}`。",'',
              f"认证证书：`{json.dumps(row['certificates'],ensure_ascii=False)}`。",'']
    if 'steps' in row:
        lines += ['| 步号 | 增量 m / rad | 公开结果 | 失败原因 | 证书查询数 | 证书耗时 s | 目标末端位置 / xyzw |',
                  '| ---: | --- | --- | --- | ---: | ---: | --- |']
        for s in row['steps']:
            value=lambda key:json.dumps(s.get(key),ensure_ascii=False) if s.get(key) is not None else '—'
            lines.append(f"| {s['step']} | {value('translation_delta_m')} / {value('rotation_delta_rad')} | {s['public_status']} | {value('failure_reason')} | {value('certificate_queries')} | {value('certificate_elapsed_seconds')} | {value('target_eef_position_world_m')} / {value('target_eef_quat_world_xyzw')} |")
        lines.append('')
    return '\n'.join(lines)

def run(root,seed,source):
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_experiment import create,run,verify_manifest
    from benchmarks.attention_harness.diagnostics.motion_tracking_matrix import ScriptedClient
    root.mkdir(parents=True,exist_ok=True)
    store=DevV2Store(root/'store.sqlite')
    selection=dict(request_key='grasp-chain-'+hashlib.sha256((source+str(seed)).encode()).hexdigest()[:24],
        mode='interactive',tasks=['pick_place_counter_to_sink'],seeds=[seed],policies=['autonomous'],
        target_depth=1,target_valid_executions=1,max_versions=1,help_budget=0,
        coverage='budget_limited',reproducible_reset=True)
    experiment=create(store,root/'artifacts',selection);manifest=verify_manifest(experiment)
    budget=root/'budget.json';budget.write_text(json.dumps(dict(goal_id=manifest['goal_id'],
        ledger_path=str(store.path.resolve()),experiment_id=experiment['experiment_id'],
        manifest_sha256=experiment['manifest']['sha256'],limits=manifest['goal_limits'],wall_limit=manifest['wall_limit'])))
    reply=json.dumps({'source':source,'reason':'scripted grasp-chain diagnostic'})
    run(store,experiment['experiment_id'],max_cells=1,budget_file=budget,client=ScriptedClient(reply))

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seed',type=int,choices=(101,102,103),default=102)
    p.add_argument('--base-shift',required=True,help='DX,DY,DYAW in robot mount frame')
    p.add_argument('--exempt',choices=('movable_objects','none'),default='movable_objects')
    p.add_argument('--held-reference-mode',choices=('fixed','settled'))
    p.add_argument('--out',type=Path,default=Path('grasp-chain-results'))
    p.add_argument('--tilt',action='store_true')
    p.add_argument('--grasp-quat',help='world TCP target quaternion x,y,z,w')
    p.add_argument('--theta-deg',type=float,help='closing axis heading in world XY, degrees')
    p.add_argument('--grasp-offset-mm',type=float,default=0.,help='signed offset along horizontal banana long axis')
    p.add_argument('--grasp-z-offset-mm',type=float,default=0.)
    p.add_argument('--close-hold-steps',type=int,default=0)
    p.add_argument('--lift-step-m',type=float,default=.025)
    p.add_argument('--lift-height-m',type=float,default=.10)
    p.add_argument('--sequence',choices=('rotate-first','translate-first','combined'),default='rotate-first')
    p.add_argument('--rotation-step',type=valid_rotation_step,default=.45,metavar='RAD',
                   help='maximum rotation vector coordinate per step in (0, 0.5] rad (default: 0.45)')
    p.add_argument('--oracle-from-reset',action='store_true',help='read target geometry once at real reset; diagnostic only')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--dry-run-state',type=Path,help='JSON with post-open eef_position, eef_quat and pregrasp_position for exact dry-run step counts')
    args=p.parse_args(argv)
    if args.out.exists() and not args.dry_run:p.error('--out already exists; choose a fresh directory')
    if args.grasp_quat and args.theta_deg is not None:p.error('--grasp-quat and --theta-deg are mutually exclusive')
    if args.tilt and (args.grasp_quat or args.theta_deg is not None):p.error('--tilt and grasp orientation are mutually exclusive')
    if args.close_hold_steps<0:p.error('--close-hold-steps requires nonnegative integer')
    if args.close_hold_steps:p.error('this dev SDK has no exact hold/settle operation; nonzero --close-hold-steps is unavailable')
    if args.sequence!='rotate-first' and not (args.grasp_quat or args.theta_deg is not None):p.error('--sequence requires grasp orientation')
    if args.oracle_from_reset and not (args.grasp_quat or args.theta_deg is not None):p.error('--oracle-from-reset requires grasp orientation')
    if args.dry_run_state and not args.dry_run:p.error('--dry-run-state requires --dry-run')
    try:
        shift=tuple(float(x) for x in args.base_shift.split(','))
        if len(shift)!=3:raise ValueError()
        quat=parse_quat(args.grasp_quat) if args.grasp_quat else theta_quat(args.theta_deg) if args.theta_deg is not None else None
        if quat is None and (args.grasp_offset_mm or args.grasp_z_offset_mm or args.lift_step_m!=.025 or args.lift_height_m!=.10):
            raise ValueError('variant offsets/lift require grasp orientation')
        oracle=None
        if args.oracle_from_reset and not args.dry_run:
            from .object_proxy_measure import reset_diagnostic_fixture, DEFAULT_ROBOT_CONFIG
            fixture=reset_diagnostic_fixture(args.seed,args.out/'oracle-reset',DEFAULT_ROBOT_CONFIG)
            oracle=fixture['diagnostic_truth']
            (args.out/'oracle-reset'/'fixture.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n')
        if args.oracle_from_reset and args.dry_run:
            oracle={'position_world_m':[0.,0.,0.], 'z_range_world_m':[0.,0.]}
        source=(oriented_policy_source(shift,quat,oracle,rotation_step=args.rotation_step) if args.sequence=='rotate-first' else
                sequenced_policy_source(shift,quat,oracle,sequence=args.sequence,rotation_step=args.rotation_step)) if quat else policy_source(shift,args.tilt)
        if quat:
            source=variant_policy_source(source,quat,offset_mm=args.grasp_offset_mm,
                z_offset_mm=args.grasp_z_offset_mm,lift_step_m=args.lift_step_m,lift_height_m=args.lift_height_m)
    except ValueError as exc:p.error('invalid --base-shift or policy: '+str(exc))
    if args.dry_run:
        if args.oracle_from_reset:print('# 使用仿真真值,仅诊断；dry-run 不执行 reset，真实运行时注入几何常量')
        if quat:
            step_literal='0.45' if args.rotation_step==.45 else repr(args.rotation_step)
            divisor=step_literal if args.rotation_step==.45 else f'(0.9*{step_literal})'
            print(f'# sequence={args.sequence}; base_steps={len(base_steps(shift))}; approach_steps 在张手后的公开位姿和目标位置确定')
            print(f'# rotate-first: rotation=floor(max_rotation_coordinate/{divisor})+1，translation=floor(distance/0.045)+1')
            print(f'# translate-first: translation=floor(distance/0.045)+1，rotation=floor(max_rotation_coordinate/{divisor})+1')
            print(f'# combined: N=max(1,ceil(distance/0.045),ceil(max_rotation_coordinate/{divisor}))')
            print('# SDK 调用数估计：上限 80；提供 --dry-run-state 后按旋转/平移及约 4 步下探估算，估计调用数 > 70 时警告')
            if args.dry_run_state:
                try:
                    state=json.loads(args.dry_run_state.read_text())
                    pos=state['eef_position'];initial_q=state['eef_quat'];pre=state['pregrasp_position']
                    from scipy.spatial.transform import Rotation
                    rv=(Rotation.from_quat(quat)*Rotation.from_quat(initial_q).inv()).as_rotvec()
                    distance=math.dist(pos,pre)
                    turns=len(rotation_vector_steps(rv,args.rotation_step))
                    travel=math.floor(distance/.045)+1 if distance else 0
                    counts={'rotate-first':turns+travel,
                            'translate-first':len(approach_steps(pos,pre,initial_q,quat,'translate-first',args.rotation_step)),
                            'combined':len(approach_steps(pos,pre,initial_q,quat,'combined',args.rotation_step))}
                    print(f'# dry-run approach step counts: {json.dumps(counts,ensure_ascii=False)}')
                    estimated={name:len(base_steps(shift))+12+(0 if args.oracle_from_reset else 1)+2*count+8
                               for name,count in counts.items()}
                    print(f'# dry-run SDK call estimates (limit 80): {json.dumps(estimated,ensure_ascii=False)}')
                    for name,calls in estimated.items():
                        if calls>70:print(f'# 警告：{name} 估计调用数 {calls} > 70（上限 80）')
                except (OSError,ValueError,KeyError,TypeError) as exc:p.error('invalid --dry-run-state: '+str(exc))
        print(source,end='');return 0
    old=os.environ.get('ATTN_DIAG_CONTACT_EXEMPT')
    old_held_reference=os.environ.get('ATTN_DIAG_HELD_REFERENCE_MODE')
    os.environ['ATTN_DIAG_CONTACT_EXEMPT']=args.exempt
    if args.held_reference_mode is not None:
        os.environ['ATTN_DIAG_HELD_REFERENCE_MODE']=args.held_reference_mode
    started=time.monotonic();error=None
    try:run(args.out,args.seed,source)
    except Exception as exc:error=f'{type(exc).__name__}: {exc}'
    finally:
        if old is None:os.environ.pop('ATTN_DIAG_CONTACT_EXEMPT',None)
        else:os.environ['ATTN_DIAG_CONTACT_EXEMPT']=old
        if args.held_reference_mode is not None:
            if old_held_reference is None:os.environ.pop('ATTN_DIAG_HELD_REFERENCE_MODE',None)
            else:os.environ['ATTN_DIAG_HELD_REFERENCE_MODE']=old_held_reference
    result=summarize(args.out,args.seed,shift,args.exempt,args.tilt,time.monotonic()-started,error,
                     grasp_quat=quat,oracle=oracle,sequence=args.sequence)
    result.update(theta_deg=args.theta_deg,close_hold_steps=args.close_hold_steps,
                  grasp_offset_mm=args.grasp_offset_mm,grasp_z_offset_mm=args.grasp_z_offset_mm,
                  lift_step_m=args.lift_step_m,lift_height_m=args.lift_height_m)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'policy.py').write_text(source)
    (args.out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (args.out/'results.md').write_text(markdown(result))
    print(json.dumps({'results':str(args.out/'results.json'),'first_failure':result['first_failure'],'object_lifted_5cm':result['object_lifted_5cm']},ensure_ascii=False))
    return 0 if error is None else 1

if __name__=='__main__':raise SystemExit(main())
