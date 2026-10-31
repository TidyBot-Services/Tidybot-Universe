from robot_sdk import sensors, left_arm, right_arm, left_gripper, right_gripper, bimanual, base

if context.get("robot") != {"model":"OpenArm","execution_arm":"both"} or context["task_id"] != "counter_to_sink":
    raise RuntimeError("Bimanual native task identity mismatch")

def target():
    items=sensors.find_objects(["obj"])
    if len(items)!=1:raise RuntimeError("Expected one native boxed drink")
    return items[0]

def left(p):left_arm.move_to_position(*p,tolerance=.004,max_steps=100)
def right(p,ori):right_arm.move_to_position(*p,tolerance=.004,max_steps=100,orientation=ori)
def shifted(p,dx,dy,dz):return [p[0]+dx,p[1]+dy,p[2]+dz]

def tcp(side):
    pose=bimanual.state()["tcp_world"][side]
    return [pose[0][3],pose[1][3],pose[2][3]]

hint=context.get('bimanual_guidance') or {}
controls=hint.get('parameters',{}) if hint.get('status')=='supported' else {}
grasp_offset=controls.get('grasp_offset_m',0.)
receiving_offset=controls.get('receiving_offset_m',.025)

base.move_delta(.03,0,0)
base.move_delta(0,0,.04)
base.move_delta(0,0,-.04)
initial=target()["position"]
left_gripper.open()
left(shifted(initial,0,0,.15))
for step in range(1,10):left(shifted(initial,0,0,.15+(grasp_offset-.15)*step/9))
left_gripper.close()
for step in range(1,9):left(shifted(initial,0,0,grasp_offset+(.13-grasp_offset)*step/8))
if target()["position"][2]<initial[2]+.07:raise RuntimeError("Native object did not follow donor lift")
bimanual.begin_handoff("left","right")
right_gripper.open()
item=target();obj=item["position"]
w,x,y,z=item["quaternion_wxyz"]
R=[[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
   [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
   [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]]
norm=(R[0][0]*R[0][0]+R[1][0]*R[1][0])**.5
if norm<1e-6:raise RuntimeError('Native object has no bounded horizontal grip axis')
vz=[-R[0][0]/norm,-R[1][0]/norm,0.]
vy=[vz[1],-vz[0],0.]
ori=[[0.,vy[0],vz[0]],[0.,vy[1],vz[1]],[1.,0.,0.]]
center=[obj[i]-receiving_offset*R[i][2] for i in range(3)]
approach=shifted(center,0,-.12,0)
if not right_arm.plan_to_position(*approach,orientation=ori)['reachable']:
    raise RuntimeError('No bounded level receiver approach orientation')
right(approach,ori)
for step in range(1,9):right(shifted(center,0,-.12+.12*step/8,0),ori)
right_gripper.close()
bimanual.handoff("left","right")
right_gripper.close()
left(shifted(tcp("left"),0,.16,0))
before=target()["position"]
p=tcp("right")
for step in range(1,9):right(shifted(p,0,0,.01*step),ori)
after=target()["position"]
if after[2]-before[2]<.07:raise RuntimeError("Native object did not follow receiver lift after donor release")
base.move_delta(-.04,0,0)
base.move_delta(0,.24,0)
base.move_delta(0,.28,0)
p=tcp("right")
for step in range(1,4):right(shifted(p,.03*step,0,0),ori)
right_gripper.open()
sensors.get_observation()
print("Bimanual physical handoff and transfer finished; Runner owns the native verdict")
