"""11-08: 'Actual 365 robot/TCP differs from authenticated V2 URDF' fired on a robot that matched its URDF.

After env.step, MuJoCo's site poses still describe the previous substep while qpos is already
integrated, so a moving arm looked misaligned by about a millimetre against a 1e-5 m tolerance.
prepare() now recomputes kinematics from the same qpos before comparing. The tolerance is unchanged:
a real mismatch still fails.
"""
from types import SimpleNamespace as NS

import numpy as np
import pytest

from openarm365_dev import planner as plan
from curobo_v2.identity import IdentityError


def fake(tmp_path, offset_after_forward):
    state = {'fresh': False}
    def forward(): state['fresh'] = True
    def step(): state['fresh'] = False  # what env.step leaves behind
    obj = NS(env=NS(sim=NS(forward=forward)), evidence=tmp_path, prepared=set(), failed=False,
             upload=lambda: None, body=lambda active: {'env_id': 'robocasa365', 'current_q': []},
             client=NS(call=lambda path, body: {'poses': {f'openarm_{s}_hand_tcp': {'position': [0, 0, 0], 'quaternion_wxyz': [1, 0, 0, 0]}
                                                          for s in ('left', 'right')}} if path == '/fk' else {}))
    obj.guard = lambda operation: operation()
    def tcp(env, side):
        pose = np.eye(4)
        pose[0, 3] = offset_after_forward if state['fresh'] else 1e-3  # stale: one substep behind a moving arm
        return pose
    return obj, step, tcp


def test_a_moving_arm_is_compared_on_fresh_kinematics(monkeypatch, tmp_path):
    obj, step, tcp = fake(tmp_path, 0.0)
    monkeypatch.setattr(plan, 'tcp_pose', tcp)
    monkeypatch.setattr(plan, 'mount_pose', lambda env: np.eye(4))
    step()
    errors = plan.MujocoV2Planner.prepare(obj, ('left',))
    assert errors['openarm_left_hand_tcp']['position_error_m'] == 0.0
    assert ('left',) in obj.prepared


def test_a_real_mismatch_still_fails_at_the_same_tolerance(monkeypatch, tmp_path):
    obj, step, tcp = fake(tmp_path, 2e-5)
    monkeypatch.setattr(plan, 'tcp_pose', tcp)
    monkeypatch.setattr(plan, 'mount_pose', lambda env: np.eye(4))
    step()
    with pytest.raises(IdentityError, match='differs from authenticated V2 URDF'):
        plan.MujocoV2Planner.prepare(obj, ('left',))
    assert ('left',) not in obj.prepared
