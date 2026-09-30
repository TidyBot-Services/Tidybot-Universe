"""Independent frozen-input and bounded offline Service review; no runs or providers."""
from pathlib import Path
import sys,json,hashlib,subprocess,collections,sqlite3,shutil,time,datetime,ast
U=Path('/home/truares/桌面/Tidybot-Universe-attention-native')
M=Path('/home/truares/桌面/attention_memory_service')
sys.path[:0]=[str(U),str(M)]
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.core.policies import POLICY_IDS,build_policy
from benchmarks.attention_harness.formal_memory_contract import ACCOUNTING,FrozenMemoryGateway,initialize_frozen_memory
from benchmarks.attention_harness.core.store import AttentionStore
from attention_memory_service import MemoryService
P=U/'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_dev_2026-09-29'
I=U.parent/'attentionbench-seven-freeze-20260929/independent'
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
read=lambda p:json.loads(Path(p).read_bytes())
git=lambda p,*a:subprocess.check_output(['git','-C',str(p),*a],text=True).strip()
issues=[]; checks=[]
def check(ok,label,**detail):
 row={'check':label,'pass':bool(ok),**detail};checks.append(row)
 if not ok:issues.append(row)
 return bool(ok)
def verify(ref,label):
 p=Path(ref['path']);actual=sha(p)
 check(actual==ref['sha256'],label,path=str(p),expected_sha256=ref['sha256'],actual_sha256=actual)
 return p
def frozen_row_hash(rows):
 return hashlib.sha256(json.dumps(sorted(rows,key=repr),ensure_ascii=False,separators=(',',':'),default=str).encode()).hexdigest()
plan=read(P/'matrix_plan.json');manifest=read(P/'sha256_manifest.json');manifest_sha=sha(P/'sha256_manifest.json')
actual_files={str(f.relative_to(P)) for f in P.rglob('*') if f.is_file() and f.name!='sha256_manifest.json'}
check(actual_files==set(manifest['files']),'manifest covers exact frozen file inventory',missing=sorted(set(manifest['files'])-actual_files),extra=sorted(actual_files-set(manifest['files'])))
for rel,expected in manifest['files'].items():check(sha(P/rel)==expected,'frozen file SHA',path=rel,sha256=expected)
check(plan['scope']=='本开发集主实验；held-out与消融另行冻结','primary-development scope only')
check(plan['planned_slots']==350 and plan['executed_slots']==0 and plan['execution_authorized'] is False and plan['heldout_executions']==0 and plan['ablation_executions']==0,'350 generated, zero executed, no heldout/ablation')
check(plan['grid']=={'tasks':[['robosuite','cube_lift'],['robocasa','counter_to_sink']],'seeds':list(range(101,126)),'conditions':list(POLICY_IDS),'repeats':1},'exact predeclared grid')
check(plan['reporting']=={'aggregation':'per_task','primary_axis':'credits','secondary_axis':'tokens','interval':'Wilson 95%'} and plan['heldout_later_sample_size']==10 and plan['heldout_protocol_pool']==list(range(1001,1101)) and plan['native_success_threshold'] is None and plan['memory_coverage_threshold'] is None,'reporting and no added native/Memory coverage threshold')
for name in ['base_protocol','resolution','specification']:verify(plan[name],name)
for name,ref in plan.get('original_protocol_sources',{}).items():verify(ref,'original '+name)
versions=read(P/'software_versions.json');runtime=plan['runtime_commit'];lock=read(P/'runtime_source_lock.json')
check(runtime==versions['universe']['runtime_commit']==lock['runtime_commit']=='03f07ded72b936c57e08ce22050f06b34ee46a0e','saved exact runtime commit')
source_lock=[]
for rel,expected in lock['file_sha256'].items():
 committed=subprocess.check_output(['git','-C',str(U),'show',runtime+':'+rel])
 ok=hashlib.sha256(committed).hexdigest()==expected==sha(U/rel)
 check(ok,'runtime file equals exact commit',path=rel,sha256=expected);source_lock.append(rel)
service_versions=[]
for name,value in versions.items():
 if name=='universe':continue
 path=Path(value['path']);head=git(path,'rev-parse','HEAD');dirty=git(path,'status','--porcelain')
 check(head==value['commit'] and not dirty,'Service exact clean commit',service=name,commit=head,dirty=dirty)
 service_versions.append({'service':name,'path':str(path),'commit':head,'clean':not dirty})
execution=read(P/'execution_contract.json');budget={'max_attempts':4,'assistance_credits':1,'token_limit':4096,'attempt_deadline_seconds':120,'whole_case_wall_seconds':300,'sdk_calls_max':200}
check(execution['execution_authorized'] is False and execution['mode']=='generation_and_inspection_only' and execution['model']=='parcc/GLM' and execution['provider_http_attempts']==1 and execution['format_attempts']==1 and execution['budget']==budget and execution['single_glm_call'] is True,'execution parameters and bounded budget')
for key in ['harness_python','robocasa_sim_python','robocasa_agent_python','service_versions']:verify(execution[key],'execution '+key)
accounting=read(P/'accounting_rules.json');check(all(accounting[k]==v for k,v in ACCOUNTING.items()) and accounting['automatic_memory_promotion'] is False and accounting['memory_reset_each_run'] is True,'unified cache, credits, tokens, Memory reset and no promotion')
check(read(P/'retry_k.json')=={'k':2},'retry k locked to 2')
prereg=read(P/'random_quota_preregistration.json');check(prereg['target_request_count']==prereg['full_method_planned_quota']==1 and prereg['total_failure_slots']==3 and prereg['predeclared_before_effects'] is True,'random quota preregistered before results')
demo_approval=read(P/'demo/independent_input_approval.json');check(demo_approval['approved'] is True and sha(P/'demo/independent_input_approval.json')==sha(I/'demo_input_approval.json'),'independent demo approval exact copy')
slots=plan['slots'];ids=[(r['suite'],r['task_id'],r['seed'],r['condition']) for r in slots]
expected={(s,t,seed,c) for s,t in [('robosuite','cube_lift'),('robocasa','counter_to_sink')] for seed in range(101,126) for c in POLICY_IDS}
check(len(slots)==350 and len(set(ids))==350 and set(ids)==expected,'no missing, duplicated or extra cells')
policies=collections.defaultdict(set);configs=collections.defaultdict(set);entry_rows=[];contracts={};hidden_probes=0
class HiddenNoCalls:
 def __getattr__(self,name):raise AssertionError('hidden Memory probed Service: '+name)
for row in slots:
 suite,task,seed,condition=row['suite'],row['task_id'],row['seed'],row['condition']
 policies[suite].add(row['policy']['sha256']);configs[(suite,seed)].add(row['config']['sha256'])
 code=verify(row['policy'],'policy bytes');config=verify(row['config'],'config bytes');contract=verify(row['memory_contract'],'Memory contract bytes');entry_file=verify(row['m1_entry_lock'],'M1 file bytes');launch_file=verify(row['launch_arguments'],'launch arguments bytes')
 for key in ['generation_receipt','original_policy_review','original_package_decision']:verify(row['policy'][key],'policy '+key)
 demo=verify(row['demo'],'condition demo') if row['demo'] else None
 policy_config=verify(row['policy_config'],'condition policy config') if row['policy_config'] else None
 args=dict(suite=suite,task_id=task,seed=seed,policy_id=condition,code=code,approved_policy_sha256=row['policy']['sha256'],config=config,approved_config_sha256=row['config']['sha256'],max_attempts=4,assistance_credits=1,token_limit=4096,assistance_mode='benchmark_proxy',human_deadline_seconds=30,overall_deadline_seconds=300,demo_prior=demo,approved_demo_sha256=row['demo']['sha256'] if demo else None,policy_config=policy_config,approved_policy_config_sha256=row['policy_config']['sha256'] if policy_config else None,memory_contract=contract,approved_memory_contract_sha256=row['memory_contract']['sha256'])
 recomputed,parsed=inspect_formal_entry(**args);saved=read(entry_file);value=read(contract)
 check(saved==recomputed and recomputed['sha256']==row['m1_entry_identity_sha256'],'M1 recomputed identity',suite=suite,seed=seed,condition=condition,identity_sha256=recomputed['sha256'])
 check(row['budget']==budget and row['run_status']=='not_executed' and row['accounting_rules_sha256']==sha(P/'accounting_rules.json') and row['execution_contract_sha256']==sha(P/'execution_contract.json'),'cell budget/accounting/execution status',suite=suite,seed=seed,condition=condition)
 launch=read(launch_file);argv=launch['fixed_arguments'];arg=lambda k:argv[argv.index(k)+1]
 required={'--suite':suite,'--task':task,'--seed':str(seed),'--attention-policy':condition,'--code':str(code),'--approved-policy-sha256':row['policy']['sha256'],'--config':str(config),'--approved-config-sha256':row['config']['sha256'],'--max-attempts':'4','--assistance-credits':'1','--token-limit':'4096','--assistance-mode':'benchmark_proxy','--human-deadline-seconds':'30','--overall-deadline-seconds':'300','--attempt-deadline-seconds':'120','--whole-case-wall-seconds':'300','--memory-contract':str(contract),'--approved-memory-contract-sha256':row['memory_contract']['sha256'],'--expected-entry-sha256':saved['sha256'],'--dev-generation-artifact':row['policy']['generation_receipt']['path']}
 if suite=='robosuite':required['--service-source-root']=versions['robosuite_service']['path']
 else:required.update({'--sim-source-root':versions['maniskill_service']['path'],'--agent-source-root':versions['agent_service']['path'],'--task-source-root':versions['task_service']['path'],'--sim-python':execution['robocasa_sim_python']['path'],'--agent-python':execution['robocasa_agent_python']['path']})
 if demo:required.update({'--demo-prior':str(demo),'--approved-demo-sha256':row['demo']['sha256']})
 if policy_config:required.update({'--policy-config':str(policy_config),'--approved-policy-config-sha256':row['policy_config']['sha256']})
 check(set(argv)==set(required)|set(required.values())|{'--single-glm-call'} and all(arg(k)==v for k,v in required.items()) and argv.count('--single-glm-call')==1 and '--artifact-root' not in argv and launch['execution_authorized'] is False,'complete fixed launch identity',suite=suite,seed=seed,condition=condition)
 if condition=='budget_matched_random_escalation':check(parsed=={'target_request_count':1,'total_failure_slots':3,'seed':seed} and row['random_selected_slots']==sorted(build_policy(condition,**parsed).selected_slots),'deterministic random preregistration',suite=suite,seed=seed)
 if condition!='full_trace_aware_attention_planner':
  check(value['visibility']=='none' and value['initial_state']=={'kind':'empty','path':None,'sha256':None,'versions':[]} and value['context'] is None,'six conditions have no Memory visibility',suite=suite,seed=seed,condition=condition)
  gateway=FrozenMemoryGateway(HiddenNoCalls(),value);check(gateway.retrieve({},now=time.time())==[],'hidden Memory never probes Service',suite=suite,seed=seed,condition=condition);hidden_probes+=1
  try:gateway.authorize_use({},memory_id='forbidden',attempt_id='offline',now=time.time());denied=False
  except PermissionError:denied=True
  check(denied,'hidden Memory grant denied',suite=suite,seed=seed,condition=condition)
 else:contracts[(suite,seed)]=value
 entry_rows.append({'suite':suite,'seed':seed,'condition':condition,'m1_identity_sha256':recomputed['sha256'],'memory_contract_sha256':row['memory_contract']['sha256'],'launch_sha256':row['launch_arguments']['sha256']})
check(policies=={'robosuite':{'fa1eff2f0bf938fc8dec6ad1bcf58c1db481a00332f59e2108bcfb94d93ee6c8'},'robocasa':{'e22f944fddcae410095d91cf6ef61a1626db4bcdb88479b9ac19281895f11780'}},'same approved base bytes across seven conditions',policy_sha256={k:sorted(v) for k,v in policies.items()})
check(len(configs)==50 and all(len(v)==1 for v in configs.values()),'same per-task seed config across all seven conditions')
memory_provenance=[];scope_rows=[];reset_rows=[];lifecycle=[];frozen_initial_shas={}
for suite in ['robosuite','robocasa']:
 provenance=read(P/'memory'/suite/'provenance.json');source=verify(provenance['source'],'Memory original authority snapshot');snapshot=verify(provenance['initial_snapshot'],'Memory frozen initial snapshot');frozen_initial_shas[suite]=sha(snapshot)
 original=sqlite3.connect(f'file:{source}?mode=ro',uri=True);frozen=sqlite3.connect(f'file:{snapshot}?mode=ro',uri=True)
 tables=[r[0] for r in original.execute("SELECT name FROM sqlite_master WHERE type='table'")];snapshot_tables=[r[0] for r in frozen.execute("SELECT name FROM sqlite_master WHERE type='table'")]
 check(set(tables)==set(snapshot_tables),'Memory snapshot table schema retained',suite=suite)
 table_checks=[]
 for table in tables:
  a=original.execute('SELECT * FROM "'+table.replace('"','""')+'"').fetchall();b=frozen.execute('SELECT * FROM "'+table.replace('"','""')+'"').fetchall()
  ok=(len(b)==0 if table=='advisor_cache' else sorted(a,key=repr)==sorted(b,key=repr))
  check(ok,'Memory snapshot only clears Advisor cache',suite=suite,table=table,source_rows=len(a),snapshot_rows=len(b));table_checks.append({'table':table,'original_rows':len(a),'snapshot_rows':len(b),'original_rows_sha256':frozen_row_hash(a),'snapshot_rows_sha256':frozen_row_hash(b),'approved_difference':table=='advisor_cache','pass':ok})
 memory=json.loads(frozen.execute('SELECT payload FROM memories WHERE id=?',(provenance['memory_id'],)).fetchone()[0])
 check(memory['status']=='trusted' and memory['version']==provenance['version']==1 and memory['expires_at']==provenance['expires_at']==None,'exact trusted unexpired v1',suite=suite,memory_id=provenance['memory_id'])
 evidence=P/'memory'/suite/'evidence';original_evidence=Path(provenance['source_evidence'])
 for rel,expected_sha in provenance['evidence_file_sha256'].items():check(sha(evidence/rel)==sha(original_evidence/rel)==expected_sha,'Memory evidence exact immutable source copy',suite=suite,path=rel,sha256=expected_sha)
 original.close();frozen.close()
 offline=I/'offline_memory_review'/suite
 first,first_evidence,service1=initialize_frozen_memory(contracts[(suite,101)],artifact_root=offline)
 AttentionStore(first).cache_put('independent-snapshot-isolation',{'offline':True})
 second,second_evidence,service2=initialize_frozen_memory(contracts[(suite,101)],artifact_root=offline)
 check(first!=second and AttentionStore(second).cache_get('independent-snapshot-isolation') is None and sha(snapshot)==frozen_initial_shas[suite],'separate per-run initialization and immutable initial state',suite=suite)
 reset_rows.append({'suite':suite,'first_store':str(first),'second_store':str(second),'distinct':first!=second,'second_cache_empty':AttentionStore(second).cache_get('independent-snapshot-isolation') is None,'frozen_snapshot_unchanged':sha(snapshot)==frozen_initial_shas[suite]})
 for action in ['promote','disable','rollback','set_expiry','authorize_dev_use']:
  try:getattr(service2,action);denied=False
  except PermissionError:denied=True
  check(denied,'primary lifecycle method prohibited',suite=suite,action=action);lifecycle.append({'suite':suite,'action':action,'denied':denied})
 for seed in range(101,126):
  value=contracts[(suite,seed)];gateway=FrozenMemoryGateway(service2.service,value);matches=gateway.retrieve(value['context'],now=time.time());expected_match=seed in [101,103,105]
  actual_ids=[(m.memory_id,m.version) for m in matches];match_ok=(actual_ids==[(provenance['memory_id'],1)] if expected_match else not actual_ids)
  check(match_ok,'real Service matches or rejects exact per-seed frozen scope',suite=suite,seed=seed,expected_match=expected_match,actual_memory_versions=actual_ids)
  attempt_id=f'attempt:independent-offline-scope-probe-{suite}-seed{seed}'
  try:grant=gateway.authorize_use(value['context'],memory_id=provenance['memory_id'],attempt_id=attempt_id,now=time.time());granted=True
  except Exception as exc:grant=None;granted=False;rejection=f'{type(exc).__name__}: {exc}'
  grant_ok=granted==expected_match and (not granted or grant['version']==1 and grant['attempt_id']==attempt_id and grant['memory_id']==provenance['memory_id'])
  check(grant_ok,'real exact-version/attempt grant or out-of-scope rejection',suite=suite,seed=seed,granted=granted)
  scope_rows.append({'suite':suite,'seed':seed,'context':value['context'],'matches':actual_ids,'expected_match':expected_match,'grant':grant,'rejection':rejection if not granted else None,'pass':match_ok and grant_ok,'counts_as_robot_execution':False})
 memory_provenance.append({'suite':suite,'memory_id':provenance['memory_id'],'version':1,'expires_at':None,'source':provenance['source'],'frozen_snapshot':provenance['initial_snapshot'],'tables':table_checks})
 check(sha(source)==provenance['source']['sha256'] and sha(snapshot)==frozen_initial_shas[suite],'Memory originals and frozen snapshots unchanged after offline review',suite=suite)
allowed={suite:[r['seed'] for r in scope_rows if r['suite']==suite and r['matches']] for suite in ['robosuite','robocasa']}
check(allowed==plan['memory_grant_eligible_seeds']=={'robosuite':[101,103,105],'robocasa':[101,103,105]},'scope eligibility count is reported accurately, without broadening')
semantics=[]
for suite,task in [('robosuite','cube_lift'),('robocasa','counter_to_sink')]:
 code=P/'base'/f'{suite}_{task}'/'policy.py';tree=ast.parse(code.read_text());context_keys=[]
 for node in ast.walk(tree):
  if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name) and node.value.id=='context' and isinstance(node.slice,ast.Constant):context_keys.append(node.slice.value)
 semantics.append({'suite':suite,'policy_sha256':sha(code),'context_keys_read':sorted(set(context_keys)),'reads_attention_input':False,'interpretation':'Static approved control code does not consume Demo, Advisor guidance, Memory guidance, or reflection inputs. Scheduler request/cost/exposure records are auditable; no robot-control benefit or code-consumption claim follows. This is a limitation, not an added success or Memory-coverage threshold.'})
check(sha(P/'sha256_manifest.json')==manifest_sha,'frozen manifest unchanged during independent inspection')
report={'schema_version':'attentionbench.independent-seven-input-freeze-review.v1','reviewer_id':'independent_admission_auditor','timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'approval_scope':'Frozen inputs and help-rule definitions for 本开发集主实验 only; no execution authorization','approved':not issues,'formal_eligible':False,'matrix_executions':0,'heldout_executions':0,'new_live_runs_by_reviewer':0,'bounded_offline_validation':{'entry_inspections':350,'hidden_gateway_probes':hidden_probes,'task_snapshot_initializations':4,'per_seed_Service_retrieval_probes':50,'per_seed_Service_grant_probes':50,'lifecycle_attribute_rejections':10,'effects_or_native_evaluations':0},'package':str(P),'matrix_plan':{'path':str(P/'matrix_plan.json'),'sha256':sha(P/'matrix_plan.json')},'manifest':{'path':str(P/'sha256_manifest.json'),'sha256':manifest_sha,'file_count':len(manifest['files'])},'runtime_commit':runtime,'runtime_source_files_checked':len(source_lock),'service_versions':service_versions,'check_count':len(checks),'issues':issues,'checks':checks,'entries':entry_rows,'memory_snapshot_provenance':memory_provenance,'memory_scope_probes':scope_rows,'memory_grant_eligible_seeds':allowed,'snapshot_reset_probes':reset_rows,'lifecycle_rejections':lifecycle,'base_policy_semantic_limits':semantics,'interpretation':'Offline Service grant probes are validation only, not raw robot retrieval/use or task feasibility/effect evidence. Historical actual retrieval/use remains separately labeled. Engineering unit tests do not count as the 350 planned runs.'}
output=I/'seven_condition_freeze_audit.json'
if output.exists():raise FileExistsError('do not overwrite previous independent result')
output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'path':str(output),'sha256':sha(output),'approved':report['approved'],'checks':len(checks),'issues':issues,'memory_eligible':allowed,'manifest_files':len(manifest['files'])},ensure_ascii=False))
