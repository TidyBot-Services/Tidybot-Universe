"""Read-only current-version applicability; original PASS judgments stay intact."""
import datetime
import hashlib
import json
import subprocess
from pathlib import Path

from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
from benchmarks.attention_harness.formal_entry import inspect_formal_entry

ROOT = Path(__file__).resolve().parents[1]
U = ROOT.parent / 'Tidybot-Universe-attention-native'
PACKAGE = U / 'benchmarks/attention_harness/protocol/v2/review_packages/guidance_adoption_v1_1_dev_2026-09-29'
SEVEN = U / 'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_dev_2026-09-29'
PRIOR = ROOT.parent / 'attentionbench-seven-freeze-20260929/independent/overall_admission_audit.json'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
ref = lambda p: {'path': str(Path(p)), 'sha256': sha(p)}
prior, approval, ast_report = read(PRIOR), read(PACKAGE / 'operator_approval.json'), read(ROOT / 'independent/autonomous_AST_alignment.json')
protected = read(ROOT / 'protected_baseline.json')
drift = [p for p, h in protected['protected_files'].items() if sha(p) != h]
policies = {suite: PACKAGE / ('frozen/final_policies/robosuite_cube_lift.py' if suite == 'robosuite' else 'frozen/final_policies/robocasa_counter_to_sink.py') for suite in ['robosuite', 'robocasa']}
versions = []
for s in prior['service_versions']:
    commit = subprocess.check_output(['git', '-C', s['path'], 'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', s['path'], 'status', '--porcelain'], text=True).strip()
    versions.append({**s, 'current_commit': commit, 'current_status': status, 'pass': commit == s['commit'] and not status})
checks = []


def check(name, passed, **detail):
    checks.append({'check': name, 'pass': bool(passed), **detail})


check('approved guidance exact REVIEW/manifest/policies remain unchanged',
      approval['decision'] == 'approved' and all(sha(PACKAGE / approval['identity'][k]['path']) == approval['identity'][k]['sha256'] for k in ['review', 'manifest']) and
      all(sha(policies[s]) == approval['identity']['policies'][s]['sha256'] for s in policies))
check('all six old PASS evidence and current runtime byte-protected', not drift, checked_files=len(protected['protected_files']), drift=drift)
check('all five Service source revisions clean and unchanged', all(v['pass'] for v in versions))
check('whole no-guidance robot program AST equivalent for both suites', ast_report['passed'] and all(r['whole_autonomous_robot_program_AST_equal'] for r in ast_report['records']))
base_refs = []
for r in ast_report['records']:
    task = 'cube_lift' if r['suite'] == 'robosuite' else 'counter_to_sink'
    p = SEVEN / f"base/{r['suite']}_{task}/policy.py"
    check('AST comparator old source equals signed historical base ' + r['suite'], sha(p) == r['old_sha256'])
    base_refs.append(ref(p))
# These are policy identity comparisons, not a rerun of the seven-condition audit.
identity_rows = []
for slot in read(SEVEN / 'matrix_plan.json')['slots']:
    lock_path, launch_path = Path(slot['m1_entry_lock']['path']), Path(slot['launch_arguments']['path'])
    lock, launch = read(lock_path), read(launch_path)
    args = launch['fixed_arguments']
    expected = sha(policies[slot['suite']])
    identity_rows.append({'suite': slot['suite'], 'seed': slot['seed'], 'condition': slot['condition'],
        'lock_path': str(lock_path), 'launch_path': str(launch_path),
        'signed_lock_sha256': slot['m1_entry_lock']['sha256'], 'actual_lock_sha256': sha(lock_path),
        'signed_launch_sha256': slot['launch_arguments']['sha256'], 'actual_launch_sha256': sha(launch_path),
        'current_approved_policy_sha256': expected, 'old_lock_policy_sha256': lock['approved_policy_sha256'],
        'old_launch_policy_sha256': args[args.index('--approved-policy-sha256') + 1],
        'current_policy_matches_old_lock': expected == lock['approved_policy_sha256'],
        'current_policy_matches_old_launch': expected == args[args.index('--approved-policy-sha256') + 1]})
boundary_probes = []
for suite in ['robosuite', 'robocasa']:
    task = 'cube_lift' if suite == 'robosuite' else 'counter_to_sink'
    lock_path = SEVEN / f'entries/{suite}_{task}_seed101/autonomous/m1_entry_lock.json'
    config = SEVEN / f'configs/{suite}_{task}_seed101.json'
    memory = SEVEN / f'entries/{suite}_{task}_seed101/autonomous/memory_contract.json'
    lock = read(lock_path)
    current_lock, _ = inspect_formal_entry(suite=suite, task_id=task, seed=101, policy_id='autonomous',
        code=policies[suite], approved_policy_sha256=sha(policies[suite]), config=config,
        approved_config_sha256=sha(config), max_attempts=4, assistance_credits=1, token_limit=4096,
        assistance_mode='benchmark_proxy', human_deadline_seconds=30, overall_deadline_seconds=300,
        memory_contract=memory, approved_memory_contract_sha256=sha(memory))
    request = FormalRunRequest(suite=suite, task_id=task, seed=101,
        policy_code_path=policies[suite], policy_sha256=sha(policies[suite]), config_path=config,
        config_sha256=sha(config), artifact_root=ROOT / 'never_executed_alignment_probe',
        overall_deadline_seconds=300, entry_sha256=lock['sha256'], entry_lock=lock)
    error = None
    try:
        request.validate()
    except ValueError as exc:
        error = str(exc)
    boundary_probes.append({'suite': suite, 'old_entry_sha256': lock['sha256'],
        'current_computed_entry_sha256': current_lock['sha256'],
        'old_entry_accepts_current_policy': error is None, 'actual_boundary_error': error,
        'new_Service_starts': 0, 'new_robot_attempts': 0, 'artifact_dir_created': request.artifact_root.exists()})
identity_matches = all(r['current_policy_matches_old_lock'] and r['current_policy_matches_old_launch'] for r in identity_rows)
check('current approved guidance SHA covered by existing seven-condition M1/launch identities', identity_matches,
      mismatched_M1_locks=sum(not r['current_policy_matches_old_lock'] for r in identity_rows),
      mismatched_launch_identities=sum(not r['current_policy_matches_old_launch'] for r in identity_rows))
base_compatible = all(c['pass'] for c in checks if c['check'] != 'current approved guidance SHA covered by existing seven-condition M1/launch identities')
issues = [c for c in checks if not c['pass']]
report = {'schema': 'attentionbench.guidance-v1.1-version-alignment.v1',
          'reviewer_role': 'author-produced read-only evidence and source audit; not an external reviewer',
          'audited_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'passed': not issues, 'autonomous_evidence_compatible': base_compatible,
          'prior_six_PASS_judgments_unchanged': True, 'chain_profile_depth_reruns': 0,
          'reuse_applicability': {
              'chain': {'compatible': base_compatible, 'scope': 'Prior autonomous robot-call evidence and native Boolean outcomes, no guided-result extrapolation'},
              'profile': {'compatible': base_compatible, 'scope': 'Prior 50-case descriptive autonomous outcomes; diagnostic compiler CPU/print overhead is not timing-equivalence proof'},
              'depth': {'compatible': base_compatible, 'scope': 'Same Robosuite19fde8a operational gate and monitor/backend; unknown historical root cause retained'},
              'task_feasibility': {'compatible': base_compatible, 'scope': 'Native task/evaluator infrastructure unchanged; historical native positives remain separate'},
              'Memory': {'compatible': base_compatible, 'scope': 'Exact v1 source/provenance/snapshot/context authority unchanged; guided Memory effect not inferred'},
              'seven_condition_approval': {'compatible': identity_matches, 'scope': 'Prior definition approval stays PASS in its original exact-SHA scope; it does not cover new M1/launch policy identities'}},
          'checks': checks, 'issues': issues, 'boundary_probes': boundary_probes,
          'current_identity_comparisons': identity_rows, 'service_versions': versions,
          'affected_canonical_gates': ['policy_identity', 'seven_condition_approval'] if not identity_matches else [],
          'protected_file_drift': drift,
          'evidence': [ref(PRIOR), ref(PACKAGE / 'operator_approval.json'), ref(PACKAGE / 'sha256_manifest.json'),
                       ref(ROOT / 'independent/autonomous_AST_alignment.json'), ref(SEVEN / 'matrix_plan.json'), *base_refs],
          'audit_script_sha256': sha(__file__), 'AST_comparator_sha256': sha(ROOT / 'independent/autonomous_ast_alignment.py'),
          'next_step_if_not_aligned': 'Separately freeze/approve the seven-condition input package, M1 locks and launch identities for the two new policy SHAs. Reuse this completed current-C negative and autonomous AST bridge; no new robot reruns are required by this identified gap. Do not modify the prior PASS package or run effects.'}
path = ROOT / 'independent/version_alignment_audit.json'
with path.open('x') as f:
    f.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
print(json.dumps({'passed': report['passed'], 'autonomous_evidence_compatible': base_compatible,
                  'issues': issues, 'boundary_probes': boundary_probes, 'sha256': sha(path)}, ensure_ascii=False))
