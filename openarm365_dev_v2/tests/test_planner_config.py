"""Frozen planner configuration checks without native execution."""
import ast
import copy
import inspect
import itertools
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
pytestmark = pytest.mark.usefixtures("r3_deployment")

from openarm365_dev_v2 import dev_config, formal_config, worker
from openarm365_dev.planner import MujocoV2Planner


@pytest.fixture(params=['dev', 'legacy', 'formal'])
def config(request, monkeypatch):
    monkeypatch.setattr(dev_config, 'verify_lock', lambda *args, **kwargs: None)
    args = ('open_cabinet', 102, {'path': 'fixture', 'sha256': 'fixture'})
    if request.param == 'formal':
        return formal_config.make_formal_config(*args, attempt_wall_seconds=480,
            whole_case_wall_seconds=600, sdk_call_limit=80)
    return dev_config._make_dev_config(*args, budget_tier=request.param,
        execution_settings=dev_config.from_config(dev_config.R3_20261025.to_dict()) if request.param=='dev' else None)


def test_defaults_and_legacy_round_trip(config):
    defaults = dict(contact_exempt_objects='movable_objects', planner_mode='ik_path',
                    ik_candidates=8, ik_max_joint_jump=1.0)
    for key, value in defaults.items():
        assert config[key] == value == getattr(dev_config, 'DEFAULT_' + key.upper())
    for legacy in (False, True):
        c = copy.deepcopy(config)
        if legacy:
            c.pop('grasp_freeze', None)
            for key in dev_config.PLANNER_CONFIG_KEYS:
                del c[key]
            c['contact_exempt_objects'] = 'none'
        before = json.dumps(c).encode()
        assert formal_config.validate_native_config(c) is c
        assert json.dumps(c).encode() == before


def test_constructor_uses_named_constants(config, monkeypatch):
    values = dict(DEFAULT_CONTACT_EXEMPT_OBJECTS='none', DEFAULT_PLANNER_MODE='plan',
                  DEFAULT_IK_CANDIDATES=3, DEFAULT_IK_MAX_JOINT_JUMP=.75)
    for name, value in values.items():
        monkeypatch.setattr(dev_config, name, value)
    if config['runner_boundary'] == 'formal':
        with pytest.raises(ValueError, match='grasp'):
            formal_config.make_formal_config('open_cabinet', 102, config['dev_lock'],
                attempt_wall_seconds=480, whole_case_wall_seconds=600, sdk_call_limit=80)
        return
    else:
        c = dev_config._make_dev_config('open_cabinet', 102, config['dev_lock'],
            budget_tier=config.get('budget_tier', 'legacy'),
            execution_settings=dev_config.from_config(config.get('execution_settings')))
    for name, value in values.items():
        assert c[name.removeprefix('DEFAULT_').lower()] == value


@pytest.mark.parametrize('keys', [keys for n in (1, 2)
    for keys in itertools.combinations(dev_config.PLANNER_CONFIG_KEYS, n)])
def test_partial_settings_rejected(config, keys):
    for key in keys:
        del config[key]
    with pytest.raises(ValueError, match='must be present together'):
        formal_config.validate_native_config(config)


@pytest.mark.parametrize('key,value', [
    ('planner_mode', 'other'), ('planner_mode', True), ('planner_mode', None),
    *[('ik_candidates', v) for v in (0, 33, True, '8')],
    *[('ik_max_joint_jump', v) for v in (0, -1, 2.5, float('nan'), float('inf'), True, '1.0')],
])
def test_invalid_settings_rejected(config, key, value):
    config[key] = value
    with pytest.raises(ValueError, match=key):
        formal_config.validate_native_config(config)


@pytest.mark.parametrize('mode,count,jump', [('plan', 1, 2), ('ik_path', 32, .01)])
def test_valid_settings_preserve_types(config, mode, count, jump):
    config.update(planner_mode=mode, ik_candidates=count, ik_max_joint_jump=jump)
    before = json.dumps(config).encode()
    if config['runner_boundary'] == 'formal':
        with pytest.raises(ValueError, match='grasp'):
            formal_config.validate_native_config(config)
        assert json.dumps(config).encode() == before
        return
    assert formal_config.validate_native_config(config) is config
    assert json.dumps(config).encode() == before


@pytest.mark.parametrize('legacy', [False, True])
def test_worker_passes_frozen_settings(config, legacy, tmp_path):
    if legacy:
        config.pop('grasp_freeze', None)
        for key in dev_config.PLANNER_CONFIG_KEYS:
            del config[key]
        config['contact_exempt_objects'] = 'none'
    # Execute the worker's actual constructor statement with a fake planner,
    # avoiding main's pipe/process ownership and native environment setup.
    tree = ast.parse(inspect.getsource(worker.main))
    statement = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
        and node.value.func.id == 'MujocoV2Planner')
    constructor = Mock()
    env = object()
    cancel = lambda: False
    scope = dict(MujocoV2Planner=constructor, env=env, c=config,
                 args=SimpleNamespace(evidence=tmp_path, deadline=123),
                 stop=SimpleNamespace(is_set=cancel),execution_settings=dev_config.from_config(config.get('execution_settings')))
    exec(compile(ast.Module(body=[statement], type_ignores=[]), worker.__file__, 'exec'), scope)
    constructor.assert_called_once_with(env, evidence=tmp_path/'planner', deadline=123,
        cancel_check=cancel, contact_exempt_objects='none' if legacy else 'movable_objects',
        planner_mode='plan' if legacy else 'ik_path', ik_candidates=None if legacy else 8,
        ik_max_joint_jump=None if legacy else 1.0,
        **({} if "execution_settings" not in config else dict(execution_settings=dev_config.from_config(config["execution_settings"]))))


def test_planner_constructor_defaults_and_storage(monkeypatch, tmp_path):
    from openarm365_dev import planner as module
    class StopBeforeService(Exception):
        pass
    def stop(*args):
        raise StopBeforeService
    monkeypatch.setattr(module, 'load_lock', stop)
    for kwargs, expected in [({}, ('plan', None, None)),
                            (dict(planner_mode='ik_path', ik_candidates=8, ik_max_joint_jump=1.0),
                             ('ik_path', 8, 1.0))]:
        planner = MujocoV2Planner.__new__(MujocoV2Planner)
        with pytest.raises(StopBeforeService):
            planner.__init__(object(), evidence=tmp_path, deadline=123,
                             cancel_check=lambda: False, **kwargs)
        assert (planner.planner_mode, planner.ik_candidates, planner.ik_max_joint_jump) == expected


@pytest.fixture
def formal(monkeypatch):
    monkeypatch.setattr(dev_config, 'verify_lock', lambda *args, **kwargs: None)
    return formal_config.make_formal_config('open_cabinet', 102, {'path': 'fixture', 'sha256': 'fixture'},
        attempt_wall_seconds=480, whole_case_wall_seconds=600, sdk_call_limit=80)


@pytest.mark.parametrize('generation', [formal_config.GRASP_FREEZE_ID, '0g', 'pre-0g'])
def test_formal_grasp_generations_accepted_without_mutation(formal, generation):
    assert formal['grasp_freeze'] == dict(id=formal_config.GRASP_FREEZE_ID,
                                        sha256=formal_config.GRASP_FREEZE_SHA256)
    if generation != formal_config.GRASP_FREEZE_ID:
        del formal['grasp_freeze']
    if generation == 'pre-0g':
        formal['contact_exempt_objects'] = 'none'
        for key in dev_config.PLANNER_CONFIG_KEYS:
            del formal[key]
    before = json.dumps(formal).encode()
    assert formal_config.grasp_generation(formal) == generation
    assert formal_config.validate_formal_config(formal) is formal
    assert json.dumps(formal).encode() == before


@pytest.mark.parametrize('frozen', [False, True])
@pytest.mark.parametrize('change', [
    dict(ik_candidates=3), dict(ik_candidates=True), dict(ik_candidates='8'), dict(ik_candidates=8.0),
    dict(ik_max_joint_jump=.75), dict(ik_max_joint_jump=1), dict(ik_max_joint_jump=True),
    dict(ik_max_joint_jump='1.0'), dict(planner_mode='plan'), dict(contact_exempt_objects='none'),
])
def test_formal_grasp_settings_are_exact(formal, frozen, change):
    if not frozen:
        del formal['grasp_freeze']
    formal.update(change)
    with pytest.raises(ValueError, match='grasp'):
        formal_config.validate_formal_config(formal)


@pytest.mark.parametrize('frozen', [False, True])
@pytest.mark.parametrize('contact', ['none', 'movable_objects'])
def test_formal_grasp_without_planner_keys(formal, frozen, contact):
    if not frozen:
        del formal['grasp_freeze']
    formal['contact_exempt_objects'] = contact
    for key in dev_config.PLANNER_CONFIG_KEYS:
        del formal[key]
    if not frozen and contact == 'none':
        assert formal_config.validate_formal_config(formal) is formal
    else:
        with pytest.raises(ValueError, match='grasp'):
            formal_config.validate_formal_config(formal)


@pytest.mark.parametrize('reference', [None, {},
    dict(id='wrong', sha256=formal_config.GRASP_FREEZE_SHA256),
    dict(id=formal_config.GRASP_FREEZE_ID, sha256='0' * 64),
    dict(id=formal_config.GRASP_FREEZE_ID, sha256=formal_config.GRASP_FREEZE_SHA256, extra=True)])
def test_formal_grasp_reference_rejected(formal, reference):
    formal['grasp_freeze'] = reference
    with pytest.raises(ValueError, match='grasp'):
        formal_config.validate_formal_config(formal)


def test_formal_grasp_tampered_record_rejected(formal, monkeypatch, tmp_path):
    path = tmp_path / 'freeze.json'
    path.write_bytes(formal_config.GRASP_FREEZE_PATH.read_bytes() + b'\n')
    monkeypatch.setattr(formal_config, 'GRASP_FREEZE_PATH', path)
    with pytest.raises(ValueError, match='grasp.*sha256'):
        formal_config.validate_formal_config(formal)


def test_grasp_freeze_cannot_enter_dev_config(config):
    if config['runner_boundary'] == 'formal':
        return
    config['grasp_freeze'] = dict(id=formal_config.GRASP_FREEZE_ID, sha256=formal_config.GRASP_FREEZE_SHA256)
    with pytest.raises(ValueError):
        formal_config.validate_native_config(config)


def test_dev_grasp_generation_retains_existing_rule(config):
    if config['runner_boundary'] == 'formal':
        return
    assert formal_config.grasp_generation(config) == '0g'
    for key in dev_config.PLANNER_CONFIG_KEYS:
        del config[key]
    assert formal_config.grasp_generation(config) == 'pre-0g'
