"""Deterministic offline validation of sealed execution evidence; never model judgement."""
import hashlib,json,sqlite3,tempfile
from pathlib import Path
from .dev_v2_loop import immutable
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
                 failure_signature,budget,developer_model,gpu_seconds,ledger_ref=None):
    """Refusal writes nothing. Failed checks retain an honest independent descriptor.

    ``grant`` includes the runner's sealed permission_scope (validation grants bind
    source/config; the consumed motion approval separately binds permissions).
    GPU seconds is an injected independent measurement, never inferred from success.
    """
    from attention_memory_service.core.store import StateConflictError
    from attention_memory_service.dev_v2 import scene_matches,public_trace_projection,signature_present,stop_position
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
        artifacts={k:read(bundle['artifacts'][k]) for k in ('native_result','trace','safety','sandbox_receipt')}
        feedback=public_trace_projection(artifacts['trace']);present=signature_present(feedback,failure_signature);position=stop_position(artifacts['trace'])
        binding=grant['binding'];bindings={k:bundle[k] for k in ('run_id','attempt_id','policy_sha256','config_sha256','task_id','seed')}
        if type(config['seed']) is not int or type(binding['version']) is not int or type(binding['seed']) is not int:raise Refused('integer grant version/seed required')
        expected=dict(cell=cell,version=version,policy_sha256=source_ref['sha256'],config_sha256=config_ref['sha256'],seed=config['seed'])
        if any(binding[k]!=v for k,v in expected.items()) or bundle['run_id']!='run:'+cell or bundle['attempt_id']!=f'attempt:{cell}:{version-1}':raise Refused('grant execution binding mismatch')
        if bundle['policy_sha256']!=source_ref['sha256'] or bundle['config_sha256']!=config_ref['sha256'] or bundle['task_id']!=config['task_id'] or bundle['seed']!=config['seed']:raise Refused('source/config identity mismatch')
        if not permission_scope or permission_scope!=grant['permission_scope']:raise Refused('permission scope mismatch')
        from .dev_planner import binding as motion_binding,digest as config_digest
        permission=motion_binding(cell,version,source,config)
        if permission is None:
            permission=dict(run_id='run:'+cell,cell=cell,code_version=version,code_sha256=source_ref['sha256'],config_sha256=config_digest(config),action='sensor_only',action_version=1,
                scope=dict(operations=['sensors'],task_id=config['task_id'],robot=config['robot'],seed=config['seed'],sdk_calls=config['sdk_call_limit'],control_steps=config['horizon'],wall_seconds=config['attempt_wall_seconds']))
        if permission_scope!=permission:raise Refused('permission scope differs from source/config')
        if config.get('scene_contract')!=contract:raise Refused('scene contract binding mismatch')
        registered=scene_contract.load_contract(contract)
        for value in artifacts.values():
            if any(value.get(k)!=bundle[k] for k in ('run_id','attempt_id')):raise Refused('artifact binding mismatch')
        costs=dict(credits=0,tokens=0,seconds=artifacts['sandbox_receipt']['elapsed_seconds'],gpu_seconds=gpu_seconds)
        if ledger_ref:
            sealed(ledger_ref,root,files)
            with sqlite3.connect(Path(ledger_ref['uri']).as_uri()+'?mode=ro',uri=True) as db:
                jobs=[json.loads(p) for p, in db.execute("SELECT payload FROM v2_jobs WHERE cell=? AND version<=? AND state='settled'",(cell,version))]
                if permission['action']!='sensor_only':
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
    vr.mkdir(parents=True,exist_ok=True);out=Path(tempfile.mkdtemp(prefix='arm-',dir=vr))
    observations=[];states=[];obs_values=[];state_values=[];scene_ok=True;errors=[]
    for i in range(2):
        try:
            obs,state=replay(config['task_id'],config['seed'],config)
            validation=scene_contract.validate(contract,config['task_id'],state,config['seed'])
            scene_ok=scene_ok and validation['matched']
            obs_values.append(scene_contract.observation_state(obs));state_values.append(state)
        except Exception as exc:
            obs={'robot':{},'perception':{'values':{}}};state={};validation={'matched':False,'error':type(exc).__name__};scene_ok=False;errors.append(type(exc).__name__)
        observations.append(immutable(out/f'observation-{i}.json',obs))
        states.append(immutable(out/f'state-{i}.json',{'state':state,'validation':validation} if registered['schema']==scene_contract.SCHEMA_V2 else state))
    scene_ok=bool(scene_ok and len(obs_values)==2 and scene_matches(*obs_values) and scene_matches(*state_values))
    native_ok=(native.get('evaluated') is True and type(native.get('native_success')) is bool and native['native_success'] is bundle['native_success']
               and not bundle.get('infrastructure_failure') and not bundle.get('outcome_unknown') and bundle['status'] in {'completed','failed'}
               and bundle.get('sandbox')==dict(process_isolated=True,sdk_rpc_only=True,deadline_enforced=True,action_cancellation_verified=True)
               and bundle.get('scene_condition',{}).get('matched') is True)
    checks=dict(native_evaluation=native_ok,safety=not violations,scene_reproduction=scene_ok,permissions=True,source_identity=True)
    comparison=dict(execution_config={k:v for k,v in config.items() if k!='seed'},budget=budget,developer_model=developer_model)
    descriptor=dict(schema_version='attentionbench.dev-v2-independent-validation.v1',purpose='offline_memory_validation',execution_import_id=execution_import_id,
        bindings=bindings,bundle=bundle_ref,native_result=bundle['artifacts']['native_result'],trace=bundle['artifacts']['trace'],native_success=bundle['native_success'],checks=checks,
        scene_replay_observations=observations,scene_replay_states=states,scene_replay_bindings={k:bundle[k] for k in ('task_id','seed','config_sha256')},
        safety=immutable(out/'safety.json',safety),permission_scope=permission_scope,failure_signature=failure_signature,
        signature_present=present,stop_position=position,costs=costs,validation_config=comparison)
    ref=immutable(out/'verdict.json',descriptor)
    trial=dict(schema_version='attentionbench.dev-v2-trial.v1',execution_import_id=execution_import_id,validation_descriptor='verdict.json',
        exposure=binding['exposure'],memory_id=binding['memory_id'] if binding['exposure']=='candidate_validation' else None,
        validation_grant_id=grant['grant_id'],costs=costs,developer_model=developer_model,budget=budget,validation_config=immutable(out/'comparison.json',comparison))
    immutable(out/'trial.json',trial)
    catalog=dict(role='validator',root=str(out),context=executor_catalog['context'],files={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()})
    return dict(status='passed' if all(checks.values()) else 'failed',descriptor=ref,catalog=catalog,checks=checks,errors=errors)


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
