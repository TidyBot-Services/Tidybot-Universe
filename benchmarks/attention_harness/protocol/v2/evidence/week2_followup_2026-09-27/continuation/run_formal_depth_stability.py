"""Execute the pre-frozen bounded formal-runner engineering smoke."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
UNIVERSE = Path('/home/truares/桌面/Tidybot-Universe-attention-native')
SERVICE = Path('/home/truares/桌面/robosuite_sim-service')
freeze = json.loads((ROOT / 'depth_stability_freeze.json').read_text())
assert hashlib.sha256(Path(freeze['policy']).read_bytes()).hexdigest() == freeze['policy_sha256']
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SERVICE, text=True).strip() == freeze['service_revision']
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SERVICE, text=True).strip()
progress = ROOT / 'formal_depth_progress.json'
assert not progress.exists()
results = []
for case in freeze['configs']:
    config = Path(case['config'])
    assert hashlib.sha256(config.read_bytes()).hexdigest() == case['sha256']
    output = ROOT / 'formal_depth_runs' / f"{case['task']}-seed{case['seed']}"
    output.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable, '-m', 'benchmarks.attention_harness.robosuite_memory.formal_cli',
        '--task', case['task'], '--seed', str(case['seed']),
        '--code', freeze['policy'], '--approved-policy-sha256', freeze['policy_sha256'],
        '--config', str(config), '--approved-config-sha256', case['sha256'],
        '--service-source-root', str(SERVICE), '--artifact-root', str(output),
        '--overall-deadline-seconds', '120',
    ]
    entry = {'task': case['task'], 'seed': case['seed'], 'command': command,
             'config_sha256': case['sha256'], 'formal_eligible': False}
    results.append(entry)
    try:
        completed = subprocess.run(command, cwd=UNIVERSE, capture_output=True,
                                   text=True, timeout=160)
        (output / 'command.stdout').write_text(completed.stdout)
        (output / 'command.stderr').write_text(completed.stderr)
        entry['exit_code'] = completed.returncode
        value = json.loads(completed.stdout)
        entry['status'] = value.get('status')
        entry['native_evaluator'] = value.get('native_evaluator')
        entry['error'] = value.get('error')
        entry['artifact_dir'] = value.get('artifact_dir')
        entry['artifacts'] = {}
        for kind in ('trace', 'safety', 'sandbox_receipt', 'native_result'):
            artifact = value['artifacts'][kind]
            actual = hashlib.sha256(Path(artifact['uri']).read_bytes()).hexdigest()
            entry['artifacts'][kind] = {'path': artifact['uri'], 'sha256': actual,
                                        'matches_receipt': actual == artifact['sha256']}
        safety = json.loads(Path(value['artifacts']['safety']['uri']).read_text())
        sandbox = json.loads(Path(value['artifacts']['sandbox_receipt']['uri']).read_text())
        native = json.loads(Path(value['artifacts']['native_result']['uri']).read_text())
        trace = json.loads(Path(value['artifacts']['trace']['uri']).read_text())
        entry['safety_unsafe_attempts'] = safety['unsafe_attempts']
        entry['service_stop'] = sandbox.get('service_stop')
        entry['native_matches_runner'] = (
            native['native_success'] == value['native_evaluator']['native_success']
            and native['evaluated'] == value['native_evaluator']['evaluated']
        )
        entry['trace_status_matches_runner'] = trace['status'] == value['status']
        entry['sdk_action_count'] = sum(e.get('event_type') == 'sdk.action' for e in trace.get('sdk_events', []))
    except Exception as exc:
        entry['audit_error'] = f'{type(exc).__name__}: {exc}'
    progress.write_text(json.dumps(results, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'task': case['task'], 'seed': case['seed'],
                      'status': entry.get('status'), 'error': entry.get('error'),
                      'audit_error': entry.get('audit_error')}), flush=True)
