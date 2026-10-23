"""Offline request routing and service-model contract for both V2 adapters."""
import importlib.util
import json
from pathlib import Path
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest


ACTIVE = [f'openarm_left_joint{i}' for i in range(1, 8)]
NAMES = [f'openarm_{side}_joint{i}' for side in ('left', 'right') for i in range(1, 8)]
TARGET = {'openarm_left_hand_tcp': {'position': [.1, .2, .3],
                                   'quaternion_wxyz': [1., 0., 0., 0.]}}


class FakeClient:
    identity = {'nonce': 'test'}
    nonce = 'test'

    def __init__(self):
        self.calls = []

    def call(self, path, body):
        self.calls.append((path, json.loads(json.dumps(body))))
        return {'status': 'success'} if path.startswith('/plan') else {}


class FakeLedger:
    def issue(self, *args, **kwargs):
        return 'offline-plan'

    def invalidate(self, *args):
        pass


def make_planner(kind, monkeypatch, tmp_path):
    if kind == 'mujoco':
        from openarm365_dev import planner as module
        monkeypatch.setattr(module, 'mount_pose', lambda env: np.eye(4))
        attachment = types.ModuleType('openarm365_dev.attachment')
        attachment.planner_context = lambda *args: None
        monkeypatch.setitem(sys.modules, 'openarm365_dev.attachment', attachment)
        monkeypatch.setattr(sys.modules['openarm365_dev'], 'attachment', attachment, raising=False)
        planner = module.MujocoV2Planner.__new__(module.MujocoV2Planner)
        planner.env = object()
        planner.planner_mode = 'plan'
        planner.ik_candidates = None
        planner.ik_max_joint_jump = None
        planner.tools = {}
        planner.body = lambda active: dict(env_id='robocasa365', current_q=[0.] * 14,
            joint_names=NAMES, locked_joints={}, active_joints=list(active))
    else:
        from curobo_v2.sapien_planner import SapienV2Planner
        planner = SapienV2Planner.__new__(SapienV2Planner)
        planner.rig = SimpleNamespace(robot=SimpleNamespace(root_pose=SimpleNamespace(
            to_transformation_matrix=lambda: np.eye(4))))
        planner.body = lambda active: dict(env_id='kitchen', current_q=[0.] * 14,
            joint_names=NAMES, locked_joints={}, active_joints=list(active))
        planner.attachment_context = lambda *args: None
    planner.evidence = tmp_path
    planner.prepared = {tuple(ACTIVE)}
    planner.failed = False
    planner.sequence = 1
    planner.owner = SimpleNamespace(close=lambda *args: None)
    planner.attachment_provider = lambda: None
    planner.upload = lambda: ({'cuboids': []}, {'world_revision': 1})
    planner.client = FakeClient()
    planner.ledger = FakeLedger()
    return planner


def request(planner):
    return planner.plan_pose([.1, .2, .3], np.eye(3), side='left', epoch=7)


def expected_bodies(kind):
    plan = dict(env_id='robocasa365' if kind == 'mujoco' else 'kitchen',
        current_q=[0.] * 14, joint_names=NAMES, locked_joints={},
        active_joints=ACTIVE, targets=TARGET, attachment=None)
    warmup = dict(env_id=plan['env_id'], current_q=[0.] * 14,
        joint_names=NAMES, locked_joints={}, active_joints=ACTIVE,
        attachment=None, tool_frames=['openarm_left_hand_tcp'])
    return warmup, plan


@pytest.mark.parametrize('kind', ['mujoco', 'sapien'])
@pytest.mark.parametrize('mode', [None, 'plan', 'ik_path'])
def test_request_bodies(monkeypatch, tmp_path, kind, mode):
    monkeypatch.delenv('ATTN_IK_MAX_JOINT_JUMP', raising=False)
    if mode is None:
        monkeypatch.delenv('ATTN_PLANNER_MODE', raising=False)
    else:
        monkeypatch.setenv('ATTN_PLANNER_MODE', mode)
    monkeypatch.setenv('ATTN_IK_CANDIDATES', '4')
    planner = make_planner(kind, monkeypatch, tmp_path)
    request(planner)
    warmup, plan = expected_bodies(kind)
    calls = planner.client.calls
    assert [path for path, _ in calls] == ['/warmup', '/plan' if mode != 'ik_path' else '/plan/ik_path']
    assert calls[0][1] == warmup
    assert 'ik_candidates' not in calls[0][1]
    if mode != 'ik_path':
        assert calls[1][1] == plan
        # Legacy plan-mode dictionaries, including insertion order, serialize identically.
        for actual, expected in zip((calls[0][1], calls[1][1]), (warmup, plan)):
            assert json.dumps(actual, separators=(',', ':')).encode() == json.dumps(
                expected, separators=(',', ':')).encode()
    else:
        assert calls[1][1] == dict(plan, ik_candidates=4)
        assert 'max_joint_jump' not in calls[1][1]
        expected = dict(plan)
        attachment = expected.pop('attachment')
        expected['ik_candidates'] = 4
        expected['attachment'] = attachment
        assert json.dumps(calls[1][1], separators=(',', ':')).encode() == json.dumps(
            expected, separators=(',', ':')).encode()
        receipt = json.loads((tmp_path / 'trajectory-0001.json').read_text())
        assert (receipt['max_joint_jump'], receipt['max_joint_jump_source']) == (.35, 'default')


@pytest.mark.parametrize('kind', ['mujoco', 'sapien'])
@pytest.mark.parametrize('raw,value', [('0.8', .8), ('2.0', 2.), ('.5', .5)])
def test_override_is_plan_only_and_excluded_from_warmup(
        monkeypatch, tmp_path, server_models, kind, raw, value):
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'ik_path')
    monkeypatch.setenv('ATTN_IK_CANDIDATES', '4')
    monkeypatch.setenv('ATTN_IK_MAX_JOINT_JUMP', raw)
    planner = make_planner(kind, monkeypatch, tmp_path)
    result = request(planner)
    warmup, plan = (body for _, body in planner.client.calls)
    assert 'max_joint_jump' not in warmup
    assert server_models.WarmupRequest.model_validate(warmup).current_q == [0.] * 14
    with pytest.raises(ValueError, match='max_joint_jump'):
        server_models.WarmupRequest.model_validate(dict(warmup, max_joint_jump=value))
    assert plan['max_joint_jump'] == value
    assert server_models.IKPathRequest.model_validate(plan).max_joint_jump == value
    assert (result['max_joint_jump'], result['max_joint_jump_source']) == (value, 'override')
    receipt = json.loads((tmp_path / 'trajectory-0001.json').read_text())
    assert (receipt['max_joint_jump'], receipt['max_joint_jump_source']) == (value, 'override')


@pytest.mark.parametrize('kind', ['mujoco', 'sapien'])
@pytest.mark.parametrize('raw', ['', 'nan', 'inf', '-inf', '0', '-0.1', 'abc',
                                 '2.0001', '1_0', ' 0.8'])
def test_invalid_override_fails_before_request(monkeypatch, tmp_path, kind, raw):
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'ik_path')
    monkeypatch.setenv('ATTN_IK_MAX_JOINT_JUMP', raw)
    planner = make_planner(kind, monkeypatch, tmp_path)
    with pytest.raises(ValueError, match='ATTN_IK_MAX_JOINT_JUMP'):
        request(planner)
    assert planner.client.calls == []


@pytest.mark.parametrize('kind', ['mujoco', 'sapien'])
@pytest.mark.parametrize('mode', [None, 'plan'])
def test_override_rejected_in_plan_mode(monkeypatch, tmp_path, kind, mode):
    if mode is None:
        monkeypatch.delenv('ATTN_PLANNER_MODE', raising=False)
    else:
        monkeypatch.setenv('ATTN_PLANNER_MODE', mode)
    monkeypatch.setenv('ATTN_IK_MAX_JOINT_JUMP', '0.8')
    planner = make_planner(kind, monkeypatch, tmp_path)
    with pytest.raises(ValueError, match='only valid in ik_path mode'):
        request(planner)
    assert planner.client.calls == []


@pytest.fixture
def server_models(monkeypatch, tmp_path):
    pytest.importorskip('pydantic', minversion='2.0')
    pytest.importorskip('fastapi')
    config = tmp_path / 'robot.yml'
    config.write_text('robot_cfg:\n  kinematics:\n    base_link: base\nservice: {}\n')
    prefix = 'curobo_v2.service.curobo_service.'
    config_module = types.ModuleType(prefix + 'config')
    config_module.Config = type('Config', (), dict(ROBOT_CFG=str(config), MAX_ENVS=1,
        DEVICE='cpu', DEFAULT_ENV='default'))
    planner_module = types.ModuleType(prefix + 'planner')
    planner_module.PlannerAdapter = lambda **kwargs: object()
    world_module = types.ModuleType(prefix + 'world')
    world_module.WorldStore = lambda **kwargs: object()
    for name, module in [('config', config_module), ('planner', planner_module),
                         ('world', world_module)]:
        monkeypatch.setitem(sys.modules, prefix + name, module)
    path = Path(__file__).parents[2] / 'curobo_v2/service/curobo_service/server.py'
    spec = importlib.util.spec_from_file_location(prefix + '_offline_contract', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('kind', ['mujoco', 'sapien'])
@pytest.mark.parametrize('mode', [None, 'plan', 'ik_path'])
def test_emitted_bodies_validate_against_server_models(
        monkeypatch, tmp_path, server_models, kind, mode):
    monkeypatch.delenv('ATTN_IK_MAX_JOINT_JUMP', raising=False)
    if mode is None:
        monkeypatch.delenv('ATTN_PLANNER_MODE', raising=False)
    else:
        monkeypatch.setenv('ATTN_PLANNER_MODE', mode)
    monkeypatch.setenv('ATTN_IK_CANDIDATES', '4')
    planner = make_planner(kind, monkeypatch, tmp_path)
    request(planner)
    warmup, plan = (body for _, body in planner.client.calls)
    assert server_models.WarmupRequest.model_validate(warmup).current_q == [0.] * 14
    with pytest.raises(ValueError, match='ik_candidates'):
        server_models.WarmupRequest.model_validate(dict(warmup, ik_candidates=4))
    model = server_models.PlanRequest if mode != 'ik_path' else server_models.IKPathRequest
    assert model.model_validate(plan).targets['openarm_left_hand_tcp'].position == [.1, .2, .3]
    if mode == 'ik_path':
        assert model.model_validate(plan).max_joint_jump == .35
