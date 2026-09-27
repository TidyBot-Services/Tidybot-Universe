"""Run the frozen two-task development smoke with per-attempt invalid-frame capture."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
UNIVERSE = Path('/home/truares/桌面/Tidybot-Universe-attention-native')
SERVICE = Path('/home/truares/桌面/robosuite_sim-service')
FREEZE = json.loads((ROOT / 'depth_capture_formal_freeze.json').read_text())
assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == FREEZE['script_sha256']
assert sys.executable == FREEZE['interpreter']
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=SERVICE, text=True).strip() == FREEZE['service_revision']
assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=SERVICE, text=True).strip()
assert hashlib.sha256((UNIVERSE / FREEZE['policy']).read_bytes()).hexdigest() == FREEZE['policy_sha256']
OUT = ROOT / 'depth_capture_formal_runs'
OUT.mkdir(exist_ok=False)
rows = []
for case in FREEZE['cases']:
    config = ROOT / case['config']
    assert hashlib.sha256(config.read_bytes()).hexdigest() == case['config_sha256']
    directory = OUT / f"{case['task']}-seed{case['seed']}"
    directory.mkdir()
    command = [
        sys.executable, '-m', 'benchmarks.attention_harness.robosuite_memory.formal_cli',
        '--task', case['task'], '--seed', str(case['seed']),
        '--code', str(UNIVERSE / FREEZE['policy']),
        '--approved-policy-sha256', FREEZE['policy_sha256'],
        '--config', str(config), '--approved-config-sha256', case['config_sha256'],
        '--service-source-root', str(SERVICE), '--artifact-root', str(directory),
        '--overall-deadline-seconds', '120',
    ]
    row = {'task': case['task'], 'seed': case['seed'], 'command': command,
           'config_sha256': case['config_sha256'], 'formal_eligible': False}
    rows.append(row)
    try:
        completed = subprocess.run(command, cwd=UNIVERSE, text=True,
                                   capture_output=True, timeout=160)
        (directory / 'command.stdout').write_text(completed.stdout)
        (directory / 'command.stderr').write_text(completed.stderr)
        row['exit_code'] = completed.returncode
        value = json.loads(completed.stdout)
        row['status'] = value['status']
        row['native_evaluator'] = value['native_evaluator']
        row['error'] = value['error']
        attempt = Path(value['artifact_dir'])
        row['artifact_dir'] = str(attempt.relative_to(ROOT))
        row['artifacts'] = {}
        for kind in ('trace', 'safety', 'sandbox_receipt', 'native_result'):
            item = value['artifacts'][kind]
            actual = hashlib.sha256(Path(item['uri']).read_bytes()).hexdigest()
            row['artifacts'][kind] = {'sha256': actual,
                                      'matches_receipt': actual == item['sha256']}
        safety = json.loads((attempt / 'safety.json').read_text())
        receipt = json.loads((attempt / 'sandbox_receipt.json').read_text())
        row['safety_unsafe_attempts'] = safety['unsafe_attempts']
        row['service_stop'] = receipt['service_stop']
        row['invalid_frame_files'] = [p.name for p in (attempt / 'invalid_depth_frames').glob('*')]
        row['native_matches_artifact'] = (
            json.loads((attempt / 'native_result.json').read_text())['native_success']
            == value['native_evaluator']['native_success']
        )
    except Exception as exc:
        row['audit_error'] = f'{type(exc).__name__}: {exc}'
    (ROOT / 'depth_capture_formal_progress.json').write_text(
        json.dumps(rows, indent=2, sort_keys=True) + '\n'
    )
    print(json.dumps({'task': case['task'], 'seed': case['seed'],
                      'status': row.get('status'), 'error': row.get('error'),
                      'audit_error': row.get('audit_error')}), flush=True)
