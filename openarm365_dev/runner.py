"""Dev-only suite adapter with private native evaluation and public SDK traces."""
import hashlib,json,threading,time,uuid
from pathlib import Path
from .config import validate_config
from .sandbox import probe_sandbox,execute_formal_policy,_json_safe
from .owner import NativeOwner,OutcomeUnknown,build_sdk
from benchmarks.attention_harness.robocasa365.recording import finalize_recording
from openarm365_dev_v2.diag_budget import diagnostic_budget,task_limits

def write(path,value):
    path.write_text(json.dumps(_json_safe(value),ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n')
    return {'uri':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

class OpenArm365DevRunner:
    suite='robocasa365'
    def __init__(self,*,service_source_root,sim_python,sim_source_root,task_source_root,asset_manifest,
                 record_replay=True,independent_evaluation=True,service_process_observer=None):
        if not independent_evaluation:raise ValueError('Dual-arm Dev requires independent evaluation')
        self.python=sim_python;self.process_observer=service_process_observer
        self.cancel_event=threading.Event()

    def execute(self,request):
        override=diagnostic_budget()
        if request.entry_lock is not None:raise ValueError('Dev-only runner cannot claim an approved formal entry')
        c=validate_config(json.loads(request.config_path.read_bytes()))
        if request.task_id!=c['task_id'] or request.seed!=101:raise ValueError('Locked 365 task/seed mismatch')
        limit=request.sdk_call_limit or c['sdk_call_limit']
        seconds=request.effective_attempt_deadline_seconds
        limits=task_limits()
        if not 0<seconds<=limits['attempt_seconds'] or not 0<limit<=limits['sdk_calls']:raise ValueError('Dual-arm execution exceeds frozen budgets')
        started=time.monotonic();deadline=started+seconds
        episode=request.artifact_root/('openarm365-dev-'+uuid.uuid4().hex[:12]);episode.mkdir(parents=True,exist_ok=False)
        ids=dict(run_id=request.run_id,attempt_id=request.attempt_id)
        binding=dict(suite=self.suite,task_id=request.task_id,seed=101,policy_sha256=request.policy_sha256,
            config_sha256=request.config_sha256,entry_lock=None,**ids)
        (episode/'policy.py').write_bytes(request.policy_code_path.read_bytes())
        (episode/'config.json').write_bytes(request.config_path.read_bytes())
        recording_context=dict(**ids,camera_name='robot0_agentview_left',started_monotonic=started,
            origin='native_pipe://owned-openarm365')
        write(episode/'recording_context.json',recording_context)
        owner=NativeOwner(python=self.python,config=episode/'config.json',evidence=episode,deadline=deadline,
            cancel_check=self.cancel_event.is_set,process_observer=self.process_observer)
        events=[];outcome=None;probe=None;status='failed';error=None;attested=False;unknown=False
        native={'evaluated':False,'native_success':False,'reason':'not_started'}
        def event(row):events.append(dict(row,timestamp=time.monotonic()-started,timestamp_basis='relative'))
        try:
            probe=probe_sandbox();ready=owner.start();attested=True
            write(episode/'initial_public_observation.json',ready['initial_observation'])
            outcome=execute_formal_policy(code=request.policy_code_path.read_text(),sdk=build_sdk(owner,event),
                context={'robot':'OpenArm365','task_id':c['task_id'],'task_prompt':c['task_prompt'],
                    'perception_mode':'sim_gt','capabilities':ready['capabilities']},
                deadline=deadline,stderr_path=episode/'policy-stderr.txt',cancel_check=self.cancel_event.is_set,max_sdk_calls=limit)
            status,error=outcome.status,outcome.error
            unknown=any(e.get('error',{}).get('type')=='OutcomeUnknown' for e in events)
            if self.cancel_event.is_set():status='cancelled'
            elif time.monotonic()>=deadline:status='timeout'
            if not self.cancel_event.is_set() and time.monotonic()<deadline:
                final=owner.rpc('owner','observe');write(episode/'final_public_observation.json',final)
                # Explicit duplicate of an actual action, using its exact saved ID
                # and payload. No uncertain request is ever retried automatically.
                successful={r['request_id'] for r in owner.ledger if r['ok']}
                action=next((json.loads(raw) for raw in reversed(list(owner.sent.values()))
                    if json.loads(raw)['group'] not in {'owner','sensors'}
                    and json.loads(raw)['request_id'] in successful),None)
                if action:
                    original=next(r for r in owner.ledger if r['request_id']==action['request_id'])
                    if original['ok']:
                        owner.rpc(action['group'],action['operation'],action['args'],action['kwargs'],request_id=action['request_id'])
                        duplicate=owner.ledger[-1];after=owner.rpc('owner','observe')
                        if original!=duplicate or after['sequence']!=final['sequence']:raise OutcomeUnknown('Duplicate action re-executed or receipt changed')
                        write(episode/'idempotence_real.json',dict(same_receipt=True,sequence_unchanged=True,request_id=action['request_id']))
                try:native=owner.rpc('owner','evaluate')
                except Exception as exc:native={'evaluated':False,'native_success':False,'reason':str(exc)}
        except Exception as exc:
            status='cancelled' if self.cancel_event.is_set() else 'timeout' if isinstance(exc,TimeoutError) else 'failed'
            error=f'{type(exc).__name__}: {exc}';unknown=isinstance(exc,OutcomeUnknown)
        finally:
            cleanup=owner.stop('operator_cancel' if self.cancel_event.is_set() else 'normal_cleanup')
            unknown=unknown or bool(owner.unresolved)
            recording_context['control_interface_status']='outcome_unknown' if unknown else 'attested' if attested else 'not_started'
            recording=finalize_recording(episode,recording_context,status)
        public_steps=[r for r in owner.ledger if r['group']!='owner']
        violations=[r['error'] for r in public_steps if not r['ok'] and any(s in r.get('error','') for s in
            ('Unexpected physical','Measured native','Stationary base','grasp contact lost','object/hand transform'))]
        artifacts={}
        trace=dict(schema_version='attentionbench.formal-trace.v1',**binding,status=status,sdk_events=events,
            backend_steps=public_steps,robot='OpenArm365',execution_arm='both',controller=c['controller'],
            perception_mode='sim_gt',error=error,runner_boundary='dev_loop')
        receipt=dict(**ids,elapsed_seconds=time.monotonic()-started,deadline_seconds=seconds,service_stop=cleanup,
            sandbox_probe=probe,policy_process=outcome.__dict__ if outcome else None,camera_recording=recording,
            sdk_call_budget={'limit':limit,'requested':outcome.call_count if outcome else 0,
                'dispatched':min(outcome.call_count,limit) if outcome else 0},outcome_unknown=unknown)
        safety=dict(**ids,source='native_per_physics_step_monitor',monitor_available=attested,
            unsafe_attempts=int(bool(violations)),violations=violations,events=[],limits=c['safety_limits'])
        native=dict(native,**ids,status=status)
        if override:
            receipt['diagnostic_budget_override']=override
            native['diagnostic_budget_override']=override
            trace['diagnostic_budget_override']=override
        for name,value in [('trace',trace),('sandbox_receipt',receipt),('safety',safety),('native_result',native)]:
            artifacts[name]=write(episode/(name+'.json'),value)
        result=dict(schema_version='attentionbench.formal-runner-result.v1',**binding,status=status,error=error,
            runner_boundary='dev_loop',formal_eligible=False,native_success=native['native_success'],native_evaluator=native,
            control_interface_status=recording_context['control_interface_status'],infrastructure_failure=not attested,
            outcome_unknown=unknown,sandbox={'process_isolated':probe is not None,'sdk_rpc_only':probe is not None,
                'deadline_enforced':True,'action_cancellation_verified':cleanup['leader_reaped'] and cleanup['process_group_gone'] and cleanup['planner_process_groups_gone']},
            artifact_dir=str(episode.resolve()),artifacts=artifacts,recording=recording)
        if override:result['diagnostic_budget_override']=override
        write(episode/'result.json',result);return result
