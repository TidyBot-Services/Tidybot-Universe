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
    if candidate['fixture_class'] in ['Stove']:fixture=candidate
if fixture is None:raise RuntimeError('pilot fixture missing')
parts=[p for p in fixture['interaction_parts'] if 'knob_' in p['name']]
selected=fixture['task_controls']['selected_knob']
parts=[p for p in parts if 'knob_'+selected+'_main' in p['name']]
if not parts:raise RuntimeError('pilot parts missing')
targets=parts[:1]
seen={}
for part in targets:
    axes=part['axes']
    if not axes:raise RuntimeError('pilot joint axis missing')
    joint=axes[0]
    if joint['joint'] in seen:continue
    seen[joint['joint']]=True
    center=part['center']
    obs=sensors.get_observation()
    left=obs['robot0_left_eef_pos']
    right=obs['robot0_right_eef_pos']
    arm=0 if sum((left[i]-center[i])**2 for i in range(3))<=sum((right[i]-center[i])**2 for i in range(3)) else 1
    start=left if arm==0 else right
    outward=[start[0]-center[0],start[1]-center[1],0.0]
    outward=list(joint['axis_world'])
    if sum(outward[i]*(start[i]-center[i]) for i in range(3))<0:outward=[-v for v in outward]
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
    gripper.open()
    pre=[center[i]+0.08*outward[i] for i in range(3)]
    move(arm,pre,forward,jaw)
    move(arm,center,forward,jaw)
    result=gripper.close()
    if result and result.get('rejected'):raise RuntimeError('pilot close rejected')
    for step in range(80):
        current=sensors.find_fixtures()
        current_fixture=[f for f in current if f['name']==fixture['name']][0]
        angle_now=current_fixture['state']['knob_angles_rad'][selected]
        if angle_now<0.25 or angle_now>6.033185307179586:break
        updated=[p for f in current if f['name']==fixture['name'] for p in f['interaction_parts'] if p['name']==part['name']][0]
        axis=updated['axes'][0]['axis_world']
        origin=updated['axes'][0]['origin_world']
        radius=[updated['center'][i]-origin[i] for i in range(3)]
        tangent=[axis[1]*radius[2]-axis[2]*radius[1],axis[2]*radius[0]-axis[0]*radius[2],axis[0]*radius[1]-axis[1]*radius[0]]
        angle=-min(0.10,max(0.01,angle_now-0.10))
        delta=[angle*v for v in tangent]
        result=action.move_delta(*delta,rotation_delta=[angle*v for v in axis])
        if result and result.get('rejected'):raise RuntimeError('pilot turn rejected')
    gripper.open()
    obs=sensors.get_observation()
    now=obs['robot0_left_eef_pos'] if arm==0 else obs['robot0_right_eef_pos']
    move(arm,[now[i]+0.10*outward[i] for i in range(3)])
