import copy,json,os
from pathlib import Path
import pytest
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_experiment import *
@pytest.fixture
def exp(tmp_path):
    s=DevV2Store(tmp_path/'s.sqlite');e=create(s,tmp_path/'artifacts',{'request_key':'matrix-test-1'})
    return s,e,tmp_path

def test_full_matrix_fairness_and_no_session_creation(exp):
    s,e,tmp=exp;m=verify_manifest(e)
    assert len(e['cells'])==14 and validate_fairness(m)
    assert {c['task_id'] for c in e['cells']}=={'pick_place_counter_to_sink','open_cabinet'}
    assert all(c['created_at'] is None and not c['jobs'] and not c['versions'] for c in e['cells'])
    assert s.goal()['used']['dev_calls']==0 and s.goal()['used']['executions']==0
    assert m['schedule'][0]['through_version']==2
    assert all(x['through_version']==1 for x in m['schedule'][1:14])

def test_idempotent_create_and_conflict(exp):
    s,e,tmp=exp
    assert create(s,tmp/'artifacts',{'request_key':'matrix-test-1'})['experiment_id']==e['experiment_id']
    with pytest.raises(DevConflict):create(s,tmp/'artifacts',{'request_key':'matrix-test-1','seeds':[102]})
@pytest.mark.parametrize('selection',[{'seeds':[True]},{'seeds':[100]},{'tasks':['bad']},{'help_budget':3},{'policies':['bad']},{'policy_parameters':{'budget_matched_random_escalation':{}}}])
def test_precreation_invalid_zero_records(tmp_path,selection):
    s=DevV2Store(tmp_path/'s.sqlite')
    with pytest.raises(ValueError):create(s,tmp_path/'a',dict(request_key='invalid-create',**selection))
    assert list_experiments(s)==[] and s.export_ledger()['jobs']==[]
def test_policy_freeze_and_cancel(exp):
    s,e,tmp=exp
    with pytest.raises(DevConflict):switch(s,e['cells'][0]['key'],'reactive_help')
    c=cancel(s,e['experiment_id']);assert all(x['state']=='cancelled' for x in c['cells'])
    with pytest.raises(DevConflict):acquire(s,e['experiment_id'],process_identity())
def test_interactive_switch_mixed(exp):
    s,e,tmp=exp
    new=create(s,tmp/'i',dict(request_key='interactive-session',mode='interactive',tasks=['open_cabinet'],seeds=[102],policies=['autonomous']))
    c=switch(s,new['cells'][0]['key'],'demo_first');assert c['mixed_policy'] and not c['experiment_eligible']
def test_serial_live_owner_and_recovery(exp):
    s,e,tmp=exp;owner=process_identity();acquire(s,e['experiment_id'],owner)
    with pytest.raises(DevConflict):acquire(DevV2Store(s.path),e['experiment_id'],owner)
    key=e['cells'][0]['key'];s.update_cell(key,state='running',owner=owner)
    j=s.reserve(key,1,'model',{'dev_calls':1});s.claim(j['job_id'],owner)
    dead={'pid':999999999,'start_ticks':'0','boot_id':'absent'}
    with s.tx() as db:
        ee=s.get(db,'experiments',e['experiment_id']);ee['owner']=dead;s.put(db,'experiments',e['experiment_id'],ee)
        c=s.get(db,'cells',key);c['owner']=dead;s.put(db,'cells',key,c)
    recovered=acquire(s,e['experiment_id'],owner)
    assert recovered['state']=='needs_review' and s.cell(key)['state']=='needs_review'
    assert s.goal()['reserved']['dev_calls']==1

def test_manifest_drift_refused(exp):
    s,e,tmp=exp;p=Path(e['manifest']['uri']);p.chmod(0o644);p.write_text('{}')
    with pytest.raises(DevConflict):verify_manifest(e)
def test_denominator_tamper_refused(exp):
    s,e,tmp=exp;m=verify_manifest(e);m['cells'].pop()
    with pytest.raises(ValueError):validate_fairness(m)
