"""Freeze bounded Robosuite engineering smoke before executing formal attempts."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UNIVERSE = Path('/home/truares/桌面/Tidybot-Universe-attention-native')
SERVICE = Path('/home/truares/桌面/robosuite_sim-service')
POLICY = UNIVERSE / 'benchmarks/attention_harness/protocol/v2/policies/robosuite_depth_repeat_smoke.py'
REVISION = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SERVICE, text=True).strip()
assert REVISION == '8fb89e8ee7722e37e13530fb019aabd64d5367ba'
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SERVICE, text=True).strip()
assert not (ROOT / 'depth_stability_freeze.json').exists()
shutil.copyfile(POLICY, ROOT / 'robosuite_depth_repeat_smoke.py')
policy_hash = hashlib.sha256((ROOT / 'robosuite_depth_repeat_smoke.py').read_bytes()).hexdigest()
discovery = json.loads((ROOT / 'robosuite_variations.json').read_text())
configs = []
config_root = ROOT / 'robosuite_stability_configs'
config_root.mkdir()
for case in discovery['cases']:
    config = {
        'schema_version': 'attentionbench.robosuite-formal-config.v1',
        'suite': 'robosuite', 'task_id': case['task'], 'seed': case['seed'],
        'perception_mode': 'sim_gt', 'camera_name': 'agentview',
        'horizon': 500, 'camera_height': 64, 'camera_width': 64,
        **case['variation'], 'service_revision': REVISION,
        'safety_limits': {'max_delta_m': 0.25, 'max_observed_step_m': 0.5},
    }
    path = config_root / f"{case['task']}-seed{case['seed']}.json"
    path.write_text(json.dumps(config, indent=2, sort_keys=True) + '\n')
    configs.append({'task': case['task'], 'seed': case['seed'], 'config': str(path),
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
freeze = {
    'schema_version': 'attentionbench.depth-engineering-freeze.v1',
    'formal_eligible': False,
    'purpose': 'bounded HTTP/formal-runner repeated-action stability, not formal scores',
    'universe_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=UNIVERSE, text=True).strip(),
    'service_revision': REVISION,
    'policy_sha256': policy_hash,
    'policy': str(ROOT / 'robosuite_depth_repeat_smoke.py'),
    'configs': configs,
    'order': 'cube_lift then cube_stack; seeds 101, 102, 103 ascending; 3 open-close pairs each',
    'budget': {'overall_deadline_seconds_per_case': 120, 'assistance_credits': 0},
}
(ROOT / 'depth_stability_freeze.json').write_text(json.dumps(freeze, indent=2, sort_keys=True) + '\n')
print(json.dumps({'cases': len(configs), 'policy_sha256': policy_hash,
                  'freeze_sha256': hashlib.sha256((ROOT / 'depth_stability_freeze.json').read_bytes()).hexdigest()}))
