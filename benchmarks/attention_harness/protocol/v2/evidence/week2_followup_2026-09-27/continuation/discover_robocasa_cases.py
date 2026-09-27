"""Bounded development variation discovery; not a policy trial or score."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '/home/truares/桌面/Tidybot-Universe-attention-native')
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.robocasa_native.formal_services import DedicatedRobocasaServices

root = Path(__file__).resolve().parent
config = json.loads((root / 'formal_robocasa_counter_to_sink_seed101_rebind.json').read_text())
service = DedicatedRobocasaServices(
    task_id='counter_to_sink',
    sim_source_root=Path('/home/truares/桌面/maniskill_sim-attention-variation'),
    agent_source_root=Path('/home/truares/文档/Tidybot-Universe/agent_server'),
    task_source_root=Path('/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks'),
    sim_python=Path('/home/truares/miniconda3/envs/maniskill/bin/python'),
    agent_python=Path('/home/truares/miniconda3/envs/maniskill/bin/python'),
    expected_sim=config['sim_service'], expected_agent=config['agent_service'],
    expected_task=config['task_source'], expected_runtime=config['sim_runtime'],
    port_offset=config['port_offset'], log_dir=root / 'robocasa_discovery_service',
    deadline=time.monotonic() + 240,
)
rows = []
try:
    with service:
        client = RobocasaSimClient('counter_to_sink', base_url=service.sim_url)
        for seed in [101, 102, 103, 104, 105]:
            reset = client._call('POST', '/reset', {'seed': seed, 'discover_variation': True}, timeout=120)
            obs = client.observe()
            perceive = client._call('POST', '/perceive', {'camera_names': ['base_camera','wrist_camera']}, timeout=120)
            row = {'seed': seed, 'reset': reset, 'language': obs.language,
                   'cameras': obs.cameras, 'object_names': [o['name'] for o in perceive['objects']],
                   'native_success': client.native_success()}
            rows.append(row)
            print(json.dumps(row), flush=True)
finally:
    (root / 'robocasa_discovered_cases.json').write_text(json.dumps({
        'formal_eligible': False, 'config': config, 'rows': rows,
        'service_stop': service.stop_receipt,
    }, indent=2) + '\n')
