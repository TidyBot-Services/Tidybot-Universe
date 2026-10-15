"""Private pipe-owned native process. Policy code never enters this process."""
import argparse,ctypes,json,os,signal,sys,threading,time,traceback,uuid,hashlib
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True);parser.add_argument('--deadline',type=float,required=True)
    parser.add_argument('--owner-pid',type=int,required=True);args=parser.parse_args()
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
        from .dev_config import validate_dev_config as validate_config
        from openarm365_dev.identity import OfficialEnvironmentIdentity
        from .environment import create_env,tcp_pose,PHYSICAL_TO_NATIVE
        from openarm365_dev.planner import MujocoV2Planner
        from openarm365_dev.executor import NativeExecutor
        from openarm365_dev.policy_validation import OPERATIONS
        from openarm365.environment import validate_physical_model,configure_display_camera
        c=validate_config(json.loads(args.config.read_bytes()));args.evidence.mkdir(parents=True,exist_ok=True)
        identity=OfficialEnvironmentIdentity({
            'robocasa':'/home/truares/Documents/Codex/2026-10-09/new-chat/work/upstream/robocasa',
            'robosuite':'/home/truares/Documents/Codex/2026-10-09/new-chat/work/upstream/robosuite'},
            '/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365/assets-manifest.json').verify_identity()
        env=create_env(c['official_env'],robots='OpenArm365',seed=c['seed'],obj_instance_split=c['obj_instance_split'],
            layout_and_style_ids=[(c['layout_id'],c['style_id'])],camera_widths=128,camera_heights=128,randomize_cameras=False,
            generative_textures=None,control_freq=c['control_freq'],horizon=c['horizon'],**({'scene_reset_seed':c['scene_reset_seed']} if 'scene_reset_seed' in c else {}))
        env.reset();validate_physical_model(env);configure_display_camera(env)
        planner=MujocoV2Planner(env,evidence=args.evidence/'planner',deadline=args.deadline,cancel_check=stop.is_set)
        send({'kind':'spawn','pid':planner.owner.process.pid})
        planner.prepare(planner.names)
        from benchmarks.attention_harness.robocasa365.recording import StepRecorder
        from benchmarks.attention_harness.robocasa365.human_camera import capture_human_view
        context=json.loads((args.evidence/'recording_context.json').read_bytes())
        recorder=StepRecorder(args.evidence,context)
        # Display-only extra angles (side, top, both wrists). Never policy input; every failure is contained.
        extra=None
        try:
            from .extra_views import ExtraViews,look_target
            extra=ExtraViews(env,args.evidence,context,episode,look_target(env))
        except Exception:extra=None
        def capture(sequence):
            capture_human_view(recorder,env,episode,sequence)
            # Copy the actual just-captured RGB, atomic metadata publication.
            if recorder.count and recorder.last_sequence==sequence:
                import shutil
                live=args.evidence/'live';live.mkdir(exist_ok=True)
                image=live/f'frame-{sequence:06d}.jpg'
                shutil.copyfile(recorder.directory/f'{recorder.count-1:06d}.jpg',image)
                row={'episode_id':episode,'version':context.get('dev_version'), 'sequence':sequence,
                     'control_step':sequence,'monotonic':time.monotonic(),'wall_time':time.time(),
                     'file':image.name,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}
                tmp=live/'latest.tmp';tmp.write_text(json.dumps(row));tmp.replace(live/'latest.json')
                if extra is not None:
                    try:extra.capture(sequence)
                    except Exception:pass
        executor=NativeExecutor(env,planner,evidence=args.evidence,deadline=args.deadline,cancel_check=stop.is_set,frame_callback=capture)
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
            # Public sim_gt fixture/handle positions, never scoring/door-state tests.
            if hasattr(env,'fxtr'):
                values['cabinet_pos']=np.asarray(env.fxtr.pos,float).tolist()
                for gid in range(env.sim.model.ngeom):
                    name=env.sim.model.geom_id2name(gid) or ''
                    if name.startswith(env.fxtr.name) and 'handle' in name:
                        values[name+'_pos']=env.sim.data.geom_xpos[gid].tolist()
            return dict(episode_id=episode,sequence=executor.sequence,robot=robot,images={},
                perception={'source':'official_public_sim_gt','values':values})
        from .scene_contract import capture_state,validate
        scene_state=capture_state(env)
        condition=validate(c['scene_contract'],c['task_id'],scene_state) if 'scene_contract' in c else None
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
        (args.evidence/'reset_receipt.json').write_text(json.dumps(dict(episode_id=episode,
            task_id=c['task_id'],seed=c['seed'],layout_id=c['layout_id'],style_id=c['style_id'],config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
            physical_to_native=PHYSICAL_TO_NATIVE,identity=identity,rng_events=env.scene_rng_events,scene_reset_seed=c.get('scene_reset_seed'),initial_public_observation=observe(),
            execution_conditions=execution_conditions,scene_xml_sha256=hashlib.sha256(scene.encode()).hexdigest()),indent=2)+'\n')
        import benchmarks,openarm365_dev_v2
        (args.evidence/'import-canary.json').write_text(json.dumps({'benchmarks':benchmarks.__file__,
            'openarm365_dev_v2':openarm365_dev_v2.__file__,'worker':__file__,'loaded_python_files':sorted({str(Path(mod.__file__).resolve()) for mod in sys.modules.values() if getattr(mod,'__file__',None) and Path(mod.__file__).suffix=='.py'}),'cwd':os.getcwd(),
            'python':sys.executable,'official_planner_lock':c['dev_lock']},indent=2))
        send({'kind':'ready' ,'episode_id':episode,'initial_observation':observe(),'scene_state':scene_state,'scene_condition':condition,
            'capabilities':{'robot':'OpenArm365','execution_arm':'both','base_mode':'stationary',
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
            if public and operation not in OPERATIONS.get(group,()):raise ValueError('Unauthorized native SDK operation')
            before=executor.sequence
            try:
                value=None
                if group=='owner':
                    if operation=='observe':value=observe()
                    elif operation=='reset':
                        if c.get('scene_contract'):raise ValueError('matrix reset requires a new metered execution')
                        env.reset();validate_physical_model(env);configure_display_camera(env)
                        value={'initial_observation':observe(),'scene_state':capture_state(env),'rng_events':list(env.scene_rng_events)}
                    elif operation=='evaluate':
                        # Separate owner-only evidence, never a public SDK return.
                        value={'evaluated':True,'native_success':bool(env._check_success())}
                    elif operation=='stop':break
                    else:raise ValueError('Unauthorized owner operation')
                elif group=='sensors':
                    obs=observe()
                    if operation=='get_observation':value=obs['robot']
                    else:value=[{'name':n[:-4],'position':p,'frame':'world'} for n,p in obs['perception']['values'].items() if n.endswith('_pos')]
                elif group in {'left_arm','right_arm'}:
                    side=group.split('_')[0]
                    executor.move_delta(side,*request['args'],**request['kwargs'],request_id=rid)
                elif group in {'left_gripper','right_gripper'}:
                    if request['args'] or request['kwargs']:raise ValueError('Gripper open/close takes no arguments')
                    executor.gripper(group.split('_')[0],-1. if operation=='open' else 1.,request_id=rid)
                elif group=='bimanual':
                    if len(request['args'])!=1 or request['kwargs']:raise ValueError('bimanual.move_joints requires one full 14-joint vector')
                    target=np.asarray(request['args'][0],float)
                    if target.shape!=(14,) or not np.isfinite(target).all():raise ValueError('Finite 14-joint vector required')
                    executor.move_joints(target,side='both',request_id=rid)
                response={'kind':'response','request_id':rid,'ok':True,'value':value,
                    'sequence':executor.sequence,'start_sequence':before}
            except Exception as exc:
                response={'kind':'response','request_id':rid,'ok':False,
                    'error':f'{type(exc).__name__}: {exc}'[:1200],'sequence':executor.sequence,'start_sequence':before}
                # Log full private exception separately, not in public feedback.
                with (args.evidence/'native-errors.jsonl').open('a') as f:
                    f.write(json.dumps({'request_id':rid,'traceback':traceback.format_exc()})+'\n')
            receipt_cache[rid]=(canonical,response)
            with (args.evidence/'rpc-receipts.jsonl').open('a') as f:
                f.write(json.dumps({'request':request,'response':response},sort_keys=True)+'\n');f.flush();os.fsync(f.fileno())
            send(response)
    except BaseException as exc:
        send({'kind':'fatal','error':f'{type(exc).__name__}: {exc}'[:1200]})
        with (args.evidence/'startup-error.txt').open('w') as f:f.write(traceback.format_exc())
    finally:
        if executor:cleanup['planner']=executor.close()
        elif planner:cleanup['planner']=planner.close()
        if env:env.close()
        (args.evidence/'native_cleanup.json').write_text(json.dumps(cleanup,indent=2)+'\n')
        output.close()

if __name__=='__main__':main()
