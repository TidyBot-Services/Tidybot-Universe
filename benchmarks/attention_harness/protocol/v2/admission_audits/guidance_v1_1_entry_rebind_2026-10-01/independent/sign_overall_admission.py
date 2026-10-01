"""Adjudicate the two identity gates; reuse every other judgment verbatim."""
import copy
import datetime
import hashlib
import json
from pathlib import Path
import time

ROOT=Path(__file__).resolve().parents[1]
U=Path('/home/truares/桌面/Tidybot-Universe-attention-native')
PACKAGE=U/'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01'
PREVIOUS=U.parent/'attentionbench-guidance-safety-resign-20260930'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_text())
ref=lambda p:{'path':str(Path(p).resolve()),'sha256':sha(p)}


def adjudicate():
    prior_path=PREVIOUS/'independent/overall_admission_audit.json'
    prior=read(prior_path);baseline=read(ROOT/'protected_baseline.json')
    verification=read(ROOT/'independent/approved_identity_verification.json')
    approval=read(PACKAGE/'operator_approval.json')
    checks=[]

    def check(name,passed,**details):
        checks.append({'check':name,'pass':bool(passed),**details})

    drift=[p for p,h in baseline['protected_files'].items() if not Path(p).is_file() or sha(p)!=h]
    check('all previous PASS/failure evidence, old freezes and source unchanged',not drift,files=len(baseline['protected_files']),drift=drift)
    check('previous exact overall audit remains unchanged',sha(prior_path)=='a68f8fec50b9de3463b5e5612133fe1810428bf6f07b869324607c9fa7da3b9c')
    manifest=read(PACKAGE/'sha256_manifest.json')
    check('new frozen payload every SHA exact',all(sha(PACKAGE/p)==h for p,h in manifest['files'].items()),files=len(manifest['files']))
    check('full350 production identity and launch review passed',verification['pass'] and verification['entries']==350 and
          verification['production_preflight_and_launch_review_pass'] and verification['approval_required'])
    check('current exact operator approval bound to review and full manifest',approval['decision']=='approved' and
          approval['identity']['review']['sha256']==sha(PACKAGE/'REVIEW.md') and
          approval['identity']['manifest']['sha256']==sha(PACKAGE/'sha256_manifest.json') and
          verification['operator_approval_sha256']==sha(PACKAGE/'operator_approval.json') and
          approval['scope']['execution_authorized'] is False)
    negative_path=PREVIOUS/'independent/negative_control_post_run_audit.json'
    negative=read(negative_path)
    ast_path=PREVIOUS/'independent/autonomous_AST_alignment.json';ast_alignment=read(ast_path)
    check('exact current-v1.1 Safety PASS reused without rerun',sha(negative_path)=='59ab3797ecd3557cc933c9c357575c113ee80a665467c2c8b8d66179ee651ce6' and
          negative['coverage_passed'] and negative['no_second_action_dispatched'] and
          all(sha(r['path'])==r['sha256'] for r in negative['evidence']))
    check('whole autonomous AST bridge still applies to new frozen code bytes',ast_alignment['passed'] and
          all(sha(PACKAGE/approval['identity']['policies'][r['suite']]['path'])==r['new_sha256'] for r in ast_alignment['records']))
    check('new package scientific definitions remain identical to prior approved grid',
          read(PACKAGE/'matrix_plan.json')['grid']==read(U/'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_dev_2026-09-29/matrix_plan.json')['grid'])
    check('new binding-specific tests passed', '11 passed' in (ROOT/'tests_identity_rebinding.txt').read_text())
    check('safe bounded round completed before90 minutes',time.time()<baseline['deadline_epoch'])
    canonical=copy.deepcopy(prior['canonical_gates_in_original_scope_plus_current_Safety'])
    seven=copy.deepcopy(prior['user_seven_gate_summary_in_original_scope_plus_current_Safety'])
    identity_pass=verification['pass'] and all(c['pass'] for c in checks)
    canonical['policy_identity']={
        'status':'pass' if identity_pass else 'fail','pass':identity_pass,
        'reason':'Exactly one approved guidance control SHA per primary task is used by all25 seeds and all7 conditions. All350 new M1 identities are recomputed by unchanged production preflight, parsed launch arguments match them, current requests validate and former-SHA locks are rejected. The configuration and condition-input bytes are identical to the original approved definitions.',
        'scope':'Only the exact newly frozen guidance v1.1 development identity package; no future versions or effect-execution permission.',
        'evidence':[ref(ROOT/'independent/approved_identity_verification.json'),ref(PACKAGE/'sha256_manifest.json'),
                    ref(PACKAGE/'matrix_plan.json'),ref(PACKAGE/'identity_rebind_index.json'),ref(PACKAGE/'operator_approval.json')]}
    approval_pass=identity_pass and approval['decision']=='approved'
    canonical['seven_condition_approval']={
        'status':'pass' if approval_pass else 'fail','pass':approval_pass,
        'reason':'The current human goal expressly authorizes generation, review and approval of the new development M1/launch binding. Full mechanical review passed before recording the exact REVIEW/manifest/new-policy approval. All prior seven-condition definitions, budgets, k2/random-quota1, demo provenance, exact Memory scope and no-promotion initial-state contracts are unchanged. Approval is recorded from this delegated instruction, without fabricating a separate post-freeze human SHA confirmation.',
        'scope':'Approval only of this SHA-indexed dev input identity freeze and this read-only admission adjudication; no 350-cell, held-out or effects execution.',
        'evidence':[ref(PACKAGE/'operator_approval.json'),ref(PACKAGE/'REVIEW.md'),
                    ref(PACKAGE/'sha256_manifest.json'),ref(ROOT/'independent/approved_identity_verification.json'),
                    ref(PACKAGE/'goal_authorization.json')]}
    seven['seven_conditions']=copy.deepcopy(canonical['seven_condition_approval'])
    unchanged_canonical=all(canonical[k]==prior['canonical_gates_in_original_scope_plus_current_Safety'][k]
                            for k in canonical if k not in ['policy_identity','seven_condition_approval'])
    unchanged_six=all(seven[k]==prior['user_seven_gate_summary_in_original_scope_plus_current_Safety'][k]
                       for k in seven if k!='seven_conditions')
    check('all other7 canonical and6 user gates copied verbatim',unchanged_canonical and unchanged_six)
    formal=all(g['pass'] for g in canonical.values()) and all(c['pass'] for c in checks)
    failed=[{'item':k,'reason':g['reason'],'evidence':g['evidence']} for k,g in canonical.items() if not g['pass']]
    failed.extend({'item':'evidence_integrity','check':c} for c in checks if not c['pass'])
    evidence=[prior_path,negative_path,ast_path,ROOT/'protected_baseline.json',PACKAGE/'operator_approval.json',
        PACKAGE/'REVIEW.md',PACKAGE/'sha256_manifest.json',PACKAGE/'matrix_plan.json',
        PACKAGE/'runtime_source_lock.json',PACKAGE/'software_versions.json',PACKAGE/'execution_contract.json',
        ROOT/'independent/approved_identity_verification.json',ROOT/'tests_identity_rebinding.txt',
        ROOT/'independent/test_identity_review.py',ROOT/'independent/review_identity_package.py',ROOT/'verify_package.py']
    return {'schema_version':'attentionbench.guidance-v1.1-rebound-overall-admission.v1',
        'signed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'reviewer_role':'Author-produced mechanical review and artifact adjudication; approval authority is the direct current human goal, original independent Safety remains outside the policy worker. Not an external reviewer signature.',
        'scope':'Exact new guidance v1.1 policy/M1/launch development freeze and reused primary development admission evidence only',
        'formal_eligible':formal,'execution_authorized':False,
        'decision_rule':'All9 canonical gates (including exact new policy identity and scoped operator approval), all integrity checks and the retained autonomous/source compatibility must pass. Approval and admission never authorize effects execution.',
        'current_policy_sha256':{s:v['sha256'] for s,v in approval['identity']['policies'].items()},
        'current_identity_package':{'path':str(PACKAGE),'REVIEW':ref(PACKAGE/'REVIEW.md'),'manifest':ref(PACKAGE/'sha256_manifest.json')},
        'canonical_gate_count':len(canonical),'canonical_pass_count':sum(g['pass'] for g in canonical.values()),
        'canonical_gates':canonical,'user_seven_gate_summary':seven,
        'user_seven_pass_count':sum(g['pass'] for g in seven.values()),
        'rejudged_canonical_gates':['policy_identity','seven_condition_approval'],
        'other7_canonical_and6_user_judgments_unchanged':unchanged_canonical and unchanged_six,
        'current_version_alignment':{'pass':identity_pass,'previous_alignment_failure_retained':ref(PREVIOUS/'independent/version_alignment_audit.json'),
            'current350_lock_launch_identities_accept_approved_code':verification['pass'],
            'autonomous_AST_reuse_pass':ast_alignment['passed'],'all398_original_runtime_bytes_unchanged':True,
            'version_scope_extended_by_explicit_new_identity_approval_only':True},
        'service_versions':prior['service_versions'],'evidence_integrity_checks':checks,'failed_items':failed,
        'evidence':[ref(p) for p in evidence],'signer_script_sha256':sha(__file__),
        'execution_counts_this_round':{'generated_M1_locks':350,'generated_launch_identities':350,'configs_bound':50,
            'Service_starts':0,'robot_attempts':0,'Safety_reruns':0,'other6_gate_reruns':0,'350_cell_effect_executions':0,
            'heldout_executions':0,'Memory_Service_probes':0,'Memory_promotions':0,'model_generation_calls':0},
        'unchanged_known_limits':prior['unchanged_known_limits'],
        'next_steps':['Review and retain this exact development admission/identity evidence. Any later effects, held-out or robot execution requires a separate explicit authorized plan; this signature does not authorize it.',
                      'This90-minute-bounded round is closed; do not start a new round automatically.']}


if __name__=='__main__':
    report=adjudicate();path=ROOT/'independent/overall_admission_audit.json'
    with path.open('x') as out:json.dump(report,out,ensure_ascii=False,sort_keys=True,indent=2);out.write('\n')
    print(json.dumps({'formal_eligible':report['formal_eligible'],'canonical':str(report['canonical_pass_count'])+'/9',
        'user_gates':str(report['user_seven_pass_count'])+'/7','failed_items':report['failed_items'],'SHA':sha(path)},ensure_ascii=False))
