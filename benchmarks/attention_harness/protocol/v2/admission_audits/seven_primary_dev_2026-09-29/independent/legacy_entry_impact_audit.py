import sys,json,hashlib
from pathlib import Path
U=Path('/home/truares/桌面/Tidybot-Universe-attention-native');sys.path.insert(0,str(U))
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
D=U.parent; rows=[]
plans=[D/'attentionbench-depth-qualifying-20260929/profile_plan.json',D/'attentionbench-fixed-base-profile-20260929/engineering_v112_isolated_continuation/profile_plan.json']
for p in plans:
 x=json.loads(p.read_text())
 for s in x['slots']:
  if p==plans[1] and s['suite']!='robocasa':continue
  a=s['argv']; arg=lambda k:a[a.index(k)+1]
  try:
   entry,_=inspect_formal_entry(suite=arg('--suite'),task_id=arg('--task'),seed=int(arg('--seed')),policy_id=arg('--attention-policy'),code=Path(arg('--code')),approved_policy_sha256=arg('--approved-policy-sha256'),config=Path(arg('--config')),approved_config_sha256=arg('--approved-config-sha256'),max_attempts=int(arg('--max-attempts')),assistance_credits=int(arg('--assistance-credits')),token_limit=int(arg('--token-limit')),assistance_mode=arg('--assistance-mode'),human_deadline_seconds=float(arg('--human-deadline-seconds')),overall_deadline_seconds=float(arg('--overall-deadline-seconds')))
   expected=arg('--expected-entry-sha256'); row={'suite':s['suite'],'task_id':s['task_id'],'seed':s['seed'],'expected_entry_sha256':expected,'current_recomputed_sha256':entry['sha256'],'matches':entry['sha256']==expected}
  except Exception as e:row={'suite':s['suite'],'seed':s['seed'],'matches':False,'error':repr(e)}
  rows.append(row)
paths=['benchmarks/attention_harness/formal_entry.py','benchmarks/attention_harness/formal_attention_run.py','benchmarks/attention_harness/formal_attention_cli.py','benchmarks/attention_harness/formal_memory_contract.py']
rep={'schema_version':'attentionbench.independent-legacy-entry-impact.v1','reviewer_id':'independent_admission_auditor','simulator_runs':0,'checked_entries':len(rows),'matches':sum(r['matches'] for r in rows),'all_passed':len(rows)==50 and all(r['matches'] for r in rows),'implementation_sha256':{p:hashlib.sha256((U/p).read_bytes()).hexdigest() for p in paths},'rows':rows,'interpretation':'Historical no-contract M1 identity hashes remain unchanged. New contract branch cannot be relabeled as executed historical Memory evidence.'}
p=D/'attentionbench-seven-freeze-20260929/independent/legacy_entry_impact_audit.json';p.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n');print(p,'sha',hashlib.sha256(p.read_bytes()).hexdigest());print('entries',len(rows),'matches',rep['matches']); print([r for r in rows if not r['matches']])
