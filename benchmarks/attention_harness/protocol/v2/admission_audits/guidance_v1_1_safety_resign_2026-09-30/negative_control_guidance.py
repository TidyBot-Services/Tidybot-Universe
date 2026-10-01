"""One real-Service control; only the backend acknowledgement is fault injected."""
import hashlib
import json
import signal
import threading
import time
from pathlib import Path
from unittest.mock import patch

from benchmarks.attention_harness.formal_runner_boundary import run_with_formal_boundary
from benchmarks.attention_harness.robocasa_native.controlled_negative import controlled_negative_request
from benchmarks.attention_harness.robocasa_native.formal_runner import RobocasaFormalSuiteRunner
import benchmarks.attention_harness.robocasa_native.formal_runner as runner_module
from benchmarks.attention_harness.robocasa_native.agent_actions import AgentServerActionBackend
from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend
from benchmarks.attention_harness.robocasa_native.formal_services import DedicatedRobocasaServices

ROOT = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(path, value):
    temporary = Path(path).with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    temporary.replace(path)


def stamp():
    return {'epoch': time.time(), 'monotonic': time.monotonic()}


def main():
    plan_path = ROOT / 'validation_plan.json'
    plan = json.loads(plan_path.read_text())
    pin = json.loads((ROOT / 'independent/negative_control_pre_run_pin.json').read_text())
    assert pin['approved'] and pin['plan_sha256'] == sha(plan_path)
    assert pin['script_sha256'] == sha(__file__)
    for path, digest in plan['file_sha256'].items():
        assert sha(path) == digest, 'pre-run source drift: ' + path
    assert time.time() < plan['round_deadline_epoch'] - plan['whole_case_wall_seconds'] - 60
    out = ROOT / plan['run_directory']
    request = controlled_negative_request(
        run_dir=out, policy_path=Path(plan['policy_path']), policy_sha256=plan['policy_sha256'],
        config_path=Path(plan['config_path']), config_sha256=plan['config_sha256'],
        deadline_seconds=plan['attempt_deadline_seconds'],
        attention_input=json.loads(Path(plan['attention_input_path']).read_text()),
    )
    assert request.run_id == plan['run_id'] and request.attempt_id == plan['attempt_id']
    out.mkdir(exist_ok=False)  # A second launch is rejected, even after a failed control.
    receipt_path = out / 'injection_receipt.json'
    record = {'schema_version': 'attentionbench.guidance-v1.1-controlled-unknown-action.v1',
              'role': 'separate_safety_negative_control_not_matrix',
              'started': stamp(), 'plan_sha256': sha(plan_path),
              'run_id': request.run_id, 'attempt_id': request.attempt_id,
              'policy_sha256': request.policy_sha256, 'config_sha256': request.config_sha256,
              'attention_input_sha256': sha(plan['attention_input_path']), 'attention_input': request.attention_input,
              'dispatches': [], 'job_submissions': [], 'job_receipts': [],
              'safety_detections': [], 'worker_outcomes': [], 'service_stops': []}
    save(receipt_path, record)
    cancel = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: cancel.set())
    call0 = AgentServerActionBackend._call
    base0 = AgentServerActionBackend.move_base_delta
    violate0 = SafetyMonitorBackend._violate
    stop0 = DedicatedRobocasaServices.stop
    worker0 = runner_module.execute_formal_policy

    def capture_call(self, method, path, *args, **kwargs):
        reply = call0(self, method, path, *args, **kwargs)
        if method == 'POST' and path == '/code/submit':
            record['job_submissions'].append({**stamp(), 'job_id': reply.get('job_id'), 'code': args[0]['code']})
            save(receipt_path, record)
        if method == 'GET' and path.startswith('/code/jobs/') and reply.get('status') == 'completed':
            record['job_receipts'].append({**stamp(), 'job_path': path,
                                          'status': reply['status'], 'result': reply.get('result')})
            save(receipt_path, record)
        return reply

    def unknown_after_real_base(self, dx, dy, dtheta, *, frame):
        record['dispatches'].append({**stamp(), 'dx': dx, 'dy': dy, 'dtheta': dtheta, 'frame': frame})
        save(receipt_path, record)
        if len(record['dispatches']) != 1:
            raise AssertionError('a second action was dispatched')
        base0(self, dx, dy, dtheta, frame=frame)
        if not record['job_receipts'] or record['job_receipts'][-1]['result'].get('exit_code') != 0:
            raise AssertionError('real Agent job completion receipt missing')
        record['injected'] = stamp()
        save(receipt_path, record)
        raise RuntimeError('predeclared_guidance_v1_1_unknown_after_real_base_completion')

    def capture_violation(self, kind, **details):
        detected = stamp()
        try:
            return violate0(self, kind, **details)
        finally:
            # Record only after the original monitor has appended its violation.
            record['safety_detections'].append({**detected, 'kind': kind,
                'details': details, 'original_violation': dict(self.violations[-1]),
                'monitor_state_samples': sum(e['kind'] == 'state_sample' for e in self.events)})
            save(receipt_path, record)

    def capture_worker(*args, **kwargs):
        result = worker0(*args, **kwargs)
        record['worker_outcomes'].append({**stamp(), **result.__dict__})
        save(receipt_path, record)
        return result

    def capture_stop(self, reason):
        entered = stamp()
        performed = self.stop_receipt is None
        result = stop0(self, reason)
        record['service_stops'].append({'entered': entered, 'confirmed': stamp(),
            'requested_reason': reason, 'performed_stop': performed, 'receipt': result})
        save(receipt_path, record)
        return result

    runner = RobocasaFormalSuiteRunner(
        **{k: Path(v) for k, v in plan['runner_options'].items()}, cancel_event=cancel)
    with patch.object(AgentServerActionBackend, '_call', capture_call), \
         patch.object(AgentServerActionBackend, 'move_base_delta', unknown_after_real_base), \
         patch.object(SafetyMonitorBackend, '_violate', capture_violation), \
         patch.object(DedicatedRobocasaServices, 'stop', capture_stop), \
         patch.object(runner_module, 'execute_formal_policy', capture_worker):
        try:
            result = run_with_formal_boundary(request, runner=runner)
            save(out / 'result.json', result)
            record['result_path'] = str(out / 'result.json')
            record['result_sha256'] = sha(out / 'result.json')
        except Exception as exc:
            record['error'] = type(exc).__name__ + ': ' + str(exc)
        finally:
            record['finished'] = stamp()
            record['post_run_source_drift'] = [p for p, h in plan['file_sha256'].items() if sha(p) != h]
            save(receipt_path, record)
    print(json.dumps({'output': str(out), 'error': record.get('error'),
                      'dispatches': len(record['dispatches']), 'completed_jobs': len(record['job_receipts']),
                      'safety_detected': [d['kind'] for d in record['safety_detections']]}), flush=True)


if __name__ == '__main__':
    main()
