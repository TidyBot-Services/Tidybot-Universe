"""One predeclared real-Service controlled unknown-action negative control."""
import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest, run_with_formal_boundary
from benchmarks.attention_harness.robocasa_native.formal_runner import RobocasaFormalSuiteRunner
from benchmarks.attention_harness.robocasa_native.agent_actions import AgentServerActionBackend
from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend
from benchmarks.attention_harness.robocasa_native.formal_services import DedicatedRobocasaServices

ROOT = Path(__file__).resolve().parent
PLAN = ROOT / 'validation_plan_v2.json'
PIN = ROOT / 'independent' / 'negative_control_pre_run_pin_v2.json'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p, d):
    Path(p).write_text(json.dumps(d, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
plan = json.loads(PLAN.read_text())
pin = json.loads(PIN.read_text())
assert pin['approved'] is True and pin['plan_sha256'] == sha(PLAN)
assert pin['script_sha256'] == sha(__file__)
for f, h in plan['file_sha256'].items():
    assert sha(f) == h, 'pre-run source drift: ' + f
assert time.time() < plan['round_deadline_epoch'] - 310
out = ROOT / 'negative_control_run_v2'
out.mkdir(exist_ok=False)
record = {'schema_version': 'attentionbench.controlled-unknown-action.v1',
          'role': 'separate_safety_negative_control_not_matrix', 'started_epoch': time.time(),
          'plan_sha256': sha(PLAN), 'dispatches': [], 'job_receipts': [], 'safety_detections': [], 'service_stops': []}
call0 = AgentServerActionBackend._call
grip0 = AgentServerActionBackend.set_gripper
violate0 = SafetyMonitorBackend._violate
stop0 = DedicatedRobocasaServices.stop

def capture_call(self, method, path, *args, **kwargs):
    reply = call0(self, method, path, *args, **kwargs)
    if method == 'GET' and path.startswith('/code/jobs/') and reply.get('status') == 'completed':
        record['job_receipts'].append({'epoch': time.time(), 'job_path': path,
                                      'status': reply['status'], 'result': reply.get('result')})
        save(out / 'injection_receipt.json', record)
    return reply

def unknown_after_real_gripper(self, command, *, settle_steps):
    record['dispatches'].append({'epoch': time.time(), 'command': command, 'settle_steps': settle_steps})
    if len(record['dispatches']) != 1:
        raise AssertionError('a second action was dispatched')
    grip0(self, command, settle_steps=settle_steps)
    if not record['job_receipts'] or record['job_receipts'][-1]['result'].get('exit_code') != 0:
        raise AssertionError('real Agent job completion receipt missing')
    record['injected_epoch'] = time.time()
    save(out / 'injection_receipt.json', record)
    raise RuntimeError('predeclared_test_unknown_action_after_real_completion')

def capture_violation(self, kind, **details):
    record['safety_detections'].append({'epoch': time.time(), 'kind': kind, 'details': details})
    save(out / 'injection_receipt.json', record)
    return violate0(self, kind, **details)

def capture_stop(self, reason):
    started = time.time()
    result = stop0(self, reason)
    record['service_stops'].append({'entered_epoch': started, 'confirmed_epoch': time.time(),
                                    'reason': reason, 'receipt': result})
    save(out / 'injection_receipt.json', record)
    return result

request = FormalRunRequest(suite='robocasa', task_id='counter_to_sink', seed=101,
    policy_code_path=Path(plan['policy_path']), policy_sha256=plan['policy_sha256'],
    config_path=Path(plan['config_path']), config_sha256=plan['config_sha256'],
    artifact_root=out / 'attempts', overall_deadline_seconds=120,
    run_id='run:seven-admission-safety-negative-control',
    attempt_id='attempt:seven-admission-safety-negative-control:0')
runner = RobocasaFormalSuiteRunner(**{k: Path(v) for k, v in plan['runner_options'].items()})
with patch.object(AgentServerActionBackend, '_call', capture_call), \
     patch.object(AgentServerActionBackend, 'set_gripper', unknown_after_real_gripper), \
     patch.object(SafetyMonitorBackend, '_violate', capture_violation), \
     patch.object(DedicatedRobocasaServices, 'stop', capture_stop):
    try:
        result = run_with_formal_boundary(request, runner=runner)
        save(out / 'result.json', result)
        record['result_path'] = str(out / 'result.json')
        record['result_sha256'] = sha(out / 'result.json')
    except Exception as exc:
        record['error'] = type(exc).__name__ + ': ' + str(exc)
record['finished_epoch'] = time.time()
record['post_run_source_drift'] = [f for f,h in plan['file_sha256'].items() if sha(f) != h]
save(out / 'injection_receipt.json', record)
print(json.dumps({'output': str(out), 'error': record.get('error'),
                  'real_dispatched_actions': len(record['dispatches']),
                  'safety_detected': [d['kind'] for d in record['safety_detections']]}), flush=True)
