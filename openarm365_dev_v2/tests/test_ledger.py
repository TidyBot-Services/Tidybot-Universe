import json,time
from concurrent.futures import ThreadPoolExecutor
import pytest
from benchmarks.attention_harness.dev_ledger import *
@pytest.fixture
def store(tmp_path):
    s=DevV2Store(tmp_path/'store.sqlite');owner={'pid':123}
    with s.tx() as db:
        s.put(db,'experiments','e',{'cancelled':False})
        for cell in ['a','b']:
            s.put(db,'cells',cell,{'experiment_id':'e','state':'running','owner':owner,'used':{k:0 for k in RESOURCES},'budget':dict(LIMITS,dev_calls=2,executions=2,provider_tokens=100000)})
    return s,owner

def test_idempotence_and_unknown(store):
    s,owner=store;j=s.reserve('a',1,'model',{'dev_calls':1,'provider_tokens':1000})
    assert j==s.reserve('a',1,'model',j['reservation'])
    s.claim(j['job_id'],owner)
    with pytest.raises(DevConflict):s.claim(j['job_id'],owner)
    s.unknown(j['job_id'],'response missing')
    assert s.goal()['reserved']['provider_tokens']==1000
    assert DevV2Store(s.path).goal()['reserved']['dev_calls']==1
    with pytest.raises(DevConflict):s.release_unclaimed(j['job_id'])

def test_settle_and_recompute(store):
    s,owner=store;j=s.reserve('a',1,'model',{'dev_calls':1,'provider_tokens':1000});s.claim(j['job_id'],owner)
    actual={'dev_calls':1,'provider_tokens':200};s.settle(j['job_id'],actual,{'usage':'known'})
    s.settle(j['job_id'],actual,{'usage':'known'})
    with pytest.raises(DevConflict):s.settle(j['job_id'],{'dev_calls':1,'provider_tokens':201},{'usage':'known'})
    assert s.goal()['used']['provider_tokens']==200
    assert s.export_ledger()['recompute_matches']

def test_global_dev_plus_advisor_token_limit(store):
    s,owner=store
    with s.tx() as db:
        g=s.get(db,'goals',GOAL);g['used']['provider_tokens']=499500;s.put(db,'goals',GOAL,g)
    j=s.reserve('a',1,'model',{'dev_calls':1,'provider_tokens':400})
    with pytest.raises(DevBudgetExhausted):s.reserve('b',1,'advisor',{'advisor_calls':1,'provider_tokens':101})
    assert len(s.export_ledger()['jobs'])==1

def test_cell_budget_not_reset(store):
    s,owner=store
    for v in [1,2]:
        j=s.reserve('a',v,'model',{'dev_calls':1});s.claim(j['job_id'],owner);s.settle(j['job_id'],{'dev_calls':1},{})
    with pytest.raises(DevBudgetExhausted):s.reserve('a',3,'model',{'dev_calls':1})

def test_complete_actual_required(store):
    s,owner=store;j=s.reserve('a',1,'model',{'dev_calls':1,'provider_tokens':100});s.claim(j['job_id'],owner)
    with pytest.raises(DevConflict):s.settle(j['job_id'],{'dev_calls':1},{})
    with pytest.raises(DevConflict):s.settle(j['job_id'],{'dev_calls':1,'provider_tokens':101},{})
    assert s.goal()['reserved']['provider_tokens']==100

def test_atomic_concurrent_budget(store):
    s,owner=store
    with s.tx() as db:
        g=s.get(db,'goals',GOAL);g['used']['dev_calls']=27;s.put(db,'goals',GOAL,g)
    def reserve(c):
        try:return s.reserve(c,1,'model',{'dev_calls':1})
        except DevBudgetExhausted:return None
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(reserve,['a','b']))
    assert sum(x is not None for x in results)==1

def test_opening_entries_idempotent(store,tmp_path):
    from benchmarks.attention_harness.dev_loop import immutable
    s,owner=store;ref=immutable(tmp_path/'probe.json',dict(executions=1,sdk_calls=2,control_steps=0,execution_seconds=10,real_started_at=time.time()-10))
    s.opening(ref);s.opening(ref)
    assert s.goal()['used']['executions']==1
    assert s.goal()['wall_used']>=10
    assert s.export_ledger()['recompute_matches']

def test_old_six_resource_sessions_unchanged(tmp_path):
    from benchmarks.attention_harness.core.store import AttentionStore
    s=AttentionStore(tmp_path/'old.sqlite');budget=dict(executions=2,model_calls=2,tokens=60000,sdk_calls=80,execution_seconds=240,wall_seconds=600)
    old=s.create_dev_session(locked={},budget=budget,artifact_dir=tmp_path,model='parcc/GLM',initial_source='print(1)',request_key='old-test')
    assert set(s.get_dev_session(old['session_id'])['budget'])==set(budget)

def test_finished_wall_frozen_and_goal_cannot_reopen(store):
    s,owner=store;now=time.time()
    with s.tx() as db:
        g=s.get(db,'goals',GOAL);g.update(real_started_at=now-100,real_finished_at=now-10);s.put(db,'goals',GOAL,g)
    assert s.goal()['wall_used']==pytest.approx(90)
    with pytest.raises(DevBudgetExhausted,match='closed'):s.reserve('a',1,'model',{'dev_calls':1})
