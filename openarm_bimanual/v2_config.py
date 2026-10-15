"""Hash-bound launch of the original stationary dual-arm V2 deployment."""
from pathlib import Path
import hashlib,math
from benchmarks.attention_harness.robocasa_native.robot_config import robot_selection
from .v2_adapter.bimanual_contract import deployment_contract,attest_runtime
SCHEMA='attentionbench.robocasa-openarm-bimanual-config.v2'
ROOT=Path(__file__).resolve().parents[1]
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def validate_config(request,value):
 required={'schema_version','suite','task_id','seed','perception_mode','robot','model_sha256','robot_parameters',
  'service_files','v2_deployment','scene_id','object_set_id','camera_names','task_prompt','safety_limits'}
 if not isinstance(value,dict)or set(value)!=required:raise ValueError('Complete certified bimanual launch binding required')
 if (value['schema_version']!=SCHEMA or value['suite']!='robocasa' or value['task_id']!='counter_to_sink'
     or request.task_id!=value['task_id'] or type(value['seed'])is not int or value['seed']!=101 or request.seed!=101
     or value['perception_mode']!='sim_gt' or robot_selection(value).model!='OpenArm'):
  raise ValueError('Frozen original SAPIEN task/seed/robot selection differs')
 contract=deployment_contract()
 if value['v2_deployment']!=contract or value['robot_parameters']!=contract['parameters']:
  raise ValueError('Config, SDK and authenticated deployment binding differs')
 if value['model_sha256']!='c82a2500c5471449374b4dd8d6037d58209174ffee211bb196a1c691cea45eff':raise ValueError('Original model changed')
 if (value['scene_id']!='scene:29f6ddc71995db3e0fd6' or value['object_set_id']!='objects:30b8da8d14e995e02588'
     or value['camera_names']!=['base_camera']):raise ValueError('Original scene/object/camera changed')
 files=value['service_files']
 required_sources={'openarm_bimanual/v2_config.py','openarm_bimanual/v2_runner.py','openarm_bimanual/sdk.py',
  'benchmarks/attention_harness/robocasa_native/formal_runner.py','benchmarks/attention_harness/robocasa_native/robot_config.py',
  'benchmarks/attention_harness/ui_launch.py','benchmarks/attention_harness/ui_server.py',
  'benchmarks/attention_harness/ui/live.js','benchmarks/attention_harness/ui/index.html'}
 required_sources|={str(p.relative_to(ROOT))for p in (ROOT/'openarm_bimanual/v2_adapter').glob('*.py')}
 if not isinstance(files,dict)or not required_sources<=set(files):raise ValueError('UI, Runner and SDK source binding incomplete')
 for name,sha in files.items():
  p=ROOT/name
  if not p.resolve().is_relative_to(ROOT)or not p.is_file()or digest(p)!=sha:raise ValueError('Bound source differs: '+name)
 limits=value['safety_limits'];maximum={'max_delta_m':.25,'max_observed_step_m':.5,'max_base_delta_m':.3,'max_base_rotation_rad':.5}
 if not isinstance(limits,dict)or set(limits)!=set(maximum):raise ValueError('Safety envelope missing')
 if any(type(v)not in (int,float)or not math.isfinite(v)or not 0<v<=maximum[k]for k,v in limits.items()):raise ValueError('Safety envelope expanded')
 return value
