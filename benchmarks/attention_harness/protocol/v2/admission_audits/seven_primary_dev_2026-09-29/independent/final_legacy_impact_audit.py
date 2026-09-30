"""Read-only final source impact / retained evidence budget review; no Services."""
import sys,json,hashlib,subprocess,collections,datetime
from pathlib import Path
U=Path('/home/truares/桌面/Tidybot-Universe-attention-native');sys.path.insert(0,str(U))
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
D=U.parent; I=D/'attentionbench-seven-freeze-20260929/independent'
plans=[D/'attentionbench-depth-qualifying-20260929/profile_plan.json',D/'attentionbench-fixed-base-profile-20260929/engineering_v112_isolated_continuation/profile_plan.json']
rows=[]
for p in plans:
 for s in json.loads(p.read_bytes())['slots']:
  if p==plans[1] and s['suite']!='robocasa':continue
  a=s['argv'];arg=lambda k:a[a.index(k)+1]
  try:
   entry,_=inspect_formal_entry(suite=arg('--suite'),task_id=arg('--task'),seed=int(arg('--seed')),policy_id=arg('--attention-policy'),code=Path(arg('--code')),approved_policy_sha256=arg('--approved-policy-sha256'),config=Path(arg('--config')),approved_config_sha256=arg('--approved-config-sha256'),max_attempts=int(arg('--max-attempts')),assistance_credits=int(arg('--assistance-credits')),token_limit=int(arg('--token-limit')),assistance_mode=arg('--assistance-mode'),human_deadline_seconds=float(arg('--human-deadline-seconds')),overall_deadline_seconds=float(arg('--overall-deadline-seconds')))
   expected=arg('--expected-entry-sha256')
   rows.append({'suite':s['suite'],'task_id':s['task_id'],'seed':s['seed'],'expected_entry_sha256':expected,'recomputed_entry_sha256':entry['sha256'],'matches':entry['sha256']==expected})
  except Exception as exc:rows.append({'suite':s['suite'],'seed':s['seed'],'matches':False,'error':repr(exc)})
reuse=json.loads((I/'evidence_reuse_audit.json').read_bytes()); budgets=collections.defaultdict(lambda:{'requested_sdk_calls':0,'attempts':0,'receipts':[]})
for ref in reuse['sha_checks']:
 p=Path(ref['path'])
 if ref['role']!='archive_four_artifact' or p.name!='sandbox_receipt.json':continue
 actual=hashlib.sha256(p.read_bytes()).hexdigest()
 if actual!=ref['expected_sha256']:raise ValueError(f'retained receipt SHA mismatch: {p}')
 value=json.loads(p.read_bytes()); rid=value['run_id'];worker=value.get('worker') or {}
 count=worker.get('call_count',0)
 if type(count) is not int or count<0:raise ValueError(f'invalid SDK call count: {p}')
 budgets[rid]['requested_sdk_calls']+=count;budgets[rid]['attempts']+=1;budgets[rid]['receipts'].append({'path':str(p),'sha256':actual,'worker_call_count':count})
paths=['formal_entry.py','formal_attention_run.py','formal_attention_cli.py','formal_memory_contract.py','formal_runner_boundary.py','sim_gt_attention_run.py','v2_advisor.py','parcc_client.py','robosuite_memory/formal_runner.py','robocasa_native/formal_runner.py','robosuite_memory/formal_sandbox.py']
source={p:hashlib.sha256((U/'benchmarks/attention_harness'/p).read_bytes()).hexdigest() for p in paths}
report={'schema_version':'attentionbench.independent-final-legacy-impact.v1','reviewer_id':'independent_admission_auditor','timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'universe_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=U,text=True).strip(),'git_status':subprocess.check_output(['git','status','--porcelain'],cwd=U,text=True).strip(),'simulator_runs':0,'checked_entries':len(rows),'matches':sum(r['matches'] for r in rows),'all_historical_entries_preserved':len(rows)==50 and all(r['matches'] for r in rows),'implementation_sha256':source,'entry_checks':rows,'retained_case_sdk_counts':dict(budgets),'retained_case_sdk_budget':{'runs':len(budgets),'attempts':sum(r['attempts'] for r in budgets.values()),'max_requested_sdk_calls_per_run':max(r['requested_sdk_calls'] for r in budgets.values()),'total_requested_sdk_calls':sum(r['requested_sdk_calls'] for r in budgets.values()),'all_runs_at_or_below_200':len(budgets)==50 and all(r['requested_sdk_calls']<=200 for r in budgets.values())},'scope':'No historical contract/strict-branch execution inferred. Optional new guards preserve no-contract identities. Actual retained case totals independently check the existing per-run SDK budget despite old per-attempt sandbox hard limits.'}
p=I/'final_legacy_impact_audit.json'
if p.exists():raise FileExistsError('Never overwrite independently signed prior final evidence')
p.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'entries':report['matches'],'budget':report['retained_case_sdk_budget']},ensure_ascii=False))
