"""Offline Dev tier and frozen formal budget checks."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from openarm365_dev_v2 import dev_config, formal_config
from openarm365_dev_v2.runner import OpenArm365DevRunnerV2
from openarm365_dev_v2.diag_budget import (SOURCE, MARKER, DEV_TIER_BUDGET_SCALE,
    dev_tier_budget, formal_task_limits, task_limits)
from benchmarks.attention_harness import dev_tasks,dev_experiment
from benchmarks.attention_harness.dev_configuration import normalize_configuration, derive_cell_budget
from benchmarks.attention_harness.dev_ledger import DevV2Store, LIMITS, RESOURCES, task_goal_limits
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest


def test_dev_default_and_prompt_and_explicit_probe(monkeypatch):
    monkeypatch.delenv(SOURCE, raising=False)
    monkeypatch.delenv(MARKER, raising=False)
    assert DEV_TIER_BUDGET_SCALE == 4
    assert task_limits() == {'sdk_calls': 320, 'control_steps': 9600, 'attempt_seconds': 1920}
    assert dev_tier_budget() == {'scale': 4, 'sdk_calls': 320, 'control_steps': 9600,
                                 'attempt_seconds': 1920, 'source': 'dev_tier_default'}
    task=dev_tasks.get_task('open_cabinet')
    assert tuple(task.public()['sdk'][k] for k in ('sdk_calls','control_steps','attempt_seconds')) == (320,9600,1920)
    assert 'At most 320 TOTAL SDK calls, 9600 control steps, 1920s.' in task.system_prompt()
    assert 'At most 80 TOTAL SDK calls, 2400 control steps, 480s.' in dev_tasks.SDK_PROMPT
    assert tuple(dev_tasks.SDK[k] for k in ('sdk_calls','control_steps','attempt_seconds')) == (80,2400,480)
    monkeypatch.setenv(MARKER,'1');monkeypatch.setenv(SOURCE,'2')
    assert task_limits() == {'sdk_calls': 160, 'control_steps': 4800, 'attempt_seconds': 960}
    with pytest.raises(ValueError,match='legacy budget tier'):
        task_limits('legacy')
    assert dev_tier_budget() is None
    assert 'At most 160 TOTAL SDK calls, 4800 control steps, 960s.' in task.system_prompt()


def test_formal_config_bytes_and_rejection(monkeypatch):
    monkeypatch.delenv(SOURCE, raising=False)
    monkeypatch.delenv(MARKER, raising=False)
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs:None)
    lock={'path':'fixture','sha256':'fixture'}
    dev=dev_config.make_dev_config('pick_place_counter_to_sink',102,lock)
    assert tuple(dev[k] for k in ('sdk_call_limit','horizon','attempt_wall_seconds')) == (320,9600,1920)
    assert dev_config.validate_dev_config(dev)==dev
    assert formal_config.validate_native_config(dev)==dev
    runner=OpenArm365DevRunnerV2(service_source_root='',sim_python='',sim_source_root='',task_source_root='',asset_manifest='')
    assert runner.budgets(SimpleNamespace(sdk_call_limit=320,effective_attempt_deadline_seconds=1920),dev)==(1920,320)
    with pytest.raises(ValueError):runner.budgets(SimpleNamespace(sdk_call_limit=321,effective_attempt_deadline_seconds=1920),dev)
    formal=formal_config.make_formal_config('pick_place_counter_to_sink',102,lock,
        attempt_wall_seconds=120,whole_case_wall_seconds=300,sdk_call_limit=40)
    assert formal_config.validate_formal_config(formal)==formal
    assert tuple(formal[k] for k in ('sdk_call_limit','horizon','attempt_wall_seconds','whole_case_wall_seconds')) == (40,2400,120,300)
    raw=json.dumps(formal,sort_keys=True,separators=(',',':')).encode()
    assert hashlib.sha256(raw).hexdigest()=='5b12aa4630e9bd6be7099cbc9e1ee6892057a3c4445b4c7c971a39959f38e913'
    assert formal_task_limits()=={'sdk_calls':80,'control_steps':2400,'attempt_seconds':480}
    with pytest.raises(ValueError):formal_config.make_formal_config('pick_place_counter_to_sink',102,lock,
        attempt_wall_seconds=1920,whole_case_wall_seconds=1920,sdk_call_limit=320)
    with pytest.raises(ValueError):formal_config.validate_formal_config(dict(formal,horizon=9600))


def test_explicit_legacy_tier_preserves_old_envelope_and_is_frozen(monkeypatch):
    monkeypatch.delenv(SOURCE, raising=False)
    monkeypatch.delenv(MARKER, raising=False)
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs:None)
    lock={'path':'fixture','sha256':'fixture'}
    legacy=dev_config.make_dev_config('open_cabinet',101,lock,budget_tier='legacy')
    dev=dev_config.make_dev_config('open_cabinet',101,lock,budget_tier='dev')
    assert 'budget_tier' not in legacy
    assert dev['budget_tier']=='dev'
    assert tuple(legacy[k] for k in ('sdk_call_limit','horizon','attempt_wall_seconds','whole_case_wall_seconds'))==(80,2400,480,1800)
    assert dev_config.validate_dev_config(legacy)==legacy
    assert dev_config.validate_dev_config(dev)==dev
    assert hashlib.sha256(json.dumps(legacy,sort_keys=True).encode()).digest()!=hashlib.sha256(json.dumps(dev,sort_keys=True).encode()).digest()
    with pytest.raises(ValueError):dev_config.validate_dev_config(dict(legacy,budget_tier='dev'))
    with pytest.raises(ValueError):dev_config.validate_dev_config(dict(dev,budget_tier='legacy'))
    with pytest.raises(ValueError,match='formal boundary refuses'):
        dev_config.make_dev_config('open_cabinet',101,lock,budget_boundary='formal',budget_tier='dev')
    old=normalize_configuration({'budget_tier':'legacy'})
    new=normalize_configuration({})
    assert old['attempt_wall_seconds']==480 and 'budget_tier' not in old
    assert new['attempt_wall_seconds']==1920 and new['budget_tier']=='dev'
    assert tuple(derive_cell_budget(old)[k] for k in ('sdk_calls','control_steps'))==(240,7200)
    assert tuple(derive_cell_budget(new)[k] for k in ('sdk_calls','control_steps'))==(960,28800)


def test_dev_model_deadline_only_and_frozen_legacy_arithmetic():
    from benchmarks.attention_harness.dev_configuration import worst_slot
    from benchmarks.attention_harness.dev_experiment import slot_wall_seconds
    from benchmarks.attention_harness.dev_probe import probe_budget
    from benchmarks.attention_harness.eval_agent import default_config as eval_default
    dev = normalize_configuration({'eval_agent': eval_default(True), 'help_budget': 5})
    old_dev = normalize_configuration({'model_reserve_seconds': 120, 'eval_agent': eval_default(True), 'help_budget': 5})
    legacy = normalize_configuration({'budget_tier': 'legacy', 'eval_agent': eval_default(True), 'help_budget': 5})
    assert (dev['model_reserve_seconds'], old_dev['model_reserve_seconds'], legacy['model_reserve_seconds']) == (240, 120, 120)
    assert derive_cell_budget(dev)['model_seconds'] == 5*240 + (5+3)*120
    assert derive_cell_budget(dev)['model_seconds'] - derive_cell_budget(old_dev)['model_seconds'] == 5*120
    assert derive_cell_budget(dev)['help_wait_seconds'] == derive_cell_budget(old_dev)['help_wait_seconds']
    assert worst_slot(dev) - worst_slot(old_dev) == 120
    assert slot_wall_seconds({'schema':'attentionbench.dev-experiment.v3', **dev}) == worst_slot(dev)
    assert [probe_budget('dev', value)['model_seconds'] for value in (120, 240)] == [120, 240]
    assert probe_budget('legacy')['model_seconds'] == 120
    for tier, seconds in [('dev', 241), ('legacy', 240)]:
        with pytest.raises(ValueError, match='SDK/provider deadline'):
            normalize_configuration({'budget_tier': tier, 'model_reserve_seconds': seconds})


def test_v2_budget_authorization_is_bound_to_tier(tmp_path):
    path=tmp_path/'budget.json'
    original={'goal':dev_experiment.GOAL,'limits':{'dev_calls':28,'advisor_calls':14,
        'provider_tokens':500000,'executions':28,'wall_seconds':7200}}
    path.write_text(json.dumps(original))
    assert dev_experiment.validate_budget_file(path,{'selection':{}})==original
    with pytest.raises(ValueError,match='tier'):
        dev_experiment.validate_budget_file(path,{'budget_tier':'dev','selection':{}})
    expanded={**original,'budget_tier':'dev','limits':{**original['limits'],'wall_seconds':28800}}
    path.write_text(json.dumps(expanded))
    assert dev_experiment.validate_budget_file(path,{'budget_tier':'dev','selection':{}})==expanded
    with pytest.raises(ValueError,match='tier'):
        dev_experiment.validate_budget_file(path,{'selection':{}})


def test_ledger_reserves_and_settles_full_dev_attempt(tmp_path):
    assert LIMITS['sdk_calls']==2240 and LIMITS['control_steps']==67200
    assert LIMITS['execution_seconds']==7200 and LIMITS['dev_calls']==28 and LIMITS['provider_tokens']==500000
    assert (task_goal_limits()['sdk_calls'],task_goal_limits()['control_steps'],task_goal_limits()['execution_seconds'])==(8960,268800,28800)
    p=normalize_configuration(dict(max_versions=1,target_depth=1,target_valid_executions=1,help_budget=0))
    b=derive_cell_budget(p)
    assert (b['sdk_calls'],b['control_steps'],b['execution_seconds'])==(320,9600,1980)
    store=DevV2Store(tmp_path/'ledger.sqlite')
    owner={'pid':1}
    with store.tx() as db:
        store.put(db,'experiments','e',{'selection':{'mode':'experiment','budget_tier':'dev'},'formal_eligible':False})
        store.put(db,'cells','c',{'experiment_id':'e','state':'running','owner':owner,
            'used':{k:0 for k in RESOURCES},'budget':b})
    reservation={'executions':1,'sdk_calls':320,'control_steps':9600,'execution_seconds':1980}
    job=store.reserve('c',1,'execute',reservation)
    store.claim(job['job_id'],owner)
    store.settle(job['job_id'],reservation,{'dev_tier_budget':dev_tier_budget()})
    assert store.cell('c')['used']['execution_seconds']==1980
    assert store.export_ledger()['formal_eligible'] is False


def test_formal_request_rejects_dev_budget_even_with_matching_hashes(monkeypatch,tmp_path):
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs:None)
    config=dev_config.make_dev_config('open_cabinet',102,{'path':'fixture','sha256':'fixture'})
    cp=tmp_path/'config.json';cp.write_text(json.dumps(config))
    policy=tmp_path/'policy.py';policy.write_text('from robot_sdk import sensors\nsensors.get_observation()\n')
    config_sha=hashlib.sha256(cp.read_bytes()).hexdigest()
    policy_sha=hashlib.sha256(policy.read_bytes()).hexdigest()
    lock=dict(suite='robocasa365',task_id='open_cabinet',seed=102,
        approved_config_sha256=config_sha,approved_policy_sha256=policy_sha,overall_deadline_seconds=300)
    lock['sha256']=hashlib.sha256(json.dumps(lock,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    request=FormalRunRequest(suite='robocasa365',task_id='open_cabinet',seed=102,
        policy_code_path=policy,policy_sha256=policy_sha,config_sha256=config_sha,
        artifact_root=tmp_path,overall_deadline_seconds=300,config_path=cp,
        entry_sha256=lock['sha256'],entry_lock=lock,sdk_call_limit=80)
    with pytest.raises(ValueError,match='development budget'):
        request.validate()
    formal=formal_config.make_formal_config('open_cabinet',102,config['dev_lock'],
        attempt_wall_seconds=120,whole_case_wall_seconds=300,sdk_call_limit=40)
    cp.write_text(json.dumps(formal))
    config_sha=hashlib.sha256(cp.read_bytes()).hexdigest()
    lock['approved_config_sha256']=config_sha
    lock.pop('sha256')
    lock['sha256']=hashlib.sha256(json.dumps(lock,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    formal_request=FormalRunRequest(suite='robocasa365',task_id='open_cabinet',seed=102,
        policy_code_path=policy,policy_sha256=policy_sha,config_sha256=config_sha,
        artifact_root=tmp_path,overall_deadline_seconds=300,config_path=cp,
        entry_sha256=lock['sha256'],entry_lock=lock,sdk_call_limit=40)
    formal_request.validate()
