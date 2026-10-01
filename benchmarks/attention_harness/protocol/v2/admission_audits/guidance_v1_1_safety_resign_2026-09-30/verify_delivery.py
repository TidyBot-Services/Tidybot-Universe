"""Read-only integrity, preserved judgments and verdict verification.

Run this against either retained delivery copy. Never starts robot code.
"""
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())


def verify():
    manifest = read(ROOT / 'sha256_manifest.json')
    issues = []
    for relative, expected in manifest['files'].items():
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            issues.append({'item': 'delivery_file_SHA', 'path': str(path)})
    protected = read(ROOT / 'protected_baseline.json')['protected_files']
    for original, expected in protected.items():
        if not Path(original).is_file() or sha(original) != expected:
            issues.append({'item': 'protected_original_SHA', 'path': original})
    overall = read(ROOT / 'independent/overall_admission_audit.json')
    prior_ref = overall['evidence'][0]
    prior = read(prior_ref['path'])
    for name, gate in prior['user_seven_gate_summary'].items():
        if name != 'safety_negative_controls' and gate != overall['user_seven_gate_summary_in_original_scope_plus_current_Safety'][name]:
            issues.append({'item': 'old_six_PASS_judgment_changed', 'name': name})
    for name, gate in prior['canonical_gates'].items():
        if name != 'safety_fault_injection' and gate != overall['canonical_gates_in_original_scope_plus_current_Safety'][name]:
            issues.append({'item': 'old_canonical_judgment_changed', 'name': name})
    negative = read(ROOT / 'independent/negative_control_post_run_audit.json')
    alignment = read(ROOT / 'independent/version_alignment_audit.json')
    for report in [overall, negative, alignment]:
        for reference in report['evidence']:
            path = reference['path']
            if not Path(path).is_file() or sha(path) != reference['sha256']:
                issues.append({'item': 'referenced_evidence_SHA', 'path': path})
    derived = (all(g['pass'] for g in overall['canonical_gates_in_original_scope_plus_current_Safety'].values()) and
               alignment['passed'] and all(c['pass'] for c in overall['evidence_integrity_checks']))
    if overall['formal_eligible'] != derived:
        issues.append({'item': 'formal_eligible_not_derived'})
    if not negative['coverage_passed'] or not negative['no_second_action_dispatched']:
        issues.append({'item': 'current_negative_coverage_missing'})
    if not alignment['autonomous_evidence_compatible']:
        issues.append({'item': 'autonomous_evidence_bridge_missing'})
    groups = []
    for item in negative['service_reaping']:
        pg = item['receipt']['process_group']
        absent = False
        try:
            os.killpg(pg, 0)
        except ProcessLookupError:
            absent = True
        groups.append({'service': item['service'], 'process_group': pg, 'absent': absent})
        if not absent:
            issues.append({'item': 'dedicated_Service_group_present', 'process_group': pg})
    return {'pass': not issues, 'delivery_files_verified': len(manifest['files']),
            'protected_originals_verified': len(protected), 'original_six_PASS_unchanged': not any(i['item']=='old_six_PASS_judgment_changed' for i in issues),
            'formal_eligible': overall['formal_eligible'], 'current_C_negative_PASS': negative['coverage_passed'],
            'version_alignment_pass': alignment['passed'], 'process_groups': groups, 'issues': issues}


if __name__ == '__main__':
    report = verify()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if report['pass'] else 1)
