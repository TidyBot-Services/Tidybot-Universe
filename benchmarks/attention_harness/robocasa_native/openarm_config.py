"""Independent original-RoboCasa/OpenArm config; legacy v3 is unchanged."""
from pathlib import Path
import hashlib
import math

from .robot_config import robot_selection
from .tasks import get_robocasa_task

SCHEMA = 'attentionbench.robocasa-openarm-formal-config.v2'
ROOT = Path(__file__).resolve().parents[3]
_REQUIRED_SOURCE = {'openarm_robocasa/service.py', 'openarm_robocasa/kitchen.py',
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
    'openarm_phase1/service.py', 'openarm_phase0_recheck/validate.py'}
_REQUIRED_SOURCE |= {'curobo_v2/'+n for n in ['deployment.py','identity.py','native_session.py',
    'native_auth.py','openarm_sdk.py','client.py','sweep.py','command_curve.py','trusted_deployments.json']}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_config(request, value):
    required = {'schema_version','suite','task_id','seed','perception_mode','robot',
       'model_sha256','robot_parameters','service_files','v2_deployment',
       'scene_id','object_set_id','camera_names','task_prompt','safety_limits'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('V2-only: OpenArm launch requires an explicit approved official V2 deployment and complete source binding')
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
    model=ROOT/'openarm_phase0_recheck/model/compat_v6/robot.urdf'
    if value['model_sha256'] != digest(model):raise ValueError('OpenArm model differs from approved hash')
    params=value['robot_parameters']
    if not isinstance(params,dict) or set(params)!={'path','sha256'} or not isinstance(params['path'],str):
        raise ValueError('OpenArm config requires exact robot parameters')
    path=Path(params['path'])
    if not path.is_absolute() or not path.resolve().is_relative_to(ROOT/'openarm_robocasa/configs'):
        raise ValueError('OpenArm parameters must use independent config directory')
    from openarm_robocasa.kitchen import load_parameters
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
    from curobo_v2.deployment import deployment_contract
    contract=deployment_contract()
    if value['v2_deployment']!=contract:
        raise ValueError('V2-only: declared runtime, native task or parameters differ from the official execution deployment')
    if params['sha256']!=contract['parameters']['sha256']:
        raise ValueError('V2-only: launch parameters differ from the actual native executor')
    limits=value['safety_limits'];maximum={'max_delta_m':.25,'max_observed_step_m':.5,'max_base_delta_m':.3,'max_base_rotation_rad':.5}
    if not isinstance(limits,dict) or set(limits)!=set(maximum):raise ValueError('invalid OpenArm safety limits')
    for key,item in limits.items():
        if isinstance(item,bool) or not isinstance(item,(int,float)) or not math.isfinite(item) or not 0<item<=maximum[key]:
            raise ValueError('OpenArm safety limit exceeds preserved envelope: '+key)
    return value


def attest_runtime(config):
    from curobo_v2.deployment import deployment_contract
    actual=deployment_contract()
    if config['v2_deployment']!=actual:
        raise ValueError('V2-only: execution deployment changed after launch binding')
    return actual
