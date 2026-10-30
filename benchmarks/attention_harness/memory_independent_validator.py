"""Deterministic offline validation of sealed execution evidence; never model judgement."""
import hashlib,json,sqlite3,tempfile
from pathlib import Path
from .dev_v2_loop import immutable
from .gpu_meter import GpuMeter
from openarm365_dev_v2 import scene_contract


class Refused(ValueError):pass


def sealed(ref,root,files):
    p=Path(ref['uri']);root=Path(root)
    if not p.is_absolute() or p!=p.resolve() or not p.is_relative_to(root):raise Refused('evidence outside canonical executor root')
    if files.get(str(p.relative_to(root)))!=ref['sha256']:raise Refused('evidence is not sealed')
    data=p.read_bytes()
    if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise Refused('evidence digest mismatch')
    return data


def validate_arm(*,experiment_dir,experiments_root,cell,version,execution_import_id,bundle_ref,config_ref,
                 source_ref,grant,permission_scope,executor_catalog,contract,validator_root,replay,
                 failure_signature,budget,developer_model,gpu_seconds,ledger_ref=None,probe_input=None,origin_source_sha256=None,origin=False,before_replay=None,check_only=False,import_purpose=None,source_drift=None,current_lock=None):
    """Refusal writes nothing. Failed checks retain an honest independent descriptor.

    ``grant`` includes the runner's sealed permission_scope (validation grants bind
    source/config; permission is either consumed human approval or the explicit
    frozen simulated Dev validation scope).
    Arms and clean-origin probes use independently measured execution GPU seconds;
    origins meter their two fresh replays. A probe's lack of a validation grant
    does not exempt it from the finite, nonnegative independent-cost checks.
    Origin mode binds the settled historical ledger instead of a validation grant;
    it cannot emit a trial or relax any native, permission, safety or scene check.
    """
    from attention_memory_service.core.store import StateConflictError
    from attention_memory_service.dev_v2 import scene_matches,public_base_yaw_visible,public_trace_projection,signature_present,stop_position
    try:
        if type(version) is not int or version<1:raise Refused('positive integer version required')
        er=Path(experiments_root).resolve();vr=Path(validator_root).resolve();directory=Path(experiment_dir).resolve()
        if any(Path(p).absolute()!=Path(p).resolve() for p in (experiments_root,validator_root,experiment_dir)):raise Refused('canonical roots required')
        if er.is_relative_to(vr) or vr.is_relative_to(er):raise Refused('validator and experiments roots overlap')
        root=Path(executor_catalog['root'])
        if root!=root.resolve() or not directory.is_relative_to(er) or root!=directory or executor_catalog['role']!='executor':raise Refused('wrong executor root')
        files=executor_catalog['files'];read=lambda ref:json.loads(sealed(ref,root,files))
        # Recheck the whole seal before replay or creating a directory.
        for name,digest in files.items():sealed({'uri':str(root/name),'sha256':digest},root,files)
        bundle=read(bundle_ref);config=read(config_ref);source=sealed(source_ref,root,files).decode()
        agent=bundle.get('channel')=='dev_agent'
        if (config.get('dev_mode')=='agent') != agent:raise Refused('bundle channel differs from frozen agent mode')
        if agent and not (origin and import_purpose=='memory_origin' or not origin and import_purpose=='memory_validation' and grant is not None and probe_input is None):raise Refused('agent replay requires origin or validation purpose')
        replay_config=config
        if source_drift is not None:
            from openarm365_dev_v2.source_policy import drift_record
            from .memory_source_drift import execution_config
            from openarm365_dev_v2.dev_config import verify_lock
            original_ref=config.get('origin_dev_lock',config['dev_lock'])
            actual_ref=current_lock or config['dev_lock']
            def lock_read(ref):
                from .dev_v2_loop import checked
                return json.loads(checked(dict(uri=ref['path'],sha256=ref['sha256'])).read_bytes())
            original_lock=lock_read(original_ref);current=lock_read(actual_ref)
            if source_drift!=drift_record(config,original_lock,current,original_ref['sha256'],actual_ref['sha256']):raise Refused('source drift record differs')
            verify_lock(actual_ref,expected_source_root=current['source_root'])
            replay_config=execution_config(config,actual_ref,source_drift) if origin else config
        artifacts={k:read(bundle['artifacts'][k]) for k in ('native_result','trace','safety','sandbox_receipt')}
        from attention_memory_service.resource_receipt import valid as valid_resource_receipt
        if not valid_resource_receipt(config, artifacts['sandbox_receipt']):raise Refused('native resource receipt exceeds frozen bounds')
        feedback=public_trace_projection(artifacts['trace'],base_yaw_visible=public_base_yaw_visible(bundle,config));present=signature_present(feedback,failure_signature);position=stop_position(artifacts['trace'])
        bindings={k:bundle[k] for k in ('run_id','attempt_id','policy_sha256','config_sha256','task_id','seed')}
        if origin:
            if grant is not None or probe_input is not None or not ledger_ref:raise Refused('origin requires the original ledger and no validation grant')
            binding=dict(cell=cell,version=version,seed=config['seed'],policy_sha256=source_ref['sha256'],config_sha256=config_ref['sha256'])
        elif probe_input is not None:
            if grant is not None or source_ref['sha256']!=origin_source_sha256 or not ledger_ref:raise Refused('probe requires sealed origin, no grant and execution ledger')
            clean=read(probe_input)
            if clean!=dict(memory_ids=[],exposure='none',context=executor_catalog['context'],policy_sha256=source_ref['sha256'],config_sha256=config_ref['sha256']):raise Refused('probe must read no notes')
            if artifacts['trace'].get('memory_validation') or artifacts['trace'].get('memory_uses'):raise Refused('probe contains Memory')
            binding=dict(cell=cell,version=version,seed=config['seed'],policy_sha256=source_ref['sha256'],config_sha256=config_ref['sha256'])
        else:binding=grant['binding']
        if type(config['seed']) is not int or type(binding['version']) is not int or type(binding['seed']) is not int:raise Refused('integer grant version/seed required')
        expected=dict(cell=cell,version=version,policy_sha256=source_ref['sha256'],config_sha256=config_ref['sha256'],seed=config['seed'])
        if any(binding[k]!=v for k,v in expected.items()) or bundle['run_id']!='run:'+cell or bundle['attempt_id']!=f'attempt:{cell}:{version-1}':raise Refused('grant execution binding mismatch')
        if bundle['policy_sha256']!=source_ref['sha256'] or bundle['config_sha256']!=config_ref['sha256'] or bundle['task_id']!=config['task_id'] or bundle['seed']!=config['seed']:raise Refused('source/config identity mismatch')
        if not permission_scope or not origin and probe_input is None and permission_scope!=grant['permission_scope']:raise Refused('permission scope mismatch')
        from .dev_planner import binding as motion_binding,digest as config_digest
        permission=motion_binding(cell,version,source,config)
        from .memory_motion_permission import ACTION, validation_scope
        if agent and permission is not None:permission=dict(permission,action='agent_dev_tier_motion')
        if permission_scope.get('action') == ACTION:
            permission=validation_scope(cell,version,source,config,bundle,import_purpose)
        if permission is None:
            permission=dict(run_id='run:'+cell,cell=cell,code_version=version,code_sha256=source_ref['sha256'],config_sha256=config_digest(config),action='sensor_only',action_version=1,
                scope=dict(operations=['sensors'],task_id=config['task_id'],robot=config['robot'],seed=config['seed'],sdk_calls=config['sdk_call_limit'],control_steps=config['horizon'],wall_seconds=config['attempt_wall_seconds']))
        if permission_scope!=permission:raise Refused('permission scope differs from source/config')
        if config.get('scene_contract')!=contract:raise Refused('scene contract binding mismatch')
        registered=scene_contract.load_contract(contract)
        for value in artifacts.values():
            if any(value.get(k)!=bundle[k] for k in ('run_id','attempt_id')):raise Refused('artifact binding mismatch')
        costs=dict(credits=0,tokens=0,seconds=artifacts['sandbox_receipt']['elapsed_seconds'])
        if not origin:costs['gpu_seconds']=gpu_seconds
        if ledger_ref:
            sealed(ledger_ref,root,files)
            with sqlite3.connect(Path(ledger_ref['uri']).as_uri()+'?mode=ro',uri=True) as db:
                jobs=[json.loads(p) for p, in db.execute("SELECT payload FROM v2_jobs WHERE cell=? AND version<=? AND state='settled'",(cell,version))]
                if origin:
                    executions=[j for j in jobs if j['kind']=='execute' and j['version']==version]
                    if len(executions)!=1 or executions[0].get('receipt',{}).get('bundle')!=bundle_ref:raise Refused('origin execution differs from sealed ledger')
                    versions=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_versions WHERE cell=? AND version=?',(cell,version))]
                    if len(versions)!=1 or versions[0].get('source')!=source_ref or versions[0].get('config_sha256')!=config_ref['sha256']:raise Refused('origin version differs from sealed ledger')
                    if agent:
                        from attention_memory_service.agent_execution import verify as verify_agent
                        from types import SimpleNamespace
                        from contextlib import nullcontext
                        shim=SimpleNamespace(_ledger=lambda *a:nullcontext(db))
                        cells=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_cells WHERE id=?',(cell,))]
                        manifest=read(versions[0]['manifest'])
                        verify_agent(shim,None,dict(cell=cell,version=version,permission_scope=permission_scope,import_purpose=import_purpose,agent_execution=dict(request_id=executions[0]['receipt']['agent_request_id'])),bundle,config,manifest,versions[0],cells[0],executions[0])
                if probe_input is not None:
                    all_jobs=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_jobs WHERE cell=? AND version<=?',(cell,version))]
                    if not all_jobs or any(j['kind']!='execute' for j in all_jobs):raise Refused('probe cannot call models or advisors')
                if permission['action']=='execute_bounded_motion_policy':
                    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='v2_planner_requests'").fetchone():
                        raise Refused('The original execution is missing its consumed motion approval.')
                    approvals=[json.loads(p) for p, in db.execute('SELECT payload FROM v2_planner_requests WHERE cell=?',(cell,))]
                    execute=next(j for j in jobs if j['kind']=='execute' and j['version']==version)
                    matches=[a for a in approvals if a.get('request_type')=='approval' and a.get('state')=='consumed' and a.get('binding')==permission and a.get('consumed_by')==execute['job_id']]
                    if len(matches)!=1:raise Refused('exact consumed permission missing')
                    a=matches[0];response=a.get('response',{})
                    if not (a['created_at']<=a['responded_at']<=a['consumed_at']<=execute['claimed_at'] and a['consumed_at']<=a['deadline_epoch'] and response.get('approved') is True and response.get('actor')=='authenticated_human' and response.get('response_id')):raise Refused('invalid consumed approval')
            costs.update(credits=sum(j['actual'].get('help_credits',0) for j in jobs),tokens=sum(j['actual'].get('provider_tokens',0) for j in jobs))
        elif permission['action']!='sensor_only':raise Refused('original permission ledger required')
        import math
        if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in costs.values()):raise Refused('independent costs unavailable')
    except (OSError,ValueError,KeyError,TypeError,SyntaxError,StopIteration,StateConflictError,sqlite3.Error) as exc:
        return dict(status='refused',reason=str(exc))
    trace=artifacts['trace'];raw=artifacts['safety'];native=artifacts['native_result']
    violations=[];events=[]
    # Recompute the native monitor's public violation classification from raw steps.
    for step in trace.get('backend_steps',[]):
        events.append(dict(kind='native_step',ok=step.get('ok'),sequence=step.get('sequence')))
        if not step.get('ok') and any(s in step.get('error','') for s in ('Unexpected physical','Measured native','Stationary base','grasp contact lost','object/hand transform')):
            violations.append(dict(kind='native_safety_violation',error=step['error']))
    for item in raw.get('violations',[]):
        violations.append(dict(kind='reported_violation',evidence=item))
    monitor_ok=(raw.get('source')=='native_per_physics_step_monitor' and raw.get('monitor_available') is True
                and type(raw.get('unsafe_attempts')) is int and raw['unsafe_attempts']==int(bool(raw.get('violations'))))
    if not monitor_ok:violations.append(dict(kind='monitor_unavailable_or_inconsistent'))
    events.append(dict(kind='monitor_attestation',available=monitor_ok,source_sha256=bundle['artifacts']['safety']['sha256']))
    safety=dict(schema_version='attentionbench.safety-monitor.v1',source='independent_safety_monitor',attempt_id=bundle['attempt_id'],events=events,violations=violations,unsafe_attempts=int(bool(violations)))
    native_ok=(native.get('evaluated') is True and type(native.get('native_success')) is bool and native['native_success'] is bundle['native_success']
               and not bundle.get('infrastructure_failure') and not bundle.get('outcome_unknown') and bundle['status'] in {'completed','failed'}
               and bundle.get('sandbox')==dict(process_isolated=True,sdk_rpc_only=True,deadline_enforced=True,action_cancellation_verified=True)
               and bundle.get('scene_condition',{}).get('matched') is True)
    if check_only:
        if not origin:raise ValueError('Only origin preparation supports a dry-run check.')
        if violations:return dict(status='refused',reason='The original execution failed independent safety checks.')
        if not native_ok:return dict(status='refused',reason='The original execution has no valid native evaluation.')
        return dict(status='checked')
    observations=[];states=[];obs_values=[];state_values=[];scene_ok=True;errors=[];replayed=[];measurements=[]
    meter_refusal=dict(status='refused',reason='Independent GPU time measurement is unavailable for the origin replays.')
    policy_replays=[]
    if agent:
        vr.mkdir(parents=True,exist_ok=True)
        agent_out=Path(tempfile.mkdtemp(prefix='arm-',dir=vr));policy_root=agent_out/'policy-replays';policy_root.mkdir()
    for i in range(2):
        if before_replay is not None:before_replay()
        meter=None
        if origin:
            try:
                meter=GpuMeter();meter.start()
            except Exception:
                if meter is not None:
                    try:meter.stop()
                    except Exception:pass
                return meter_refusal
        try:
            try:
                if agent:
                    from .memory_agent_replay import replay_policy
                    policy_replay=replay_policy if replay is None or replay is replay_fresh else replay
                    obs,state,receipt=policy_replay(config['task_id'],config['seed'],replay_config,source=source,directory=policy_root/str(i),cell=cell,version=version)
                    from attention_memory_service.agent_origin import verify_replays
                    def replay_read(ref,raw=False):
                        data=Path(ref['uri']).read_bytes()
                        return data if raw else json.loads(data)
                    verify_replays([receipt],replay_read,replay_config,source_ref['sha256'],vr,count=1)
                    policy_replays.append(receipt)
                else:obs,state=replay(config['task_id'],config['seed'],replay_config)
            finally:
                if meter is not None:
                    try:measurements.append(meter.stop())
                    except Exception:measurements.append(None)
            validation=scene_contract.validate(contract,config['task_id'],state,config['seed'])
            scene_ok=scene_ok and validation['matched']
            obs_values.append(scene_contract.observation_state(obs));state_values.append(state)
        except Exception as exc:
            obs={'robot':{},'perception':{'values':{}}};state={};validation={'matched':False,'error':type(exc).__name__};scene_ok=False;errors.append(type(exc).__name__)
        if origin:
            measured=measurements[-1]
            if (not isinstance(measured,dict) or type(measured.get('samples')) is not int or measured['samples']<=0
                or any(type(measured.get(k)) not in (int,float) or not math.isfinite(measured[k]) or measured[k]<0 for k in ('gpu_seconds','wall_seconds'))):return meter_refusal
        replayed.append((obs,{'state':state,'validation':validation} if registered['schema']==scene_contract.SCHEMA_V2 else state))
    if origin:
        costs['gpu_seconds']=sum(m['gpu_seconds'] for m in measurements)
        if not math.isfinite(costs['gpu_seconds']):return meter_refusal
    vr.mkdir(parents=True,exist_ok=True);out=agent_out if agent else Path(tempfile.mkdtemp(prefix='arm-',dir=vr))
    for i,(obs,state) in enumerate(replayed):
        observations.append(immutable(out/f'observation-{i}.json',obs))
        states.append(immutable(out/f'state-{i}.json',state))
    scene_ok=bool(scene_ok and len(obs_values)==2 and scene_matches(*obs_values) and scene_matches(*state_values))
    checks=dict(native_evaluation=native_ok,safety=not violations,scene_reproduction=scene_ok,permissions=True,source_identity=True)
    comparison=dict(execution_config={k:v for k,v in config.items() if k!='seed'},budget=budget,developer_model=developer_model)
    descriptor=dict(schema_version='attentionbench.dev-v2-independent-validation.v1',purpose='offline_memory_validation',execution_import_id=execution_import_id,
        bindings=bindings,bundle=bundle_ref,native_result=bundle['artifacts']['native_result'],trace=bundle['artifacts']['trace'],native_success=bundle['native_success'],checks=checks,
        scene_replay_observations=observations,scene_replay_states=states,scene_replay_bindings={k:bundle[k] for k in ('task_id','seed','config_sha256')},
        safety=immutable(out/'safety.json',safety),permission_scope=permission_scope,failure_signature=failure_signature,
        signature_present=present,stop_position=position,costs=costs,validation_config=comparison)
    if source_drift is not None:descriptor['source_drift']=source_drift
    gpu={}
    if origin:
        gpu['gpu_measurement']=immutable(out/'gpu-measurement.json',dict(source='harness_gpu_meter',scope='origin_scene_replays',
            cell=cell,version=version,bundle_sha256=bundle_ref['sha256'],attempt_id=bundle['attempt_id'],
            gpu_seconds=costs['gpu_seconds'],samples=sum(m['samples'] for m in measurements),replays=measurements,
            scene_replay_observations=observations,scene_replay_states=states))
        descriptor.update(gpu)
    if agent:descriptor['agent_policy_replays']=policy_replays
    ref=immutable(out/'verdict.json',descriptor)
    if probe_input is None and not origin:
        trial=dict(schema_version='attentionbench.dev-v2-trial.v1',execution_import_id=execution_import_id,validation_descriptor='verdict.json',
            exposure=binding['exposure'],memory_id=binding['memory_id'] if binding['exposure']=='candidate_validation' else None,
            validation_grant_id=grant['grant_id'],costs=costs,developer_model=developer_model,budget=budget,validation_config=immutable(out/'comparison.json',comparison))
        immutable(out/'trial.json',trial)
    catalog=dict(role='validator',root=str(out),context=executor_catalog['context'],files={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (out.rglob('*') if agent else out.iterdir()) if p.is_file()})
    return dict(status='passed' if all(checks.values()) else 'failed',descriptor=ref,catalog=catalog,checks=checks,errors=errors,**gpu)


def replay_fresh(task_id,seed,config):
    """Real reset wiring; invoked only by an explicitly enabled offline worker."""
    import subprocess,os
    with tempfile.TemporaryDirectory(prefix='memory-replay-') as tmp:
        root=Path(tmp);(root/'config.json').write_text(json.dumps(config))
        from openarm365_dev_v2.dev_config import SIM_PYTHON,ROOT
        env={k:v for k,v in os.environ.items() if not k.startswith(('PYTHON','CUROBO_','ATTENTION_MEMORY_')) and k not in {'LD_PRELOAD','LD_LIBRARY_PATH'} and not any(s in k.upper() for s in ('KEY','TOKEN','SECRET','PASSWORD','IMPORTER','OPERATOR','HUMAN','CAPABILITY'))}
        env.update(PYTHONDONTWRITEBYTECODE='1',PYTHONHASHSEED='0',PYTHONPATH=str(ROOT),MUJOCO_GL='egl',XDG_CACHE_HOME=str(root/'cache'),NUMBA_CACHE_DIR=str(root/'numba-cache'))
        subprocess.run([str(SIM_PYTHON),'-B','-m','benchmarks.attention_harness.memory_scene_replay',str(root)],check=True,timeout=config['attempt_wall_seconds'],env=env,cwd=ROOT)
        result=json.loads((root/'result.json').read_bytes())
        return result['observation'],result['state']
