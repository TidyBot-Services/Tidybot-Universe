"""Read-only evidence + function compatibility audit; no runtime or simulator calls."""
from pathlib import Path
import json, hashlib, ast, subprocess, datetime
D=Path('/home/truares/桌面');U=D/'Tidybot-Universe-attention-native';O=D/'attentionbench-seven-freeze-20260929/independent'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def obj(p):return json.loads(Path(p).read_text())
def git(repo,*args):return subprocess.check_output(['git','-C',str(repo),*args],text=True).strip()
def func(repo,ref,path,name):
 text=git(repo,'show',ref+':'+path);tree=ast.parse(text)
 node=next(n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)
 return hashlib.sha256(ast.dump(node,include_attributes=False).encode()).hexdigest()
refs=[]; issues=[]
def check(p,expected,label):
 try: actual=sha(p)
 except FileNotFoundError:actual=None
 row={'path':str(p),'expected_sha256':expected,'actual_sha256':actual,'matches':actual==expected,'role':label};refs.append(row)
 if actual!=expected:issues.append(row)
 return actual==expected
x=obj(U/'benchmarks/attention_harness/protocol/v2/formal_admission_v2_4_resolution_2026-09-30.json')
check(U/'benchmarks/attention_harness/protocol/v2'/x['base_protocol'],x['base_protocol_sha256'],'protocol_v2_2')
for k in ['combined_admission_archive','overall_admission_audit']:
 e=x[k];check(e['path'],e['sha256'],k)
for e in [x['depth_500']['independent_audit'],x['new_version_robosuite_profile']['audit'],x['new_version_robosuite_profile']['ledger'],x['robocasa_impact_review']]:
 check(e['path'],e['sha256'],'prior_qualified_gate')
archive=obj(x['combined_admission_archive']['path']); roles={'native_result':'native_result.json','safety':'safety.json','sandbox_receipt':'sandbox_receipt.json','trace':'trace.json'}
ids=[]; safety=0
for slot in archive['slots']:
 check(slot['case_audit_path'],slot['case_audit_sha256'],'archive_case_audit')
 for a in slot['attempts']:
  root=Path(a['artifact_root']);ids.append(a['attempt_id'])
  for r,ex in a['four_artifact_sha256'].items():check(root/roles[r],ex,'archive_four_artifact')
  s=obj(root/'safety.json');safety+=s['unsafe_attempts']
old_faults=obj(D/'attentionbench-m3-20260928/fault_progress.json')
for a in old_faults:
 for r,e in a['artifacts'].items():check(e['uri'],e['sha256'],'historical_safety_fault_'+a['case'])
feas=obj(D/'attentionbench-fixed-base-profile-20260929/engineering_v35_profile/task_feasibility_audit.json')
for a in feas['robocasa']['rows']:check(a['raw_result'],a['raw_result_sha256'],'labeled_historical_feasibility')
check(feas['robosuite']['native_result'],feas['robosuite']['native_result_sha256'],'labeled_historical_feasibility')
mem_audits=[D/'attentionbench-m5-20260928/robosuite-current-v3/audit.json',D/'attentionbench-depth-memory-risk-20260929/robocasa-memory-v3/postpromotion/audit.json']
for p in mem_audits:
 a=obj(p)
 for arm in a['attempts']:
  for role,e in arm['artifacts'].items():
   if isinstance(e,dict):check(e['uri'],e['sha256'],'historical_memory_use')
   else:
    files=list((p.parent/'runs').glob('**/'+roles[role]));match=[f for f in files if sha(f)==e]
    check(match[0] if len(match)==1 else '/missing',e,'historical_memory_use')
compat=[]
for repo,ref,path,names in [
 (D/'robosuite_sim-depth-recovery-engineering','081cd57ec9383895dc150050ca5d05c24628e7fa','robosuite_sim/backend.py',['native_success','__init__']),
 (D/'maniskill_sim-attention-guard-v5','320020a0c94434af31ec02df3413229576490fef','maniskill_server/server.py',['_cmd_evaluate','_cmd_teleport']),
 (U,'e1b76bdaf475c54c0847ac57e9dc24f98efc2c6c','benchmarks/attention_harness/robocasa_native/safety_monitor.py',['_violate','observe','move_arm_delta']),
]:
 for n in names:
  old=func(repo,ref,path,n);new=func(repo,'a42ab8201277ffa806281b89a4f14e3ef33b1059' if repo==U else 'HEAD',path,n);compat.append({'repo':str(repo),'path':path,'function':n,'old_revision':ref,'current_revision':git(repo,'rev-parse','HEAD'),'old_function_ast_sha256':old,'current_function_ast_sha256':new,'equivalent_ast':old==new})
memcompat=[]
for old in ['24975b59479b1dd44ceb817cf540911886bae4a1','a55416bdc8cfcd0183e0a6db3049b00d151c0d9c']:
 for path in ['benchmarks/attention_harness/sim_gt_memory.py','benchmarks/attention_harness/memory_service_client.py','benchmarks/attention_harness/memory_v2.py','benchmarks/attention_harness/core/memory.py']:
  b=subprocess.check_output(['git','-C',str(U),'show',old+':'+path]);c=subprocess.check_output(['git','-C',str(U),'show','a42ab8201277ffa806281b89a4f14e3ef33b1059:'+path]);memcompat.append({'path':path,'old_universe':old,'current_universe':'a42ab8201277ffa806281b89a4f14e3ef33b1059','old_sha256':hashlib.sha256(b).hexdigest(),'current_sha256':hashlib.sha256(c).hexdigest(),'identical_bytes':b==c})
versions=[]
for repo in [U,D/'robosuite_sim-depth-recovery-engineering',D/'maniskill_sim-attention-guard-v5',Path('/home/truares/文档/Tidybot-Universe/agent_server-attention-rejection-v5'),D/'attention_memory_service',Path('/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks')]:versions.append({'repo':str(repo),'head':git(repo,'rev-parse','HEAD'),'dirty':git(repo,'status','--short')})
report={'schema_version':'attentionbench.independent-evidence-reuse-audit.v1','reviewer_id':'independent_admission_auditor','timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'no_runs_started':True,'version_binding':versions,'prior_archive':{'planned_slots':len(archive['slots']),'unique_attempts':len(set(ids)),'all_attempt_ids_unique':len(set(ids))==len(ids),'unsafe_attempts':safety,'preserved_native_failures':archive['valid_native_failures'],'minimum_success_required':False},'sha_checks':refs,'sha_issues':issues,'compatibility_function_checks':compat,'memory_component_compatibility':memcompat,'provisional_remaining_gate_adjudication':{'task_feasibility':{'pass':all(c['equivalent_ast'] for c in compat if c['function'] in ['native_success','__init__','_cmd_evaluate','_cmd_teleport']) and not issues,'scope':'Labeled native task/evaluator infrastructure solvability only. Historical reference/Memory positive cases; unchanged native task/evaluator and current pinned task source, no base-policy success claims.'},'safety_fault_injection':{'pass':False,'reuse':'28 historical four-artifact SHA checks; current R persistent14 negative controls available separately','missing':'Current RoboCasa unknown-action controlled negative with timely stop and exact current-version service reaping; current unsafe guard compatibility can reuse old guard and bounded no-simulator validation.'},'trusted_memory':{'pass':False,'reuse':'Existing trusted v1 provenance, five-pair promotion and standalone raw retrieval/grant/use with scope/lifecycle controls are intact; Service and core Memory components unchanged','missing':'Current matrix-specific context match/reject coverage, exact-version enforcement and no auto promotion / same initial state enforcement require final frozen runtime review; no broad new scene/seed grants authorized.'}}}
p=O/'evidence_reuse_audit.json';p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(p,'sha',sha(p));print('sha checks',len(refs),'issues',len(issues));print(json.dumps(report['provisional_remaining_gate_adjudication'],ensure_ascii=False,indent=2)); print('compatibility',[(c['function'],c['equivalent_ast']) for c in compat])
