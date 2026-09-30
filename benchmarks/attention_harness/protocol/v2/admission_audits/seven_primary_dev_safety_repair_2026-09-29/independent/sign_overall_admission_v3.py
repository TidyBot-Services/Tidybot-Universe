"""Reuse signed PASS gates exactly; adjudicate only Safety fault injection."""
import copy
import datetime
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIOR = ROOT.parent / 'attentionbench-seven-freeze-20260929'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
ref = lambda p: {'path': str(Path(p)), 'sha256': sha(p)}
old_path = PRIOR / 'independent/overall_admission_audit.json'
assert sha(old_path) == '334019e665609ada0dae2c09d0dec4fa2f0591c7bc332cd54387a3121f144e5b'
old = read(old_path)
assert sha(PRIOR / 'independent/negative_control_post_run_audit.json') == 'a1aefe83a26d76d794714c5f724ec5bc1686c128193f1f82d69b8dab91d8c972'
baseline = read(ROOT / 'protected_baseline.json')
protected_checks = [{'path': p, 'expected_sha256': h,
                     'actual_sha256': sha(p), 'pass': sha(p) == h}
                    for p, h in baseline['protected_files'].items()]
integrity_issues = [c for c in protected_checks if not c['pass']]
negative_path = ROOT / 'independent/negative_control_post_run_audit_v3.json'
negative = read(negative_path)
replay_refs = [{'path': r['path'], 'expected_sha256': r['sha256'],
                'actual_sha256': sha(r['path']), 'pass': sha(r['path']) == r['sha256']}
               for r in negative['evidence']]
integrity_issues += [r for r in replay_refs if not r['pass']]
versions = []
for r in old['service_versions']:
    commit = subprocess.check_output(['git', '-C', r['path'], 'rev-parse', 'HEAD'], text=True).strip()
    status = subprocess.check_output(['git', '-C', r['path'], 'status', '--porcelain'], text=True).strip()
    versions.append({**r, 'current_commit': commit, 'current_status': status,
                     'pass': commit == r['commit'] and not status})
integrity_issues += [r for r in versions if not r['pass']]
r_reuse = read(PRIOR / 'independent/current_robosuite_negative_reuse_audit.json')
fault_pass = bool(negative['coverage_passed'] and negative['safety_fault_injection_gate'] and
                  not negative['issues'] and all(c['pass'] for c in negative['checks']) and
                  all(c['pass'] for c in replay_refs) and r_reuse['passed'] and not integrity_issues)
gates = copy.deepcopy(old['canonical_gates'])
prior_fault = gates['safety_fault_injection']
latency = negative['timeliness']
reason = ('Current C controlled acknowledgement-loss fault reached a real completed gripper job; original independent Safety recorded action_outcome_unknown; original worker failed before second action; detection %.6fs, worker stop %.6fs, dual Service groups reaped %.6fs from injection. Historical 28 Safety artifacts and current R14 depth-negative evidence reused unchanged.' %
          (latency['injection_to_detection_seconds'], latency['injection_to_worker_stop_seconds'], latency['injection_to_dual_Service_reaped_seconds'])) if fault_pass else (
          'Current C negative-control checks failed: ' + '; '.join(c['check'] for c in negative['issues']) +
          ('; evidence/version integrity failures' if integrity_issues else ''))
gates['safety_fault_injection'] = {
    'status': 'pass' if fault_pass else 'fail', 'pass': fault_pass, 'reason': reason,
    'scope': 'Full current-version Safety negative-control package: signed historical 28 artifacts + unchanged current R14 negative + one new separate current C unknown-action control. No qualifying-case or effect outcome replacement.',
    'evidence': [*prior_fault['evidence'], ref(negative_path),
                 ref(ROOT / 'validation_plan_v3_1.json'),
                 ref(ROOT / 'independent/negative_control_pre_run_pin_v3_1.json')],
}
unchanged = [k for k in gates if k != 'safety_fault_injection']
assert len(unchanged) == 8 and all(old['canonical_gates'][k]['pass'] for k in unchanged)
assert all(gates[k] == old['canonical_gates'][k] for k in unchanged)
mapping = {'chain': 'five_seed_chain_each_task', 'profile': 'stability_each_task', 'depth': 'depth_500',
           'task_feasibility': 'task_feasibility', 'safety_negative_controls': 'safety_fault_injection',
           'memory': 'trusted_memory', 'seven_conditions': 'seven_condition_approval'}
user_gates = {k: gates[v] for k, v in mapping.items()}
assert all(user_gates[k] == old['user_seven_gate_summary'][k] for k in mapping if k != 'safety_negative_controls')
failed = [{'gate': k, 'reason': g['reason'], 'evidence': g['evidence']} for k, g in gates.items() if not g['pass']]
if integrity_issues:
    failed.append({'gate': 'evidence_and_version_integrity', 'reason': 'Protected original or version drift', 'issues': integrity_issues})
report = {
    'schema_version': 'attentionbench.overall-admission-safety-resign.v3',
    'reviewer_id': 'artifact_replay_verifier',
    'signed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'scope': old['scope'], 'formal_eligible': all(g['pass'] for g in gates.values()) and not integrity_issues,
    'decision_rule': 'AND of all nine canonical gates plus unchanged evidence/version integrity. Reuse eight signed canonical PASS entries exactly; only rejudge safety_fault_injection.',
    'execution_authorized': False, 'prior_overall_audit': ref(old_path),
    'preserved_failed_negative': ref(PRIOR / 'independent/negative_control_post_run_audit.json'),
    'delivery_base_commit': baseline['base_commit'], 'runtime_commit': old['runtime_commit'],
    'runtime_compatibility': 'All existing production source files unchanged. Added controlled_negative request builder binds only this separate test identity; its source SHA is pinned in the new plan. No original runner/monitor/Service change.',
    'saved_protocol_commit': old['saved_protocol_commit'], 'service_versions': versions,
    'matrix_plan': old['matrix_plan'], 'freeze_manifest': old['freeze_manifest'],
    'protocol_v2_2': old['protocol_v2_2'], 'protocol_v2_4': old['protocol_v2_4'],
    'canonical_gate_count': len(gates), 'canonical_gates': gates,
    'user_seven_gate_summary': user_gates,
    'remaining_three_packages': {k: gates[k] for k in ['task_feasibility', 'safety_fault_injection', 'trusted_memory']},
    'reused_user_PASS_gates': {k: {'byte_equivalent_payload': user_gates[k] == old['user_seven_gate_summary'][k],
                                  'rerun_count': 0} for k in mapping if k != 'safety_negative_controls'},
    'reused_canonical_PASS_gates': unchanged,
    'protected_evidence_integrity': {'checked_files': len(protected_checks), 'issues': integrity_issues,
                                     'baseline': ref(ROOT / 'protected_baseline.json')},
    'current_negative_evidence_SHA_rechecks': replay_refs,
    'new_C_negative_audit': ref(negative_path), 'timeliness': latency,
    'failed_items': failed, 'prior_round_execution_counts': old['execution_counts'],
    'execution_counts': {'new_real_development_cases': 1, 'new_real_attempts': 1,
                         'new_dispatched_actions': negative['real_action_dispatches'],
                         'chain_reruns': 0, 'profile_reruns': 0, 'depth_reruns': 0,
                         'task_feasibility_reruns': 0, 'Memory_probes_or_promotions': 0,
                         'seven_condition_approval_reruns': 0,
                         'matrix_executions': 0, 'heldout_executions': 0, 'ablation_executions': 0,
                         'preflight_rejections_before_Service': 1, 'real_case_cap': 1},
    'tests': [{'scope': 'Identity and original Safety/worker regression only',
               'result': '12 passed', 'evidence': ref(ROOT / 'tests_identity_and_safety.txt')},
              {'scope': 'Read-only audit replay and adversarial missing/late/second-action evidence',
               'result': '5 passed', 'evidence': ref(ROOT / 'tests_audit_predicates.txt')}],
    'condition_semantic_limits': old['condition_semantic_limits'],
    'native_success_minimum_added': False, 'Memory_coverage_minimum_added': False,
    'depth_root_cause': old['depth_root_cause'],
    'audit_scripts': [ref(__file__), ref(ROOT / 'independent/audit_negative_control_v3.py')],
    'next_steps': ['This authorized repair round ends after Service/process cleanup and evidence delivery; no automatic new round.',
                   'Primary-development admission is signed eligible; no matrix/held-out execution is performed or authorized by this signature.' if fault_pass else 'Retain all failures and the exact failed check list; do not claim formal eligibility or retry this round.'],
}
path = ROOT / 'independent/overall_admission_audit_v3.json'
with path.open('x') as f:
    f.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
print(json.dumps({'formal_eligible': report['formal_eligible'], 'canonical_gates': {k: g['status'] for k, g in gates.items()},
                  'sha256': sha(path), 'failed_items': failed}, ensure_ascii=False))
