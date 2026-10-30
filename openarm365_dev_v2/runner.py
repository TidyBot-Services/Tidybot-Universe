"""Dev-only suite adapter with private native evaluation and public SDK traces."""
import hashlib,json,threading,time,uuid
from pathlib import Path
from .dev_config import validate_dev_config as validate_config
from openarm365_dev.sandbox import probe_sandbox,execute_formal_policy,_json_safe
from .owner import NativeOwnerV2 as NativeOwner
from openarm365_dev.owner import OutcomeUnknown,build_sdk
from benchmarks.attention_harness.robocasa365.recording import finalize_recording
from .diag_budget import diagnostic_budget,dev_tier_budget,task_limits

def write(path,value):
    path.write_text(json.dumps(_json_safe(value),ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)+'\n')
    return {'uri':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def dev_version_label(attempt_id):
    # Attempt ids end with the 0-based index (attempt:<cell>:<version-1>); the live monitor wants the 1-based Dev version.
    index=(attempt_id or 'attempt:0').rsplit(':',1)[-1]
    return str(int(index)+1) if index.isdigit() else index

class OpenArm365DevRunnerV2:
    suite='robocasa365'
    # The formal subclass overrides these; the defaults are the Dev behaviour.
    runner_boundary='dev_loop'
    allow_entry_lock=False
    def __init__(self,*,service_source_root,sim_python,sim_source_root,task_source_root,asset_manifest,
                 record_replay=True,independent_evaluation=True,service_process_observer=None,episode_observer=None,memory_validation=None,agent_mode=False):
        if not independent_evaluation:raise ValueError('Dual-arm Dev requires independent evaluation')
        self.agent_mode=agent_mode
        self.python=sim_python;self.process_observer=service_process_observer;self.episode_observer=episode_observer
        self.cancel_event=threading.Event();self.memory_validation=memory_validation

    def load_config(self,request):
        return validate_config(json.loads(request.config_path.read_bytes()))

    def budgets(self,request,c):
        """(attempt seconds, SDK call limit) for this attempt; raises when outside the frozen envelope."""
        limit=request.sdk_call_limit or c['sdk_call_limit']
        seconds=request.effective_attempt_deadline_seconds
        if not 0<seconds<=c['attempt_wall_seconds'] or not 0<limit<=c['sdk_call_limit']:raise ValueError('Dual-arm execution exceeds frozen budgets')
        return seconds,limit

    def safety_record(self,record):
        return record

    def policy_context(self,request,c,ready,validation_public,episode):
        return {'robot':'OpenArm365','task_id':c['task_id'],'task_prompt':c['task_prompt'],
            'perception_mode':'sim_gt','capabilities':ready['capabilities'],
            **({'memory_validation':validation_public} if validation_public else {})}

    def execute(self,request):
        override=diagnostic_budget()
        tier=dev_tier_budget() if self.runner_boundary=='dev_loop' else None
        if override and (self.runner_boundary!='dev_loop' or request.entry_lock is not None):
            raise ValueError('diagnostic budget cannot enter the formal boundary')
        if request.entry_lock is not None and not self.allow_entry_lock:raise ValueError('Dev-only runner cannot claim an approved formal entry')
        c=self.load_config(request)
        agent = getattr(self, 'agent_mode', False)
        policy_executor, sdk_builder = execute_formal_policy, build_sdk
        if agent:
            if self.runner_boundary != 'dev_loop' or c.get('budget_tier') != 'dev' or self.memory_validation:
                raise ValueError('agent requires dev tier without Memory validation')
            from .agent_sandbox import execute_agent_policy
            from .agent_sdk import build_sdk as agent_sdk
            policy_executor, sdk_builder = execute_agent_policy, agent_sdk
        from curobo_v2.execution_settings import from_config,receipt_fields
        execution_labels=receipt_fields(from_config(c.get('execution_settings')))
        if c.get('bimanual_collaboration') in ('c1','handoff'):execution_labels['bimanual_collaboration']=c['bimanual_collaboration']
        if c.get('budget_tier','legacy')!='dev':tier=None
        if request.task_id!=c['task_id'] or request.seed!=c['seed']:raise ValueError('Locked 365 task/seed mismatch')
        seconds,limit=self.budgets(request,c)
        started=time.monotonic();deadline=started+seconds
        episode=request.artifact_root/('openarm365-dev-'+uuid.uuid4().hex[:12]);episode.mkdir(parents=True,exist_ok=False)
        ids=dict(run_id=request.run_id,attempt_id=request.attempt_id)
        if self.episode_observer:self.episode_observer(str(episode.resolve()))
        binding=dict(suite=self.suite,task_id=request.task_id,seed=c['seed'],policy_sha256=request.policy_sha256,
            config_sha256=request.config_sha256,entry_lock=request.entry_lock,**ids)
        (episode/'policy.py').write_bytes(request.policy_code_path.read_bytes())
        (episode/'config.json').write_bytes(request.config_path.read_bytes())
        recording_context=dict(**ids,camera_name='robot0_agentview_left',started_monotonic=started,
            origin='native_pipe://owned-openarm365-v2',dev_version=dev_version_label(request.attempt_id))
        write(episode/'recording_context.json',recording_context)
        owner=NativeOwner(python=self.python,config=episode/'config.json',evidence=episode,deadline=deadline,
            cancel_check=self.cancel_event.is_set,process_observer=self.process_observer,**({"agent_mode":True} if agent else {}))
        validation_public=validation_receipt=None
        if self.memory_validation:
            from benchmarks.attention_harness.dev_memory_validation import policy_input
            validation_public,validation_receipt=policy_input(self.memory_validation,request)
        events=[];outcome=None;probe=None;status='failed';error=None;attested=False;unknown=False
        native={'evaluated':False,'native_success':False,'reason':'not_started'}
        def event(row):events.append(dict(row,timestamp=time.monotonic()-started,timestamp_basis='relative'))
        try:
            probe=probe_sandbox();ready=owner.start();attested=True
            write(episode/'initial_public_observation.json',ready['initial_observation'])
            condition=ready.get('scene_condition')
            write(episode/'scene-condition.json',{'state':ready.get('scene_state'),'validation':condition})
            if condition and not condition['matched']:
                status='condition_invalid';error='scene_contract_mismatch';native={'evaluated':False,'native_success':False,'reason':error}
            else:
                outcome=policy_executor(code=request.policy_code_path.read_text(),sdk=sdk_builder(owner,event),
                    **({'source_config':c} if self.runner_boundary=='dev_loop' and c.get('budget_tier')=='dev' else {}),
                    context=self.policy_context(request,c,ready,validation_public,episode),
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
            video=episode/'advisor_replay.mp4'
            if video.is_file():recording['video']={'uri':str(video.resolve()),'sha256':hashlib.sha256(video.read_bytes()).hexdigest()}
            # Display-only extra angles: best effort, never changes the recording receipt or the result.
            try:
                from .extra_views import finalize_extra_views
                finalize_extra_views(episode)
            except Exception:pass
        public_steps=[r for r in owner.ledger if r['group']!='owner']
        violations=[r['error'] for r in public_steps if not r['ok'] and any(s in r.get('error','') for s in
            ('Unexpected physical','Measured native','Stationary base','grasp contact lost','object/hand transform'))]
        artifacts={}
        trace=dict(schema_version='attentionbench.formal-trace.v1',**binding,status=status,sdk_events=events,
            backend_steps=public_steps,robot='OpenArm365',execution_arm='both',controller=c['controller'],
            perception_mode='sim_gt',error=error,runner_boundary=self.runner_boundary)
        if validation_receipt and outcome is not None:trace['memory_validation']=validation_receipt
        receipt=dict(**ids,**execution_labels,elapsed_seconds=time.monotonic()-started,deadline_seconds=seconds,service_stop=cleanup,
            sandbox_probe=probe,policy_process=outcome.__dict__ if outcome else None,camera_recording=recording,control_steps=max((r.get('sequence',0) for r in owner.ledger),default=0),
            sdk_call_budget={'limit':limit,'requested':outcome.call_count if outcome else 0,
                'dispatched':min(outcome.call_count,limit) if outcome else 0},outcome_unknown=unknown)
        safety=self.safety_record(dict(**ids,source='native_per_physics_step_monitor',monitor_available=attested,
            unsafe_attempts=int(bool(violations)),violations=violations,events=[],limits=c['safety_limits']))
        native=dict(native,**ids,status=status)
        if override:
            receipt['diagnostic_budget_override']=override
            native['diagnostic_budget_override']=override
            trace['diagnostic_budget_override']=override
        if tier:
            receipt['dev_tier_budget']=tier
            native['dev_tier_budget']=tier
            trace['dev_tier_budget']=tier
        for name,value in [('trace',trace),('sandbox_receipt',receipt),('safety',safety),('native_result',native)]:
            artifacts[name]=write(episode/(name+'.json'),value)
        result=dict(schema_version='attentionbench.formal-runner-result.v1',**binding,status=status,error=error,
            runner_boundary=self.runner_boundary,formal_eligible=False,native_success=native['native_success'],native_evaluator=native,
            control_interface_status=recording_context['control_interface_status'],infrastructure_failure=not attested,
            outcome_unknown=unknown,sandbox={'process_isolated':probe is not None,'sdk_rpc_only':probe is not None,
                'deadline_enforced':True,'action_cancellation_verified':cleanup['leader_reaped'] and cleanup['process_group_gone'] and cleanup['planner_process_groups_gone']},
            artifact_dir=str(episode.resolve()),artifacts=artifacts,recording=recording,scene_condition=ready.get('scene_condition') if attested else None)
        if execution_labels.get('deployment_kind')=='dev-local':result.update(deployment_kind='dev-local',planner_lock_sha256=execution_labels['planner_lock_sha256'])
        if override:result['diagnostic_budget_override']=override
        if tier:result['dev_tier_budget']=tier
        write(episode/'result.json',result);return result
