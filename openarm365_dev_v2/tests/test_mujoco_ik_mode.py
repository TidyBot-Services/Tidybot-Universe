"""Offline routing checks for the physical OpenArm365 Dev planner."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from openarm365_dev import planner as planner_module


@pytest.fixture
def planner(monkeypatch, tmp_path):
    monkeypatch.setattr(planner_module, 'mount_pose', lambda env: np.eye(4))
    from openarm365_dev import attachment
    monkeypatch.setattr(attachment, 'planner_context', lambda *args: None)

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
    assert planner.ledger.issued[0][1]['epoch'] == 7
    assert json.loads((planner.evidence / 'trajectory-0001.json').read_text())['source'] == 'ik_path'


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
