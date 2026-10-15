"""Independent dev configuration; no approved/formal digest fields are minted."""
import copy,hashlib,json
from pathlib import Path

PROFILE_ID='robocasa365-openarm-dual-counter-sink-dev'
SCHEMA='attentionbench.openarm365-dual-dev-config.v1'
LOCK=Path('/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-02/runtime-lock.json')
LOCK_SHA='07e18c15e946423838dedca5f52b2c98ec2b2292a8bf8b0ca8b62f9d13022ff6'

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def make_config():
    from openarm365.config import make_config as base
    from .environment import initial_joints
    c=copy.deepcopy(base('pick_place_counter_to_sink'))
    c.update(schema_version=SCHEMA,execution_arm='both',sdk_call_limit=80,
        attempt_wall_seconds=480,whole_case_wall_seconds=1800,
        initial_joint_targets=initial_joints(),base_mode='stationary',runner_boundary='dev_loop',
        execution_controller_input='absolute',reset_controller_input='delta',global_random_seed=101,
        gripper_command_endpoints_m=[.001,.043],formal_eligible=False,
        planner_lock={'path':str(LOCK),'sha256':LOCK_SHA})
    c['dev_adapter_sources']={str(p.resolve()):sha(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
    return c

def validate_config(c):
    if not isinstance(c,dict) or c!=make_config():raise ValueError('OpenArm365 dual dev frozen configuration mismatch')
    if sha(LOCK)!=LOCK_SHA:raise ValueError('OpenArm365 authenticated planner lock drift')
    from openarm365.config import make_config as base,validate_config as validate_base
    validate_base(base('pick_place_counter_to_sink'))
    return c
