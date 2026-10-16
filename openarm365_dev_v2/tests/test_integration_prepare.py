"""Controlled offline prepare checks; never touch the real integration ledger."""
import argparse
import json
from pathlib import Path

import pytest

from benchmarks.attention_harness import dev_integration as integration
from benchmarks.attention_harness.core.dev_store import DevBudgetExhausted, DevConflict
from benchmarks.attention_harness.dev_configuration import derive_cell_budget, normalize_configuration
from benchmarks.attention_harness.dev_ledger import DevV2Store, RESOURCES
from benchmarks.attention_harness.dev_planner import POLICY


def plan(wait=600):
    return normalize_configuration(dict(max_versions=2, target_depth=2,
        target_valid_executions=2, help_budget=1, policies=[POLICY],
        planner_features={'human_wait_seconds': wait}, coverage='budget_limited'))


@pytest.fixture
def prepared_env(tmp_path, monkeypatch):
    ledger=tmp_path/'controlled.sqlite'
    state=tmp_path/'host'
    store=DevV2Store(ledger)
    # Exactly the original real goal limits, copied as data; no real DB opened.
    limits=dict(dev_calls=16,advisor_calls=8,executions=32,provider_tokens=400000,
        input_tokens=400000,output_tokens=400000,sdk_calls=2560,control_steps=76800,
        help_count=24,help_credits=24,help_wait_seconds=7200,model_seconds=2880,execution_seconds=7200)
    store.ensure_goal(integration.GOAL_ID, limits, 7200)
    args=argparse.Namespace(memory_url='http://127.0.0.1:1',memory_store_id='controlled-authority',
        namespace='controlled-prepare',port=8876,seed=101,wait_seconds=600)
    monkeypatch.setattr(integration,'LEDGER',ledger)
    monkeypatch.setattr(integration,'STATE',state)
    monkeypatch.setattr(integration,'epoch',lambda: {'test_only_epoch':'controlled; not real readiness'})
    def forbidden(*a,**kw):
        raise AssertionError('offline prepare attempted goal provisioning, clock, network, provider or native dispatch')
    monkeypatch.setattr(DevV2Store,'ensure_goal',forbidden)
    monkeypatch.setattr(DevV2Store,'start_cell_clock',forbidden)
    monkeypatch.setattr(DevV2Store,'claim',forbidden)
    monkeypatch.setattr(integration.subprocess,'Popen',forbidden)
    from benchmarks.attention_harness import dev_v2_loop, memory_service_client
    monkeypatch.setattr(dev_v2_loop,'ParccClient',forbidden)
    monkeypatch.setattr(dev_v2_loop,'OpenArm365DevRunnerV2',forbidden)
    monkeypatch.setattr(memory_service_client,'MemoryServiceClient',forbidden)
    import socket
    monkeypatch.setattr(socket,'socket',forbidden)
    return store,state,args


def raw_goal(store):
    with store.tx() as db:return store.get(db,'goals',integration.GOAL_ID)


def test_prepare_600_freezes_derived_budget_and_is_idempotent_without_clock(prepared_env):
    store,state,args=prepared_env
    before=raw_goal(store)
    result=integration.prepare(args)
    frozen={str(p.relative_to(state)):p.read_bytes() for p in state.rglob('*') if p.is_file()}
    with store.tx() as db:
        event_count=db.execute('SELECT COUNT(*) FROM v2_events').fetchone()[0]
    assert integration.prepare(args)==result
    assert frozen=={str(p.relative_to(state)):p.read_bytes() for p in state.rglob('*') if p.is_file()}
    assert raw_goal(store)==before
    manifest=json.loads(next(state.glob('artifacts/devexp-*/manifest.json')).read_bytes())
    assert manifest['cell_budget']==derive_cell_budget(plan())
    assert manifest['cell_budget']['help_wait_seconds']==1320
    assert manifest['planner_features']['human_wait_seconds']==600
    assert manifest['max_versions']==manifest['target_valid_executions']==2
    assert len(manifest['cells'])==2 and manifest['goal_limits']=={**before['limits'],'eval_calls':before['limits'].get('eval_calls',0)}
    with store.tx() as db:
        assert db.execute('SELECT COUNT(*) FROM v2_events').fetchone()[0]==event_count
        assert db.execute('SELECT COUNT(*) FROM v2_goals').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM v2_experiments').fetchone()[0]==1
        assert db.execute('SELECT COUNT(*) FROM v2_cells').fetchone()[0]==2
        assert db.execute('SELECT COUNT(*) FROM v2_jobs').fetchone()[0]==0
    assert store.goal(integration.GOAL_ID)['real_started_at'] is None


@pytest.mark.parametrize('wait',[1,60])
def test_prepare_valid_short_waits(prepared_env,wait):
    store,state,args=prepared_env
    args.wait_seconds=wait
    integration.prepare(args)
    manifest=json.loads(next(state.glob('artifacts/devexp-*/manifest.json')).read_bytes())
    assert manifest['cell_budget']['help_wait_seconds']==120+2*wait
    assert manifest['planner_features']['human_wait_seconds']==wait
    assert raw_goal(store)['real_started_at'] is None


@pytest.mark.parametrize('wait',[0,601,True,False])
def test_prepare_invalid_wait_refused_before_any_immutable_artifacts(prepared_env,wait):
    store,state,args=prepared_env
    before=raw_goal(store);args.wait_seconds=wait
    with pytest.raises(ValueError,match='human_wait_seconds'):integration.prepare(args)
    assert not state.exists() and raw_goal(store)==before
    # The failed preflight must not poison a later valid preparation.
    args.wait_seconds=600
    integration.prepare(args)


# Prepared plans reserve no recording-evaluator calls (cell budget 0), so that resource cannot run short.
@pytest.mark.parametrize('resource',[r for r in RESOURCES if r!='eval_calls'])
def test_prepare_original_resource_remaining_insufficient(prepared_env,resource):
    store,state,args=prepared_env
    required=2*derive_cell_budget(plan())[resource]
    with store.tx() as db:
        goal=store.get(db,'goals',integration.GOAL_ID)
        goal['used'][resource]=goal['limits'][resource]-required+1
        store.put(db,'goals',integration.GOAL_ID,goal)
    before=raw_goal(store)
    with pytest.raises(DevBudgetExhausted,match='remaining resources'):integration.prepare(args)
    assert not state.exists() and raw_goal(store)==before


def test_prepare_original_allocation_insufficient(prepared_env):
    store,state,args=prepared_env
    with store.tx() as db:
        store.put(db,'experiments','controlled-other',dict(goal_id=integration.GOAL_ID,experiment_id='controlled-other'))
        budget=derive_cell_budget(plan())
        budget['help_wait_seconds']=7200-2*budget['help_wait_seconds']+1
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)',('controlled-cell','controlled-other',json.dumps(dict(budget=budget))))
    before=raw_goal(store)
    with pytest.raises(DevBudgetExhausted,match='allocation'):integration.prepare(args)
    assert not state.exists() and raw_goal(store)==before


def test_prepare_unknown_hold_remains_held(prepared_env):
    store,state,args=prepared_env
    with store.tx() as db:
        store.put(db,'experiments','controlled-other',dict(goal_id=integration.GOAL_ID,experiment_id='controlled-other'))
        # Prior charged waiting plus a distinct unknown claim exhaust remaining
        # capacity, even though the outstanding cell allocations still fit.
        goal=store.get(db,'goals',integration.GOAL_ID)
        goal['used']['help_wait_seconds']=2000
        store.put(db,'goals',integration.GOAL_ID,goal)
        budget={k:0 for k in RESOURCES};budget['help_wait_seconds']=4000
        db.execute('INSERT INTO v2_cells VALUES (?,?,?)',('controlled-cell','controlled-other',json.dumps(dict(experiment_id='controlled-other',budget=budget))))
        job=dict(cell='controlled-cell',reservation={'help_wait_seconds':3000})
        db.execute('INSERT INTO v2_jobs VALUES (?,?,?,?,?,?)',('unknown','controlled-cell',1,'human','needs_review',json.dumps(job)))
    before=store.export_ledger(integration.GOAL_ID)
    with pytest.raises(DevBudgetExhausted,match='remaining resources'):integration.prepare(args)
    assert not state.exists() and store.export_ledger(integration.GOAL_ID)==before


def test_prepare_retry_does_not_double_allocate_exact_capacity(prepared_env):
    store,state,args=prepared_env
    with store.tx() as db:
        goal=store.get(db,'goals',integration.GOAL_ID)
        goal['limits']={k:2*v for k,v in derive_cell_budget(plan()).items()}
        store.put(db,'goals',integration.GOAL_ID,goal)
    result=integration.prepare(args)
    assert integration.prepare(args)==result


def test_prepare_original_total_limit_insufficient(prepared_env):
    store,state,args=prepared_env
    with store.tx() as db:
        goal=store.get(db,'goals',integration.GOAL_ID)
        goal['limits']['help_wait_seconds']=2639
        store.put(db,'goals',integration.GOAL_ID,goal)
    before=raw_goal(store)
    with pytest.raises(DevBudgetExhausted,match='allocation'):integration.prepare(args)
    assert not state.exists() and raw_goal(store)==before


@pytest.mark.parametrize('field,value',[('seed',126),('port',8769),('memory_url','http://remote.invalid')])
def test_prepare_invalid_other_inputs_cannot_poison_configuration(prepared_env,field,value):
    store,state,args=prepared_env
    before=raw_goal(store)
    setattr(args,field,value)
    with pytest.raises(ValueError):integration.prepare(args)
    assert not state.exists() and raw_goal(store)==before


def test_prepare_missing_original_goal_is_not_provisioned(prepared_env):
    store,state,args=prepared_env
    with store.tx() as db:db.execute('DELETE FROM v2_goals')
    with pytest.raises(DevConflict,match='unknown goals'):integration.prepare(args)
    assert not state.exists()
    with store.tx() as db:assert db.execute('SELECT COUNT(*) FROM v2_goals').fetchone()[0]==0


def test_prepare_missing_original_ledger_is_not_created(prepared_env,monkeypatch):
    store,state,args=prepared_env
    missing=state.parent/'missing.sqlite'
    monkeypatch.setattr(integration,'LEDGER',missing)
    with pytest.raises(FileNotFoundError,match='original goal ledger missing'):integration.prepare(args)
    assert not missing.exists() and not state.exists()


def test_prepare_existing_clock_and_deadline_remain_unchanged(prepared_env):
    store,state,args=prepared_env
    import time
    with store.tx() as db:
        goal=store.get(db,'goals',integration.GOAL_ID)
        goal.update(real_started_at=time.time()-10,authorized_until=time.time()+6000)
        store.put(db,'goals',integration.GOAL_ID,goal)
    before=raw_goal(store)
    result=integration.prepare(args)
    assert integration.prepare(args)==result and raw_goal(store)==before


def test_prepare_source_verification_failure_writes_no_host_configuration(prepared_env,monkeypatch):
    store,state,args=prepared_env
    def drift():raise ValueError('controlled source drift')
    monkeypatch.setattr(integration,'epoch',drift)
    with pytest.raises(ValueError,match='source drift'):integration.prepare(args)
    assert not state.exists()


@pytest.mark.parametrize('condition',['wall','deadline','closed'])
def test_prepare_insufficient_original_wall_does_not_renew(prepared_env,condition):
    store,state,args=prepared_env
    import time
    with store.tx() as db:
        goal=store.get(db,'goals',integration.GOAL_ID)
        if condition=='wall':goal['real_started_at']=time.time()-(7200-5270)
        if condition=='deadline':goal['authorized_until']=time.time()+5270
        if condition=='closed':goal['real_finished_at']=time.time()
        store.put(db,'goals',integration.GOAL_ID,goal)
    before=raw_goal(store)
    with pytest.raises(DevBudgetExhausted,match='remaining wall'):integration.prepare(args)
    assert not state.exists() and raw_goal(store)==before


@pytest.mark.parametrize('changed',['wait_seconds','seed','port','memory_store_id'])
def test_prepare_changed_binding_preserves_previous_files(prepared_env,changed):
    store,state,args=prepared_env
    integration.prepare(args)
    frozen={str(p):p.read_bytes() for p in state.rglob('*') if p.is_file()}
    before=raw_goal(store)
    setattr(args,changed,{'wait_seconds':60,'seed':102,'port':8878,'memory_store_id':'other-authority'}[changed])
    with pytest.raises(DevConflict):integration.prepare(args)
    assert frozen=={str(p):p.read_bytes() for p in state.rglob('*') if p.is_file()}
    assert raw_goal(store)==before


def test_prepare_source_drift_refused_before_creation(prepared_env,monkeypatch):
    store,state,args=prepared_env
    integration.prepare(args)
    frozen={str(p):p.read_bytes() for p in state.rglob('*') if p.is_file()}
    monkeypatch.setattr(integration,'epoch',lambda: {'changed':'source'})
    with pytest.raises(DevConflict):integration.prepare(args)
    assert frozen=={str(p):p.read_bytes() for p in state.rglob('*') if p.is_file()}


def test_cli_wait_default_remains_60(monkeypatch):
    seen=[]
    monkeypatch.setattr('sys.argv',['dev_integration','prepare'])
    monkeypatch.setattr(integration,'prepare',lambda args:seen.append(args.wait_seconds))
    integration.main()
    assert seen==[60]
