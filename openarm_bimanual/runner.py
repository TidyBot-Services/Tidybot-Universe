"""Original RoboCasa runner with one owned OpenArm process and SDK-only policy."""
from contextlib import ExitStack
from pathlib import Path
import hashlib
import io
import json
import time
import urllib.request
import numpy as np

from tidybot_sdk import TidyBotSDK
from .perception import BimanualPerceptionBackend
from benchmarks.attention_harness.robosuite_memory.formal_sandbox import execute_formal_policy, probe_sandbox, _json_safe
from benchmarks.attention_harness.service_recovery import service_stop_confirmed
from benchmarks.attention_harness.ui_media import AttemptCameraRecording, rgb_png
from benchmarks.attention_harness.public_station import publish_public_station
from benchmarks.attention_harness.robocasa_native.mobile_sdk import _RobocasaArmFacade, _BaseFacade
from .backend import MobileOpenArmActionBackend as OpenArmActionBackend
from .config import validate_config, attest_runtime, digest
from benchmarks.attention_harness.robocasa_native.policy_sandbox import validate_generated_policy
from benchmarks.attention_harness.robocasa_native.robot_config import robot_selection
from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend
from benchmarks.attention_harness.robocasa_native.tasks import get_robocasa_task
from .session import OwnedOpenArmService


from .sdk import BimanualSDK as OpenArmSDK, DualSafetyMonitor


def write(path, value):
    path.write_text(json.dumps(_json_safe(value), indent=2, sort_keys=True, allow_nan=False)+'\n')
    return path.resolve()


class BimanualOpenArmFormalSuiteRunner:
    suite='robocasa'

    def __init__(self, *, sim_python, task_source_root, cancel_event=None, policy_start_hook=None, record_replay=False):
        self.sim_python=Path(sim_python);self.task_source_root=Path(task_source_root)
        self.cancel_event=cancel_event;self.policy_start_hook=policy_start_hook;self.record_replay=record_replay

    def execute(self, request):
        request.validate()
        config=validate_config(request,json.loads(request.config_path.read_bytes()))
        selection=robot_selection(config);code=request.policy_code_path.read_text()
        validate_generated_policy(code)
        attest_runtime(config,python=self.sim_python,task_source_root=self.task_source_root)
        started=time.monotonic();deadline=started+request.effective_attempt_deadline_seconds
        episode=request.artifact_root/f'{request.task_id}-seed{request.seed}-openarm-{time.time_ns()}'
        episode.mkdir(parents=True,exist_ok=False)
        write(episode/'approved_config.json',config);(episode/'approved_policy.py').write_bytes(code.encode())
        probe=probe_sandbox();events=[];recording=None;monitor=None;action=None;outcome=None
        status='failed';error=None;native=None;attestation=None;first_sha=None;last_sha=None;interface_attested=False
        owned=OwnedOpenArmService(episode/'owned',python=self.sim_python,task=request.task_id,seed=request.seed,
            arm=selection.execution_arm,deadline=deadline,cancel_event=self.cancel_event,
            parameters_path=config['robot_parameters']['path'])
        interrupted=lambda:bool(self.cancel_event and self.cancel_event.is_set())
        try:
            with owned, ExitStack() as cameras:
                action=OpenArmActionBackend(owned.url,execution_arm=selection.execution_arm,
                    interrupt_check=interrupted,timeout_seconds=min(90.,request.effective_attempt_deadline_seconds))
                action.set_episode_deadline(deadline)
                health=action._call('GET','/health')
                if health.get('model_sha256')!=config['model_sha256'] or health.get('parameters_sha256')!=config['robot_parameters']['sha256']:
                    raise RuntimeError('OpenArm loaded model/config identity differs from approved launch')
                interface_attested=True
                attestation=action._call('GET','/task/info')
                if (attestation.get('robot')!=selection.artifact() or attestation.get('lang')!=config['task_prompt'] or
                      attestation.get('cameras')!=config['camera_names'] or
                      attestation.get('variation')!={k:config[k] for k in ['scene_id','object_set_id']}):
                    raise RuntimeError('OpenArm native task/scene/object/camera attestation disagrees with approved config')
                initial_native=action._call('GET','/task/success')
                write(episode/'initial_native.json',initial_native)
                if initial_native['native_success']:raise RuntimeError('OpenArm native success was true immediately after reset')
                def rgb():
                    with urllib.request.urlopen(owned.url+'/camera',timeout=3.) as response:raw=response.read()
                    with np.load(io.BytesIO(raw),allow_pickle=False) as obs:
                        return obs['rgb_u8'].copy(),time.time(),int(obs['total_steps'])
                if request.run_id and request.attempt_id:
                    publish_public_station(run_dir=request.artifact_root.parent,run_id=request.run_id,
                        attempt_id=request.attempt_id,suite='robocasa',origin=owned.url,
                        device_id='openarm_base_camera',camera_transport='openarm_http',robot=selection.artifact())
                if self.record_replay:
                    from .recording import MultiViewRecording
                    recording=cameras.enter_context(MultiViewRecording(episode,owned.out/'service',
                        run_dir=request.artifact_root.parent if request.run_id else None,
                        run_id=request.run_id,attempt_id=request.attempt_id,arm=selection.execution_arm))
                monitor=DualSafetyMonitor(action,**config['safety_limits'],interrupt_check=interrupted)
                from .phases import PhaseLedger
                phases=PhaseLedger()
                def clock():
                    now=time.monotonic()
                    if recording:recording.bind_trace_origin(now)
                    return now
                def emit(event):
                    before,after=phases.consume(event)
                    events.extend(before);events.append(event);events.extend(after)
                    hook=getattr(self,'attention_boundary_hook',None)
                    if hook and event.get('status')=='completed' and event.get('event_type') in {'sdk.arm_command','sdk.gripper_command','sdk.base_command','sdk.handoff'}:hook(event)
                sdk=OpenArmSDK(BimanualPerceptionBackend(monitor,mode='sim_gt',target='robocasa_sim',provider=action),
                              monitor,emit,clock)
                initial=sdk.sensors.get_observation()
                (episode/'initial_observation.npz').write_bytes(action._call('GET','/observation'))
                first_sha=hashlib.sha256(json.dumps(_json_safe(initial),sort_keys=True).encode()).hexdigest()
                objects=sdk.sensors.find_objects()
                if self.policy_start_hook:self.policy_start_hook()
                from .hint import compile_hint
                public_guidance=request.attention_input or {}
                scoped_hint=compile_hint(public_guidance.get('advisor_guidance',''),public_guidance.get('guidance_scope')) if selection.execution_arm=='both' else None
                outcome=execute_formal_policy(code=code,sdk=sdk,context={'suite':'robocasa','task_id':request.task_id,
                    'perception_mode':'sim_gt','goal':get_robocasa_task(request.task_id).goal,'language':config['task_prompt'],
                    'robot':selection.artifact(),'initial_objects':objects,'bimanual_guidance':scoped_hint,'attention_input':request.attention_input or {}},
                    deadline=deadline,stderr_path=episode/'policy.stderr',max_sdk_calls=request.sdk_call_limit or 200,
                    cancel_check=interrupted)
                status,error=outcome.status,outcome.error
                if not interrupted() and time.monotonic()<deadline:
                    native=action._call('GET','/task/success')
                    final=action.observe()
                    (episode/'final_observation.npz').write_bytes(action._call('GET','/observation'))
                    last_sha=hashlib.sha256(json.dumps(_json_safe(final),sort_keys=True).encode()).hexdigest()
                    final_state=action._state()
                    write(episode/'final_state.json',final_state)
                    if recording and action.action_receipts:
                        recording.await_physical_step(max(a.get('finished_step',0) for a in action.action_receipts),final_state['epoch'])
        except Exception as exc:
            status='cancelled' if interrupted() else 'timeout' if time.monotonic()>=deadline else 'failed'
            error=f'{type(exc).__name__}: {exc}'
        if recording:
            recording.finalize()
            from .control_recording import publish_stopped_control
            publish_stopped_control(request.artifact_root.parent if request.run_id else None,request.run_id,request.attempt_id,owned.stop_receipt)
        if owned.stop_receipt and owned.stop_receipt['reason']=='operator_cancel':status='cancelled'
        elif owned.stop_receipt and owned.stop_receipt['reason']=='episode_deadline':status='timeout'
        source_unchanged=False
        try:
            request.validate();validate_config(request,config)
            attest_runtime(config,python=self.sim_python,task_source_root=self.task_source_root)
            source_unchanged=True
        except Exception as exc:status='failed';error=f'Approved source/config changed: {exc}'
        scored=bool(status=='completed' and native and native['native_success'])
        identity={'robot':selection.artifact(),'model_sha256':config['model_sha256'],
            'parameters_sha256':config['robot_parameters']['sha256'],'config_sha256':request.config_sha256,
            'base':{'model':'Tidyverse planar chassis','capability':'mobile_base_joint_drive'}}
        common={'run_id':request.run_id,'attempt_id':request.attempt_id}
        physics={}
        for name in ['trajectory.npz','base_trajectory.jsonl','object_trajectory.jsonl','events.jsonl','http.jsonl','display_scene.json','contacts.jsonl']:
            path=episode/'owned/service'/name
            if path.is_file():physics[name]={'uri':str(path.resolve()),'sha256':digest(path)}
        for name in ['initial_observation.npz','final_observation.npz']:
            path=episode/name
            if path.is_file():physics[name]={'uri':str(path.resolve()),'sha256':digest(path)}
        trace=write(episode/'trace.json',{'schema_version':'attentionbench.formal-trace.v1','suite':self.suite,
            'task_id':request.task_id,'seed':request.seed,'policy_sha256':request.policy_sha256,
            'config_sha256':request.config_sha256,'entry_lock':request.entry_lock,**common,
            'robot_configuration':identity,'attention_input':request.attention_input or {},'status':status,'error':error,
            'reset_attestation':attestation,'sdk_events':events,'backend_steps':[],
            'physical_evidence':physics,'action_receipts':action.action_receipts if action else [],
            'planned_motion_receipts':action.planned_motion_receipts if action else [],
            'initial_observation_sha256':first_sha,'final_observation_sha256':last_sha})
        if monitor:
            safety=monitor.write_artifact(episode/'safety.json',attempt_id=request.attempt_id or episode.name,run_id=request.run_id)
        else:
            safety=write(episode/'safety.json',{'schema_version':'attentionbench.safety-monitor.v1',**common,
                'source':'independent_safety_monitor','unsafe_attempts':0 if status=='cancelled' else 1,
                'coverage':'no_actions_started','events':[],'violations':[] if status=='cancelled' else [{'kind':'monitor_not_initialized'}]})
        stopped=service_stop_confirmed(owned.stop_receipt,self.suite)
        sandbox=write(episode/'sandbox_receipt.json',{'schema_version':'attentionbench.formal-sandbox-receipt.v1',**common,
            'robot_configuration':identity,'probe':probe,'worker':outcome.__dict__ if outcome else None,
            'service_stop':owned.stop_receipt,'service_source_unchanged':source_unchanged,
            'action_cancellation_receipts':action.cancellation_receipts if action else [],
            'planned_motion_receipts':action.planned_motion_receipts if action else [],
            'plan_query_receipts':action.plan_query_receipts if action else [],
            'source_sha256_before':request.policy_sha256,'source_sha256_after':digest(request.policy_code_path),
            'config_sha256_after':digest(request.config_path),'deadline_seconds':request.effective_attempt_deadline_seconds,
            'sdk_call_budget':{'limit':request.sdk_call_limit or 200,'requested':outcome.call_count if outcome else 0,
                'dispatched':min(outcome.call_count if outcome else 0,request.sdk_call_limit or 200)},
            'elapsed_seconds':time.monotonic()-started})
        native_path=write(episode/'native_result.json',{'schema_version':'attentionbench.formal-native-result.v1',**common,
            'source':'unchanged original RoboCasa evaluate()','evaluated':native is not None,
            'service_native_success':native['native_success'] if native else None,'native_success':scored,
            'status':status,'native_evaluation':native,'robot_configuration':identity})
        artifacts={name:{'uri':str(path),'sha256':digest(path)} for name,path in
            [('trace',trace),('safety',safety),('sandbox_receipt',sandbox),('native_result',native_path)]}
        result={'schema_version':'attentionbench.formal-runner-result.v1','suite':self.suite,'task_id':request.task_id,
            'seed':request.seed,'policy_sha256':request.policy_sha256,'config_sha256':request.config_sha256,
            'entry_lock':request.entry_lock,**common,'robot_configuration':identity,'status':status,'error':error,
            'control_interface_status':'attested' if interface_attested else 'unavailable',
            'action_completion':{'completed':sum(a['status']=='completed' for a in action.action_receipts) if action else 0,
                'failed':sum(a['status']=='failed' for a in action.action_receipts) if action else 0},'execution_status':status,
            'native_success':scored,'native_evaluator':{'native_success':scored,'evaluated':native is not None,
                'service_native_success':native['native_success'] if native else None},
            'sandbox':{'process_isolated':probe['host_home_visible'] is False,
                'sdk_rpc_only':probe['worker_has_simulator_client'] is False,
                'deadline_enforced':stopped,'action_cancellation_verified':stopped},
            'service_revision':{'openarm_files':config['service_files'],'task':config['task_source']['revision']},
            'artifact_dir':str(episode.resolve()),'artifacts':artifacts,'formal_eligible':False}
        write(episode/'result.json',result)
        return result
