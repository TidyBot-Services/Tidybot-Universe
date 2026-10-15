import pytest
from benchmarks.attention_harness.dev_ledger import DevV2Store, RESOURCES, GOAL, experiment_goal
from benchmarks.attention_harness.dev_experiment import snapshot
from benchmarks.attention_harness.core.dev_store import DevConflict


def test_legacy_independent_goal_projects_without_creating_or_resetting_default(tmp_path):
    store=DevV2Store(tmp_path/'legacy.sqlite',configuration={'goal_id':'legacy-run-goal',
        'limits':{key:3 for key in RESOURCES},'wall_seconds':300})
    with store.tx() as db:
        store.put(db,'experiments','e',{'experiment_id':'e','state':'planned',
            'selection':{'configuration':{'goal_id':'legacy-run-goal'}}})
        goal=store.get(db,'goals','legacy-run-goal');goal['used']['dev_calls']=2
        store.put(db,'goals','legacy-run-goal',goal)
    before=store.path.read_bytes()
    restarted=DevV2Store(store.path)
    assert restarted.goal_id=='legacy-run-goal'
    assert snapshot(restarted,'e')['resources']['used']['dev_calls']==2
    assert restarted.export_ledger()['recompute_matches'] is False  # no receipt for the deliberately corrupt fixture
    assert restarted.path.read_bytes()==before
    with store.tx() as db:assert not db.execute('SELECT 1 FROM v2_goals WHERE id=?',(GOAL,)).fetchone()


def test_missing_new_review_budget_is_zero_and_does_not_expand_existing_limits(tmp_path):
    limits={key:3 for key in RESOURCES if key!='eval_calls'}
    store=DevV2Store(tmp_path/'legacy.sqlite',configuration={'goal_id':'legacy-zero-review',
        'limits':limits,'wall_seconds':300})
    before=store.path.read_bytes()
    assert store.goal()['remaining']['eval_calls']==0
    store.ensure_goal('legacy-zero-review',{**limits,'eval_calls':0},300)
    assert store.path.read_bytes()==before
    with pytest.raises(DevConflict,match='immutable'):
        store.ensure_goal('legacy-zero-review',{**limits,'eval_calls':1},300)


def test_explicit_and_legacy_goal_must_agree():
    with pytest.raises(DevConflict,match='identity conflict'):
        experiment_goal({'goal_id':'one','selection':{'configuration':{'goal_id':'two'}}})
