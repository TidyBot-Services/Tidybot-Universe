"""Scripted seed 101–103 grasp-chain probe; real execution is explicit."""
from __future__ import annotations
import argparse, hashlib, json, math, os, time
from pathlib import Path
from openarm365_dev.policy_validation import validate_generated_policy

STAGES=('base','open','pregrasp','descend','close','lift','held')

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
        # Two world-frame rotations respect the 0.5 rad SDK coordinate bound.
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

def summarize(root,seed,shift,exempt,tilt,elapsed,error=None):
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
    stages=[];boundary_ops={'base':'get_observation','open':'get_observation','pregrasp':'get_observation',
        'descend':'get_observation','close':'get_observation','lift':'get_observation','held':'find_objects'}
    stage_idx=0;last=0;first_failure=None;last_object=initial.get('perception',{}).get('values',{}).get('obj_pos')
    last_eef=initial.get('robot',{}).get('robot0_left_eef_pos');last_gripper=initial.get('robot',{}).get('robot0_left_gripper_qpos')
    object_before_lift=None;object_after_lift=None;cert_cursor=0
    for i,event in enumerate(events):
        if event.get('operation')=='find_objects' and event.get('status')=='completed':
            seen=next((x.get('position') for x in event.get('objects',[]) if x.get('name')=='obj'),None)
            if seen is not None:
                last_object=seen
                if stage_idx<=5:object_before_lift=seen
                else:object_after_lift=seen
        if event.get('status')!='completed' and first_failure is None:
            first_failure={'operation':event.get('operation'),'reason':event.get('error')}
        if stage_idx>=len(STAGES):break
        name=STAGES[stage_idx]
        if event.get('operation')==boundary_ops[name] and event.get('status')=='completed':
            segment=events[last:i+1];obs=event.get('observation') or {}
            if isinstance(obs,dict) and 'robot' in obs:obs=obs['robot']
            if isinstance(obs,dict):
                last_eef=obs.get('robot0_left_eef_pos',last_eef)
                last_gripper=obs.get('robot0_left_gripper_qpos',last_gripper)
            arm_moves=sum(x.get('operation')=='move_delta' and x.get('source')=='left_arm' for x in segment)
            stage_certs=certs[cert_cursor:cert_cursor+arm_moves];cert_cursor+=arm_moves
            stages.append(dict(stage=name,public_status='completed',sdk_calls=len(segment),
                certification_count=arm_moves,
                certification_elapsed_seconds=sum(float(x.get('elapsed_seconds') or 0) for x in stage_certs),
                eef_position=last_eef,gripper_qpos=last_gripper,
                object_position=last_object))
            last=i+1;stage_idx+=1
    public_error=_json(feedback).get('public_error') if feedback else None
    if stage_idx<len(STAGES):
        stages.append(dict(stage=STAGES[stage_idx],public_status='failed',first_failure=first_failure or error or public_error))
    before=initial.get('perception',{}).get('values',{}).get('obj_pos')
    after=final.get('perception',{}).get('values',{}).get('obj_pos')
    lifted=bool(object_before_lift and object_after_lift and object_after_lift[2]-object_before_lift[2]>=.05)
    if not lifted and stages and stages[-1]['stage']=='held' and stages[-1]['public_status']=='completed':
        stages[-1].update(public_status='failed',first_failure=public_error or 'object rose less than 5cm')
        first_failure=first_failure or {'operation':'held','reason':stages[-1]['first_failure']}
    return dict(seed=seed,base_shift=shift,exempt=exempt,tilt=tilt,wall_seconds=elapsed,
        stages=stages,certificates=certs,first_failure=first_failure or error or public_error,
        initial_eef=initial.get('robot',{}).get('robot0_left_eef_pos'),final_eef=final.get('robot',{}).get('robot0_left_eef_pos'),
        initial_object=before,final_object=after,object_before_lift=object_before_lift,object_after_lift=object_after_lift,
        initial_gripper=initial.get('robot',{}).get('robot0_left_gripper_qpos'),
        final_gripper=final.get('robot',{}).get('robot0_left_gripper_qpos'),object_lifted_5cm=lifted,
        execution_dir=str(episode) if episode else None)

def markdown(row):
    lines=[f"# Seed {row['seed']} 抓取链路探针",'',
           f"豁免：`{row['exempt']}`；底座位移：`{row['base_shift']}`；倾斜：`{row['tilt']}`。",'',
           '| 阶段 | 公开结果 | SDK 次数 | 认证动作数 | 认证耗时(s) | 末端世界坐标 | 物体世界坐标 | 夹爪开度 | 首个失败 |',
           '| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |']
    for s in row['stages']:
        val=lambda key:json.dumps(s.get(key),ensure_ascii=False) if s.get(key) is not None else '—'
        lines.append(f"| {s['stage']} | {s['public_status']} | {s.get('sdk_calls','—')} | {s.get('certification_count','—')} | {s.get('certification_elapsed_seconds','—')} | {val('eef_position')} | {val('object_position')} | {val('gripper_qpos')} | {val('first_failure')} |")
    lines += ['',f"物体上升至少 5 cm：`{row['object_lifted_5cm']}`。",'',
              f"首个失败：`{json.dumps(row['first_failure'],ensure_ascii=False)}`。",'',
              f"认证证书：`{json.dumps(row['certificates'],ensure_ascii=False)}`。",'']
    return '\n'.join(lines)

def run(root,seed,source):
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    from benchmarks.attention_harness.dev_experiment import create,run,verify_manifest
    from benchmarks.attention_harness.diagnostics.motion_tracking_matrix import ScriptedClient
    root.mkdir(parents=True,exist_ok=False)
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
    p.add_argument('--dry-run',action='store_true')
    args=p.parse_args(argv)
    try:
        shift=tuple(float(x) for x in args.base_shift.split(','))
        if len(shift)!=3:raise ValueError()
        source=policy_source(shift,args.tilt)
    except ValueError as exc:p.error('invalid --base-shift or policy: '+str(exc))
    if args.dry_run:
        print(source,end='');return 0
    if args.out.exists():p.error('--out already exists; choose a fresh directory')
    old=os.environ.get('ATTN_DIAG_CONTACT_EXEMPT')
    os.environ['ATTN_DIAG_CONTACT_EXEMPT']=args.exempt
    started=time.monotonic();error=None
    try:run(args.out,args.seed,source)
    except Exception as exc:error=f'{type(exc).__name__}: {exc}'
    finally:
        if old is None:os.environ.pop('ATTN_DIAG_CONTACT_EXEMPT',None)
        else:os.environ['ATTN_DIAG_CONTACT_EXEMPT']=old
    result=summarize(args.out,args.seed,shift,args.exempt,args.tilt,time.monotonic()-started,error)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'policy.py').write_text(source)
    (args.out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (args.out/'results.md').write_text(markdown(result))
    print(json.dumps({'results':str(args.out/'results.json'),'first_failure':result['first_failure'],'object_lifted_5cm':result['object_lifted_5cm']},ensure_ascii=False))
    return 0 if error is None else 1

if __name__=='__main__':raise SystemExit(main())
