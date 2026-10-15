"""Independent original-RoboCasa/OpenArm config; legacy v3 is unchanged."""
from pathlib import Path
import hashlib
import math

from benchmarks.attention_harness.robocasa_native.robot_config import robot_selection
from benchmarks.attention_harness.robocasa_native.tasks import get_robocasa_task

SCHEMA = 'attentionbench.robocasa-openarm-bimanual-config.v1'
ROOT = Path(__file__).resolve().parents[1]
_REQUIRED_SOURCE = {'openarm_robocasa/run_ui.sh','benchmarks/attention_harness/run_ui.sh','openarm_robocasa/service.py', 'openarm_robocasa/kitchen.py',
    'openarm_robocasa/planning.py', 'openarm_robocasa/session.py', 'openarm_robocasa/contact_policy.py',
    'openarm_robocasa/mimic.py', 'openarm_robocasa/contact_controller.py', 'openarm_robocasa/variation.py',
    'openarm_robocasa/camera.py',
    'benchmarks/attention_harness/robocasa_native/openarm_backend.py',
    'benchmarks/attention_harness/robocasa_native/openarm_runner.py',
    'benchmarks/attention_harness/robocasa_native/openarm_config.py',
    'benchmarks/attention_harness/robocasa_native/robot_config.py',
    'benchmarks/attention_harness/robocasa_native/safety_monitor.py',
    'benchmarks/attention_harness/robocasa_native/mobile_sdk.py',
    'benchmarks/attention_harness/robosuite_memory/formal_sandbox.py',
    'benchmarks/attention_harness/robosuite_memory/formal_policy_worker.py',
    'benchmarks/attention_harness/robocasa_native/policy_sandbox.py',
    'openarm_mobile/backend.py',
    'benchmarks/attention_harness/core/models.py',
    'benchmarks/attention_harness/core/store.py',
    'benchmarks/attention_harness/formal_attention_run.py',
    'benchmarks/attention_harness/hint_compilation.py',
    'benchmarks/attention_harness/ui_launch.py',
    'benchmarks/attention_harness/ui_media.py',
    'benchmarks/attention_harness/ui_server.py',
    'benchmarks/attention_harness/ui/live.js',
    'benchmarks/attention_harness/ui/index.html',
    'benchmarks/attention_harness/public_station.py',
    'benchmarks/attention_harness/service_recovery.py',
    'tidybot_sdk/facade.py',
    'tidybot_sdk/perception.py',
    'benchmarks/attention_harness/core/runtime.py',
    'benchmarks/attention_harness/policy_input.py',
    'benchmarks/attention_harness/sim_gt_attention_run.py',
    'benchmarks/attention_harness/robocasa_native/tasks.py',
    'benchmarks/attention_harness/task_dag.py',
    'benchmarks/attention_harness/ui_observability.py',
    'openarm_phase1/service.py', 'openarm_phase0_recheck/validate.py'}
_REQUIRED_SOURCE |= {str(p.relative_to(ROOT)) for p in (ROOT/'openarm_bimanual').glob('*.py')}
_REQUIRED_SOURCE |= {'openarm_mobile/model/source/tidyverse.urdf',
    'openarm_mobile/model/source/tidyverse_base/base_vis.stl',
    'openarm_bimanual/model/mobile_openarm.urdf','openarm_bimanual/model/mobile_openarm.srdf',
    'benchmarks/attention_harness/robocasa_native/formal_runner.py',
    'benchmarks/attention_harness/live_preview.py','benchmarks/attention_harness/episode_trace.py'}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_config(request, value):
    required = {'schema_version','suite','task_id','seed','perception_mode','robot',
       'model_sha256','robot_parameters','service_files','task_source','sim_runtime',
       'scene_id','object_set_id','camera_names','task_prompt','safety_limits'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('OpenArm config has missing or unexpected fields')
    if (value['schema_version'] != SCHEMA or value['suite'] != 'robocasa' or
            value['task_id'] != request.task_id or type(value['seed']) is not int or
            value['seed'] != request.seed or value['perception_mode'] != 'sim_gt'):
        raise ValueError('OpenArm config disagrees with original RoboCasa task/seed/track')
    get_robocasa_task(request.task_id)
    selected = robot_selection(value)
    if selected.model != 'OpenArm' or set(value['robot']) != {'model','execution_arm'}:
        raise ValueError('Independent OpenArm config requires explicit model and execution arm')
    for key in ['scene_id','object_set_id','task_prompt']:
        if not isinstance(value[key], str) or not value[key]:raise ValueError('OpenArm config lacks '+key)
    if value['camera_names'] != ['base_camera']:
        raise ValueError('OpenArm config supports only calibrated base_camera')
    model=ROOT/'openarm_bimanual/model/mobile_openarm.urdf'
    if value['model_sha256'] != digest(model):raise ValueError('OpenArm model differs from approved hash')
    params=value['robot_parameters']
    if not isinstance(params,dict) or set(params)!={'path','sha256'} or not isinstance(params['path'],str):
        raise ValueError('OpenArm config requires exact robot parameters')
    path=Path(params['path'])
    if not path.is_absolute() or not path.resolve().is_relative_to(ROOT/'openarm_bimanual/configs'):
        raise ValueError('OpenArm parameters must use independent config directory')
    from .kitchen import load_parameters
    parameters, actual=load_parameters(path)
    if params['sha256']!=actual or parameters['model_sha256']!=value['model_sha256']:
        raise ValueError('OpenArm robot parameters differ from approved hash/model')
    files=value['service_files']
    if not isinstance(files,dict) or not _REQUIRED_SOURCE<=set(files):
        raise ValueError('OpenArm config requires complete service source identities')
    for name, expected in files.items():
        file=ROOT/name
        if not file.resolve().is_relative_to(ROOT) or not file.is_file() or expected!=digest(file):
            raise ValueError('OpenArm source differs from approved hash: '+name)
    task=value['task_source']
    if not isinstance(task,dict) or set(task)!={'root','revision','tree_sha256'}:
        raise ValueError('OpenArm config requires original task source identity')
    if not isinstance(task['root'],str) or not Path(task['root']).is_absolute() or Path(task['root']).name!='robocasa_tasks':
        raise ValueError('OpenArm config requires original robocasa_tasks checkout')
    for key,length in [('revision',40),('tree_sha256',64)]:
        item=task[key]
        if not isinstance(item,str) or len(item)!=length or any(c not in '0123456789abcdef' for c in item):
            raise ValueError('invalid OpenArm task source identity')
    runtime=value['sim_runtime']
    keys={'python_version','python_sha256','mani_skill_version','mani_skill_root',
          'mani_skill_python_tree_sha256','mani_skill_files','task_module_path'}
    if not isinstance(runtime,dict) or set(runtime)!=keys or runtime['python_version']!='3.11.15' or runtime['mani_skill_version']!='3.0.0b22':
        raise ValueError('OpenArm config requires compatible pinned simulator runtime')
    for key in keys-{'mani_skill_files'}:
        if not isinstance(runtime[key],str) or not runtime[key]:raise ValueError('invalid OpenArm runtime '+key)
    if not isinstance(runtime['mani_skill_files'],dict) or not runtime['mani_skill_files']:
        raise ValueError('OpenArm config lacks native runtime source hashes')
    for item in [runtime['python_sha256'],runtime['mani_skill_python_tree_sha256'],*runtime['mani_skill_files'].values()]:
        if not isinstance(item,str) or len(item)!=64 or any(c not in '0123456789abcdef' for c in item):raise ValueError('invalid OpenArm runtime digest')
    limits=value['safety_limits'];maximum={'max_delta_m':.25,'max_observed_step_m':.5,'max_base_delta_m':.3,'max_base_rotation_rad':.5}
    if not isinstance(limits,dict) or set(limits)!=set(maximum):raise ValueError('invalid OpenArm safety limits')
    for key,item in limits.items():
        if isinstance(item,bool) or not isinstance(item,(int,float)) or not math.isfinite(item) or not 0<item<=maximum[key]:
            raise ValueError('OpenArm safety limit exceeds preserved envelope: '+key)
    return value


def attest_runtime(config, *, python, task_source_root):
    from benchmarks.attention_harness.robocasa_native.formal_services import source_identity, simulator_runtime_identity
    if task_source_root.resolve()!=Path(config['task_source']['root']).resolve():
        raise ValueError('OpenArm task checkout disagrees with launch')
    task=source_identity(task_source_root)
    if any(task[key]!=config['task_source'][key] for key in ['revision','tree_sha256']):
        raise ValueError('OpenArm original task source changed')
    if simulator_runtime_identity(python,task_source_root)!=config['sim_runtime']:
        raise ValueError('OpenArm simulator runtime differs from approved identity')
