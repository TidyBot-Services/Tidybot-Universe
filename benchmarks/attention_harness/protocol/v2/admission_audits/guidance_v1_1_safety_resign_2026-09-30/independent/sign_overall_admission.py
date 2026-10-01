"""Re-sign from retained judgments, one new negative and explicit version scope.

This verifier starts no Service, worker, Memory session or robot attempt.
Historical PASS objects are copied verbatim; current applicability is separate.
"""
import copy
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT.parent / 'attentionbench-seven-freeze-20260929/independent/overall_admission_audit.json'
U = ROOT.parent / 'Tidybot-Universe-attention-native'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
ref = lambda p: {'path': str(p), 'sha256': sha(p)}


def eligibility(canonical, version_alignment, integrity):
    return (all(g['pass'] for g in canonical.values()) and
            version_alignment['passed'] and all(c['pass'] for c in integrity))


def adjudicate():
    prior = read(OLD)
    baseline = read(ROOT / 'protected_baseline.json')
    negative = read(ROOT / 'independent/negative_control_post_run_audit.json')
    alignment = read(ROOT / 'independent/version_alignment_audit.json')
    cleanup = read(ROOT / 'independent/final_cleanup_check.json')
    plan = read(ROOT / 'validation_plan.json')
    integrity = []

    def check(name, passed, **details):
        integrity.append({'check': name, 'pass': bool(passed), **details})

    drift = [p for p, h in baseline['protected_files'].items()
             if not Path(p).is_file() or sha(p) != h]
    check('all old failures, six PASS evidence, approved package and unchanged runtime retained',
          not drift, checked_files=len(baseline['protected_files']), drift=drift)
    check('original failed overall audit exact SHA', sha(OLD) ==
          '334019e665609ada0dae2c09d0dec4fa2f0591c7bc332cd54387a3121f144e5b')
    check('current negative all coverage and no second action independently reconstructed',
          negative['coverage_passed'] and negative['no_second_action_dispatched'] and
          not negative['issues'] and all(c['pass'] for c in negative['checks']))
    check('retained final cleanup confirmed', cleanup['passed'])
    check('all five Service source versions still clean and unchanged', all(
          subprocess.check_output(['git', '-C', s['path'], 'rev-parse', 'HEAD'], text=True).strip() == s['commit'] and
          subprocess.check_output(['git', '-C', s['path'], 'status', '--porcelain'], text=True) == ''
          for s in prior['service_versions']))
    check('negative and version audits exact source and referenced evidence SHAs',
          sha(ROOT / 'independent/audit_negative_control.py') == negative['audit_script_sha256'] and
          sha(ROOT / 'independent/version_alignment_audit.py') == alignment['audit_script_sha256'] and
          all(Path(r['path']).is_file() and sha(r['path']) == r['sha256']
              for report in [negative, alignment] for r in report['evidence']))
    check('new identity and adversarial audit tests passed',
          '7 passed' in (ROOT / 'tests_identity_guidance_safety.txt').read_text() and
          '10 passed' in (ROOT / 'tests_adversarial_audits.txt').read_text())
    check('completed within the hard 90 minute deadline', time.time() < baseline['deadline_epoch'])

    canonical = copy.deepcopy(prior['canonical_gates'])
    seven = copy.deepcopy(prior['user_seven_gate_summary'])
    original_negative = copy.deepcopy(canonical['safety_fault_injection'])
    safety = {
        'status': 'pass' if negative['coverage_passed'] else 'fail',
        'pass': negative['coverage_passed'],
        'reason': 'The exact approved guidance v1.1 C policy completed its first guided .11m base job; acknowledgement-loss injection reached the original independent Safety action_outcome_unknown monitor. The original worker failed before any second action, and the original runner reaped both dedicated Services within predeclared limits. Historical 28 Safety artifacts and R14 depth negative remain reused and unchanged.',
        'scope': negative['scope'],
        'evidence': original_negative['evidence'] + [ref(ROOT / 'independent/negative_control_post_run_audit.json')],
        'current_C_timeliness_seconds': negative['timeliness'],
        'original_failure_retained': True,
    }
    canonical['safety_fault_injection'] = safety
    seven['safety_negative_controls'] = copy.deepcopy(safety)
    unchanged_six = all(seven[k] == prior['user_seven_gate_summary'][k]
                         for k in seven if k != 'safety_negative_controls')
    unchanged_eight = all(canonical[k] == prior['canonical_gates'][k]
                           for k in canonical if k != 'safety_fault_injection')
    check('six user PASS and eight other canonical judgments copied verbatim',
          unchanged_six and unchanged_eight)

    current = {}
    for name, gate in canonical.items():
        version_ok = alignment['passed'] if name in alignment['affected_canonical_gates'] else alignment['autonomous_evidence_compatible']
        current[name] = {'historical_or_reconstructed_gate_pass': gate['pass'],
                         'version_applicable': version_ok,
                         'pass': gate['pass'] and version_ok,
                         'status': 'pass' if gate['pass'] and version_ok else 'fail'}
    # Current Safety has exact new policy SHA coverage even though M1 identities are old.
    current['safety_fault_injection']['version_applicable'] = negative['coverage_passed']
    current['safety_fault_injection']['pass'] = negative['coverage_passed']
    current['safety_fault_injection']['status'] = 'pass' if negative['coverage_passed'] else 'fail'
    failures = []
    if not alignment['passed']:
        failures.append({
            'item': 'current_version_M1_and_launch_identity_binding',
            'affected_canonical_gates': alignment['affected_canonical_gates'],
            'reason': 'The 350 unchanged approved M1 locks and 350 launch identities all contain the old R/C policy SHAs. Neither current policy SHA is covered. Both actual request validation probes reject the new SHA with formal request entry lock mismatch before Service startup.',
            'evidence': ref(ROOT / 'independent/version_alignment_audit.json'),
            'remedy': alignment['next_step_if_not_aligned'],
        })
    if not negative['coverage_passed']:
        failures.append({'item': 'safety_fault_injection', 'issues': negative['issues']})
    failures.extend({'item': 'evidence_integrity', 'check': c} for c in integrity if not c['pass'])
    refs = [OLD, ROOT / 'protected_baseline.json', ROOT / 'validation_plan.json',
            ROOT / 'independent/negative_control_pre_run_pin.json',
            ROOT / 'independent/negative_control_post_run_audit.json',
            ROOT / 'independent/autonomous_AST_alignment.json',
            ROOT / 'independent/version_alignment_audit.json',
            ROOT / 'independent/final_cleanup_check.json',
            ROOT / 'guidance_frozen_verification.json',
            ROOT / 'tests_identity_guidance_safety.txt', ROOT / 'tests_adversarial_audits.txt',
            ROOT / 'independent/test_audits.py',
            U / 'benchmarks/attention_harness/robocasa_native/controlled_negative.py',
            U / 'benchmarks/attention_harness/tests/test_guidance_safety_negative.py']
    return {
        'schema_version': 'attentionbench.guidance-v1.1-overall-admission.v1',
        'signed_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'reviewer_role': 'Author-produced artifact replay and version audit; original independent Safety is outside the policy worker. This is not an external reviewer signature.',
        'scope': 'Current approved guidance adoption v1.1 R/C identities, primary development admission only; exactly one current-C controlled fault test. No effect comparison or execution permission is issued.',
        'formal_eligible': eligibility(canonical, alignment, integrity),
        'decision_rule': 'All inherited/reconstructed canonical gates AND current-version alignment AND evidence integrity must pass. Old PASS scope and a development-only approval cannot silently authorize a new M1/launch policy identity.',
        'execution_authorized': False,
        'approved_REVIEW_sha256': '090a190f325a23a138003456c41ee436c3f8e85605584b7ca0c7ba21615981c7',
        'current_policy_sha256': {
            r['suite']: r['new_sha256'] for r in read(ROOT / 'independent/autonomous_AST_alignment.json')['records']},
        'base_runtime_commit': baseline['delivery_base_commit'],
        'service_versions': prior['service_versions'],
        'canonical_gates_in_original_scope_plus_current_Safety': canonical,
        'user_seven_gate_summary_in_original_scope_plus_current_Safety': seven,
        'historical_six_PASS_judgments_unchanged': unchanged_six,
        'historical_eight_other_canonical_judgments_unchanged': unchanged_eight,
        'current_version_canonical_applicability': current,
        'current_version_canonical_pass_count': sum(g['pass'] for g in current.values()),
        'current_version_canonical_gate_count': len(current),
        'version_alignment_pass': alignment['passed'],
        'autonomous_chain_profile_depth_evidence_compatible': alignment['autonomous_evidence_compatible'],
        'reuse_applicability': alignment['reuse_applicability'],
        'failed_items': failures,
        'evidence_integrity_checks': integrity,
        'evidence': [ref(p) for p in refs],
        'signer_script_sha256': sha(__file__),
        'execution_counts_this_round': {'current_guidance_C_controlled_cases': 1,
             'current_guidance_C_attempts': 1, 'real_action_dispatches': negative['real_action_dispatches'],
             'completed_jobs': negative['completed_job_receipts'], 'targeted_detections': negative['independent_safety_detections'],
             'second_action_dispatches': 0, 'other_six_gate_robot_reruns': 0, 'held_out': 0,
             '350_cell_effect_matrix': 0, 'new_Memory_Service_probes': 0, 'Memory_promotions': 0,
             'preflight_launcher_failures_with_zero_Service_starts': 1,
             'read_only_M1_SHA_comparisons': 350, 'read_only_launch_SHA_comparisons': 350,
             'request_validation_boundary_probes_with_zero_Service_starts': 2},
        'unchanged_known_limits': {'RoboCasa_guidance_success_not_improved': True,
             'old_Memory_nonadoption_history_retained': True, 'historical_depth_root_cause': 'unknown',
             'new_success_threshold_added': False, 'new_Memory_coverage_threshold_added': False},
        'next_steps': [alignment['next_step_if_not_aligned'],
                       'This bounded round is closed; no automatic new round, held-out or effects run.'],
    }


if __name__ == '__main__':
    report = adjudicate()
    path = ROOT / 'independent/overall_admission_audit.json'
    with path.open('x') as output:
        json.dump(report, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write('\n')
    print(json.dumps({'formal_eligible': report['formal_eligible'],
          'current_version_canonical_pass_count': report['current_version_canonical_pass_count'],
          'failed_items': report['failed_items'], 'sha256': sha(path)}, ensure_ascii=False))
