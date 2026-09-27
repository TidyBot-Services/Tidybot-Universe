"""Backend-only RPC diagnostic; never policy-visible or scored."""
import json
import sys
import time
from multiprocessing.managers import BaseManager
from pathlib import Path

import numpy as np

sys.path.insert(0, '/home/truares/桌面/Tidybot-Universe-attention-native')
sys.path.insert(0, '/home/truares/文档/maniskill-tidyverse/tidybot_uni/sims/bridges/maniskill/base_tidybot')
from base_server.config import BASE_RPC_AUTHKEY  # noqa: E402
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient  # noqa: E402
from benchmarks.attention_harness.robocasa_native.formal_services import DedicatedRobocasaServices  # noqa: E402

ROOT = Path(__file__).resolve().parent
U = Path('/home/truares/桌面/Tidybot-Universe-attention-native')
config = json.loads((U / 'benchmarks/attention_harness/protocol/v2/formal_robocasa_counter_to_sink_seed101_week2.json').read_text())


class Manager(BaseManager):
    pass


Manager.register('Base')
service = DedicatedRobocasaServices(
    task_id='counter_to_sink',
    sim_source_root=Path('/home/truares/桌面/maniskill_sim-attention-variation'),
    agent_source_root=Path('/home/truares/文档/Tidybot-Universe/agent_server'),
    task_source_root=Path('/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks'),
    sim_python=Path('/home/truares/miniconda3/envs/maniskill/bin/python'),
    agent_python=Path('/home/truares/miniconda3/envs/maniskill/bin/python'),
    expected_sim=config['sim_service'], expected_agent=config['agent_service'],
    expected_task=config['task_source'], expected_runtime=config['sim_runtime'],
    port_offset=config['port_offset'], log_dir=ROOT / 'base_bridge_service',
    deadline=time.monotonic() + 180,
)
record = {'schema_version': 'attentionbench.base-bridge-diagnostic.v1',
          'formal_eligible': False, 'task': 'counter_to_sink', 'seed': 101,
          'service_config': config, 'samples': []}
try:
    with service:
        client = RobocasaSimClient('counter_to_sink', base_url=service.sim_url)
        client.reset(101)
        manager = Manager(address=('127.0.0.1', 50000 + config['port_offset']),
                          authkey=BASE_RPC_AUTHKEY)
        manager.connect()
        base = manager.Base()
        initial = base.get_full_state()
        record['initial_pose'] = np.asarray(initial['base_pose']).tolist()
        target = np.asarray(initial['base_pose'], dtype=float) + [0.125, -0.1, 0.0]
        record['target_pose'] = target.tolist()
        base.execute_action({'base_pose': target})
        for index in range(50):
            state = base.get_full_state()
            record['samples'].append({'index': index, 'elapsed_s': index * 0.1,
                                      'base_pose': np.asarray(state['base_pose']).tolist(),
                                      'base_velocity': np.asarray(state['base_velocity']).tolist()})
            time.sleep(0.1)
        record['native_success'] = client.native_success()
finally:
    record['service_stop'] = service.stop_receipt
    (ROOT / 'base_bridge_diagnostic.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'initial': record.get('initial_pose'), 'target': record.get('target_pose'),
                  'final': record['samples'][-1] if record['samples'] else None,
                  'stop': record['service_stop']}))
