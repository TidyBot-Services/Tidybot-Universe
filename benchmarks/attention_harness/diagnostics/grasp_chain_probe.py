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

def rotation_steps(current_xyzw,target_xyzw):
    from scipy.spatial.transform import Rotation
    delta=(Rotation.from_quat(target_xyzw)*Rotation.from_quat(current_xyzw).inv()).as_rotvec()
    n=max(1,math.ceil(max(abs(float(x)) for x in delta)/.5))
    return [tuple(float(x/n) for x in delta)]*n

def oriented_policy_source(shift,quat,oracle=None):
    """Generated SDK policy; oracle is read once by the host at reset."""
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
            "rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
            'turns = int(max(abs(v) for v in rv)/0.45)+1',
            "assert calls + 2*turns <= 75, 'SDK call budget exceeded'",
            'for k in range(turns):',
            "    rv = remaining_rotvec(obs_open['robot0_left_eef_quat'], grasp_quat)",
            '    step = [v/(turns-k) for v in rv]',
            "    assert max(abs(v) for v in step) <= 0.5, 'rotation coordinate bound'",
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

def summarize(root,seed,shift,exempt,tilt,elapsed,error=None,*,grasp_quat=None,oracle=None):
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
    stage_names=ORIENTED_STAGES if grasp_quat is not None else STAGES
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
    lifted=bool(object_before_lift and object_after_lift and object_after_lift[2]-object_before_lift[2]>=.05)
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
    if grasp_quat is not None:
        result.update(grasp_quat_world_xyzw=grasp_quat,oracle=oracle,
                      diagnostic_truth_notice='使用仿真真值,仅诊断' if oracle else None)
    return result

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
              f"首个失败：`{json.dumps(row['first_failure'],ensure_ascii=False)}`。",'',
              f"认证证书：`{json.dumps(row['certificates'],ensure_ascii=False)}`。",'']
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
    p.add_argument('--out',type=Path,default=Path('grasp-chain-results'))
    p.add_argument('--tilt',action='store_true')
    p.add_argument('--grasp-quat',help='world TCP target quaternion x,y,z,w')
    p.add_argument('--oracle-from-reset',action='store_true',help='read target geometry once at real reset; diagnostic only')
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args(argv)
    if args.out.exists() and not args.dry_run:p.error('--out already exists; choose a fresh directory')
    if args.tilt and args.grasp_quat:p.error('--tilt and --grasp-quat are mutually exclusive')
    if args.oracle_from_reset and not args.grasp_quat:p.error('--oracle-from-reset requires --grasp-quat')
    try:
        shift=tuple(float(x) for x in args.base_shift.split(','))
        if len(shift)!=3:raise ValueError()
        quat=parse_quat(args.grasp_quat) if args.grasp_quat else None
        oracle=None
        if args.oracle_from_reset and not args.dry_run:
            from .object_proxy_measure import reset_diagnostic_fixture, DEFAULT_ROBOT_CONFIG
            fixture=reset_diagnostic_fixture(args.seed,args.out/'oracle-reset',DEFAULT_ROBOT_CONFIG)
            oracle=fixture['diagnostic_truth']
            (args.out/'oracle-reset'/'fixture.json').write_text(json.dumps(fixture,ensure_ascii=False,indent=2)+'\n')
        if args.oracle_from_reset and args.dry_run:
            oracle={'position_world_m':[0.,0.,0.], 'z_range_world_m':[0.,0.]}
        source=oriented_policy_source(shift,quat,oracle) if quat else policy_source(shift,args.tilt)
    except ValueError as exc:p.error('invalid --base-shift or policy: '+str(exc))
    if args.dry_run:
        if args.oracle_from_reset:print('# 使用仿真真值,仅诊断；dry-run 不执行 reset，真实运行时注入几何常量')
        print(source,end='');return 0
    old=os.environ.get('ATTN_DIAG_CONTACT_EXEMPT')
    os.environ['ATTN_DIAG_CONTACT_EXEMPT']=args.exempt
    started=time.monotonic();error=None
    try:run(args.out,args.seed,source)
    except Exception as exc:error=f'{type(exc).__name__}: {exc}'
    finally:
        if old is None:os.environ.pop('ATTN_DIAG_CONTACT_EXEMPT',None)
        else:os.environ['ATTN_DIAG_CONTACT_EXEMPT']=old
    result=summarize(args.out,args.seed,shift,args.exempt,args.tilt,time.monotonic()-started,error,
                     grasp_quat=quat,oracle=oracle)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'policy.py').write_text(source)
    (args.out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (args.out/'results.md').write_text(markdown(result))
    print(json.dumps({'results':str(args.out/'results.json'),'first_failure':result['first_failure'],'object_lifted_5cm':result['object_lifted_5cm']},ensure_ascii=False))
    return 0 if error is None else 1

if __name__=='__main__':raise SystemExit(main())
