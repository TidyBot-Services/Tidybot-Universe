"""Controlled contract tests for the OpenArm365 formal runner.

Fake owner and policy sandbox only: no simulator, provider or human is involved, and nothing
here is evidence that the robot completes a task.
"""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import formal_attention_cli
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest, run_with_formal_boundary
from benchmarks.attention_harness.robocasa365.config import _config, memory_context, validate_config
from benchmarks.attention_harness.robocasa365.formal_runner import RoboCasa365FormalSuiteRunner
from openarm365_dev_v2 import formal_runner as formal_module, runner as runner_module
from openarm365_dev_v2.dev_config import validate_dev_config
from openarm365_dev_v2.formal_config import (RUN_BUDGET_SCHEMA, SCHEMA, make_formal_config, validate_formal_config,
                                             validate_formal_run_budget, validate_native_config)
from openarm365_dev_v2.tests.test_planner_features import SENSOR, setup

BUDGET = dict(attempt_wall_seconds=120, whole_case_wall_seconds=300, sdk_call_limit=40)


@pytest.fixture
def env(tmp_path):
    s, e, c, cfg, owner, root = setup(tmp_path)
    formal = make_formal_config(cfg['task_id'], cfg['seed'], cfg['dev_lock'], **BUDGET)
    path = tmp_path / 'formal-config.json'
    path.write_text(json.dumps(formal, sort_keys=True))
    policy = tmp_path / 'policy.py'
    policy.write_text(SENSOR)
    return SimpleNamespace(dev=cfg, formal=formal, config=path, policy=policy, tmp=tmp_path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def entry_lock(env, request_kw):
    lock = dict(suite='robocasa365', task_id=env.formal['task_id'], seed=env.formal['seed'],
                approved_config_sha256=sha(env.config), approved_policy_sha256=sha(env.policy),
                overall_deadline_seconds=request_kw['overall_deadline_seconds'])
    digest = hashlib.sha256(json.dumps(lock, sort_keys=True, ensure_ascii=False,
                                       separators=(',', ':')).encode()).hexdigest()
    return {**lock, 'sha256': digest}, digest


def make_request(env, **kw):
    base = dict(suite='robocasa365', task_id=env.formal['task_id'], seed=env.formal['seed'],
                policy_code_path=env.policy, policy_sha256=sha(env.policy), config_path=env.config,
                config_sha256=sha(env.config), artifact_root=env.tmp / 'executions',
                overall_deadline_seconds=300, attempt_deadline_seconds=120, sdk_call_limit=40,
                run_id='run:formal-test', attempt_id='attempt:formal-test:0')
    base.update(kw)
    return FormalRunRequest(**base)


# --- configuration -------------------------------------------------------------------------

def test_formal_config_round_trip_and_boundary_fields(env):
    c = validate_formal_config(json.loads(env.config.read_bytes()))
    assert c['schema_version'] == SCHEMA and c['runner_boundary'] == 'formal'
    assert (c['attempt_wall_seconds'], c['whole_case_wall_seconds'], c['sdk_call_limit']) == (120, 300, 40)
    # Robot, scene, controller and identity lock are exactly the Dev v2 ones.
    for key in ('robot', 'execution_arm', 'controller', 'layout_id', 'style_id', 'camera_names',
                'dev_lock', 'initial_joint_targets', 'asset_manifest_sha256', 'object_set_id'):
        assert c[key] == env.dev[key]


def test_dev_configuration_is_unchanged_and_not_accepted_as_formal(env):
    validate_dev_config(env.dev)
    with pytest.raises(ValueError):
        validate_formal_config(env.dev)
    with pytest.raises(ValueError):
        validate_dev_config(env.formal)


@pytest.mark.parametrize('field,value', [
    ('attempt_wall_seconds', 481), ('attempt_wall_seconds', 0), ('attempt_wall_seconds', True),
    ('sdk_call_limit', 81), ('sdk_call_limit', 0), ('whole_case_wall_seconds', 1801),
    ('whole_case_wall_seconds', 100)])
def test_formal_budget_outside_the_verified_envelope_is_refused(env, field, value):
    kw = dict(BUDGET, **{field: value})
    with pytest.raises(ValueError):
        make_formal_config(env.dev['task_id'], env.dev['seed'], env.dev['dev_lock'], **kw)
    with pytest.raises(ValueError):
        validate_formal_config(dict(env.formal, **{field: value}))


@pytest.mark.parametrize('change', [dict(controller='OSC'), dict(execution_arm='left'),
                                    dict(runner_boundary='dev_loop'), dict(eval_agent={'enabled': True, 'model': 'parcc/Qwen'}),
                                    dict(task_prompt='Do something else.')])
def test_formal_config_tampering_is_refused(env, change):
    with pytest.raises(ValueError):
        validate_formal_config(dict(env.formal, **change))


def test_shared_validator_and_request_check_accept_the_formal_schema(env):
    assert validate_config(json.loads(env.config.read_bytes()))['schema_version'] == SCHEMA
    assert _config(make_request(env))['task_id'] == env.formal['task_id']
    with pytest.raises(ValueError, match='deadline exceeds freeze'):
        _config(make_request(env, overall_deadline_seconds=301))
    with pytest.raises(ValueError, match='deadline exceeds freeze'):
        _config(make_request(env, attempt_deadline_seconds=121))


def test_cli_chooses_the_runner_from_the_config_schema(env):
    assert formal_attention_cli.robocasa365_runner_class(env.config) is formal_module.OpenArm365FormalSuiteRunner
    legacy = env.tmp / 'legacy.json'
    legacy.write_text(json.dumps({'schema_version': 'attentionbench.robocasa365-config.v1'}))
    assert formal_attention_cli.robocasa365_runner_class(legacy) is RoboCasa365FormalSuiteRunner
    assert formal_attention_cli.robocasa365_runner_class(None) is RoboCasa365FormalSuiteRunner


# --- runner --------------------------------------------------------------------------------

@pytest.fixture
def fake_native(monkeypatch):
    seen = SimpleNamespace(contexts=[], limits=[], owners=[])

    class Owner:
        def __init__(self, **kw):
            self.ledger = []
            self.sent = {}
            self.unresolved = []
            seen.owners.append(kw)

        def start(self):
            return {'initial_observation': {'robot': {}, 'perception': {'values': {}}}, 'capabilities': {}}

        def rpc(self, group, method, *a, **kw):
            return ({'robot': {}, 'perception': {'values': {}}} if method == 'observe'
                    else {'evaluated': True, 'native_success': False})

        def stop(self, reason):
            return dict(leader_reaped=True, process_group_gone=True, planner_process_groups_gone=True)

    def policy_run(**kw):
        seen.contexts.append(kw['context'])
        seen.limits.append(kw['max_sdk_calls'])
        return SimpleNamespace(status='completed', error=None, call_count=2)

    monkeypatch.setattr(runner_module, 'NativeOwner', Owner)
    monkeypatch.setattr(runner_module, 'probe_sandbox', lambda: {'controlled': True})
    monkeypatch.setattr(runner_module, 'build_sdk', lambda *args: None)
    monkeypatch.setattr(runner_module, 'finalize_recording', lambda *args: {})
    monkeypatch.setattr(runner_module, 'execute_formal_policy', policy_run)
    return seen


def test_runner_passes_the_formal_boundary_and_carries_the_entry_lock(env, fake_native):
    lock, digest = entry_lock(env, dict(overall_deadline_seconds=300))
    request = make_request(env, entry_lock=lock, entry_sha256=digest)
    result = run_with_formal_boundary(request, runner=formal_module.OpenArm365FormalSuiteRunner(
        service_source_root='', sim_python='', sim_source_root='', task_source_root='', asset_manifest=''))
    assert result['boundary_checked'] and result['entry_lock'] == lock
    assert result['runner_boundary'] == 'formal' and result['formal_eligible'] is False
    trace = json.loads(Path(result['artifacts']['trace']['uri']).read_bytes())
    assert trace['entry_lock'] == lock and trace['runner_boundary'] == 'formal'
    assert result['sandbox']['action_cancellation_verified'] is True
    receipt = json.loads(Path(result['artifacts']['sandbox_receipt']['uri']).read_bytes())
    assert receipt['sdk_call_budget'] == {'limit': 40, 'requested': 2, 'dispatched': 2}


def test_runner_hands_the_public_attention_input_to_the_policy(env, fake_native):
    attention = {'advisor_guidance': 'Approach the handle 2 cm lower.'}
    request = make_request(env, attention_input=attention)
    result = formal_module.OpenArm365FormalSuiteRunner(
        service_source_root='', sim_python='', sim_source_root='', task_source_root='',
        asset_manifest='').execute(request)
    context = fake_native.contexts[0]
    assert context['attention_input'] == attention and context['demo_steps'] == []
    assert context['robot'] == 'OpenArm365' and context['perception_mode'] == 'sim_gt'
    written = json.loads((Path(result['artifact_dir']) / 'policy_context.json').read_bytes())
    assert written['attention_input'] == attention


def test_runner_without_help_gives_an_empty_attention_input(env, fake_native):
    formal_module.OpenArm365FormalSuiteRunner(
        service_source_root='', sim_python='', sim_source_root='', task_source_root='',
        asset_manifest='').execute(make_request(env))
    assert fake_native.contexts[0]['attention_input'] == {}


def test_runner_uses_the_frozen_sdk_limit_and_refuses_longer_deadlines(env, fake_native):
    runner = formal_module.OpenArm365FormalSuiteRunner(
        service_source_root='', sim_python='', sim_source_root='', task_source_root='', asset_manifest='')
    runner.execute(make_request(env, sdk_call_limit=10))
    assert fake_native.limits == [10]
    with pytest.raises(ValueError, match='deadline exceeds freeze'):
        runner.execute(make_request(env, attempt_deadline_seconds=121))
    # A request limit above the frozen one is clamped, so the boundary refuses the receipt mismatch.
    request = make_request(env, sdk_call_limit=80)
    with pytest.raises(RuntimeError, match='SDK call budget'):
        run_with_formal_boundary(request, runner=runner)


def test_runner_rejects_a_dev_config_and_a_mismatched_task(env, fake_native, tmp_path):
    runner = formal_module.OpenArm365FormalSuiteRunner(
        service_source_root='', sim_python='', sim_source_root='', task_source_root='', asset_manifest='')
    dev_path = tmp_path / 'dev-config.json'
    dev_path.write_text(json.dumps(env.dev, sort_keys=True))
    with pytest.raises(ValueError):
        runner.execute(make_request(env, config_path=dev_path, config_sha256=sha(dev_path)))
    with pytest.raises(ValueError, match='task/seed'):
        runner.execute(make_request(env, seed=env.formal['seed'] + 1))
    assert fake_native.contexts == []


def test_dev_runner_still_refuses_an_entry_lock_and_keeps_its_boundary_label(env, fake_native):
    lock, digest = entry_lock(env, dict(overall_deadline_seconds=300))
    dev_runner = runner_module.OpenArm365DevRunnerV2(
        service_source_root='', sim_python='', sim_source_root='', task_source_root='', asset_manifest='')
    with pytest.raises(ValueError, match='Dev-only'):
        dev_runner.execute(make_request(env, entry_lock=lock, entry_sha256=digest))
    assert dev_runner.runner_boundary == 'dev_loop' and dev_runner.allow_entry_lock is False


def test_memory_scope_is_bound_to_the_openarm365_robot_and_controller(env):
    from benchmarks.attention_harness.robocasa365.memory import ScopedMemoryGateway
    context = memory_context(_config(make_request(env)))
    assert (context['robot'], context['controller'], context['suite']) == ('OpenArm365', 'BASIC', 'robocasa365')
    assert context['task_id'] == env.formal['task_id'] and context['perception_mode'] == 'sim_gt'
    # The same scope the Dev config yields, so Memory written for one is not silently another robot's.
    assert context == memory_context(env.dev)
    ScopedMemoryGateway(SimpleNamespace(), context)


def test_the_native_worker_check_accepts_dev_and_formal_configurations(env):
    assert validate_native_config(env.dev) == env.dev
    assert validate_native_config(env.formal) == env.formal
    with pytest.raises(ValueError):
        validate_native_config(dict(env.formal, controller='OSC'))


def test_formal_safety_evidence_uses_the_scheduler_label_and_keeps_the_monitor_kind(env, fake_native):
    formal = formal_module.OpenArm365FormalSuiteRunner(
        service_source_root='', sim_python='', sim_source_root='', task_source_root='', asset_manifest='')
    result = formal.execute(make_request(env))
    safety = json.loads(Path(result['artifacts']['safety']['uri']).read_bytes())
    assert safety['source'] == 'independent_safety_monitor' and safety['monitor'] == 'native_per_physics_step_monitor'
    assert isinstance(safety['unsafe_attempts'], int)
    dev_path = env.tmp / 'dev-config.json'
    dev_path.write_text(json.dumps(env.dev, sort_keys=True))
    dev = runner_module.OpenArm365DevRunnerV2(
        service_source_root='', sim_python='', sim_source_root='', task_source_root='', asset_manifest='')
    dev_result = dev.execute(make_request(env, config_path=dev_path, config_sha256=sha(dev_path)))
    dev_safety = json.loads(Path(dev_result['artifacts']['safety']['uri']).read_bytes())
    assert dev_safety['source'] == 'native_per_physics_step_monitor' and 'monitor' not in dev_safety


def run_budget(env, **kw):
    value = dict(schema_version=RUN_BUDGET_SCHEMA, suite='robocasa365', formal_eligible=False, max_attempts=4,
                 assistance_credits=1, token_limit=4096, human_deadline_seconds=30, attempt_wall_seconds=280,
                 whole_case_wall_seconds=300, approved_config_sha256s=[sha(env.config)],
                 approved_policy_sha256=sha(env.policy))
    value.update(kw)
    return value


def test_run_budget_loads_through_the_shared_loader_and_sets_the_limits(env):
    from benchmarks.attention_harness.robocasa365.budget import limits_for_budget, load_run_budget
    path = env.tmp / 'budget.json'
    path.write_text(json.dumps(run_budget(env), sort_keys=True))
    budget = load_run_budget(path, sha(path), config_sha256=sha(env.config), policy_sha256=sha(env.policy))
    assert limits_for_budget(budget) == dict(max_attempts=4, assistance_credits=1, token_limit=4096,
                                             human_deadline_seconds=30, overall_deadline_seconds=300)
    with pytest.raises(ValueError, match='digest'):
        load_run_budget(path, '0' * 64, config_sha256=sha(env.config), policy_sha256=sha(env.policy))


@pytest.mark.parametrize('change', [dict(attempt_wall_seconds=481), dict(whole_case_wall_seconds=601),
                                    dict(attempt_wall_seconds=300, whole_case_wall_seconds=200),
                                    dict(max_attempts=11), dict(token_limit=8001), dict(human_deadline_seconds=31),
                                    dict(max_attempts=True), dict(formal_eligible=True),
                                    dict(approved_policy_sha256='0' * 64), dict(approved_config_sha256s=['0' * 64]),
                                    dict(approved_config_sha256s=[]), dict(extra=1)])
def test_run_budget_outside_the_envelope_or_unbound_is_refused(env, change):
    with pytest.raises(ValueError):
        validate_formal_run_budget(run_budget(env, **change), sha(env.config), sha(env.policy))


def test_formal_config_requires_reproducible_reset(env):
    assert env.formal['scene_reset_seed'] == env.formal['seed']
    without = {k: v for k, v in env.formal.items() if k != 'scene_reset_seed'}
    with pytest.raises(ValueError, match='reproducible reset'):
        validate_formal_config(without)
    with pytest.raises(ValueError, match='reproducible reset'):
        validate_formal_config(dict(env.formal, scene_reset_seed=env.formal['seed'] + 1))
    with pytest.raises(ValueError, match='scene reset seed'):
        make_formal_config(env.dev['task_id'], env.dev['seed'], env.dev['dev_lock'],
                           scene_reset_seed=env.dev['seed'] + 1, **BUDGET)
