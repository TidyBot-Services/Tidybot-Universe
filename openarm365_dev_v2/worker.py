"""Private pipe-owned native process. Policy code never enters this process."""
from benchmarks.attention_harness.workspace_paths import get_str
import argparse,ctypes,json,os,signal,sys,threading,time,traceback,uuid,hashlib
from pathlib import Path

def native_evaluation(env, config):
    """Owner-only task evidence; never returned through the public SDK."""
    if getattr(env, '_attentionbench_success', None):
        # R5 fixture runs: either arm presses, both arms released, task fixes (fixture_success.py).
        from .fixture_success import evaluate
        value = {'evaluated': True, **evaluate(env)}
    else:
        value = {'evaluated': True, 'native_success': bool(env._check_success())}
    if config.get('execution_settings') and config['task_id'] == 'pick_place_counter_to_sink':
        from robocasa.utils import object_utils
        value['object_in_sink'] = bool(object_utils.obj_inside_of(env, 'obj', env.sink, partial_check=True))
        value['object_in_sink_source'] = 'robocasa.utils.object_utils.obj_inside_of(partial_check=True)'
    if config.get('execution_settings') and config['task_id'] == 'pick_place_counter_to_cabinet':
        from robocasa.utils import object_utils
        # Match upstream _check_success: full object bounds, default th=0.05.
        value['object_in_cabinet'] = bool(object_utils.obj_inside_of(env, 'obj', env.cab))
        value['object_in_cabinet_source'] = 'robocasa.utils.object_utils.obj_inside_of(partial_check=False, th=0.05)'
    return value

def cabinet_public_values(env):
    return {'cabinet_pos': [float(x) for x in env.cab.pos]} if hasattr(env, 'cab') else {}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--deadline',type=float,required=True)
    parser.add_argument('--owner-pid',type=int,required=True);parser.add_argument('--agent-mode',action='store_true');args=parser.parse_args()
    output=os.fdopen(os.dup(sys.stdout.fileno()),'w',buffering=1)
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno())  # Also redirect native C/C++ stdout.
    sys.stdout=sys.stderr  # Upstream imports may print; stdout is exclusively RPC.
    def send(value):output.write(json.dumps(value,allow_nan=False)+'\n');output.flush()
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,9,0,0,0)!=0 or os.getppid()!=args.owner_pid:raise RuntimeError('Cannot bind native owner lifetime')
    stop=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stop.set());signal.signal(signal.SIGINT,lambda *_:stop.set())
    env=planner=executor=None;cleanup={};receipt_cache={};episode=uuid.uuid4().hex
    try:
        import numpy as np
        from scipy.spatial.transform import Rotation
        from .formal_config import validate_native_config as validate_config
        from openarm365_dev.identity import OfficialEnvironmentIdentity
        from .environment import create_env,tcp_pose,PHYSICAL_TO_NATIVE
        from openarm365_dev.planner import MujocoV2Planner
        from openarm365_dev.executor import NativeExecutor
        from curobo_v2.execution_settings import from_config,receipt_fields
        from curobo_v2.fixture_telemetry import FixtureContactBaseDisplacementRejected
        from openarm365_dev.policy_validation import OPERATIONS
        from openarm365.environment import validate_physical_model,configure_display_camera
        from .diag_budget import diagnostic_budget,dev_tier_budget
        override=diagnostic_budget()
        c=validate_config(json.loads(args.config.read_bytes()));args.evidence.mkdir(parents=True,exist_ok=True)
        execution_settings=from_config(c.get('execution_settings'))
        from curobo_v2.fixture_interaction import enabled as fixture_enabled,find_fixtures
        from openarm365_dev.policy_validation import operations
        allowed_operations=operations(c)
        if fixture_enabled(execution_settings):
            from .fixture_contract import configure_camera,camera_target
            def configure_display_camera(env):configure_camera(env,c)
        if args.agent_mode:
            if c.get('budget_tier') != 'dev' or c['runner_boundary'] != 'dev_loop':
                raise ValueError('agent requires dev tier')
            from .agent_planner import AgentPlanner as MujocoV2Planner
            from .agent_rejection import RecoverablePlanRejection
            from .agent_executor import AgentExecutor as NativeExecutor
        tier=dev_tier_budget() if c['runner_boundary']=='dev_loop' and c.get('budget_tier','legacy')=='dev' else None
        if override and c['runner_boundary']!='dev_loop':raise ValueError('diagnostic budget cannot enter formal worker')
        identity=OfficialEnvironmentIdentity({
            'robocasa':get_str('sim.robocasa_root'),
            'robosuite':get_str('sim.robosuite_root')},
            get_str('sim.assets_manifest')).verify_identity()
        env=create_env(c['official_env'],robots='OpenArm365',seed=c['seed'],obj_instance_split=c['obj_instance_split'],
            **({'fixture_profile':execution_settings.profile} if fixture_enabled(execution_settings) else {}),
            layout_and_style_ids=[(c['layout_id'],c['style_id'])],camera_widths=128,camera_heights=128,randomize_cameras=False,
            generative_textures=None,control_freq=c['control_freq'],horizon=c['horizon'],**({'scene_reset_seed':c['scene_reset_seed']} if 'scene_reset_seed' in c else {}))
        env.reset();validate_physical_model(env);configure_display_camera(env)
        fixture_condition=None
        if fixture_enabled(execution_settings):
            from .fixture_success import install as install_success
            install_success(env)
            from .fixture_contract import validate_reset
            fixture_condition=validate_reset(env,c,execution_settings)
        elif execution_settings and c['task_id']=='pick_place_counter_to_sink':
            # 11-10 (user): either arm may finish Counter -> Sink too, and neither may still hold the object
            # (upstream checks the physical left gripper only). Dev tier only; formal runs keep the upstream verdict.
            from .fixture_success import install as install_success
            install_success(env)
        test_fixture_receipt=None
        if 'test_fixture' in c:
            from .test_fixture import apply_test_fixture
            test_fixture_receipt=apply_test_fixture(env,c['test_fixture'])
        planner=MujocoV2Planner(env,evidence=args.evidence/'planner',deadline=args.deadline,cancel_check=stop.is_set,
            contact_exempt_objects=c['contact_exempt_objects'],planner_mode=c.get('planner_mode','plan'),
            ik_candidates=c.get('ik_candidates'),ik_max_joint_jump=c.get('ik_max_joint_jump'),
            **({} if 'execution_settings' not in c else dict(execution_settings=execution_settings)))
        send({'kind':'spawn','pid':planner.owner.process.pid})
        planner.prepare(planner.names)
        from benchmarks.attention_harness.robocasa365.recording import StepRecorder
        from benchmarks.attention_harness.robocasa365.human_camera import capture_human_view
        from .extra_views import live_version
        context=json.loads((args.evidence/'recording_context.json').read_bytes())
        recorder=StepRecorder(args.evidence,context)
        # Display-only extra angles (side, top, both wrists). Never policy input; every failure is contained.
        extra=None
        try:
            from .extra_views import ExtraViews,look_target
            extra=ExtraViews(env,args.evidence,context,episode,camera_target(env,c) if fixture_enabled(execution_settings) else look_target(env))
        except Exception:extra=None
        def capture(sequence):
            capture_human_view(recorder,env,episode,sequence)
            # Copy the actual just-captured RGB, atomic metadata publication.
            if recorder.count and recorder.last_sequence==sequence:
                import shutil
                live=args.evidence/'live';live.mkdir(exist_ok=True)
                image=live/f'frame-{sequence:06d}.jpg'
                shutil.copyfile(recorder.directory/f'{recorder.count-1:06d}.jpg',image)
                row={'episode_id':episode,'version':live_version(context), 'sequence':sequence,
                     'control_step':sequence,'monotonic':time.monotonic(),'wall_time':time.time(),
                     'file':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}
                tmp=live/'latest.tmp';tmp.write_text(json.dumps(row));tmp.replace(live/'latest.json')
                if extra is not None:
                    try:extra.capture(sequence)
                    except Exception:pass
        executor=NativeExecutor(env,planner,evidence=args.evidence,deadline=args.deadline,cancel_check=stop.is_set,
            frame_callback=capture,held_reference_configured=c['held_reference_mode'],
            held_reference_per_motion=c.get('test_fixture',{}).get('held_reference')=='per_motion',
            held_contact_only=c.get('test_fixture',{}).get('held_reference')=='contact_only',
            handoff_receiver_fingers=c.get('test_fixture',{}).get('handoff_receiver_fingers') is True,
            bimanual_collaboration=c.get('bimanual_collaboration','none'),
            **({} if c.get('arm_motion')!='long' else dict(arm_motion='long')),
            **({} if execution_settings is None else dict(execution_settings=execution_settings)))
        capture(0)
        def observe():
            robot={}
            r=env.robots[0]
            for side,native in PHYSICAL_TO_NATIVE.items():
                t=tcp_pose(env,side)
                robot[f'robot0_{side}_eef_pos']=t[:3,3].tolist()
                robot[f'robot0_{side}_eef_quat']=Rotation.from_matrix(t[:3,:3]).as_quat().tolist()
                robot[f'robot0_{side}_joint_pos']=[float(executor.planner.state()[executor.planner.names.index(f'openarm_{side}_joint{i}')]) for i in range(1,8)]
                robot[f'robot0_{side}_gripper_qpos']=env.sim.data.qpos[r._ref_gripper_joint_pos_indexes[native]].tolist()
            values={n+'_pos':env.sim.data.body_xpos[bid].tolist() for n,bid in env.obj_body_id.items()}
            if hasattr(env,'sink'):values['sink_pos']=np.asarray(env.sink.pos,float).tolist()
            values.update(cabinet_public_values(env))
            # Public sim_gt fixture/handle positions, never scoring/door-state tests.
            if hasattr(env,'fxtr'):
                values['cabinet_pos']=np.asarray(env.fxtr.pos,float).tolist()
                for gid in range(env.sim.model.ngeom):
                    name=env.sim.model.geom_id2name(gid) or ''
                    if name.startswith(env.fxtr.name) and 'handle' in name:
                        values[name+'_pos']=env.sim.data.geom_xpos[gid].tolist()
            result=dict(episode_id=episode,sequence=executor.sequence,robot=robot,images={},
                perception={'source':'official_public_sim_gt','values':values})
            if fixture_enabled(execution_settings):result['fixtures']=find_fixtures(env,execution_settings)
            return result
        from .scene_contract import capture_state,validate,host_info
        scene_state=capture_state(env)
        condition=validate(c['scene_contract'],c['task_id'],scene_state,c['seed']) if 'scene_contract' in c else None
        if condition is not None:
            try:condition['host']=host_info()
            except Exception as exc:condition['host']={'error':repr(exc)}
        (args.evidence/'scene-condition.json').write_text(json.dumps({'state':scene_state,'validation':condition},sort_keys=True,indent=2)+'\n')
        scene=env.model.get_xml()
        (args.evidence/'scene.xml').write_text(scene)
        robot=env.robots[0]
        execution_conditions=dict(robot_model=type(robot.robot_model).__name__,
            native_action_dimension=len(env.action_spec[0]),
            native_controller_slices=executor.parts,
            controller_inputs={side:robot.composite_controller.part_controllers[native].input_type
                for side,native in PHYSICAL_TO_NATIVE.items()},
            grippers={side:type(robot.gripper[native]).__name__ for side,native in PHYSICAL_TO_NATIVE.items()},
            joint_names=planner.names,base_mode='stationary')
        reset_receipt=dict(episode_id=episode,
            task_id=c['task_id'],seed=c['seed'],layout_id=c['layout_id'],style_id=c['style_id'],config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
            physical_to_native=PHYSICAL_TO_NATIVE,identity=identity,rng_events=env.scene_rng_events,scene_reset_seed=c.get('scene_reset_seed'),initial_public_observation=observe(),
            execution_conditions=execution_conditions,scene_xml_sha256=hashlib.sha256(scene.encode()).hexdigest())
        reset_receipt.update(receipt_fields(execution_settings))
        if test_fixture_receipt is not None:reset_receipt['test_fixture']=test_fixture_receipt
        if c.get('bimanual_collaboration') in ('c1','handoff'):reset_receipt['bimanual_collaboration']=c['bimanual_collaboration']
        if c.get('bimanual_collaboration')=='handoff':reset_receipt.update(executor.collaboration_receipt())
        if fixture_condition:reset_receipt['fixture_scene_condition']=fixture_condition
        if override:reset_receipt['diagnostic_budget_override']=override
        if tier:reset_receipt['dev_tier_budget']=tier
        (args.evidence/'reset_receipt.json').write_text(json.dumps(reset_receipt,indent=2)+'\n')
        import benchmarks,openarm365_dev_v2
        (args.evidence/'import-canary.json').write_text(json.dumps({'benchmarks':benchmarks.__file__,
            'openarm365_dev_v2':openarm365_dev_v2.__file__,'worker':__file__,'loaded_python_files':sorted({str(Path(mod.__file__).resolve()) for mod in sys.modules.values() if getattr(mod,'__file__',None) and Path(mod.__file__).suffix=='.py'}),'cwd':os.getcwd(),
            'python':sys.executable,'official_planner_lock':c['dev_lock']},indent=2))
        send({'kind':'ready' ,'episode_id':episode,'initial_observation':observe(),'scene_state':scene_state,'scene_condition':condition,
            'capabilities':{'robot':'OpenArm365','execution_arm':'both','base_mode':'commanded_motion',
                'arm_frame':'world','controller':'BASIC / authenticated V2 joint trajectories','formal_eligible':False}})
        for line in sys.stdin:
            if stop.is_set() or time.monotonic()>=args.deadline:break
            request=json.loads(line);rid=request['request_id'];canonical=json.dumps(request,sort_keys=True,allow_nan=False)
            if rid in receipt_cache:
                old,payload=receipt_cache[rid]
                if old!=canonical:raise RuntimeError('Request ID reused with a different payload')
                send(payload);continue
            group,operation=request['group'],request['operation']
            public=group!='owner'
            if public and operation not in allowed_operations.get(group,()):raise ValueError('Unauthorized native SDK operation')
            before=executor.sequence
            before_physics=executor.physics_steps
            if args.agent_mode:
                from .agent_probe import state_fingerprint
                before_state=state_fingerprint(env, executor)
            try:
                value=None;empty_close=None
                if group=='owner':
                    if operation=='observe':value=observe()
                    elif operation=='gripper_move_to' and fixture_enabled(execution_settings):
                        value=executor.gripper_move_to(*request['args'],**request['kwargs'],request_id=rid)
                    elif operation=='find_fixtures' and fixture_enabled(execution_settings):
                        if request['args'] or request['kwargs']:raise ValueError('find_fixtures takes no arguments')
                        value=find_fixtures(env,execution_settings)
                    elif operation=='agent_probe' and args.agent_mode:
                        from .agent_probe import probe
                        value=probe(env, executor, planner, observe, request['kwargs'])
                    elif operation=='reset':
                        if c.get('scene_contract'):raise ValueError('matrix reset requires a new metered execution')
                        env.reset();validate_physical_model(env);configure_display_camera(env)
                        if fixture_enabled(execution_settings):validate_reset(env,c,execution_settings)
                        if 'test_fixture' in c:apply_test_fixture(env,c['test_fixture'])
                        value={'initial_observation':observe(),'scene_state':capture_state(env),'rng_events':list(env.scene_rng_events)}
                    elif operation=='evaluate':
                        # Separate owner-only evidence, never a public SDK return.
                        value=native_evaluation(env,c)
                    elif operation=='stop':break
                    else:raise ValueError('Unauthorized owner operation')
                elif group=='sensors':
                    obs=observe()
                    if operation=='get_observation':value=obs['robot']
                    elif operation=='find_fixtures':
                        if request['args'] or request['kwargs']:raise ValueError('find_fixtures takes no arguments')
                        value=find_fixtures(env,execution_settings)
                    else:value=[{'name':n[:-4],'position':p,'frame':'world'} for n,p in obs['perception']['values'].items() if n.endswith('_pos')]
                elif group in {'left_arm','right_arm'}:
                    side=group.split('_')[0]
                    receipt=executor.move_delta(side,*request['args'],**request['kwargs'],request_id=rid)
                    from curobo_v2.grasp_heading import feedback
                    public_heading=feedback(receipt)
                    if public_heading:value=public_heading
                elif group=='base':
                    executor.base_move(*request['args'],**request['kwargs'],request_id=rid)
                elif group in {'left_gripper','right_gripper'}:
                    if operation=='move_to' and fixture_enabled(execution_settings):
                        value=executor.gripper_move_to(group.split('_')[0],*request['args'],**request['kwargs'],request_id=rid)
                    else:
                        if request['args'] or request['kwargs']:raise ValueError('Gripper open/close takes no arguments')
                        gripper_receipt=executor.gripper(group.split('_')[0],-1. if operation=='open' else 1.,request_id=rid)
                        if fixture_enabled(execution_settings):empty_close=gripper_receipt['empty_close']
                elif group=='bimanual':
                    if len(request['args'])!=1 or request['kwargs']:raise ValueError('bimanual.move_joints requires one full 14-joint vector')
                    target=np.asarray(request['args'][0],float)
                    if target.shape!=(14,) or not np.isfinite(target).all():raise ValueError('Finite 14-joint vector required')
                    executor.move_joints(target,side='both',request_id=rid)
                response={'kind':'response','request_id':rid,'ok':True,'value':value,
                    'sequence':executor.sequence,'start_sequence':before}
                if empty_close is not None:response['empty_close']=empty_close
            except Exception as exc:
                from curobo_v2.fixture_gripper import GripperWidthRejected
                response={'kind':'response','request_id':rid,'ok':False,
                    'error':f'{type(exc).__name__}: {exc}'[:1200],'sequence':executor.sequence,'start_sequence':before}
                if (fixture_enabled(execution_settings) and isinstance(exc, (FixtureContactBaseDisplacementRejected,GripperWidthRejected))
                        and not executor.failed):
                    response.update(ok=True,value=exc.public())
                    response.pop('error',None)
                elif (args.agent_mode and isinstance(exc, RecoverablePlanRejection)
                        and executor.sequence == before and not executor.failed
                        and state_fingerprint(env, executor) == before_state):
                    response.update(ok=True, value=exc.public())
                    response.pop('error',None)
                # Log full private exception separately, not in public feedback.
                with (args.evidence/'native-errors.jsonl').open('a') as f:
                    f.write(json.dumps({'request_id':rid,'traceback':traceback.format_exc()})+'\n')
            response.update(receipt_fields(execution_settings))
            if c.get('bimanual_collaboration') in ('c1','handoff'):response.update(executor.collaboration_receipt())
            if fixture_enabled(execution_settings) and public and group!='sensors':
                from curobo_v2.fixture_telemetry import receipt as fixture_receipt
                response.update(fixture_receipt(executor,after_physics_step=before_physics))
            if override:response['diagnostic_budget_override']=override
            if tier:response['dev_tier_budget']=tier
            receipt_cache[rid]=(canonical,response)
            with (args.evidence/'rpc-receipts.jsonl').open('a') as f:
                f.write(json.dumps({'request':request,'response':response},sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
            send(response)
    except BaseException as exc:
        send({'kind':'fatal','error':f'{type(exc).__name__}: {exc}'[:1200]})
        with (args.evidence/'startup-error.txt').open('w') as f:f.write(traceback.format_exc())
    finally:
        from .live_retention import close_execution
        close_execution(args.evidence,executor,planner,env,cleanup)
        (args.evidence/'native_cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
        output.close()

if __name__=='__main__':main()
