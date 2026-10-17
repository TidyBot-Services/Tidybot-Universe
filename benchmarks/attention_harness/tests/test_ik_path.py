import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import types
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from curobo_v2.command_curve import CommandCurve
from curobo_v2.ik_path import quintic_path, ranked_ik_candidates
from curobo_v2.sweep import certify_trajectory, input_sha256


def fixture_data():
    return json.loads((Path(__file__).parent / 'fixtures/ik_path_world.json').read_text())


def test_quintic_samples_and_limits():
    q0 = np.array([0., .2, -.1])
    q1 = np.array([.2, -.05, .1])
    speed = np.array([.4, .5, .3])
    q, t, d = quintic_path(q0, q1, dt=.025, minimum_duration=.5,
                           speed=speed, acceleration=.8, jerk=2.)
    assert np.array_equal(q[[0, -1]], [q0, q1])
    assert np.array_equal(d['velocity'][[0, -1]], np.zeros((2, 3)))
    assert np.array_equal(d['acceleration'][[0, -1]], np.zeros((2, 3)))
    assert np.allclose(np.diff(t), .025)
    response = dict(status='success', joint_names=['a', 'b', 'c'], trajectory=q.tolist(),
                    time_from_start_seconds=t.tolist(),
                    trajectory_derivatives={k: v.tolist() for k, v in d.items()})
    curve = CommandCurve(response)
    motion = curve.certify_motion(position_limits=np.array([[-1, 1]]*3),
                                  maximum_speed=dict(zip(response['joint_names'], speed)),
                                  max_acceleration=.8, max_jerk=2.)
    assert motion['result'] == 'PASS'
    middle = slice(2, -2)
    assert np.allclose(np.gradient(q, .025, axis=0)[middle], d['velocity'][middle], atol=2e-3)
    assert np.allclose(np.gradient(d['velocity'], .025, axis=0)[middle], d['acceleration'][middle], atol=1e-2)
    assert np.allclose(curve.evaluate(t), q, atol=1e-10)


def test_saved_world_continuous_certificate():
    pytest.importorskip('sapien', reason='需要官方环境的 sapien')
    saved = fixture_data()
    q0 = np.asarray(saved['q0'])
    q1 = q0.copy(); q1[0] += .001
    q, t, d = quintic_path(q0, q1, dt=.025, minimum_duration=.5,
                           speed=np.full(len(q0), 2.), acceleration=10., jerk=100.)
    response = dict(status='success', joint_names=saved['joint_names'],
                    trajectory=q.tolist(), time_from_start_seconds=t.tolist(),
                    trajectory_derivatives={k: v.tolist() for k, v in d.items()})
    urdf = Path(__file__).parents[3] / 'openarm_bimanual/model/mobile_openarm.urdf'
    receipt, motion = certify_trajectory(response, urdf=urdf, world=saved['world'],
        locks=saved['locked_joints'], attachment=None,
        position_limits=np.column_stack((q0-10, q0+10)),
        maximum_speed=dict.fromkeys(saved['joint_names'], 2.),
        max_acceleration=10., max_jerk=100.)
    assert receipt['result'] == 'PASS', receipt.get('failure')
    assert receipt['queries'] > 0
    assert motion['result'] == 'PASS'


def fake_core(monkeypatch, jump, successes=None):
    import curobo_v2.identity as identity
    monkeypatch.setattr(identity, '_service_context', ({'assets_root': '/tmp'}, ''), raising=False)
    class FakeTensor(np.ndarray):
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return np.asarray(self)
    fake_torch = types.ModuleType('torch')
    fake_torch.Tensor = FakeTensor
    fake_torch.tensor = lambda value: np.asarray(value).view(FakeTensor)
    monkeypatch.setitem(sys.modules, 'torch', fake_torch)
    from curobo_v2.service.curobo_service.planner_core import CuroboPlanner
    import curobo_v2.sweep as sweep

    names = [f'openarm_left_joint{i}' for i in range(1, 8)]
    names += [f'openarm_right_joint{i}' for i in range(1, 8)]
    q0 = np.zeros(14)
    jumps = [jump] if np.isscalar(jump) else jump
    qgoals = np.zeros((len(jumps), 7)); qgoals[:, 0] = jumps
    recorded = {}
    result = SimpleNamespace(success=fake_torch.tensor([successes or [True] * len(jumps)]),
                             solution=fake_torch.tensor(qgoals[None]))
    def solve_pose(goal, *, current_state, return_seeds):
        recorded.update(seed=current_state.copy(), return_seeds=return_seeds)
        return result
    planner = SimpleNamespace(joint_names=names[:7], action_dim=7,
                              ik_solver=SimpleNamespace(solve_pose=solve_pose))
    joints = {}
    for name in names:
        node = ET.Element('joint'); ET.SubElement(node, 'limit', velocity='2')
        joints[name] = node
    core = SimpleNamespace(joint_names=names, limits=np.array([[-1., 1.]]*14),
        _joints=joints, _config={'kinematics': {'cspace': {'max_acceleration': 10., 'max_jerk': 100.},
                                 'urdf_path': '/unused'}}, interpolation_dt=.025,
        _world_cuboids=[], _attachment_context=None, _models={('fake',): (planner, None, None, {})},
        settings={})
    core.named = lambda q, names=None: np.asarray(q, float)
    core._select = lambda *a, **kw: (planner, None)
    core._state = lambda q, planner: q.copy()
    core._goal = lambda targets: targets
    core.validate = lambda trajectory, planner, checker, **kw: (setattr(core, 'last_validation', {
        'joint_limits': True, 'collision_free': True,
        'world_sha256': hashlib.sha256(b'[]').hexdigest()}) or True)
    core._validate_tool_endpoints = lambda *a: True
    def certify(response, **kwargs):
        recorded['candidate'] = response
        return ({'schema': sweep.SCHEMA, 'result': 'PASS',
                 'input_sha256': input_sha256(response), 'joint_names': names,
                 'world_sha256': hashlib.sha256(b'[]').hexdigest(),
                 'clearance_m': sweep.CLEARANCE, 'queries': 21}, {'result': 'PASS'})
    monkeypatch.setattr(sweep, 'certify_trajectory', certify)
    targets = {'openarm_left_hand_tcp': {'position': [0., 0., .1],
                                         'quaternion_wxyz': [1., 0., 0., 0.]}}
    return CuroboPlanner, core, q0, targets, recorded


def test_fake_ik_path_and_response(monkeypatch):
    CuroboPlanner, core, q0, targets, recorded = fake_core(monkeypatch, .1)
    trajectory = CuroboPlanner.plan_ik_path(core, q0, targets=targets,
                                            active_joints=core.joint_names[:7])
    assert recorded['return_seeds'] == 1
    assert np.array_equal(recorded['seed'], q0[None])
    assert core.last_failure is None
    assert trajectory.shape == (21, 14)
    from curobo_v2.service.curobo_service.planner import PlannerAdapter
    adapter = PlannerAdapter.__new__(PlannerAdapter)
    adapter._planner = core
    adapter._world = SimpleNamespace(active=lambda: 'test', get=lambda _: SimpleNamespace(revision=1, sha256='world'))
    from threading import RLock
    adapter._lock = RLock()
    adapter._ensure_world = lambda env_id: None
    core.reorder = lambda values, names: values
    core.set_attachment = lambda attachment: None
    core.plan_ik_path = lambda *a, **kw: CuroboPlanner.plan_ik_path(core, *a, **kw)
    core.identity = {}
    standard_keys = set(adapter._result(trajectory, core.joint_names))
    response = adapter.plan_ik_path('test', q0, targets=targets,
                                    active_joints=core.joint_names[:7])
    assert standard_keys | {'source', 'ik_candidates', 'ik_candidate_distances',
                            'ik_selected_joint_distance_rad', 'ik_selected_max_joint_change_rad',
                            'ik_selection_seconds', 'ik_seconds'} <= set(response)
    assert response['source'] == 'ik_path'
    assert {'trajectory', 'joint_names', 'validation', 'time_from_start_seconds',
            'trajectory_derivatives', 'world_revision', 'world_sha256'} <= response.keys()
    assert response['validation']['continuous_geometry']['result'] == 'PASS'
    assert response['validation']['continuous_motion']['result'] == 'PASS'
    from curobo_v2.trajectory import TrajectoryLedger
    from curobo_v2.client import PlanningRejected
    ledger = TrajectoryLedger()
    identity = {'runtime': {'generation': 2}, 'nonce': 'test'}
    pid = ledger.issue(response, epoch=3, state=q0, world=[], identity=identity, attachment=None)
    assert ledger.claim(pid, epoch=3, state=q0, world=[], identity=identity, attachment=None)['source'] == 'ik_path'
    with pytest.raises(PlanningRejected):
        ledger.claim(pid, epoch=3, state=q0, world=[], identity=identity, attachment=None)


def test_ik_jump_rejected(monkeypatch):
    CuroboPlanner, core, q0, targets, _ = fake_core(monkeypatch, .4)
    assert CuroboPlanner.plan_ik_path(core, q0, targets=targets,
                                     active_joints=core.joint_names[:7]) is None
    assert core.last_failure == 'ik_path_joint_jump'


def test_candidate_distance_and_ties():
    names = list('abc')
    current = np.zeros(3)
    solutions = np.array([[.5, 0., 0.], [.3, .4, 0.], [.3, .4, 0.], [.1, 0., 9.]])
    order, distances, changes = ranked_ik_candidates(
        current, names, names[:2], solutions, [True] * 4)
    assert order == [3, 1, 2, 0]
    assert distances == pytest.approx([.5, .5, .5, .1])
    assert changes == pytest.approx([.5, .4, .4, .1])


def test_candidates_choose_nearest_feasible_and_skip_costly_checks(monkeypatch):
    CuroboPlanner, core, q0, targets, recorded = fake_core(monkeypatch, [.25, .1, .2])
    checked = []
    original = core.validate
    def validate(trajectory, planner, checker, **kw):
        if kw.get('measured_start') is False:
            checked.append(float(trajectory[0, 0]))
            return float(trajectory[0, 0]) != .1
        return original(trajectory, planner, checker, **kw)
    core.validate = validate
    trajectory = CuroboPlanner.plan_ik_path(core, q0, targets=targets,
        active_joints=core.joint_names[:7], ik_candidates=3)
    assert recorded['return_seeds'] == 3
    assert checked == pytest.approx([.1, .2])
    assert trajectory[-1, 0] == pytest.approx(.2)
    assert core.last_ik_path_metrics['selected_candidate_index'] == 2
    assert core.last_ik_path_metrics['candidate_distances'] == pytest.approx([.25, .1, .2])


@pytest.mark.parametrize('jumps,successes,reason', [
    ([.1, .2], [False, False], 'official_ik_non_success'),
    ([.1, .2], None, 'ik_geometry_validation_failed'),
    ([.4, .5], None, 'ik_path_joint_jump'),
    ([.1, .2], None, 'ik_joint_limits'),
    ([.1, .2], None, 'ik_tool_endpoint_error'),
])
def test_candidate_failure_reasons(monkeypatch, jumps, successes, reason):
    CuroboPlanner, core, q0, targets, _ = fake_core(monkeypatch, jumps, successes)
    if reason == 'ik_geometry_validation_failed':
        original = core.validate
        core.validate = lambda trajectory, planner, checker, **kw: (
            False if kw.get('measured_start') is False else
            original(trajectory, planner, checker, **kw))
    if reason == 'ik_joint_limits':
        core.limits[0] = [-.05, .05]
    if reason == 'ik_tool_endpoint_error':
        core._validate_tool_endpoints = lambda *a: False
    assert CuroboPlanner.plan_ik_path(core, q0, targets=targets,
        active_joints=core.joint_names[:7], ik_candidates=2) is None
    assert core.last_failure == reason


def test_mode_and_client_allowlist(monkeypatch, tmp_path):
    from curobo_v2.sapien_planner import SapienV2Planner
    from curobo_v2.client import V2Client, PlanningRejected
    planner = SapienV2Planner.__new__(SapienV2Planner)
    planner.rig = SimpleNamespace(robot=SimpleNamespace(root_pose=SimpleNamespace(
        to_transformation_matrix=lambda: np.eye(4))))
    seen = []
    planner._plan = lambda path, extra, **kw: seen.append((path, extra))
    monkeypatch.delenv('ATTN_PLANNER_MODE', raising=False)
    planner.plan_pose([0, 0, 0], np.eye(3), side='left', epoch=1)
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'ik_path')
    planner.plan_pose([0, 0, 0], np.eye(3), side='left', epoch=1)
    assert [path for path, _ in seen] == ['/plan', '/plan/ik_path']
    assert 'ik_candidates' not in seen[0][1]
    assert seen[1][1]['ik_candidates'] == 1
    monkeypatch.setenv('ATTN_IK_CANDIDATES', '4')
    planner.plan_pose([0, 0, 0], np.eye(3), side='left', epoch=1)
    assert seen[-1][1]['ik_candidates'] == 4
    for bad in ('0', '-2', '1.5', 'abc', ' 4', ''):
        monkeypatch.setenv('ATTN_IK_CANDIDATES', bad)
        with pytest.raises(ValueError, match='ATTN_IK_CANDIDATES'):
            planner.plan_pose([0, 0, 0], np.eye(3), side='left', epoch=1)
    monkeypatch.setenv('ATTN_PLANNER_MODE', 'bad')
    with pytest.raises(ValueError, match='ATTN_PLANNER_MODE'):
        planner.plan_pose([0, 0, 0], np.eye(3), side='left', epoch=1)
    client = V2Client.__new__(V2Client)
    client.failed = False; client.worlds = {}; client.identity = {}; client.evidence = tmp_path
    client.nonce = 'nonce'; client.lock_sha = 'lock'; client._check_live = lambda: None
    client.attest = lambda: None
    with pytest.raises(PlanningRejected, match='world upload'):
        client.call('/plan/ik_path', {'env_id': 'test'})
