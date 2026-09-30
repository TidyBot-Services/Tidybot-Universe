"""Correct a path-string-only auditor false positive without repeating Memory probes."""
from pathlib import Path
import hashlib,json,datetime,collections
U=Path('/home/truares/桌面/Tidybot-Universe-attention-native')
P=U/'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_dev_2026-09-29'
I=U.parent/'attentionbench-seven-freeze-20260929/independent'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
prior=read(I/'seven_condition_freeze_audit.json');plan=read(P/'matrix_plan.json');execution=read(P/'execution_contract.json');versions=read(P/'software_versions.json')
issues=[];rows=[]
original_problem_set={(r['suite'],r['seed'],r['condition']) for r in prior['issues']}
expected_problem_set={(r['suite'],r['seed'],r['condition']) for r in plan['slots'] if r['suite']=='robocasa'}
prior_issues_expected=(len(prior['issues'])==175 and original_problem_set==expected_problem_set and all(r['check']=='complete fixed launch identity' for r in prior['issues']))
if not prior_issues_expected:issues.append('Unexpected first-pass failures cannot be resolved by Python alias correction')
if prior['manifest']['sha256']!=sha(P/'sha256_manifest.json'):issues.append('Frozen manifest changed after first pass')
for row in plan['slots']:
 suite,seed,condition,task=row['suite'],row['seed'],row['condition'],row['task_id']
 launch_path=Path(row['launch_arguments']['path']);launch=read(launch_path);a=launch['fixed_arguments'];parsed={};index=0;parse_error=None
 while index<len(a):
  key=a[index]
  if key in parsed or not key.startswith('--'):parse_error='duplicate or invalid argument';break
  if key=='--single-glm-call':parsed[key]=True;index+=1
  else:
   if index+1>=len(a):parse_error='missing argument value';break
   parsed[key]=a[index+1];index+=2
 required={'--suite':suite,'--task':task,'--seed':str(seed),'--attention-policy':condition,'--code':row['policy']['path'],'--approved-policy-sha256':row['policy']['sha256'],'--config':row['config']['path'],'--approved-config-sha256':row['config']['sha256'],'--max-attempts':'4','--assistance-credits':'1','--token-limit':'4096','--assistance-mode':'benchmark_proxy','--human-deadline-seconds':'30','--overall-deadline-seconds':'300','--attempt-deadline-seconds':'120','--whole-case-wall-seconds':'300','--memory-contract':row['memory_contract']['path'],'--approved-memory-contract-sha256':row['memory_contract']['sha256'],'--expected-entry-sha256':row['m1_entry_identity_sha256'],'--dev-generation-artifact':row['policy']['generation_receipt']['path'],'--single-glm-call':True}
 if suite=='robosuite':required['--service-source-root']=versions['robosuite_service']['path']
 else:required.update({'--sim-source-root':versions['maniskill_service']['path'],'--agent-source-root':versions['agent_service']['path'],'--task-source-root':versions['task_service']['path'],'--sim-python':execution['robocasa_sim_python']['path'],'--agent-python':execution['robocasa_agent_python']['path']})
 if row['demo']:required.update({'--demo-prior':row['demo']['path'],'--approved-demo-sha256':row['demo']['sha256']})
 if row['policy_config']:required.update({'--policy-config':row['policy_config']['path'],'--approved-policy-config-sha256':row['policy_config']['sha256']})
 aliases=[];failures=[]
 if parse_error or set(parsed)!=set(required):failures.append(parse_error or 'argument inventory differs')
 for key,expected in required.items():
  actual=parsed.get(key)
  if key in ['--sim-python','--agent-python'] and isinstance(actual,str):
   ref=execution['robocasa_sim_python' if key=='--sim-python' else 'robocasa_agent_python'];match=Path(actual).resolve()==Path(expected).resolve() and sha(actual)==ref['sha256']
   aliases.append({'argument':key,'launch_path':actual,'locked_resolved_path':expected,'actual_resolved_path':str(Path(actual).resolve()),'sha256':sha(actual),'same_target_and_bytes':match})
  else:match=actual==expected
  if not match:failures.append('argument mismatch: '+key)
 if sha(launch_path)!=row['launch_arguments']['sha256'] or launch['execution_authorized'] is not False or launch['dynamic_output_argument_required']!='--artifact-root':failures.append('launch file SHA/authorization mismatch')
 ok=not failures
 rows.append({'suite':suite,'seed':seed,'condition':condition,'launch_sha256':row['launch_arguments']['sha256'],'pass':ok,'python_aliases':aliases,'failures':failures})
 if not ok:issues.append(rows[-1])
approved=not issues and prior_issues_expected and len(rows)==350
report={'schema_version':'attentionbench.independent-seven-condition-approval.v1','reviewer_id':'independent_admission_auditor','timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'approval_id':'independent-seven-primary-approval-'+sha(P/'matrix_plan.json')[:16],'approved':approved,'formal_eligible':False,'execution_authorized':False,'approval_scope':prior['approval_scope'],'prior_full_review':{'path':str(I/'seven_condition_freeze_audit.json'),'sha256':sha(I/'seven_condition_freeze_audit.json'),'checks':prior['check_count'],'original_mismatches':len(prior['issues'])},'auditor_correction':'First-pass reviewer compared executable path strings instead of symlink targets. All 175 C entries use bin/python, which resolves to the locked bin/python3.11 and matches its exact byte SHA. Original report and failures are retained; frozen inputs are unchanged. This report derives approval from rechecked exact full launch identities plus all other prior independent checks.','launch_rechecks':rows,'issues':issues,'matrix_plan':prior['matrix_plan'],'manifest':prior['manifest'],'runtime_commit':prior['runtime_commit'],'service_versions':prior['service_versions'],'memory_grant_eligible_seeds':prior['memory_grant_eligible_seeds'],'bounded_offline_validation':prior['bounded_offline_validation'],'base_policy_semantic_limits':prior['base_policy_semantic_limits'],'no_new_Memory_probes_or_effect_runs':True,'condition_inputs':{'same_base_policy_bytes_all_conditions':True,'seed_configs_identical_across_conditions':True,'demo_inputs_independently_approved':True,'retry_k':2,'random_quota':1,'memory_exact_version':1,'nonfull_memory_visibility':'none','full_memory_visibility':'trusted_exact_versions, matched source scope only','no_automatic_promotion':True,'initial_state_reset_each_run':True},'limits':'Approves frozen inputs/help-rule definitions, not robot consumption or action improvements; no extra native success or Memory coverage minimum; overall eligibility requires all admission gates.'}
p=I/'seven_condition_freeze_approval.json'
if p.exists():raise FileExistsError('never overwrite independently signed approval')
p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'path':str(p),'sha256':sha(p),'approved':approved,'launch_identities_checked':len(rows),'issues':issues},ensure_ascii=False))
