"""Frozen planner configuration checks without native execution."""
import ast
import copy
import inspect
import itertools
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from openarm365_dev_v2 import dev_config, formal_config, worker
from openarm365_dev.planner import MujocoV2Planner


@pytest.fixture(params=['dev', 'legacy', 'formal'])
def config(request, monkeypatch):
    monkeypatch.setattr(dev_config, 'verify_lock', lambda *args, **kwargs: None)
    args = ('open_cabinet', 102, {'path': 'fixture', 'sha256': 'fixture'})
    if request.param == 'formal':
        return formal_config.make_formal_config(*args, attempt_wall_seconds=480,
            whole_case_wall_seconds=600, sdk_call_limit=80)
    return dev_config.make_dev_config(*args, budget_tier=request.param)


def test_defaults_and_legacy_round_trip(config):
    defaults = dict(contact_exempt_objects='movable_objects', planner_mode='ik_path',
                    ik_candidates=8, ik_max_joint_jump=1.0)
    for key, value in defaults.items():
        assert config[key] == value == getattr(dev_config, 'DEFAULT_' + key.upper())
    for legacy in (False, True):
        c = copy.deepcopy(config)
        if legacy:
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
        c = formal_config.make_formal_config('open_cabinet', 102, config['dev_lock'],
            attempt_wall_seconds=480, whole_case_wall_seconds=600, sdk_call_limit=80)
    else:
        c = dev_config.make_dev_config('open_cabinet', 102, config['dev_lock'],
            budget_tier=config.get('budget_tier', 'legacy'))
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
    assert formal_config.validate_native_config(config) is config
    assert json.dumps(config).encode() == before


@pytest.mark.parametrize('legacy', [False, True])
def test_worker_passes_frozen_settings(config, legacy, tmp_path):
    if legacy:
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
                 stop=SimpleNamespace(is_set=cancel))
    exec(compile(ast.Module(body=[statement], type_ignores=[]), worker.__file__, 'exec'), scope)
    constructor.assert_called_once_with(env, evidence=tmp_path/'planner', deadline=123,
        cancel_check=cancel, contact_exempt_objects='none' if legacy else 'movable_objects',
        planner_mode='plan' if legacy else 'ik_path', ik_candidates=None if legacy else 8,
        ik_max_joint_jump=None if legacy else 1.0)


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
