import json,sqlite3
from pathlib import Path
from benchmarks.attention_harness.dev_failure_analysis import classify

def test_historical_real_samples():
 from benchmarks.attention_harness.workspace_paths import get_str
 db=sqlite3.connect('file:'+get_str('test.dev_v2_db')+'?mode=ro',uri=True)
 samples=[]
 for key,p in db.execute("SELECT id,payload FROM v2_cells WHERE exp='devexp-4274c67ebf659455e3f48163'"):
  c=json.loads(p)
  for v, in db.execute('SELECT payload FROM v2_versions WHERE cell=?',(key,)):
   version=json.loads(v);row=db.execute("SELECT state,payload FROM v2_jobs WHERE cell=? AND version=? AND kind='execute'",(key,version['version'])).fetchone();job={**json.loads(row[1]),'state':row[0]} if row else None
   samples.append(classify(version,job))
 db.close()
 assert any(x['category']=='sdk_usage' and 'list' in str(x['detail']) for x in samples)
 assert any(x['category']=='planning_execution' and 'PlanningRejected' in str(x['detail']) for x in samples)
 assert any(x['category']=='code_validation' for x in samples)

def test_unknown_never_forced_and_completed_false(tmp_path):
 from openarm365_dev_v2.tests.test_iteration import setup
 from openarm365_dev_v2.tests.fakes import Client,Runner
 from benchmarks.attention_harness.dev_experiment import run
 from benchmarks.attention_harness.dev_loop import immutable
 class Normal(Runner):
  def execute(self,r):
   result=super().execute(r);ep=Path(result['artifact_dir']);result['status']='completed'
   old=json.loads(Path(result['artifacts']['trace']['uri']).read_text());trace={**old,'status':'completed','sdk_events':[],'error':None}
   result['artifacts']['trace']=immutable(ep/'public-trace.json',trace)
   native=json.loads(Path(result['artifacts']['native_result']['uri']).read_text());native['status']='completed'
   result['native_evaluator']=native
   result['artifacts']['native_result']=immutable(ep/'normal-native.json',native)
   return result
 s,e,b=setup(tmp_path);f=run(s,e['experiment_id'],max_cells=1,budget_file=b,client=Client(),runner_factory=Normal)
 c=f['cells'][0];v=c['versions'][0];j=next(j for j in c['jobs'] if j['kind']=='execute' and j['version']==1)
 assert classify(v,j)['category']=='task_incomplete'
