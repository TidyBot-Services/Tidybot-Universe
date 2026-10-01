"""Verify this delivered audit, all prior evidence and the new frozen identities."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
PACKAGE=Path('/home/truares/桌面/Tidybot-Universe-attention-native/benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())


def verify():
    manifest=read(ROOT/'sha256_manifest.json');issues=[]
    for name,h in manifest['files'].items():
        if not (ROOT/name).is_file() or sha(ROOT/name)!=h:issues.append({'item':'delivery SHA','path':name})
    overall=read(ROOT/'independent/overall_admission_audit.json')
    prior=read(overall['evidence'][0]['path'])
    for k,g in prior['canonical_gates_in_original_scope_plus_current_Safety'].items():
        if k not in ['policy_identity','seven_condition_approval'] and overall['canonical_gates'][k]!=g:
            issues.append({'item':'inherited canonical changed','gate':k})
    for k,g in prior['user_seven_gate_summary_in_original_scope_plus_current_Safety'].items():
        if k!='seven_conditions' and overall['user_seven_gate_summary'][k]!=g:
            issues.append({'item':'inherited user gate changed','gate':k})
    protected=read(ROOT/'protected_baseline.json')['protected_files']
    for p,h in protected.items():
        if not Path(p).is_file() or sha(p)!=h:issues.append({'item':'protected original SHA','path':p})
    for r in overall['evidence']:
        if not Path(r['path']).is_file() or sha(r['path'])!=r['sha256']:issues.append({'item':'audit evidence SHA','path':r['path']})
    spec=importlib.util.spec_from_file_location('new_package_verify',ROOT/'verify_package.py')
    verifier=importlib.util.module_from_spec(spec);spec.loader.exec_module(verifier)
    package_result=verifier.verify()
    if not package_result['pass']:issues.append({'item':'new frozen identity/approval verification','report':package_result})
    derived=all(g['pass'] for g in overall['canonical_gates'].values()) and all(c['pass'] for c in overall['evidence_integrity_checks']) and package_result['pass']
    if overall['formal_eligible']!=derived:issues.append({'item':'eligibility not derived'})
    if overall['execution_authorized'] is not False:issues.append({'item':'unexpected execution authority'})
    status=read(PACKAGE/'approval_status.json');receipt=read(PACKAGE/'approval_verification_receipt.json')
    if not(status['operator_approval_sha256']==sha(PACKAGE/'operator_approval.json') and
           status['approved_verification_sha256']==sha(PACKAGE/'approval_verification_receipt.json') and
           status['overall_admission']['sha256']==sha(status['overall_admission']['path'])==sha(ROOT/'independent/overall_admission_audit.json') and
           receipt['operator_approval_sha256']==sha(PACKAGE/'operator_approval.json') and receipt['pass']):
        issues.append({'item':'approval sidecars mismatch'})
    return {'pass':not issues,'formal_eligible':overall['formal_eligible'],'execution_authorized':overall['execution_authorized'],
        'canonical_PASS':sum(g['pass'] for g in overall['canonical_gates'].values()),
        'user_PASS':sum(g['pass'] for g in overall['user_seven_gate_summary'].values()),
        'protected_files':len(protected),'delivery_files':len(manifest['files']),
        'new_frozen_files':package_result['files'],'entries':package_result['entries'],'issues':issues,
        'Service_starts':0,'robot_runs':0}


if __name__=='__main__':
    report=verify();print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    raise SystemExit(0 if report['pass'] else 1)
