from robot_sdk import sensors, arm, gripper, base

carrying=False

def observe():
    return sensors.get_observation()

def world_to_base(v,q):
    x,y,z,w=q
    return [(1-2*y*y-2*z*z)*v[0]+(2*x*y+2*z*w)*v[1]+(2*x*z-2*y*w)*v[2],(2*x*y-2*z*w)*v[0]+(1-2*x*x-2*z*z)*v[1]+(2*y*z+2*x*w)*v[2],(2*x*z+2*y*w)*v[0]+(2*y*z-2*x*w)*v[1]+(1-2*x*x-2*y*y)*v[2]]

def move_to(target,limit=100,tolerance=.018):
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

items=sensors.find_objects(target_names=['obj','sink','obj_aperture','obj_handle'])
obj=next_item=aperture=handle=None
for item in items:
    if item['name']=='obj': obj=item['position']
    if item['name']=='sink': sink=item['position']
    if item['name']=='obj_aperture': aperture=item['position']
    if item['name']=='obj_handle': handle=item['position']
if obj is None: raise RuntimeError('target object unavailable')
# The seed101 mug body exceeds the original 88mm jaw stroke.
# Use its reviewed, read-only outer handle annotation; retain physical contacts.
grasp=[obj[0],obj[1],obj[2]+.01]
if aperture is not None: grasp=[aperture[0]+.045,aperture[1],obj[2]+.005]
if handle is not None: grasp=[handle[0],handle[1],obj[2]+.01]
print('phase approach',obj,sink,'grasp',grasp)
gripper.open(settle_steps=10)
o=observe(); p=o['robot0_eef_pos']
# Existing initialized down-facing TCP is already above the counter.
# Use the joint driven chassis to align beside the source counter, without touching objects.
slide_base_eef_to([p[0]-.05,grasp[1]])
for i in range(80): arm.move_delta(dx=0,dy=0,dz=0,rotation_delta=[0,0,-.35])
move_to([grasp[0],grasp[1],obj[2]+.14])
move_to(grasp)
print('phase grasp')
gripper.close(settle_steps=10);hold(15)
print('phase lift')
move_to([grasp[0],grasp[1],obj[2]+.18]);hold(5)
lifted=sensors.find_objects(target_names=['obj'])[0]['position']
if lifted[2]<obj[2]+.10: raise RuntimeError('physical grasp/lift failed')
carrying=True
print('phase carry',lifted)
o=observe();p=o['robot0_eef_pos']
slide_base_eef_to([p[0],sink[1]])
move_to([obj[0],sink[1],max(sink[2]+.14,obj[2]+.18)])
move_to([obj[0],sink[1],sink[2]+.03])
carrying=False
print('phase release')
gripper.open(settle_steps=10)
move_to([obj[0],sink[1],sink[2]+.18]);hold(40)
print('completed full pick lift transport release')
