"""Ledger policy tests with no provider, simulator, or robot model."""
import json
import csv
import io

import pytest

from benchmarks.attention_harness.core.dev_store import DevBudgetExhausted, DevConflict
from benchmarks.attention_harness.dev_ledger import DevV2Store, GOAL, LIMITS, RESOURCES
from benchmarks.attention_harness.dev_ui_server import budget_preview
from benchmarks.attention_harness.free_models import FREE_MODELS, is_unmetered_model


def ledger(tmp_path, *, eval_model='parcc/Qwen', advisor_model=None, model_seconds=30):
    store=DevV2Store(tmp_path/'ledger.sqlite')
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'max_versions':5}))
    owner={'pid':123}
    limits=dict(LIMITS,eval_calls=0,provider_tokens=0,input_tokens=0,output_tokens=0,model_seconds=model_seconds)
    budget=dict(limits)
    with store.tx() as db:
        store.put(db,'goals',GOAL,dict(limits=limits,used={k:0 for k in RESOURCES},real_started_at=None,wall_limit=1000,opening=[]))
        selection={'eval_agent':{'enabled':True,'model':eval_model}}
        if advisor_model is not None:selection['advisor_proxy']={'model':advisor_model}
        store.put(db,'experiments','e',dict(cancelled=False,selection=selection,manifest={'uri':str(manifest)}))
        store.put(db,'cells','a',dict(experiment_id='e',state='running',owner=owner,used={k:0 for k in RESOURCES},budget=budget))
    return store,owner


@pytest.mark.parametrize('proxy,planner,expected,rejected',[
    (None, False, 'parcc/GLM', 'parcc/Qwen'),
    ({'enabled':False,'model':'parcc/Qwen'}, False, 'parcc/GLM', 'parcc/Qwen'),
    ({'enabled':False,'model':'parcc/Qwen'}, True, 'parcc/GLM', 'parcc/Qwen'),
    ({'enabled':True,'model':'parcc/Qwen'}, False, 'parcc/Qwen', 'parcc/GLM'),
    ({'enabled':True,'model':'parcc/Qwen'}, True, 'parcc/Qwen', 'parcc/GLM'),
    ({'enabled':True,'model':'parcc/GLM'}, False, 'parcc/GLM', 'parcc/Qwen'),
])
def test_advisor_model_matches_frozen_route(tmp_path,proxy,planner,expected,rejected):
    store,_=ledger(tmp_path)
    with store.tx() as db:
        experiment=store.get(db,'experiments','e')
        if proxy is not None:experiment['selection']['advisor_proxy']=proxy
        store.put(db,'experiments','e',experiment)
        cell=store.get(db,'cells','a')
        if planner:cell['planner_features']={'human_wait_seconds':1}
        store.put(db,'cells','a',cell)
    reservation={'advisor_calls':1}
    with pytest.raises(DevConflict,match='model differs from fixed provider configuration'):
        store.reserve('a',1,'advisor',reservation,model_name=rejected)
    assert not store.cell('a')['jobs']
    assert store.reserve('a',1,'advisor',reservation,model_name=expected)['kind']=='advisor'


@pytest.mark.parametrize('kind,model,call_key',[
    ('model','parcc/GLM','dev_calls'),('advisor','parcc/GLM','advisor_calls'),
    ('advisor','parcc/Qwen','advisor_calls'),
    ('eval','parcc/Qwen','eval_calls')])
def test_free_model_zero_token_goal_reserves_settles_and_exports(tmp_path,kind,model,call_key):
    store,owner=ledger(tmp_path,advisor_model=model if kind=='advisor' else None)
    reservation={call_key:1,'provider_tokens':100,'input_tokens':70,'output_tokens':30,'model_seconds':1}
    if kind=='eval':
        store.reserve_check('a',reservation,unmetered_eval=True,model_name=model)
    else:store.reserve_check('a',reservation,model_name=model)
    job=store.reserve('a',1,kind,reservation,unmetered_eval=kind=='eval',model_name=model)
    store.claim(job['job_id'],owner)
    actual={**reservation,'provider_tokens':7,'input_tokens':5,'output_tokens':2,'model_seconds':0.25}
    store.settle(job['job_id'],actual,{'usage_accounting':'measured'})
    result=store.export_ledger()
    assert result['recompute_matches'] and result['jobs'][0]['actual']==actual
    assert result['goal']['used']['provider_tokens']==7
    assert result['goal']['used']['input_tokens']==5
    assert result['goal']['used']['output_tokens']==2
    assert result['goal']['remaining']['provider_tokens']==0
    assert store.cell('a')['used']['provider_tokens']==7
    assert result['goal']['used']['model_seconds']==0.25
    if kind=='eval':
        assert result['goal']['used']['eval_calls']==1
        assert result['eval_usage']==result['recomputed_eval_usage']
        assert result['eval_usage']['eval_calls']==1 and result['eval_usage']['provider_tokens']==7
        assert result['eval_cost']==result['eval_usage']
        from benchmarks.attention_harness.dev_report import report_csv
        table=list(csv.DictReader(io.StringIO(report_csv({'groups':[{'wilson95':None,'resources':result['goal']['used'],'eval_cost':result['eval_cost']}]}))))
        assert table[0]['eval_calls_used']=='1' and table[0]['eval_provider_tokens_used']=='7'
    else:assert result['goal']['used'][call_key]==1


@pytest.mark.parametrize('resource',('help_credits','help_count','advisor_calls','hint_human_seconds','help_wait_seconds','executions','sdk_calls','control_steps','execution_seconds'))
def test_non_cost_resources_still_limited(tmp_path,resource):
    store,_=ledger(tmp_path)
    with store.tx() as db:
        goal=store.get(db,'goals',GOAL);goal['limits'][resource]=0;store.put(db,'goals',GOAL,goal)
    kind='advisor' if resource in {'help_credits','help_count','advisor_calls','hint_human_seconds','help_wait_seconds'} else 'execute'
    amount={resource:1,'provider_tokens':5} if kind=='advisor' else {resource:1}
    with pytest.raises(DevBudgetExhausted):store.reserve('a',1,kind,amount,model_name='parcc/GLM' if kind=='advisor' else None)


def test_eval_model_seconds_still_limited(tmp_path):
    store,_=ledger(tmp_path,model_seconds=0)
    with pytest.raises(DevBudgetExhausted):store.reserve('a',1,'eval',{'eval_calls':1,'model_seconds':1},unmetered_eval=True,model_name='parcc/Qwen')


def test_eval_call_safety_cap(tmp_path):
    store,owner=ledger(tmp_path)
    for version in range(1,11):
        reservation={'eval_calls':1,'model_seconds':1}
        job=store.reserve('a',version,'eval',reservation,unmetered_eval=True,model_name='parcc/Qwen')
        store.claim(job['job_id'],owner);store.settle(job['job_id'],{'eval_calls':1,'model_seconds':0},{})
    with pytest.raises(DevConflict,match=r'Eval safety call cap reached \(10\)'):
        store.reserve('a',11,'eval',{'eval_calls':1,'model_seconds':1},unmetered_eval=True,model_name='parcc/Qwen')
    assert store.cell('a')['state']=='needs_review'


def test_other_eval_model_returns_to_metered_limits(tmp_path):
    store,_=ledger(tmp_path,eval_model='other')
    assert not is_unmetered_model('other') and FREE_MODELS=={'parcc/GLM','parcc/Qwen'}
    with pytest.raises(DevBudgetExhausted):store.reserve('a',1,'eval',{'eval_calls':1,'provider_tokens':5,'input_tokens':4,'output_tokens':1,'model_seconds':1},model_name='other')
    with pytest.raises(DevConflict,match='fixed free Qwen'):
        store.reserve('a',1,'eval',{'eval_calls':1,'model_seconds':1},unmetered_eval=True,model_name='other')


@pytest.mark.parametrize('kind',('model','advisor'))
def test_other_dev_or_advisor_model_returns_to_metered_tokens(tmp_path,monkeypatch,kind):
    store,_=ledger(tmp_path,advisor_model='other' if kind=='advisor' else None)
    if kind=='model':
        from benchmarks.attention_harness.dev_experiment import MODEL
        monkeypatch.setitem(MODEL,'model','other')
    with pytest.raises(DevBudgetExhausted):
        store.reserve('a',1,kind,{'provider_tokens':1},model_name='other')


def test_free_usage_does_not_consume_later_paid_token_quota(tmp_path,monkeypatch):
    store,owner=ledger(tmp_path)
    with store.tx() as db:
        goal=store.get(db,'goals',GOAL);goal['limits']['provider_tokens']=5;store.put(db,'goals',GOAL,goal)
        cell=store.get(db,'cells','a');cell['budget']['provider_tokens']=5;store.put(db,'cells','a',cell)
    free=store.reserve('a',1,'model',{'dev_calls':1,'provider_tokens':100},model_name='parcc/GLM')
    store.claim(free['job_id'],owner);store.settle(free['job_id'],{'dev_calls':1,'provider_tokens':7},{})
    assert store.goal()['used']['provider_tokens']==7 and store.goal()['remaining']['provider_tokens']==5
    from benchmarks.attention_harness.dev_experiment import MODEL
    monkeypatch.setitem(MODEL,'model','other')
    paid=store.reserve('a',2,'model',{'dev_calls':1,'provider_tokens':5},model_name='other')
    store.claim(paid['job_id'],owner);store.settle(paid['job_id'],{'dev_calls':1,'provider_tokens':5},{})
    exported=store.export_ledger()
    assert exported['recompute_matches'] and exported['goal']['used']['provider_tokens']==12
    assert exported['goal']['remaining']['provider_tokens']==0


def test_old_job_without_free_marker_recomputes_as_before(tmp_path):
    store,owner=ledger(tmp_path)
    with store.tx() as db:
        goal=store.get(db,'goals',GOAL);goal['limits']['provider_tokens']=5;store.put(db,'goals',GOAL,goal)
        cell=store.get(db,'cells','a');cell['budget']['provider_tokens']=5;store.put(db,'cells','a',cell)
    job=store.reserve('a',1,'model',{'dev_calls':1,'provider_tokens':5})
    store.claim(job['job_id'],owner);store.settle(job['job_id'],{'dev_calls':1,'provider_tokens':3},{})
    reopened=DevV2Store(store.path).export_ledger()
    assert reopened['recompute_matches'] and reopened['goal']['used']['provider_tokens']==3


def test_old_metered_eval_reservation_remains_readable(tmp_path):
    store,owner=ledger(tmp_path)
    with store.tx() as db:
        goal=store.get(db,'goals',GOAL);goal['limits'].update(eval_calls=1,provider_tokens=5);store.put(db,'goals',GOAL,goal)
        cell=store.get(db,'cells','a');cell['budget'].update(eval_calls=1,provider_tokens=5);store.put(db,'cells','a',cell)
    job=store.reserve('a',1,'eval',{'eval_calls':1,'provider_tokens':5,'model_seconds':1})
    store.claim(job['job_id'],owner)
    store.settle(job['job_id'],{'eval_calls':1,'provider_tokens':3,'model_seconds':0},{})
    reopened=DevV2Store(store.path).export_ledger()
    assert reopened['recompute_matches']
    assert reopened['goal']['used']['eval_calls']==1 and reopened['goal']['used']['provider_tokens']==3
    assert reopened['eval_usage']['eval_calls']==0 and reopened['eval_cost']['eval_calls']==1


def test_preview_displays_free_cost_and_limited_time():
    preview=budget_preview(dict(tasks=['open_cabinet'],seeds=[101],policies=['autonomous'],
        max_versions=2,target_depth=2,target_valid_executions=2,eval_agent={'enabled':True}))
    assert preview['eval_accounting']=='不计限额，仅记录'
    assert set(preview['model_token_accounting'].values())=={'不计限额，仅记录'}
    assert preview['required_per_cell']['eval_calls']==2
    assert preview['required_per_cell']['provider_tokens']>0
    assert preview['required_per_cell']['model_seconds']>0
    assert preview['eval_safety_call_cap']==10
