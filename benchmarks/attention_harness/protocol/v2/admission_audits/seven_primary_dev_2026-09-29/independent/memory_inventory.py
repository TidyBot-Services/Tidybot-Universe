import sqlite3,pathlib,json,hashlib
D=pathlib.Path('/home/truares/桌面'); out=[]
for suite,d,mid in [('robosuite','attentionbench-m5-20260928/robosuite-current-v3/authority','candidate:attempt:attention-robosuite-cube_lift-seed101-68b4cbf67d66:0'),('robocasa','attentionbench-depth-memory-risk-20260929/robocasa-memory-v3/authority','candidate:m5:robocasa-counter-to-sink-public-sdk-v3')]:
 p=D/d/'attention_memory.sqlite3';c=sqlite3.connect('file:'+str(p)+'?mode=ro',uri=True)
 def read(t,col='id'):
  row=c.execute(f'select payload from {t} where {col}=?',(mid,)).fetchone(); return json.loads(row[0]) if row else None
 m=read('memories');plan=read('memory_v2_plans','memory_id');prov=read('memory_v2_provenance','memory_id')
 row={'suite':suite,'source_sqlite':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'integrity_check':c.execute('pragma integrity_check').fetchone()[0],'memory_id':mid,'status':m['status'],'version':m['version'],'expires_at':m['expires_at'],'source_run_id':prov['source_run_id'],'source_trace_id':prov['source_trace_id'],'source_attempt_id':prov['source_attempt_id'],'scope':plan,'provenance_artifact_sha256':prov['artifact_sha256'],'raw_evidence_refs':prov['raw_evidence_refs'],'grants':[json.loads(x[0]) for x in c.execute('select payload from memory_v2_use_grants where memory_id=?',(mid,))],'uses':[json.loads(x[0]) for x in c.execute('select payload from memory_uses') if json.loads(x[0]).get('memory_id')==mid]}
 out.append(row)
p=D/'attentionbench-seven-freeze-20260929/independent/memory_inventory.json';p.write_text(json.dumps({'schema_version':'attentionbench.independent-memory-inventory.v1','read_only':True,'memories':out},ensure_ascii=False,indent=2)+'\n')
print(p,'sha',hashlib.sha256(p.read_bytes()).hexdigest());print(json.dumps([{k:r[k] for k in ['suite','source_sqlite','sha256','memory_id','status','version','expires_at','source_run_id']} for r in out],ensure_ascii=False,indent=2))
