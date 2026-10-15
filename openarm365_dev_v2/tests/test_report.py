import json
from pathlib import Path
import pytest
from benchmarks.attention_harness.dev_report import *
from openarm365_dev_v2.tests.test_loop import setup
from openarm365_dev_v2.tests.fakes import Client,Runner
from benchmarks.attention_harness.dev_experiment import run,create

def test_wilson_known():
    assert wilson(5,10)==pytest.approx([.236593090512564,.763406909487436])
    assert wilson(0,0) is None
    assert wilson(0,1)==pytest.approx([0,.7934506856227626])
def test_all_planned_cells_kept(tmp_path):
    s,e,b=setup(tmp_path);r=report(s,e['experiment_id']);g=r['groups'][0]
    assert g['planned']==1 and g['evaluated']==0 and g['native_false']==0 and g['unevaluated']==1
    assert g['wilson95'] is None

def test_false_independent_native_and_corruption(tmp_path):
    s,e,b=setup(tmp_path);run(s,e['experiment_id'],max_cells=1,budget_file=b,client=Client(),runner_factory=Runner)
    r=report(s,e['experiment_id']);assert r['groups'][0]['native_false']==1 and r['groups'][0]['evaluated']==1
    assert report_csv(r).startswith('task_id,policy_id')
    ref=r['cells'][0]['evidence_refs'][-1];p=Path(ref['uri']);p.chmod(0o644);p.write_text('{}')
    r=report(s,e['experiment_id']);assert r['groups'][0]['evaluated']==0 and r['groups'][0]['unknown_evidence']==1 and r['groups'][0]['native_false']==0

def test_tasks_aggregated_separately_and_offline_first_success(tmp_path):
    s,e,b=setup(tmp_path)
    matrix=create(s,tmp_path/'matrix',{'request_key':'report-two-tasks','policies':['autonomous']})
    class Success(Runner):native_success=True
    run(s,matrix['experiment_id'],max_cells=2,budget_file=b,client=Client(),runner_factory=Success)
    r=report(s,matrix['experiment_id']);assert len(r['groups'])==2
    assert all(g['success']==1 and g['first_success_versions']==[1] and g['evaluated']==1 for g in r['groups'])
    assert r['resource_ledger']['recompute_matches']
