"""Offline routing checks for the physical OpenArm365 Dev planner."""
import json
import sys
import types
from types import SimpleNamespace

import numpy as np
import pytest
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
pytestmark = pytest.mark.usefixtures("r3_deployment")
from scipy.spatial.transform import Rotation

from openarm365_dev import executor as executor_module
from openarm365_dev import planner as planner_module


@pytest.fixture
def planner(monkeypatch, tmp_path):
    for name in ('ATTN_PLANNER_MODE', 'ATTN_IK_CANDIDATES', 'ATTN_IK_MAX_JOINT_JUMP'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(planner_module, 'mount_pose', lambda env: np.eye(4))
    attachment = types.ModuleType('openarm365_dev.attachment')
    attachment.planner_context = lambda *args: None
    monkeypatch.setitem(sys.modules, 'openarm365_dev.attachment', attachment)
    monkeypatch.setattr(sys.modules['openarm365_dev'], 'attachment', attachment, raising=False)

    class FakeClient:
        identity = {'nonce': 'test'}

        def __init__(self):
            self.calls = []

        def call(self, path, body):
            self.calls.append((path, body.copy()))
            if path.startswith('/plan'):
                return {'status': 'success', 'source': 'ik_path' if path == '/plan/ik_path' else 'motion_gen'}
            return {}

    class FakeLedger:
        def __init__(self):
            self.issued = []

        def issue(self, response, **kwargs):
            self.issued.append((response.copy(), kwargs))
            return 'test-plan-id'

    result = planner_module.MujocoV2Planner.__new__(planner_module.MujocoV2Planner)
    result.env = object()
    result.planner_mode = 'plan'
    result.ik_candidates = None
    result.ik_max_joint_jump = None
    result.evidence = tmp_path
    result.prepared = {tuple(f'openarm_left_joint{i}' for i in range(1, 8))}
    result.sequence = 1
    result.failed = False
    result.owner = SimpleNamespace(close=lambda reason: None)
    result.tools = {}
    result.attachment_provider = lambda: None
    result.upload = lambda: ({'cuboids': []}, {'world_revision': 1})
    result.body = lambda active: dict(env_id='robocasa365', current_q=[0.] * 14,
                                      joint_names=[f'joint{i}' for i in range(14)],
                                      locked_joints={}, active_joints=list(active))
    result.client = FakeClient()
    result.ledger = FakeLedger()
    return result


def request(planner):
    return planner.plan_pose([.1, .2, .3], np.eye(3), side='left', epoch=7)


def expected_body():
    return dict(env_id='robocasa365', current_q=[0.] * 14,
                joint_names=[f'joint{i}' for i in range(14)], locked_joints={},
                active_joints=[f'openarm_left_joint{i}' for i in range(1, 8)],
                targets={'openarm_left_hand_tcp': dict(position=[.1, .2, .3],
                    quaternion_wxyz=[1., 0., 0., 0.])}, attachment=None)


@pytest.mark.parametrize('mode', [None, 'plan'])
def test_plan_mode_keeps_original_request(monkeypatch, planner, mode):
    if mode is None:
        monkeypatch.delenv('ATTN_PLANNER_MODE', raising=False)
    else:
        monkeypatch.setenv('ATTN_PLANNER_MODE', mode)
    monkeypatch.setenv('ATTN_IK_CANDIDATES', '8')
    response = request(planner)
    assert planner.client.calls[-1] == ('/plan', expected_body())
    assert json.dumps(planner.client.calls[-1][1]).encode() == json.dumps(expected_body()).encode()
    assert response['plan_id'] == 'test-plan-id'
    receipt = json.loads((planner.evidence / 'trajectory-0001.json').read_text())
    assert receipt['planner_mode'] == 'plan'
    assert receipt['planner_mode_source'] == ('config' if mode is None else 'ATTN_PLANNER_MODE')
    assert not {'ik_candidates', 'ik_candidates_source', 'max_joint_jump', 'max_joint_jump_source'} & receipt.keys()


@pytest.mark.parametrize('raw, count', [(None, 1), ('8', 8)])
def test_ik_path_reaches_client_and_ledger(monkeypatch, planner, raw, count):
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'ik_path')
    if raw is None:
        monkeypatch.delenv('ATTN_IK_CANDIDATES', raising=False)
    else:
        monkeypatch.setenv('ATTN_IK_CANDIDATES', raw)
    response = request(planner)
    assert planner.client.calls[-1] == ('/plan/ik_path', dict(expected_body(), ik_candidates=count))
    assert response['source'] == 'ik_path'
    assert planner.ledger.issued[0][0]['source'] == 'ik_path'
    assert planner.ledger.issued[0][0]['max_joint_jump'] == .35
    assert planner.ledger.issued[0][0]['max_joint_jump_source'] == 'default'
    assert planner.ledger.issued[0][1]['epoch'] == 7
    assert json.loads((planner.evidence / 'trajectory-0001.json').read_text())['source'] == 'ik_path'
    assert response['planner_mode'] == 'ik_path'
    assert response['planner_mode_source'] == 'ATTN_PLANNER_MODE'
    assert response['ik_candidates'] == count
    assert response['ik_candidates_source'] == ('default' if raw is None else 'ATTN_IK_CANDIDATES')


def test_legacy_config_mode_override_records_defaults(monkeypatch, planner):
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'ik_path')
    assert planner.planner_mode == 'plan'
    assert planner.ik_candidates is None
    assert planner.ik_max_joint_jump is None
    response = request(planner)
    assert planner.client.calls[-1] == ('/plan/ik_path', dict(expected_body(), ik_candidates=1))
    receipt = json.loads((planner.evidence / 'trajectory-0001.json').read_text())
    assert receipt == response
    assert receipt['ik_candidates'] == 1
    assert receipt['ik_candidates_source'] == 'default'
    assert receipt['max_joint_jump_source'] == 'default'


@pytest.mark.parametrize('mode, path', [(None, '/plan'), ('plan', '/plan'),
                                      ('ik_path', '/plan/ik_path')])
def test_rotating_move_delta_routes_pose_target(monkeypatch, planner, mode, path):
    if mode is None:
        monkeypatch.delenv('ATTN_PLANNER_MODE', raising=False)
    else:
        monkeypatch.setenv('ATTN_PLANNER_MODE', mode)
    monkeypatch.delenv('ATTN_IK_CANDIDATES', raising=False)
    current = np.eye(4)
    current[:3, 3] = [.1, .2, .3]
    monkeypatch.setattr(executor_module, 'tcp_pose', lambda env, side: current)
    executor = executor_module.NativeExecutor.__new__(executor_module.NativeExecutor)
    executor.env = object()
    executor.planner = planner
    executor.epoch = 'test-epoch'
    executor.held = None
    executor.check_live = lambda: None
    executor.any_object_contact = lambda: False
    executor.apply = lambda response, *, request_id: (response, request_id)

    response, request_id = executor.move_delta('left', .01, -.02, .03,
                                               [0, np.pi / 8, 0], request_id='rotate')
    expected = expected_body()
    expected['targets'] = {'openarm_left_hand_tcp': dict(
        position=[.1 + .01, .2 - .02, .3 + .03],
        quaternion_wxyz=Rotation.from_rotvec([0, np.pi / 8, 0]).as_quat()[[3, 0, 1, 2]].tolist())}
    if mode == 'ik_path':
        expected['ik_candidates'] = 1
    assert planner.client.calls[-1] == (path, expected)
    if mode != 'ik_path':
        assert json.dumps(planner.client.calls[-1][1]).encode() == json.dumps(expected).encode()
    assert response['source'] == ('ik_path' if mode == 'ik_path' else 'motion_gen')
    assert request_id == 'rotate'


@pytest.mark.parametrize('bad', ['0', '-2', '1.5', 'abc', ' 4', ''])
def test_bad_candidate_count(monkeypatch, planner, bad):
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'ik_path')
    monkeypatch.setenv('ATTN_IK_CANDIDATES', bad)
    with pytest.raises(ValueError, match='^ATTN_IK_CANDIDATES must be a positive integer$'):
        request(planner)
    assert not planner.client.calls


def test_bad_mode(monkeypatch, planner):
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'other')
    with pytest.raises(ValueError, match='^ATTN_PLANNER_MODE must be plan or ik_path$'):
        request(planner)
    assert not planner.client.calls


@pytest.mark.parametrize('legacy', [False, True])
def test_frozen_config_routes_and_records(monkeypatch, planner, legacy):
    from openarm365_dev_v2 import dev_config
    monkeypatch.setattr(dev_config, 'verify_lock', lambda *a, **kw: None)
    config = dev_config._make_dev_config('open_cabinet', 102, {},
        execution_settings=dev_config.from_config(dev_config.R3_20261025.to_dict()))
    if legacy:
        for key in dev_config.PLANNER_CONFIG_KEYS:
            del config[key]
        config['contact_exempt_objects'] = 'none'
    dev_config.validate_dev_config(config)
    planner.planner_mode = config.get('planner_mode', 'plan')
    planner.ik_candidates = config.get('ik_candidates')
    planner.ik_max_joint_jump = config.get('ik_max_joint_jump')
    response = request(planner)
    body = expected_body()
    if not legacy:
        body.update(ik_candidates=8, max_joint_jump=1.0)
    assert planner.client.calls[-1] == ('/plan' if legacy else '/plan/ik_path', body)
    receipt = json.loads((planner.evidence / 'trajectory-0001.json').read_text())
    assert receipt == response
    assert receipt['planner_mode'] == ('plan' if legacy else 'ik_path')
    assert receipt['planner_mode_source'] == 'config'
    if legacy:
        assert not {'ik_candidates', 'max_joint_jump'} & receipt.keys()
    else:
        assert (receipt['ik_candidates'], receipt['max_joint_jump']) == (8, 1.0)
        assert receipt['ik_candidates_source'] == receipt['max_joint_jump_source'] == 'config'
        assert planner.ledger.issued[0][0]['max_joint_jump_source'] == 'config'
    assert not {'ik_candidates', 'max_joint_jump', 'planner_mode'} & planner.client.calls[0][1].keys()


@pytest.mark.parametrize('override', ['mode', 'candidates', 'jump', 'all'])
def test_overrides_win_over_config(monkeypatch, planner, override):
    planner.planner_mode = 'ik_path'
    planner.ik_candidates = 8
    planner.ik_max_joint_jump = 1.0
    if override == 'mode':
        monkeypatch.setenv('ATTN_PLANNER_MODE', 'plan')
    if override == 'all':
        planner.planner_mode = 'plan'
        monkeypatch.setenv('ATTN_PLANNER_MODE', 'ik_path')
    if override in ('candidates', 'all'):
        # Diagnostic candidate validation historically has no upper bound.
        monkeypatch.setenv('ATTN_IK_CANDIDATES', '33')
    if override in ('jump', 'all'):
        monkeypatch.setenv('ATTN_IK_MAX_JOINT_JUMP', '.8')
    receipt = request(planner)
    assert receipt == json.loads((planner.evidence / 'trajectory-0001.json').read_text())
    assert receipt['planner_mode_source'] == ('ATTN_PLANNER_MODE' if override in ('mode', 'all') else 'config')
    if override == 'mode':
        assert planner.client.calls[-1] == ('/plan', expected_body())
        assert receipt['planner_mode'] == 'plan'
        assert not {'ik_candidates', 'max_joint_jump'} & receipt.keys()
    else:
        candidates = 33 if override in ('candidates', 'all') else 8
        jump = .8 if override in ('jump', 'all') else 1.0
        assert planner.client.calls[-1] == ('/plan/ik_path', dict(expected_body(),
            ik_candidates=candidates, max_joint_jump=jump))
        assert receipt['ik_candidates'] == candidates
        assert receipt['max_joint_jump'] == jump
        assert receipt['ik_candidates_source'] == ('ATTN_IK_CANDIDATES' if override in ('candidates', 'all') else 'config')
        assert receipt['max_joint_jump_source'] == ('override' if override in ('jump', 'all') else 'config')


def test_jump_override_rejected_when_env_switches_to_plan(monkeypatch, planner):
    planner.planner_mode = 'ik_path'
    planner.ik_max_joint_jump = 1.0
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'plan')
    monkeypatch.setenv('ATTN_IK_MAX_JOINT_JUMP', '1.0')
    with pytest.raises(ValueError, match='^ATTN_IK_MAX_JOINT_JUMP is only valid in ik_path mode$'):
        request(planner)
    assert not planner.client.calls


@pytest.mark.parametrize('raw', ['0', '-1', '2.5', 'nan', 'inf', 'True', ' 1.0'])
def test_config_ik_path_preserves_jump_error(monkeypatch, planner, raw):
    planner.planner_mode = 'ik_path'
    planner.ik_max_joint_jump = 1.0
    monkeypatch.setenv('ATTN_IK_MAX_JOINT_JUMP', raw)
    with pytest.raises(ValueError, match='^ATTN_IK_MAX_JOINT_JUMP must be a finite positive decimal at most 2\\.0 rad$'):
        request(planner)
    assert not planner.client.calls
