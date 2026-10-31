from robot_sdk import sensors, left_arm, right_arm, left_gripper, right_gripper

def cross(a,b):
    return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]

def rotation_step(current,target):
    axis=cross(current,target)
    sine=sum(v*v for v in axis)**0.5
    cosine=max(-1.0,min(1.0,sum(current[i]*target[i] for i in range(3))))
    if sine<0.000001:
        if cosine>0:return [0.0,0.0,0.0]
        axis=cross(current,[1.0,0.0,0.0] if abs(current[0])<0.8 else [0.0,1.0,0.0])
        sine=sum(v*v for v in axis)**0.5
    half=((1.0-cosine)/2.0)**0.5
    t=half/max(1.0+((1.0+cosine)/2.0)**0.5,0.000001)
    angle=4.0*t*(0.7853981633974483+0.273*(1.0-t))
    return [min(0.35,angle)*v/sine for v in axis]

def move(arm,target,forward=None,jaw=None):
    action=left_arm if arm==0 else right_arm
    for step in range(60):
        obs=sensors.get_observation()
        pos=obs['robot0_left_eef_pos'] if arm==0 else obs['robot0_right_eef_pos']
        delta=[target[i]-pos[i] for i in range(3)]
        size=sum(v*v for v in delta)**0.5
        rotation=[0.0,0.0,0.0]
        if forward is not None:
            q=obs['robot0_left_eef_quat'] if arm==0 else obs['robot0_right_eef_quat']
            x,y,z,w=q
            actual=[2*(x*z+w*y),2*(y*z-w*x),1-2*(x*x+y*y)]
            rotation=rotation_step(actual,forward)
            if sum(v*v for v in rotation)**0.5<0.04 and jaw is not None:
                actual=[2*(x*y-w*z),1-2*(x*x+z*z),2*(y*z+w*x)]
                rotation=rotation_step(actual,jaw)
        if size<0.006 and sum(v*v for v in rotation)**0.5<0.04:return
        scale=min(1.0,0.04/max(size,0.000001)) if size>=0.006 else 0.0
        result=action.move_delta(delta[0]*scale,delta[1]*scale,delta[2]*scale,rotation_delta=rotation)
        if result and result.get('rejected'):raise RuntimeError('pilot approach rejected: '+str(result.get('reason')))
    raise RuntimeError('pilot approach did not converge')

fixtures=sensors.find_fixtures()
fixture=None
for candidate in fixtures:
    if candidate['fixture_class'] in ['Microwave']:fixture=candidate
if fixture is None:raise RuntimeError('pilot fixture missing')
parts=[p for p in fixture['interaction_parts'] if 'start_button' in p['name']]
if not parts:raise RuntimeError('pilot parts missing')
targets=parts[:1]
seen={}
for part in targets:
    axes=part['axes']
    joint=axes[0] if axes else None
    center=part['center']
    obs=sensors.get_observation()
    left=obs['robot0_left_eef_pos']
    right=obs['robot0_right_eef_pos']
    arm=0 if sum((left[i]-center[i])**2 for i in range(3))<=sum((right[i]-center[i])**2 for i in range(3)) else 1
    start=left if arm==0 else right
    outward=[start[0]-center[0],start[1]-center[1],0.0]
    length=max(sum(v*v for v in outward)**0.5,0.000001)
    outward=[v/length for v in outward]
    forward=[-v for v in outward]
    jaw=[0.0,0.0,1.0]
    if abs(forward[2])>0.9:jaw=[1.0,0.0,0.0]
    projection=sum(jaw[i]*forward[i] for i in range(3))
    jaw=[jaw[i]-projection*forward[i] for i in range(3)]
    length=max(sum(v*v for v in jaw)**0.5,0.000001)
    jaw=[v/length for v in jaw]
    gripper=left_gripper if arm==0 else right_gripper
    action=left_arm if arm==0 else right_arm
    gripper.close()
    pre=[center[i]+0.08*outward[i] for i in range(3)]
    move(arm,pre,forward,jaw)
    move(arm,center,forward,jaw)
    axis=joint['axis_world'] if joint else [-v for v in outward]
    direction=(1 if joint['position']<(joint['range'][0]+joint['range'][1])/2 else -1) if joint else 1
    for step in range(4):
        current=[f for f in sensors.find_fixtures() if f['name']==fixture['name']][0]
        if current['state']['turned_on']:break
        result=action.move_delta(*[direction*0.012*v for v in axis])
        if result and result.get('rejected'):raise RuntimeError('pilot press rejected')
    gripper.open()
    obs=sensors.get_observation()
    now=obs['robot0_left_eef_pos'] if arm==0 else obs['robot0_right_eef_pos']
    move(arm,[now[i]+0.25*outward[i] for i in range(3)])
