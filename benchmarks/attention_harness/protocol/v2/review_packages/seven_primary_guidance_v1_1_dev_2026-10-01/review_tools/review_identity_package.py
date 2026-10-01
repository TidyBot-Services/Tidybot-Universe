"""Review every new identity using production preflight and CLI parser data.

No launcher main, Service, Memory session, policy worker or SDK is invoked.
"""
import ast
import argparse
import copy
import datetime
import hashlib
import json
from pathlib import Path
import subprocess

from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.core.policies import POLICY_IDS
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest

U = Path('/home/truares/桌面/Tidybot-Universe-attention-native')
ROOT = Path(__file__).resolve().parents[1]
OLD = U / 'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_dev_2026-09-29'
GUIDANCE = U / 'benchmarks/attention_harness/protocol/v2/review_packages/guidance_adoption_v1_1_dev_2026-09-29'
PACKAGE = U / 'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
read = lambda p: json.loads(Path(p).read_text())
ref = lambda p: {'path': str(Path(p).resolve()), 'sha256': sha(p)}


def cli_parser():
    """Evaluate only parser declarations extracted from the unchanged CLI source."""
    tree = ast.parse((U / 'benchmarks/attention_harness/formal_attention_cli.py').read_text())
    main = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    declarations = []
    for statement in main.body:
        if isinstance(statement, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'args' for t in statement.targets):
            break
        allowed = (isinstance(statement, ast.Assign) and len(statement.targets)==1 and
                   isinstance(statement.targets[0], ast.Name) and statement.targets[0].id=='parser') or (
                   isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call) and
                   isinstance(statement.value.func, ast.Attribute) and
                   isinstance(statement.value.func.value, ast.Name) and statement.value.func.value.id=='parser' and
                   statement.value.func.attr=='add_argument')
        if not allowed:
            raise ValueError('CLI parser extraction encountered executable non-parser code')
        declarations.append(statement)
    namespace = {'argparse': argparse, 'Path': Path, 'POLICY_IDS': POLICY_IDS,
                 'AssistanceMode': AssistanceMode, '__doc__': ast.get_docstring(tree)}
    exec(compile(ast.Module(body=declarations, type_ignores=[]), '<production CLI parser declarations>', 'exec'), namespace)
    return namespace['parser']


def inspect_launch(arguments):
    args = cli_parser().parse_args(arguments + ['--artifact-root', str(ROOT / 'never_executed')])
    if not args.single_glm_call or args.attempt_deadline_seconds != 120 or args.whole_case_wall_seconds != 300:
        raise ValueError('launch must retain single-call and 120/300 guards')
    lock, _ = inspect_formal_entry(suite=args.suite, task_id=args.task, seed=args.seed,
        policy_id=args.attention_policy, code=args.code, approved_policy_sha256=args.approved_policy_sha256,
        config=args.config, approved_config_sha256=args.approved_config_sha256,
        max_attempts=args.max_attempts, assistance_credits=args.assistance_credits, token_limit=args.token_limit,
        assistance_mode=args.assistance_mode, human_deadline_seconds=args.human_deadline_seconds,
        overall_deadline_seconds=args.overall_deadline_seconds, demo_prior=args.demo_prior,
        approved_demo_sha256=args.approved_demo_sha256, policy_config=args.policy_config,
        approved_policy_config_sha256=args.approved_policy_config_sha256,
        memory_contract=args.memory_contract, approved_memory_contract_sha256=args.approved_memory_contract_sha256)
    if args.expected_entry_sha256 != lock['sha256']:
        raise ValueError('launch expected entry SHA mismatch')
    generation = read(args.dev_generation_artifact)
    if generation.get('sha256') != args.approved_policy_sha256 or generation.get('source') != str(args.code.resolve()):
        raise ValueError('Dev generation receipt does not match approved source')
    return lock, args


def review(package=PACKAGE, *, frozen=False):
    checks, rows = [], []

    def check(name, passed, **details):
        checks.append({'check': name, 'pass': bool(passed), **details})

    prior = read(OLD / 'matrix_plan.json')
    plan = read(package / 'matrix_plan.json')
    baseline = read(ROOT / 'protected_baseline.json')
    drift = [p for p,h in baseline['protected_files'].items() if not Path(p).is_file() or sha(p)!=h]
    check('all previous evidence and source protected', not drift, files=len(baseline['protected_files']), drift=drift)
    check('exact full development grid preserved', plan['grid']==prior['grid'] and len(plan['slots'])==350 and
          len({(r['suite'],r['seed'],r['condition']) for r in plan['slots']})==350)
    check('zero execution and unchanged original scientific rules',
          all(plan[k]==prior[k] for k in ['executed_slots','execution_authorized','heldout_executions',
              'ablation_executions','reporting','memory_grant_eligible_seeds','memory_coverage_threshold',
              'native_success_threshold']) and plan['executed_slots']==0 and plan['execution_authorized'] is False)
    approval = read(GUIDANCE / 'operator_approval.json')
    check('exact guidance approval REVIEW and manifest identities unchanged', approval['decision']=='approved' and
          all(sha(GUIDANCE / approval['identity'][k]['path'])==approval['identity'][k]['sha256'] for k in ['review','manifest']))
    runtime = read(package / 'runtime_source_lock.json')
    check('current exact runtime byte lock retained', all(sha(U/rel)==h for rel,h in runtime['file_sha256'].items()),
          files=len(runtime['file_sha256']))
    old_runtime = read(OLD/'runtime_source_lock.json')
    check('all original398 runtime bytes unchanged', all(sha(U/rel)==h for rel,h in old_runtime['file_sha256'].items()))
    versions = read(package/'software_versions.json')
    for service, data in versions.items():
        if service=='universe': continue
        check('clean pinned '+service, subprocess.check_output(['git','-C',data['path'],'rev-parse','HEAD'],text=True).strip()==data['commit'] and
              subprocess.check_output(['git','-C',data['path'],'status','--porcelain'],text=True)=='')
    for new_row, old_row in zip(plan['slots'], prior['slots']):
        key={k:new_row[k] for k in ['suite','task_id','seed','condition']}
        failures=[]
        try:
            assert all(new_row[k]==old_row[k] for k in ['order','suite','task_id','seed','condition','budget','trusted_memory_grant_eligible','run_status'])
            for k in ['config','memory_contract','demo','policy_config']:
                if old_row[k] is None: assert new_row[k] is None
                else:
                    assert new_row[k]['sha256']==old_row[k]['sha256']==sha(new_row[k]['path'])
                    assert Path(new_row[k]['path']).resolve().is_relative_to(package.resolve())
            for k in ['m1_entry_lock','launch_arguments']:
                assert sha(new_row[k]['path'])==new_row[k]['sha256']
                assert Path(new_row[k]['path']).resolve().is_relative_to(package.resolve())
            code=Path(new_row['policy']['path'])
            expected=approval['identity']['policies'][new_row['suite']]['sha256']
            assert sha(code)==new_row['policy']['sha256']==expected
            assert code.read_bytes()==(GUIDANCE/approval['identity']['policies'][new_row['suite']]['path']).read_bytes()
            old_lock=read(old_row['m1_entry_lock']['path']); saved=read(new_row['m1_entry_lock']['path'])
            assert {k for k in set(saved)|set(old_lock) if saved.get(k)!=old_lock.get(k)}=={'approved_policy_sha256','sha256'}
            launch=read(new_row['launch_arguments']['path'])
            assert launch['execution_authorized'] is False and launch['dynamic_output_argument_required']=='--artifact-root'
            generated,args=inspect_launch(launch['fixed_arguments'])
            assert saved==generated and saved['sha256']==new_row['m1_entry_identity_sha256']
            assert args.suite==key['suite'] and args.task==key['task_id'] and args.seed==key['seed'] and args.attention_policy==key['condition']
            assert args.config==Path(new_row['config']['path']) and args.memory_contract==Path(new_row['memory_contract']['path'])
            assert args.code==code and args.dev_generation_artifact==Path(new_row['policy']['generation_receipt']['path'])
            for opt,rc in [('demo_prior','demo'),('policy_config','policy_config')]:
                assert getattr(args,opt)==(Path(new_row[rc]['path']) if new_row[rc] else None)
            old_args=read(old_row['launch_arguments']['path'])['fixed_arguments']
            actual=launch['fixed_arguments']; assert len(actual)==len(old_args)
            allowed={'--code','--approved-policy-sha256','--config','--memory-contract','--expected-entry-sha256','--dev-generation-artifact','--demo-prior','--policy-config'}
            assert all(a==b or (i>0 and actual[i-1] in allowed) for i,(a,b) in enumerate(zip(actual,old_args)))
            for flag in ['--sim-python','--agent-python']:
                if flag in actual:
                    executable=Path(actual[actual.index(flag)+1]).resolve()
                    contract=read(package/'execution_contract.json')
                    expected_python=contract['robocasa_'+('sim' if flag=='--sim-python' else 'agent')+'_python']
                    assert executable==Path(expected_python['path']).resolve() and sha(executable)==expected_python['sha256']
            request=FormalRunRequest(suite=key['suite'],task_id=key['task_id'],seed=key['seed'],
                policy_code_path=code,policy_sha256=expected,config_path=args.config,config_sha256=args.approved_config_sha256,
                artifact_root=ROOT/'never_executed',overall_deadline_seconds=args.overall_deadline_seconds,
                entry_sha256=saved['sha256'],entry_lock=saved)
            request.validate()
            # Every new entry accepts current bytes while rejecting its former policy identity.
            rejected=False
            try:
                FormalRunRequest(**{**request.__dict__,'entry_sha256':old_lock['sha256'],'entry_lock':old_lock}).validate()
            except ValueError as error:
                rejected=str(error)=='formal request entry lock mismatch'
            assert rejected
            generation=read(args.dev_generation_artifact)
            assert generation['generation_kind']=='approved_guidance_control_revision' and generation['new_model_generation_calls']==0
            assert generation['lineage']['approved_guidance_operator_approval']['sha256']==sha(GUIDANCE/'operator_approval.json')
            if 'random_selected_slots' in old_row: assert new_row['random_selected_slots']==old_row['random_selected_slots']
        except (AssertionError,ValueError,KeyError,OSError) as error:
            failures.append(type(error).__name__+': '+str(error))
        rows.append({**key,'pass':not failures,'failures':failures,'entry_sha256':new_row['m1_entry_identity_sha256']})
    check('all350 launch parses, M1 recomputations, new acceptance and old rejection',len(rows)==350 and all(r['pass'] for r in rows))
    check('unchanged condition and Memory authority bytes', all(sha(package/name)==sha(OLD/name) for name in
          ['accounting_rules.json','retry_k.json','random_quota_preregistration.json','semantic_limitations.md',
           'memory/robosuite/initial.sqlite3','memory/robocasa/initial.sqlite3',
           'demo/robosuite_cube_lift_manifest.json','demo/robocasa_counter_to_sink_manifest.json']))
    check('no artifact directory created', not (ROOT/'never_executed').exists())
    if frozen:
        manifest=read(package/'sha256_manifest.json')
        bad=[rel for rel,h in manifest['files'].items() if not (package/rel).is_file() or sha(package/rel)!=h]
        check('all frozen file SHA exact',not bad,files=len(manifest['files']),drift=bad)
    issues=[c for c in checks if not c['pass']]
    return {'schema':'attentionbench.guidance-v1.1-identity-review.v1',
        'reviewer_role':'Author-produced mechanical review using unchanged production inspectors; not an external reviewer',
        'reviewed_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'pass':not issues,'scope':'New development input identities only; no execution authorization',
        'checks':checks,'entries':rows,'issues':issues,'entries_reviewed':len(rows),
        'Service_starts':0,'robot_runs':0,'policy_worker_runs':0,'Memory_Service_probes':0,
        'audit_script_sha256':sha(__file__),'evidence':[ref(package/'matrix_plan.json'),ref(GUIDANCE/'operator_approval.json'),
           ref(GUIDANCE/'frozen/REVIEW.md'),ref(OLD/'matrix_plan.json'),ref(ROOT/'protected_baseline.json'),
           ref(U/'benchmarks/attention_harness/formal_entry.py'),ref(U/'benchmarks/attention_harness/formal_runner_boundary.py'),
           ref(U/'benchmarks/attention_harness/formal_attention_cli.py')]}


if __name__=='__main__':
    report=review()
    with (ROOT/'independent/identity_review_draft.json').open('x') as out:
        json.dump(report,out,ensure_ascii=False,sort_keys=True,indent=2);out.write('\n')
    print(json.dumps({'pass':report['pass'],'entries':report['entries_reviewed'],'issues':report['issues'],
        'failed_entries':[r for r in report['entries'] if not r['pass']][:5]},ensure_ascii=False))
    raise SystemExit(0 if report['pass'] else 1)
