from pathlib import Path
import json,hashlib
D=Path('/home/truares/桌面/attentionbench-depth-bias-gate-20260929');out=Path('/home/truares/桌面/attentionbench-seven-freeze-20260929/independent/current_robosuite_negative_reuse_audit.json')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest();read=lambda p:json.loads(Path(p).read_text());checks=[];issues=[];rows=[]
def check(p,h):
 a=sha(p);checks.append({'path':str(p),'expected_sha256':h,'actual_sha256':a,'matches':a==h})
 if a!=h:issues.append('SHA mismatch '+str(p))
ledger=read(D/'persistent/ledger.json');check(D/'persistent/plan.json',ledger['plan_sha256'])
for row in ledger['rows']:
 p=Path(row['audit_path']);check(p,row['audit_sha256']);a=read(p);check(a['scheduler_path'],a['scheduler_sha256']);st=a['service_stop']
 for at in a['attempts']:
  root=p.parent/'gate_artifacts'/str(at['index'])
  for k,h in at['artifact_sha256'].items():check(root/(k+'.json'),h)
  b=read(root/'bundle.json')
  for e in b.values():check(e['path'],e['sha256'])
  s=read(root/'safety.json');t=read(root/'trace.json');r=read(root/'sandbox_receipt.json')
  ok=(a['attempt_count']==1 and a['stopped_reason']=='independent_safety_stop' and s['unsafe_attempts']==1 and any(v['kind']=='observation_unavailable_after_executed_action' for v in s['violations']) and st['leader_reaped'] is True and st['process_group_gone'] is True)
  if not ok:issues.append('Negative control failed '+str(p))
 rows.append({'task':a['task_id'],'condition':a['condition'],'independent_safety_unsafe_attempts':1,'single_attempt_and_immediate_scheduler_stop':ok,'service_stop':st,'case_audit_sha256':row['audit_sha256']})
rep={'schema_version':'attentionbench.independent-current-robosuite-negative-reuse.v1','reviewer_id':'independent_admission_auditor','new_runs':0,'service_revision':'19fde8aa7c47c283fce7edcb25348e4ccb2fd905','scope':'Persistent depth failure after executed action on Robosuite engineering grid; preserves confirmed action receipt and fails closed, cannot count as primary effects or chain/profile success.','cases':rows,'sha_checks':checks,'issues':issues,'passed':len(rows)==14 and not issues,'audit_source_sha256':sha(__file__)}
out.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n');print(out,'sha',sha(out),'checks',len(checks),'issues',len(issues),rep['passed'])
