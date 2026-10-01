"""Read-only preflight of identities, original bytes and bounded control plan."""
import datetime
import hashlib
import json
import subprocess
import tempfile
import time
from pathlib import Path

from benchmarks.attention_harness.public_station import publish_public_station, read_public_station
from benchmarks.attention_harness.robocasa_native.controlled_negative import controlled_negative_request

ROOT = Path(__file__).resolve().parents[1]
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
plan_path = ROOT / 'validation_plan.json'
plan = json.loads(plan_path.read_text())
baseline = json.loads((ROOT / 'protected_baseline.json').read_text())
checks = []


def check(name, passed, **details):
    checks.append({'check': name, 'pass': bool(passed), **details})


for path, digest in plan['file_sha256'].items():
    check('pinned source/input SHA', sha(path) == digest, path=path, sha256=digest)
drift = [p for p, h in baseline['protected_files'].items() if sha(p) != h]
check('all prior evidence and runtime files unchanged', not drift, checked_files=len(baseline['protected_files']), drift=drift)
for row in plan['versions'].values():
    commit = subprocess.check_output(['git', '-C', row['path'], 'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', row['path'], 'status', '--porcelain'], text=True).strip()
    check('clean exact Service commit', commit == row['commit'] and not status,
          path=row['path'], commit=commit, status=status)
with tempfile.TemporaryDirectory(prefix='robocasa-station-preflight-') as temp:
    request = controlled_negative_request(
        run_dir=Path(temp) / plan['run_directory'], policy_path=Path(plan['policy_path']),
        policy_sha256=plan['policy_sha256'], config_path=Path(plan['config_path']),
        config_sha256=plan['config_sha256'], deadline_seconds=plan['attempt_deadline_seconds'],
        attention_input=json.loads(Path(plan['attention_input_path']).read_text()))
    publish_public_station(run_dir=request.artifact_root.parent, run_id=request.run_id,
                          attempt_id=request.attempt_id, suite='robocasa',
                          origin='ws://127.0.0.1:6380', device_id='maniskill_base')
    station = read_public_station(Path(temp), request.run_id, 'robocasa', {request.attempt_id})
    check('original station publisher and reader accept corrected paired identity',
          station is not None and request.run_id == plan['run_id'] and request.attempt_id == plan['attempt_id'])
approval = json.loads(Path(plan['operator_approval_path']).read_text())
check('exact approved v1.1 dev policy and public input fixture',
      approval['decision'] == 'approved' and
      plan['policy_sha256'] == approval['identity']['policies']['robocasa']['sha256'] and
      request.attention_input == json.loads(Path(plan['attention_input_path']).read_text()))
check('exactly one separate attempt; prohibited executions zero',
      plan['max_attempts'] == plan['max_new_real_cases'] == 1 and
      plan['effect_cases'] == plan['heldout_cases'] == 0 and plan['no_reruns_or_replacements'])
check('original timing thresholds retained', plan['detect_limit_seconds'] == 1 and plan['stop_limit_seconds'] == 15)
check('bounded case fits remaining deadline', time.time() + plan['whole_case_wall_seconds'] + 60 < plan['round_deadline_epoch'])
check('no control launched yet', not (ROOT / plan['run_directory']).exists())
report = {'schema_version': 'attentionbench.current-C-negative-pre-run-pin.v3',
          'reviewer_id': 'artifact_preflight_verifier',
          'signed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'approved': all(c['pass'] for c in checks), 'plan_sha256': sha(plan_path),
          'script_sha256': sha(ROOT / 'negative_control_guidance.py'),
          'audit_script_sha256': sha(ROOT / 'independent/audit_negative_control.py'),
          'preflight_script_sha256': sha(__file__), 'checks': checks,
          'scope': 'Approval of identity and bounded test only; no post-run gate conclusion.'}
path = ROOT / 'independent/negative_control_pre_run_pin.json'
with path.open('x') as f:
    f.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
print(json.dumps({'approved': report['approved'], 'sha256': sha(path), 'failed': [c for c in checks if not c['pass']]}))
if not report['approved']:
    raise SystemExit(1)
