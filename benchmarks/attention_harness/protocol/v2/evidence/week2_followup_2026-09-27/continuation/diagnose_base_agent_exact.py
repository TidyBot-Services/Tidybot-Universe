"""Public Agent SDK job diagnostic; not a policy trial or score."""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, '/home/truares/桌面/Tidybot-Universe-attention-native')
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient  # noqa: E402
from benchmarks.attention_harness.robocasa_native.formal_services import DedicatedRobocasaServices  # noqa: E402

ROOT = Path(__file__).resolve().parent
U = Path('/home/truares/桌面/Tidybot-Universe-attention-native')
config = json.loads((U / 'benchmarks/attention_harness/protocol/v2/formal_robocasa_counter_to_sink_seed101_week2.json').read_text())
service = DedicatedRobocasaServices(
    task_id='counter_to_sink',
    sim_source_root=Path('/home/truares/桌面/maniskill_sim-attention-variation'),
    agent_source_root=Path('/home/truares/文档/Tidybot-Universe/agent_server'),
    task_source_root=Path('/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks'),
    sim_python=Path('/home/truares/miniconda3/envs/maniskill/bin/python'),
    agent_python=Path('/home/truares/miniconda3/envs/maniskill/bin/python'),
    expected_sim=config['sim_service'], expected_agent=config['agent_service'],
    expected_task=config['task_source'], expected_runtime=config['sim_runtime'],
    port_offset=config['port_offset'], log_dir=ROOT / 'base_agent_exact_service',
    deadline=time.monotonic() + 180,
)
code = '''from robot_sdk import base
base.move_delta(dx=0.125, dy=-0.1, dtheta=0.0, frame='local')
'''
record = {'schema_version': 'attentionbench.base-agent-sdk-diagnostic.v1',
          'formal_eligible': False, 'task': 'counter_to_sink', 'seed': 101,
          'code': code, 'config': config}


def request(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(service.agent_url + path, data=data, method=method,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)


try:
    with service:
        client = RobocasaSimClient('counter_to_sink', base_url=service.sim_url)
        client.reset(101)
        submitted = request('POST', '/code/submit',
                            {'code': code, 'holder': 'attentionbench-base-diagnostic',
                             'timeout': 30, 'reset_env': False})
        record['submitted'] = submitted
        job_id = submitted['job_id']
        while time.monotonic() < service.deadline:
            job = request('GET', f'/code/jobs/{job_id}')
            if job['status'] in {'completed', 'failed', 'cancelled'}:
                record['job'] = job
                break
            time.sleep(0.2)
        else:
            record['error'] = 'job polling deadline'
        record['native_success'] = client.native_success()
finally:
    record['service_stop'] = service.stop_receipt
    (ROOT / 'base_agent_exact_diagnostic.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps({'job_status': record.get('job', {}).get('status'),
                  'service_stop': record['service_stop']}))
