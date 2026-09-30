"""Reconstruct coverage from retained artifacts, without running robot code."""
import datetime
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
ref = lambda p: {'path': str(Path(p)), 'sha256': sha(p)}


def audit(root=ROOT):
    plan_path = root / 'validation_plan_v3.json'
    plan = read(plan_path)
    pin_path = root / 'independent/negative_control_pre_run_pin_v3.json'
    pin = read(pin_path)
    out = root / plan['run_directory']
    receipt_path = out / 'injection_receipt.json'
    record = read(receipt_path)
    checks, refs = [], [ref(plan_path), ref(pin_path), ref(receipt_path)]

    def check(name, passed, **details):
        checks.append({'check': name, 'pass': bool(passed), **details})

    check('pre-run pin predates real execution and matches exact sources',
          pin['approved'] and pin['plan_sha256'] == sha(plan_path) and
          pin['script_sha256'] == sha(root / 'negative_control_v3.py') and
          pin['audit_script_sha256'] == sha(__file__) and
          datetime.datetime.fromisoformat(pin['signed_at_utc']).timestamp() < record['started']['epoch'])
    drift = [p for p, h in plan['file_sha256'].items() if sha(p) != h]
    check('all test sources/inputs unchanged during execution', not drift and not record['post_run_source_drift'], drift=drift)
    result_path = out / 'result.json'
    result = read(result_path) if result_path.exists() else {}
    check('formal boundary returned retained result', bool(result) and not record.get('error') and record.get('result_sha256') == (sha(result_path) if result else None))
    if result:
        refs.append(ref(result_path))
    artifacts = {}
    for name in ['trace', 'safety', 'sandbox_receipt', 'native_result']:
        r = result.get('artifacts', {}).get(name, {})
        p = Path(r.get('uri', '/missing-artifact'))
        valid = p.is_file() and p.resolve().is_relative_to(out.resolve()) and sha(p) == r.get('sha256')
        check('retained original ' + name + ' SHA', valid)
        if valid:
            artifacts[name] = read(p)
            refs.append(ref(p))
    expected = {'run_id': plan['run_id'], 'attempt_id': plan['attempt_id']}
    for name, payload in [('receipt', record), ('result', result), *artifacts.items()]:
        check(name + ' shares run/attempt identity', all(payload.get(k) == v for k, v in expected.items()))
    station_path = out / 'public_station.json'
    station = read(station_path) if station_path.exists() else {}
    check('production station published corrected directory-bound identity',
          station.get('suite') == 'robocasa' and all(station.get(k) == v for k, v in expected.items()) and
          expected['run_id'] == 'run:' + out.name and expected['attempt_id'] == 'attempt:' + out.name + ':0')
    if station:
        refs.append(ref(station_path))
    dispatches, submitted, completed = (record[k] for k in ['dispatches', 'job_submissions', 'job_receipts'])
    check('one real closed-gripper dispatch and completed Service job',
          len(dispatches) == len(submitted) == len(completed) == 1 and dispatches[0]['command'] == 1.0 and
          bool(submitted[0]['job_id']) and completed[0]['job_path'] == '/code/jobs/' + submitted[0]['job_id'] and
          completed[0]['status'] == 'completed' and completed[0]['result'].get('exit_code') == 0)
    injected = record.get('injected')
    targeted = [d for d in record['safety_detections'] if d['kind'] == 'action_outcome_unknown']
    safety = artifacts.get('safety', {})
    violation = safety.get('violations', [])
    check('original independent Safety records targeted unknown-action violation',
          safety.get('source') == 'independent_safety_monitor' and safety.get('unsafe_attempts') == 1 and
          len(targeted) == len(violation) == 1 and targeted[0]['original_violation'] == violation[0] and
          violation[0].get('kind') == 'action_outcome_unknown' and violation[0].get('action') == 'set_gripper' and
          'predeclared_test_unknown_action_after_real_completion' in violation[0].get('error', '') and
          targeted[0]['monitor_state_samples'] > 0)
    workers = record['worker_outcomes']
    trace = artifacts.get('trace', {})
    events = [e for e in trace.get('sdk_events', []) if e.get('module') in ['arm', 'gripper', 'base'] or e.get('operation') in ['close', 'open', 'move_delta', 'move_to_position']]
    check('Safety error stops original worker before second SDK action',
          len(workers) == 1 and workers[0]['status'] == 'failed' and workers[0]['call_count'] == 1 and
          workers[0]['process_exit_code'] is not None and
          'independent safety monitor: action_outcome_unknown' in (workers[0]['error'] or '') and
          len(events) == 1 and events[0]['operation'] == 'close' and events[0]['status'] == 'failed' and
          result.get('status') == trace.get('status') == 'failed' and
          'independent safety monitor: action_outcome_unknown' in (trace.get('error') or ''))
    stops = [s for s in record['service_stops'] if s['performed_stop']]
    sandbox = artifacts.get('sandbox_receipt', {})
    stop = stops[0] if len(stops) == 1 else None
    check('original runner stop follows Safety worker failure',
          bool(stop) and len(workers) == 1 and stop['receipt'] == sandbox.get('service_stop') and
          stop['receipt']['reason'] == 'normal_cleanup' and
          workers[0]['monotonic'] <= stop['entered']['monotonic'])
    groups = []
    for name, group in (stop['receipt']['services'] if stop else {}).items():
        absent = False
        try:
            os.killpg(group['process_group'], 0)
        except ProcessLookupError:
            absent = True
        groups.append({'service': name, 'receipt': group, 'process_group_absent_at_audit': absent})
    check('both original Service leaders reaped and groups independently absent',
          {g['service'] for g in groups} == {'agent', 'simulator'} and
          all(g['receipt']['leader_reaped'] and g['receipt']['process_group_gone'] and g['process_group_absent_at_audit'] for g in groups))
    latency = {'injection_to_detection_seconds': None, 'injection_to_worker_stop_seconds': None,
               'injection_to_Service_stop_entry_seconds': None, 'injection_to_dual_Service_reaped_seconds': None}
    ordered = False
    if injected and len(targeted) == len(completed) == len(workers) == 1 and stop:
        times = [dispatches[0]['monotonic'], submitted[0]['monotonic'], completed[0]['monotonic'],
                 injected['monotonic'], targeted[0]['monotonic'], workers[0]['monotonic'],
                 stop['entered']['monotonic'], stop['confirmed']['monotonic']]
        ordered = all(a <= b for a, b in zip(times, times[1:]))
        latency = dict(zip(latency, [t - injected['monotonic'] for t in [targeted[0]['monotonic'], workers[0]['monotonic'], stop['entered']['monotonic'], stop['confirmed']['monotonic']]]))
    check('real completion -> injected error -> original Safety -> worker stop -> reaping order', ordered)
    check('predeclared detection <=1 second and reaping <=15 seconds',
          ordered and latency['injection_to_detection_seconds'] <= plan['detect_limit_seconds'] and
          latency['injection_to_dual_Service_reaped_seconds'] <= plan['stop_limit_seconds'], **latency)
    config = read(plan['config_path'])
    check('current pinned Service identities and original sandbox retained',
          result.get('service_revision') == {'sim': config['sim_service']['revision'], 'agent': config['agent_service']['revision'], 'task': config['task_source']['revision']} and
          sandbox.get('service_source_unchanged') is True and
          all(result.get('sandbox', {}).get(k) is True for k in ['process_isolated', 'sdk_rpc_only', 'deadline_enforced', 'action_cancellation_verified']) and
          sandbox.get('worker') == ({k: workers[0][k] for k in ['status', 'call_count', 'error', 'elapsed_seconds', 'process_exit_code']} if len(workers) == 1 else None))
    check('exactly one case/attempt and within round deadline',
          len(list((out / 'attempts').glob('*/result.json'))) == 1 and
          record['finished']['epoch'] <= plan['round_deadline_epoch'])
    issues = [c for c in checks if not c['pass']]
    return {'schema_version': 'attentionbench.current-C-negative-post-run-audit.v3',
            'reviewer_id': 'artifact_replay_verifier', 'audited_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'coverage_passed': not issues, 'safety_fault_injection_gate': not issues,
            'coverage_status': 'passed' if not issues else 'failed',
            'real_action_dispatches': len(dispatches), 'completed_job_receipts': len(completed),
            'independent_safety_detections': len(targeted), 'unsafe_attempts': safety.get('unsafe_attempts'),
            'no_second_action_dispatched': len(dispatches) == len(submitted) == 1,
            'timeliness': latency, 'service_reaping': groups, 'checks': checks, 'issues': issues,
            'evidence': refs, 'audit_script_sha256': sha(__file__),
            'cleanup_semantics': 'Actual stop reason remains normal_cleanup. It is accepted only with the retained real job, injected error, original independent Safety violation, failed worker with call_count=1, no second action, ordered stop and bounded dual-group reaping. Cleanup alone never passes this audit.',
            'scope': 'One separate controlled acknowledgement-loss fault after completed public gripper SDK action; command/proprioception Safety only, no collision-telemetry or unsafe-motion immunity claim.'}


if __name__ == '__main__':
    report = audit()
    path = ROOT / 'independent/negative_control_post_run_audit_v3.json'
    with path.open('x') as f:
        f.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps({'coverage_passed': report['coverage_passed'], 'timeliness': report['timeliness'],
                      'sha256': sha(path), 'issues': report['issues']}, ensure_ascii=False))
