from robot_sdk import sensors, arm, gripper, base

carrying=False

def observe():
    return sensors.get_observation()

def world_to_base(v,q):
    x,y,z,w=q
    return [(1-2*y*y-2*z*z)*v[0]+(2*x*y+2*z*w)*v[1]+(2*x*z-2*y*w)*v[2],(2*x*y-2*z*w)*v[0]+(1-2*x*x-2*z*z)*v[1]+(2*y*z+2*x*w)*v[2],(2*x*z+2*y*w)*v[0]+(2*y*z-2*x*w)*v[1]+(1-2*x*x-2*y*y)*v[2]]

def move_to(target,limit=80,tolerance=.018):
    for i in range(limit):
        o=observe();p=o['robot0_eef_pos'];d=[target[j]-p[j] for j in range(3)]
        if sum(v*v for v in d)<tolerance*tolerance: return
        scale=max(1,(sum(v*v for v in d)**.5)/.025)
        local=world_to_base([v/scale for v in d],o['robot0_base_quat'])
        arm.move_delta(dx=local[0],dy=local[1],dz=local[2],rotation_delta=[0,0,0])
    raise RuntimeError('TCP position did not converge')

def slide_base_eef_to(target_xy,limit=260):
    for i in range(limit):
        o=observe();p=o['robot0_eef_pos'];d=[target_xy[0]-p[0],target_xy[1]-p[1],0]
        if sum(v*v for v in d)<.012*.012:
            base.move_normalized([0,0,0]); return
        if carrying and i%25==0:
            held=sensors.find_objects(target_names=['obj'])[0]['position']
            if held[2]<obj[2]+.08 or sum((held[j]-p[j])**2 for j in range(3))>.15**2:
                base.move_normalized([0,0,0]);raise RuntimeError('grasp lost during transport')
        local=world_to_base(d,o['robot0_base_quat'])
        base.move_normalized([max(-.5,min(.5,local[0]*8)),max(-.5,min(.5,local[1]*8)),0])
    base.move_normalized([0,0,0]);raise RuntimeError('base alignment did not converge')

def hold(n):
    for i in range(n): arm.move_delta(dx=0,dy=0,dz=0,rotation_delta=[0,0,0])

items=sensors.find_objects(target_names=['cabinet_left_handle','cabinet_right_handle','cabinet_left_hinge','cabinet_right_hinge'])
geometry={item['name']:item['position'] for item in items}
for side in ['left','right']:
    handle=geometry.get('cabinet_'+side+'_handle');hinge=geometry.get('cabinet_'+side+'_hinge')
    if handle is None or hinge is None: raise RuntimeError('cabinet handle/hinge geometry unavailable')
    print('phase handle approach',side,handle,hinge)
    gripper.open(settle_steps=10)
    slide_base_eef_to([handle[0],handle[1]-.14])
    move_to([handle[0],handle[1]-.003,handle[2]])
    print('phase handle grasp',side)
    gripper.close(settle_steps=10);hold(10)
    print('phase hinge pull',side)
    sign=-1 if side=='left' else 1
    radius=[handle[0]-hinge[0],handle[1]-hinge[1]]
    for i in range(1,341):
        theta=sign*1.50*min(i,300)/300
        # Deterministic sine/cosine series on [-1.5,1.5], error below 0.02mm at this handle radius.
        sin=theta-theta**3/6+theta**5/120-theta**7/5040+theta**9/362880
        cos=1-theta**2/2+theta**4/24-theta**6/720+theta**8/40320
        endpoint=[hinge[0]+cos*radius[0]-sin*radius[1],hinge[1]+sin*radius[0]+cos*radius[1],handle[2]]
        if i==100:
            moved=sensors.find_objects(target_names=['cabinet_'+side+'_handle'])[0]['position']
            if sum((moved[j]-handle[j])**2 for j in range(3))<.035**2:
                base.move_normalized([0,0,0]);raise RuntimeError('cabinet handle did not follow physical pull')
        o=observe();p=o['robot0_eef_pos'];local=world_to_base([endpoint[0]-p[0],endpoint[1]-p[1],0],o['robot0_base_quat'])
        base.move_normalized([max(-.5,min(.5,local[0]*8)),max(-.5,min(.5,local[1]*8)),0])
    base.move_normalized([0,0,0]);hold(5)
    print('phase handle release',side)
    gripper.open(settle_steps=10)
    move_to([endpoint[0],endpoint[1]-.13,endpoint[2]])
hold(20)
print('completed both cabinet handle grasp/pull sequences; native evaluator determines result')
