"""New development conditions and immutable resource lock, separate from v1 freeze."""
import copy,hashlib,json
from pathlib import Path
from benchmarks.attention_harness.dev_tasks import get_task,validate_selection
from benchmarks.attention_harness.robocasa365.config import EXPECTED
from openarm365_dev.config import LOCK,LOCK_SHA
from .diag_budget import task_limits,formal_task_limits,case_wall_seconds,check_tier
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
    models=model_sources(runtime_root=expected_root)
    if lock.get('model_sources')!=models or any(lock['files'].get(k)!=v for k,v in models.items()):raise ValueError('transitive robot model lock incomplete/drifted')
    for name,digest in lock['files'].items():
        if sha(name)!=digest:raise ValueError('resource source drift: '+name)
    if lock['planner_lock']!={'path':str(LOCK),'sha256':LOCK_SHA} or sha(LOCK)!=LOCK_SHA:raise ValueError('official V2 lock drift')
    return lock
def make_lock(path):
    path=Path(path)
    # Explicit files; never hash runtime artifacts or the generated lock itself.
    files=[]
    for folder in ['openarm365_dev_v2','openarm365_dev','openarm365','curobo_v2']:
        files.extend(p for p in (ROOT/folder).rglob('*.py') if '__pycache__' not in p.parts)
    files.extend(p for p in (ROOT/'benchmarks/attention_harness').glob('dev*.py'))
    files.append(ROOT/'benchmarks/attention_harness/v2_evaluator.py')
    files.append(ROOT/'benchmarks/attention_harness/daily_dev_v2.py')
    for name in ['core/policies.py','core/assessment.py','core/models.py','core/trace.py','core/dev_store.py','formal_runner_boundary.py','policy_input.py','parcc_client.py','dev_attention.py','ui_server.py','ui/live.js','ui/index.html','ui_history.py','ui_observability.py','ui_media.py','robocasa_native/policy_sandbox.py','robocasa365/recording.py','robocasa365/human_camera.py']:
        files.append(ROOT/'benchmarks/attention_harness'/name)
    files.extend(p for p in (ROOT/'benchmarks/attention_harness/dev_ui').glob('*') if p.is_file())
    files.append(ROOT/'benchmarks/attention_harness/dev_failure_rules.json')
    files.extend([LOCK,SIM_PYTHON,ASSETS,ASSETS.with_name('assets-installation-lock.json'),ASSETS.with_name('assets-installed-manifest.json'),ROOT/'openarm365/configs/robot_parameters_v1.json',ROOT/'openarm365/model/identity.json'])
    for folder in [ROBocasa/'robocasa',ROBOSUITE/'robosuite']:
        files.extend(p for p in folder.rglob('*.py') if '__pycache__' not in p.parts)
    models=model_sources();files.extend(Path(p) for p in models)
    value={'model_sources':models,'schema':'attentionbench.dev-identity-lock.v2','source_root':str(ROOT),'source_sets':{str(ROOT/f):sorted(str(p.absolute()) for p in (ROOT/f).rglob('*.py') if '__pycache__' not in p.parts) for f in ['openarm365_dev_v2','openarm365_dev','openarm365','curobo_v2']},'files':{str(p.absolute()):sha(p) for p in sorted(set(files))},'planner_lock':{'path':str(LOCK),'sha256':LOCK_SHA},'sim_python':str(SIM_PYTHON),'sim_source_root':str(ROBocasa),'task_source_root':str(ROBOSUITE),'asset_manifest':str(ASSETS),'formal_eligible':False}
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(value,f,sort_keys=True,indent=2)
    path.chmod(0o444)
    return {'path':str(path.absolute()),'sha256':sha(path)}
def make_dev_config(task_id,seed,lock,layout_id=11,style_id=34,*,expected_source_root=None,budget_boundary='dev_loop',budget_tier='dev'):
    if budget_boundary not in ('dev_loop','formal'):raise ValueError('unknown budget boundary')
    check_tier(budget_tier)
    if budget_boundary=='formal' and budget_tier!='legacy':raise ValueError('formal boundary refuses dev budget tier')
    limits=formal_task_limits() if budget_boundary=='formal' else task_limits(budget_tier)
    t=validate_selection(task_id,seed,layout_id,style_id);verify_lock(lock,expected_source_root=expected_source_root)
    c=copy.deepcopy(EXPECTED)
    c.update(schema_version=SCHEMA,task_id=t.task_id,official_env=t.official_env,task_prompt=t.task_prompt,
       seed=seed,robot=t.robot,execution_arm=t.execution_arm,controller='BASIC',
       camera_names=['robot0_agentview_left','gripper0_right_eye_in_hand'],horizon=limits['control_steps'],
       sdk_call_limit=limits['sdk_calls'],attempt_wall_seconds=limits['attempt_seconds'],whole_case_wall_seconds=1800 if budget_boundary in ('formal',) or budget_tier=='legacy' else case_wall_seconds(),
       asset_manifest_sha256=sha(ASSETS),object_set_id='robocasa365:pretrain:'+sha(ASSETS),
       base_mode='commanded_motion',runner_boundary='dev_loop',execution_controller_input='absolute',
       reset_controller_input='delta',global_random_seed=seed,gripper_command_endpoints_m=[.001,.043],
       dev_lock=lock,contact_exempt_objects='none',held_reference_mode='fixed')
    from .environment import initial_joints
    c['initial_joint_targets']=initial_joints(task_id)
    if budget_tier=='dev':c['budget_tier']='dev'
    return c
def validate_dev_config(c,*,expected_source_root=None,budget_boundary='dev_loop'):
    if not isinstance(c,dict):raise ValueError('Dev config required')
    tier=c.get('budget_tier','legacy')
    expected=make_dev_config(c.get('task_id'),c.get('seed'),c.get('dev_lock'),c.get('layout_id'),c.get('style_id'),expected_source_root=expected_source_root,budget_boundary=budget_boundary,budget_tier=tier)
    if c.get('contact_exempt_objects') not in ('none','movable_objects'):
        raise ValueError('contact_exempt_objects must be none or movable_objects')
    expected['contact_exempt_objects']=c['contact_exempt_objects']
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
        expected['eval_agent']=dict(review) if isinstance(review,dict) and set(review)=={'enabled','model'} and type(review['enabled']) is bool and review['model']=='parcc/Qwen' else normalize_eval(review)
    if 'human_proxy' in c:
        from benchmarks.attention_harness.human_proxy import normalize
        expected['human_proxy']=normalize(c['human_proxy'])
    if 'advisor_proxy' in c:
        from benchmarks.attention_harness.dev_advisor_proxy import normalize
        expected['advisor_proxy']=normalize(c['advisor_proxy'])
    if 'eval_agent' in expected and 'advisor_proxy' in expected and expected['eval_agent']['model']==expected['advisor_proxy']['model']:raise ValueError('Eval and Advisor models must differ')
    if c!=expected or any(type(c[k]) is not type(v) for k,v in expected.items()):raise ValueError('Dev configuration mismatch')
    return c
