from robot_sdk import sensors, left_arm, right_arm, left_gripper, right_gripper, bimanual, base
objects = sensors.find_objects()
for obj in objects:
    if obj['name'] == 'obj':
        initial_obj = obj['position']
    if obj['name'] == 'sink':
        sink_pos = obj['position']
print('Detected banana',initial_obj,'sink',sink_pos)
print('Grasp thick middle at [.517,-2.387,.947], finger-gap heading 2.54 rad.')
print('Plan: base Y +.33; grasp and verify; lift/tilt; base Y -.54; release inside basin.')
left_gripper.open()
right_gripper.open()
ok = True
held = False
for i in range(2):
    if ok:
        result = base.move_delta(0.0,0.165,0.0)
        ok = result.get('ok',False)
        print('Base approach',i+1,'ok',ok,'reason',result.get('reason'))
if ok:
    result = bimanual.move_joints([-0.5293226242,-0.7631379366,0.4721921980,1.2225111723,-0.8395673037,-0.5278142095,1.5183473825,0.4,0.8,0.0,0.8,0.0,0.0,0.0])
    ok = result.get('ok',False)
    print('Pregrasp z .965 at flipped heading','ok',ok,'reason',result.get('reason'))
if ok:
    for target_z in [0.955,0.947]:
        obs = sensors.get_observation()
        p = obs['robot0_left_eef_pos']
        result = left_arm.move_delta(0.517-p[0],-2.387-p[1],target_z-p[2])
        ok = result.get('ok',False)
        print('Open-gripper descent',target_z,'ok',ok,'reason',result.get('reason'))
        if not ok:
            break
if ok:
    result = left_gripper.close()
    ok = result.get('ok',False)
    print('Bilateral close','ok',ok,'reason',result.get('reason'))
if ok:
    result = left_arm.move_delta(0.0,0.0,0.018)
    ok = result.get('ok',False)
    print('Verification lift .018m','ok',ok,'reason',result.get('reason'))
    for obj in sensors.find_objects():
        if obj['name'] == 'obj':
            obj_p = obj['position']
    held = obj_p[2] > initial_obj[2]+0.010
    ok = ok and held
    print('Banana after lift',obj_p,'follows gripper',held)
if ok:
    for target_z in [0.995,1.025,1.060]:
        obs = sensors.get_observation()
        p = obs['robot0_left_eef_pos']
        result = left_arm.move_delta(0.517-p[0],-2.387-p[1],target_z-p[2],rotation_delta=[0.0,0.1,0.0])
        ok = result.get('ok',False)
        print('Raise and tilt toward carry pose',target_z,'ok',ok,'reason',result.get('reason'))
        if not ok:
            break
if ok:
    print('Carry above counter and sink rim at TCP z 1.06m, single left grasp.')
    for i in range(3):
        result = base.move_delta(0.0,-0.18,0.0)
        ok = result.get('ok',False)
        print('Base transport step',i+1,'mount Y -.18m','ok',ok,'reason',result.get('reason'))
        if not ok:
            break
if ok:
    print('Move inward above basin to [.400,-1.800,1.060].')
    obs = sensors.get_observation()
    p = obs['robot0_left_eef_pos']
    dx = (0.400-p[0])/3.0
    dy = (-1.800-p[1])/3.0
    dz = (1.060-p[2])/3.0
    for i in range(3):
        result = left_arm.move_delta(dx,dy,dz)
        ok = result.get('ok',False)
        print('Basin approach step',i+1,'ok',ok,'reason',result.get('reason'))
        if not ok:
            break
if ok:
    print('Banana before release',sensors.find_objects())
    result = left_gripper.open()
    ok = result.get('ok',False)
    held = False
    print('Release over basin','ok',ok,'reason',result.get('reason'))
if ok:
    result = base.move_delta(-0.08,0.0,0.0)
    print('Retreat base .08m away from counter','ok',result.get('ok'),'reason',result.get('reason'))
obs = sensors.get_observation()
for obj in sensors.find_objects():
    if obj['name'] == 'obj':
        obj_p = obj['position']
lp = obs['robot0_left_eef_pos']
rp = obs['robot0_right_eef_pos']
ld2 = (lp[0]-obj_p[0])**2+(lp[1]-obj_p[1])**2+(lp[2]-obj_p[2])**2
rd2 = (rp[0]-obj_p[0])**2+(rp[1]-obj_p[1])**2+(rp[2]-obj_p[2])**2
inside = 0.140009 < obj_p[0] < 0.519583 and -1.972447 < obj_p[1] < -1.423721 and 0.707369 < obj_p[2] < 0.918222
print('Final banana',obj_p,'inside basin',inside)
print('Final TCP positions left',lp,'right',rp,'both farther than .27m',ld2>0.0729 and rd2>0.0729)
print('Final finger positions',obs['robot0_left_gripper_qpos'],obs['robot0_right_gripper_qpos'])
if ok and inside and not held and ld2>0.0729 and rd2>0.0729:
    print('COMPLETE: banana settled inside sink; both grippers released and clear.')
else:
    print('INCOMPLETE: motion/grasp or final placement-clearance check not satisfied.')
