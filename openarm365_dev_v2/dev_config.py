"""New development conditions and immutable resource lock, separate from v1 freeze."""
import copy,hashlib,json,math
from pathlib import Path
from benchmarks.attention_harness.dev_tasks import get_task,validate_selection
from benchmarks.attention_harness.robocasa365.config import EXPECTED
from openarm365_dev.config import LOCK,LOCK_SHA
from .diag_budget import task_limits,formal_task_limits,case_wall_seconds,check_tier
from curobo_v2.execution_settings import R3_20261025,R4_20261027,R5_FIXTURE_20261031,from_config
from curobo_v2.ik_path_jump import MAX_DIAGNOSTIC_JOINT_JUMP
DEFAULT_CONTACT_EXEMPT_OBJECTS='movable_objects'
DEFAULT_PLANNER_MODE='ik_path'
DEFAULT_IK_CANDIDATES=8
DEFAULT_IK_MAX_JOINT_JUMP=1.0
PLANNER_CONFIG_KEYS=('planner_mode','ik_candidates','ik_max_joint_jump')
SCHEMA='attentionbench.openarm365-dual-dev-config.v2'
ROOT=Path(__file__).resolve().parents[1]
SIM_PYTHON=Path('/home/truares/Documents/Codex/2026-10-09/new-chat/work/service_env/bin/python')
ROBocasa=Path('/home/truares/Documents/Codex/2026-10-09/new-chat/work/upstream/robocasa')
ROBOSUITE=ROBocasa.parent/'robosuite'
ASSETS=Path('/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365/assets-manifest.json')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
NATIVE_TREE=Path('/home/truares/桌面/Tidybot-Universe-attention-native')
def model_sources(identity_path=None,declared_root=NATIVE_TREE,runtime_root=ROOT):
    identity_path=Path(identity_path or ROOT/'openarm365/model/identity.json')
    identity=json.loads(identity_path.read_bytes());files={}
    for original,expected in identity['sources'].items():
        original=Path(original)
        candidates=[original]
        if original.is_relative_to(declared_root):
            # Native XML loads from the copy, while its frozen mesh references
            # still point at the preserved original paths. Verify BOTH views.
            copied=runtime_root/original.relative_to(declared_root)
            if copied!=original:candidates.append(copied)
        for path in candidates:
            if not path.is_file() or sha(path)!=expected:raise ValueError('native robot model identity drift: '+str(path))
            files[str(path.absolute())]=expected
    if not files:raise ValueError('complete native robot identity required')
    return files
def verify_lock(ref,*,expected_source_root=None):
    path=Path(ref['path'])
    if sha(path)!=ref['sha256']:raise ValueError('Dev identity lock changed')
    lock=json.loads(path.read_bytes())
    expected_root=Path(expected_source_root) if expected_source_root is not None else ROOT
    if lock['source_root']!=str(expected_root):raise ValueError('source root mismatch')
    if lock.get('schema')!='attentionbench.dev-identity-lock.v2':raise ValueError('invalid Dev lock schema')
    for folder,expected in lock.get('source_sets',{}).items():
        actual=sorted(str(p.absolute()) for p in Path(folder).rglob('*.py') if '__pycache__' not in p.parts)
        if actual!=expected:raise ValueError('source file set drift: '+folder)
    models=model_sources(identity_path=expected_root/'openarm365/model/identity.json',runtime_root=expected_root)
    if lock.get('model_sources')!=models or any(lock['files'].get(k)!=v for k,v in models.items()):raise ValueError('transitive robot model lock incomplete/drifted')
    for name,digest in lock['files'].items():
        if sha(name)!=digest:raise ValueError('resource source drift: '+name)
    planner=lock['planner_lock']
    if lock.get('planner_deployment_kind')=='dev-local':
        from curobo_v2.identity import load_lock
        from curobo_v2.dev_local import is_dev_local
        runtime,actual=load_lock(planner['path'],tier='dev',boundary='dev_loop')
        if not is_dev_local(runtime) or actual!=planner['sha256']:raise ValueError('dev-local planner lock drift')
    elif planner!={'path':str(LOCK),'sha256':LOCK_SHA}:
        pin=R3_20261025.to_dict()['planner_deployment']
        if planner!={'path':pin['lock_path'],'sha256':pin['sha256']}:raise ValueError('official V2 lock drift')
    if sha(planner['path'])!=planner['sha256']:raise ValueError('official V2 lock drift')
    return lock
def _planner_source(value, tier='dev', boundary='dev_loop'):
    if boundary!='dev_loop' or tier!='dev':raise ValueError('planner_source dev-local requires dev_loop boundary and dev tier')
    if not isinstance(value,dict) or set(value)!={'kind','source_root'} or value['kind']!='dev-local':raise ValueError('invalid planner_source')
    root=value['source_root']
    if not isinstance(root,str) or not Path(root).is_absolute() or not Path(root).is_dir():raise ValueError('planner_source source_root must be an absolute directory')
    return value


def _source_settings(source,profile=R4_20261027):
    from dataclasses import replace
    from curobo_v2.dev_local import materialize
    snapshot=materialize(_planner_source(source)['source_root'])
    return from_config(replace(profile,planner_deployment={k:snapshot[k] for k in ('lock_path','sha256')}).to_dict())


def make_lock(path, *, execution_settings=None, planner_source=None):
    path=Path(path)
    if planner_source is not None:
        _planner_source(planner_source)
        if execution_settings is None:execution_settings=_source_settings(planner_source)
    pin=None if execution_settings is None else from_config(execution_settings.to_dict()).planner_deployment
    planner_path=LOCK if pin is None else Path(pin['lock_path'])
    planner_sha=LOCK_SHA if pin is None else pin['sha256']
    # Explicit files; never hash runtime artifacts or the generated lock itself.
    files=[]
    for folder in ['openarm365_dev_v2','openarm365_dev','openarm365','curobo_v2']:
        files.extend(p for p in (ROOT/folder).rglob('*.py') if '__pycache__' not in p.parts)
    files.extend(p for p in (ROOT/'benchmarks/attention_harness').glob('dev*.py'))
    files.append(ROOT/'benchmarks/attention_harness/memory_agent_validation.py')
    # Cabinet resources belong only to the opt-in dev-local identity.
    from curobo_v2.execution_settings import receipt_fields
    if execution_settings is not None and receipt_fields(execution_settings).get('deployment_kind')=='dev-local':
        files.append(ROOT/'openarm365_dev_v2/prompts/pick_place_counter_to_cabinet.json')
        files.append(ROOT/'benchmarks/attention_harness/robocasa365/tasks.py')
        from curobo_v2.fixture_interaction import enabled,PILOT_TASKS
        if enabled(execution_settings):
            from .fixture_contract import resource_paths
            files.extend(resource_paths())
            files.extend(ROOT/'openarm365_dev_v2/prompts'/(tid+'.json') for tid in PILOT_TASKS if tid not in ('open_cabinet','pick_place_counter_to_cabinet'))
    files.append(ROOT/'benchmarks/attention_harness/v2_evaluator.py')
    files.append(ROOT/'benchmarks/attention_harness/daily_dev_v2.py')
    for name in ['core/policies.py','core/assessment.py','core/models.py','core/trace.py','core/dev_store.py','formal_runner_boundary.py','policy_input.py','parcc_client.py','dev_attention.py','ui_server.py','ui/live.js','ui/index.html','ui_history.py','ui_observability.py','ui_media.py','robocasa_native/policy_sandbox.py','robocasa365/recording.py','robocasa365/human_camera.py']:
        files.append(ROOT/'benchmarks/attention_harness'/name)
    files.extend(p for p in (ROOT/'benchmarks/attention_harness/dev_ui').glob('*') if p.is_file())
    files.append(ROOT/'benchmarks/attention_harness/dev_failure_rules.json')
    files.extend([planner_path,SIM_PYTHON,ASSETS,ASSETS.with_name('assets-installation-lock.json'),ASSETS.with_name('assets-installed-manifest.json'),ROOT/'openarm365/configs/robot_parameters_v1.json',ROOT/'openarm365/model/identity.json'])
    for folder in [ROBocasa/'robocasa',ROBOSUITE/'robosuite']:
        files.extend(p for p in folder.rglob('*.py') if '__pycache__' not in p.parts)
    models=model_sources();files.extend(Path(p) for p in models)
    value={'model_sources':models,'schema':'attentionbench.dev-identity-lock.v2','source_root':str(ROOT),'source_sets':{str(ROOT/f):sorted(str(p.absolute()) for p in (ROOT/f).rglob('*.py') if '__pycache__' not in p.parts) for f in ['openarm365_dev_v2','openarm365_dev','openarm365','curobo_v2']},'files':{str(p.absolute()):sha(p) for p in sorted(set(files))},'planner_lock':{'path':str(planner_path),'sha256':planner_sha},'sim_python':str(SIM_PYTHON),'sim_source_root':str(ROBocasa),'task_source_root':str(ROBOSUITE),'asset_manifest':str(ASSETS),'formal_eligible':False}
    if execution_settings is not None:
        from curobo_v2.execution_settings import receipt_fields
        if receipt_fields(execution_settings).get('deployment_kind')=='dev-local':value['planner_deployment_kind']='dev-local'
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,sort_keys=True,indent=2)
    path.chmod(0o444)
    return {'path':str(path.absolute()),'sha256':sha(path)}
def new_execution_settings(*,budget_tier='dev',budget_boundary='dev_loop',planner_source=None,rules_profile=None,fixture_telemetry_hz=None):
    """Select matching settings for a new run's identity lock and config.

    Frozen configs must retain their own settings, including legacy absence.
    """
    if fixture_telemetry_hz is not None and (rules_profile!=R5_FIXTURE_20261031.profile or type(fixture_telemetry_hz) is not int or fixture_telemetry_hz!=20):raise ValueError('R5 product fixture telemetry requires 20 Hz')
    if planner_source is not None:_planner_source(planner_source,budget_tier,budget_boundary)
    if rules_profile is not None:
        if rules_profile!=R5_FIXTURE_20261031.profile or budget_tier!='dev' or budget_boundary!='dev_loop':raise ValueError('fixture profile requires dev tier and dev_loop boundary')
        from curobo_v2.execution_settings import R5_PRODUCT_20261031
        return _source_settings(planner_source or dict(kind='dev-local',source_root=str(ROOT)),R5_FIXTURE_20261031 if fixture_telemetry_hz is None else R5_PRODUCT_20261031)
    return _source_settings(planner_source or dict(kind='dev-local',source_root=str(ROOT))) if budget_boundary=='dev_loop' and budget_tier=='dev' else None


def make_dev_config(task_id,seed,lock,layout_id=11,style_id=34,*,expected_source_root=None,budget_boundary='dev_loop',budget_tier='dev',planner_source=None,execution_settings=None,bimanual_collaboration='none'):
    if task_id=='pick_place_counter_to_cabinet' and (budget_tier!='dev' or budget_boundary!='dev_loop'):
        raise ValueError('cabinet pick/place requires dev tier and dev_loop boundary')
    if planner_source is None and budget_tier=='dev' and budget_boundary=='dev_loop':
        planner_source=dict(kind='dev-local',source_root=str(ROOT))
    if planner_source is not None:_planner_source(planner_source,budget_tier,budget_boundary)
    settings=execution_settings
    if settings is None:
        settings=new_execution_settings(budget_tier=budget_tier,budget_boundary=budget_boundary,planner_source=planner_source)
    else:
        settings=from_config(settings.to_dict())
        if budget_tier!='dev' or budget_boundary!='dev_loop':raise ValueError('execution_settings requires dev_loop boundary and dev tier')
    # The frozen constructor seals r4 only; legacy/r3 retain their exact bytes.
    return _make_dev_config(task_id,seed,lock,layout_id,style_id,expected_source_root=expected_source_root,
        budget_boundary=budget_boundary,budget_tier=budget_tier,execution_settings=settings,planner_source=planner_source,bimanual_collaboration=bimanual_collaboration)


def _make_dev_config(task_id,seed,lock,layout_id=11,style_id=34,*,expected_source_root=None,budget_boundary='dev_loop',budget_tier='dev',execution_settings=None,planner_source=None,bimanual_collaboration='none'):
    if bimanual_collaboration not in ('none','c1','handoff'):raise ValueError('bimanual_collaboration must be none, c1 or handoff')
    from curobo_v2.fixture_interaction import enabled,PILOT_TASKS
    fixture=enabled(execution_settings)
    if fixture and (task_id not in PILOT_TASKS or budget_tier!='dev' or budget_boundary!='dev_loop' or planner_source is None):
        raise ValueError('fixture profile requires a pilot task and dev-local dev_loop settings')
    if task_id in PILOT_TASKS and task_id not in ('open_cabinet','pick_place_counter_to_cabinet') and not fixture:
        raise ValueError('fixture pilot task requires r5-fixture-20261031')
    if task_id=='pick_place_counter_to_cabinet' and (budget_tier!='dev' or budget_boundary!='dev_loop'):
        raise ValueError('cabinet pick/place requires dev tier and dev_loop boundary')
    if task_id=='pick_place_counter_to_cabinet' and (execution_settings is None or execution_settings.profile not in ('r4-20261027','r5-fixture-20261031') or planner_source is None):
        raise ValueError('cabinet pick/place requires r4 dev-local execution settings')
    if planner_source is not None:
        _planner_source(planner_source,budget_tier,budget_boundary)
        from curobo_v2.identity import load_lock
        from curobo_v2.dev_local import is_dev_local
        if execution_settings is None:raise ValueError('planner_source requires dev-local execution_settings')
        runtime,actual=load_lock(execution_settings.planner_deployment['lock_path'],tier='dev',boundary='dev_loop')
        if not is_dev_local(runtime) or actual!=execution_settings.planner_deployment['sha256']:raise ValueError('planner_source requires dev-local planner pin')
    if budget_boundary not in ('dev_loop','formal'):raise ValueError('unknown budget boundary')
    check_tier(budget_tier)
    if budget_boundary=='formal' and budget_tier!='legacy':raise ValueError('formal boundary refuses dev budget tier')
    limits=formal_task_limits() if budget_boundary=='formal' else task_limits(budget_tier)
    t=validate_selection(task_id,seed,layout_id,style_id,fixture=fixture)
    if fixture and hasattr(execution_settings,'fixture_telemetry_hz'):
        from benchmarks.attention_harness.dev_fixture_tasks import validate
        validate(task_id,seed)
    identity=verify_lock(lock,expected_source_root=expected_source_root)
    if identity is not None and identity.get('planner_deployment_kind')=='dev-local':
        if planner_source is None:raise ValueError('dev-local lock requires planner_source')
        if execution_settings is None:raise ValueError('dev-local lock requires execution_settings')
    pin={'path':str(LOCK),'sha256':LOCK_SHA} if execution_settings is None else dict(
        path=execution_settings.planner_deployment['lock_path'],sha256=execution_settings.planner_deployment['sha256'])
    if identity is not None and identity['planner_lock']!=pin:raise ValueError('Dev identity planner lock differs from execution settings')
    c=copy.deepcopy(EXPECTED)
    c.update(schema_version=SCHEMA,task_id=t.task_id,official_env=t.official_env,task_prompt=t.task_prompt,
       seed=seed,robot=t.robot,execution_arm=t.execution_arm,controller='BASIC',
       camera_names=['robot0_agentview_left','gripper0_right_eye_in_hand'],horizon=limits['control_steps'],
       sdk_call_limit=limits['sdk_calls'],attempt_wall_seconds=limits['attempt_seconds'],whole_case_wall_seconds=1800 if budget_boundary in ('formal',) or budget_tier=='legacy' else case_wall_seconds(),
       asset_manifest_sha256=sha(ASSETS),object_set_id='robocasa365:pretrain:'+sha(ASSETS),
       base_mode='commanded_motion',runner_boundary='dev_loop',execution_controller_input='absolute',
       reset_controller_input='delta',global_random_seed=seed,gripper_command_endpoints_m=[.001,.043],
       dev_lock=lock,contact_exempt_objects=DEFAULT_CONTACT_EXEMPT_OBJECTS,held_reference_mode='fixed',
       planner_mode=DEFAULT_PLANNER_MODE,ik_candidates=DEFAULT_IK_CANDIDATES,
       ik_max_joint_jump=DEFAULT_IK_MAX_JOINT_JUMP)
    from .environment import initial_joints
    c['initial_joint_targets']=initial_joints(task_id,fixture=True) if fixture else initial_joints(task_id)
    if budget_tier=='dev':c['budget_tier']='dev'
    if execution_settings is not None:c['execution_settings']=execution_settings.to_dict()
    if bimanual_collaboration in ('c1','handoff'):c['bimanual_collaboration']=bimanual_collaboration
    if planner_source is not None:c['planner_source']=dict(planner_source)
    if fixture:
        from .fixture_contract import contract_ref,success_ref
        c.update(scene_reset_seed=seed,fixture_scene_contract=contract_ref(task_id),fixture_success_condition=success_ref(task_id))
        if hasattr(execution_settings,'fixture_telemetry_hz'):c['scene_verification']='fixture_contract_only'
    from benchmarks.attention_harness.dev_tasks import base_description_enabled,dev_sdk_seal
    if base_description_enabled(c) or bimanual_collaboration in ('c1','handoff'):c.update(dev_sdk_seal(c))
    return c
def validate_dev_config(c,*,expected_source_root=None,budget_boundary='dev_loop'):
    if not isinstance(c,dict):raise ValueError('Dev config required')
    tier=c.get('budget_tier','legacy')
    source=c.get('model_source','parcc')
    if source not in ('parcc','codex') or (source=='codex' and (tier!='dev' or budget_boundary=='formal')):raise ValueError('Codex model source requires dev tier')
    if 'planner_source' in c:_planner_source(c['planner_source'],tier,budget_boundary)
    if c.get('planner_source') is not None and c.get('runner_boundary')!='dev_loop':raise ValueError('planner_source dev-local requires dev_loop boundary and dev tier')
    settings=None
    if 'execution_settings' in c:
        if budget_boundary!='dev_loop' or c.get('runner_boundary')!='dev_loop' or tier!='dev':raise ValueError('execution_settings requires dev_loop boundary and dev tier')
        if c['execution_settings'] is None:raise ValueError('execution_settings: complete execution profile block required')
        settings=from_config(c['execution_settings'])
    collaboration=c.get('bimanual_collaboration','none')
    if collaboration not in ('none','c1','handoff'):raise ValueError('bimanual_collaboration must be none, c1 or handoff')
    expected=_make_dev_config(c.get('task_id'),c.get('seed'),c.get('dev_lock'),c.get('layout_id'),c.get('style_id'),expected_source_root=expected_source_root,budget_boundary=budget_boundary,budget_tier=tier,execution_settings=settings,planner_source=c.get('planner_source'),bimanual_collaboration=collaboration)
    if 'bimanual_collaboration' in c:expected['bimanual_collaboration']=collaboration
    if source=='codex':expected['model_source']='codex'
    if 'dev_mode' in c:
        if c['dev_mode']!='agent' or tier!='dev' or budget_boundary!='dev_loop' or c.get('runner_boundary')!='dev_loop':
            raise ValueError('agent requires dev tier and dev_loop boundary')
        expected['dev_mode']='agent'
    if 'agent_policy_version' in c:
        if c.get('dev_mode')!='agent' or c['agent_policy_version'] not in ('agent-policy-v1','agent-policy-v2'):
            raise ValueError('agent policy version requires agent dev config')
        expected['agent_policy_version']=c['agent_policy_version']
    if 'dev_version_history' in c:
        if type(c['dev_version_history']) is not bool or tier!='dev' or budget_boundary!='dev_loop':
            raise ValueError('version history requires dev tier and dev_loop boundary')
        expected['dev_version_history']=c['dev_version_history']
    if c.get('contact_exempt_objects') not in ('none','movable_objects'):
        raise ValueError('contact_exempt_objects must be none or movable_objects')
    expected['contact_exempt_objects']=c['contact_exempt_objects']
    present=[key in c for key in PLANNER_CONFIG_KEYS]
    if any(present) and not all(present):
        raise ValueError('planner_mode, ik_candidates and ik_max_joint_jump must be present together')
    if all(present):
        if c['planner_mode'] not in ('plan','ik_path'):
            raise ValueError('planner_mode must be plan or ik_path')
        if type(c['ik_candidates']) is not int or not 1<=c['ik_candidates']<=32:
            raise ValueError('ik_candidates must be an integer in 1..32')
        jump=c['ik_max_joint_jump']
        if type(jump) not in (int,float) or not 0<jump<=MAX_DIAGNOSTIC_JOINT_JUMP or not math.isfinite(jump):
            raise ValueError('ik_max_joint_jump must be finite and in (0, 2.0]')
        expected.update({key:c[key] for key in PLANNER_CONFIG_KEYS})
    else:
        for key in PLANNER_CONFIG_KEYS:expected.pop(key)
    if c.get('held_reference_mode') not in ('fixed','settled'):
        raise ValueError('held_reference_mode must be fixed or settled')
    expected['held_reference_mode']=c['held_reference_mode']
    seconds=c.get('attempt_wall_seconds')
    limits=formal_task_limits() if budget_boundary=='formal' else task_limits(tier)
    if type(seconds) is not int or not 1<=seconds<=limits['attempt_seconds']:raise ValueError('SDK attempt wall exceeded')
    expected['attempt_wall_seconds']=seconds
    if 'scene_reset_seed' in c:
        if type(c['scene_reset_seed']) is not int or c['scene_reset_seed']!=c['seed']:raise ValueError('scene reset seed mismatch')
        expected['scene_reset_seed']=c['scene_reset_seed']
    if 'scene_contract' in c:
        from .scene_contract import validate
        from .scene_contract import load_contract,seed_entry,SCHEMA_V2
        ref=c['scene_contract'];contract=load_contract(ref)
        if contract['schema']==SCHEMA_V2:seed_entry(contract,c['task_id'],c['seed'])
        else:validate(ref,c['task_id'],contract['tasks'][c['task_id']]['state'])
        if 'scene_reset_seed' not in c:raise ValueError('scene contract requires reproducible reset')
        expected['scene_contract']=ref
    if 'eval_agent' in c:
        review=c['eval_agent']
        from benchmarks.attention_harness.eval_agent import normalize as normalize_eval
        expected['eval_agent']=dict(review) if source=='parcc' and isinstance(review,dict) and set(review)=={'enabled','model'} and type(review['enabled']) is bool and review['model']=='parcc/Qwen' else normalize_eval(review,source,agent=c.get('dev_mode')=='agent')
    if 'human_proxy' in c:
        from benchmarks.attention_harness.human_proxy import normalize
        expected['human_proxy']=normalize(c['human_proxy'])
    if 'advisor_proxy' in c:
        from benchmarks.attention_harness.dev_advisor_proxy import normalize
        expected['advisor_proxy']=normalize(c['advisor_proxy'],source,agent=c.get('dev_mode')=='agent')
    if 'eval_agent' in expected and 'advisor_proxy' in expected:
        from benchmarks.attention_harness.dev_configuration import independent_eval_advisor
        independent_eval_advisor(expected['eval_agent'],expected['advisor_proxy'])
    if 'origin_dev_lock' in c:
        from openarm365_dev_v2.source_policy import drift_record,dev_tier
        if not dev_tier(c) or budget_boundary!='dev_loop':raise ValueError('origin lock requires dev tier')
        original=c['origin_dev_lock']
        if sha(original['path'])!=original['sha256']:raise ValueError('origin identity lock changed')
        drift_record(c,json.loads(Path(original['path']).read_bytes()),json.loads(Path(c['dev_lock']['path']).read_bytes()),original['sha256'],c['dev_lock']['sha256'])
        expected['origin_dev_lock']=original
    from benchmarks.attention_harness.dev_tasks import SDK_SEAL_KEYS,verify_dev_sdk_seal,dev_sdk_seal
    verify_dev_sdk_seal(c)
    if any(k in c for k in SDK_SEAL_KEYS):expected.update(dev_sdk_seal(c))
    if c!=expected or any(type(c[k]) is not type(v) for k,v in expected.items()):raise ValueError('Dev configuration mismatch')
    return c
