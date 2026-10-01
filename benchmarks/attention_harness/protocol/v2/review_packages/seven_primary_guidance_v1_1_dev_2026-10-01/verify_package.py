"""Read-only full identity freeze/approval verification. Starts no executions."""
import importlib.util
import hashlib
import json
from pathlib import Path

PACKAGE=Path('/home/truares/桌面/Tidybot-Universe-attention-native/benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())
SIDECARS={'sha256_manifest.json','operator_approval.json','approval_verification_receipt.json','approval_status.json'}


def verify(package=PACKAGE,*,require_approval=True):
    spec=importlib.util.spec_from_file_location('package_identity_reviewer',package/'review_tools/review_identity_package.py')
    reviewer=importlib.util.module_from_spec(spec);spec.loader.exec_module(reviewer)
    report=reviewer.review(package,frozen=True)
    issues=list(report['issues'])
    manifest=read(package/'sha256_manifest.json')
    actual={str(p.relative_to(package)) for p in package.rglob('*') if p.is_file() and
            not any(s in p.parts for s in ['__pycache__','.pytest_cache']) and str(p.relative_to(package)) not in SIDECARS}
    if actual!=set(manifest['files']):issues.append({'check':'manifest complete membership','pass':False,
        'missing':sorted(set(manifest['files'])-actual),'unlisted':sorted(actual-set(manifest['files']))})
    if require_approval:
        p=package/'operator_approval.json'
        if not p.exists():issues.append({'check':'operator approval present','pass':False})
        else:
            approval=read(p);authorization=read(package/'goal_authorization.json')
            statement=authorization['human_instruction_verbatim']
            if not (approval['decision']=='approved' and approval['identity']['review']['sha256']==sha(package/'REVIEW.md') and
                    approval['identity']['manifest']['sha256']==sha(package/'sha256_manifest.json') and
                    approval['operator_message_verbatim']==statement and
                    approval['operator_message_sha256']==hashlib.sha256(statement.encode()).hexdigest() and
                    approval['operator_statement_verbatim'] in statement and
                    approval['approval_basis']=='explicit current goal delegates generation + review + approval of identity-only dev package' and
                    approval['human_postfreeze_SHA_confirmation'] is False and
                    approval['scope']['execution_authorized'] is False and
                    approval['scope']['classification']=='new_guidance_v1_1_M1_launch_identity_only_dev_freeze' and
                    approval['scope']['excluded']==['held-out','350-cell effects','any effects matrix','formal effect comparison','robot or Service execution','other versions or scopes'] and
                    all(sha(package/v['path'])==v['sha256'] for v in approval['identity']['policies'].values()) and
                    sha(Path(approval['verification']['path']))==approval['verification']['sha256'] and
                    read(Path(approval['verification']['path']))['pass']):
                issues.append({'check':'exact operator authority scope/identity/review binding','pass':False})
    return {'schema':'attentionbench.guidance-identity-freeze-verification.v1',
        'pass':not issues,'files':len(manifest['files']),'entries':report['entries_reviewed'],
        'production_preflight_and_launch_review_pass':report['pass'],
        'approval_required':require_approval,'Service_starts':0,'robot_runs':0,
        'issues':issues,'reviewer_role':'author-produced full mechanical review; original user goal supplies approval authority',
        'verifier_sha256':sha(__file__)}


if __name__=='__main__':
    report=verify();print(json.dumps(report,ensure_ascii=False,sort_keys=True))
    raise SystemExit(0 if report['pass'] else 1)
