"""Seed 114 request geometry; CPU arithmetic and explicit native/GPU test doubles."""
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest

from curobo_v2.attachment import SingleArmAttachment, cuboid_pose
from curobo_v2.execution_settings import R3_20261025, R4_20261027
from curobo_v2.proxy_departure import support_departure
from test_ik_path import fake_core

SUPPORT = 'mujoco:1214:counter_1_left_group_top_left_1'


def request_geometry():
    data = json.loads((Path(__file__).parent / 'fixtures/c21b_seed114_lift.json').read_text())
    context = data['attachment']
    attachment = SingleArmAttachment(context, data['world'], [context['tool_frame']],
                                     execution_settings=R4_20261027)
    tcp = attachment.initial_pose @ np.linalg.inv(attachment.transform)
    target = data['target']
    goal = cuboid_pose(dict(center=target['position'], quat_wxyz=target['quaternion_wxyz']))
    def at(pose):
        spheres = attachment.spheres[:attachment.sphere_count].copy()
        spheres[:, :3] = spheres[:, :3] @ pose[:3, :3].T + pose[:3, 3]
        return spheres
    return data, attachment, at(tcp), at(goal)


def test_seed114_goal_precheck_and_measured_departure():
    data, attachment, start, goal = request_geometry()
    assert attachment.sphere_count == 95 and len(attachment.cuboids) == 32
    result = support_departure(goal[None], data['world'], attachment.supports, measured_start=False)
    gaps = np.array(result['supports'][SUPPORT]['start_separation_m'])
    # C21 rejected these four at sample 0: unmeasured => all ineligible => 5 mm.
    assert np.flatnonzero(gaps < .005 - 1e-7).tolist() == [60, 63, 66, 86]
    for index, logged in data['logged_goal_gaps'].items():
        # Nominal target reconstruction, not GPU FK replay; logged IK residuals
        # account for the <6 micrometre differences in these four separations.
        assert gaps[int(index)] == pytest.approx(logged, abs=6e-6)
    assert result['collision_free'] and result['authoritative'] is False
    assert result['measured_start'] is False
    assert result['supports'][SUPPORT]['floor_m'] == [-.0001] * 95
    path = np.array([start + t * (goal - start) for t in np.linspace(0, 1, 101)])
    result = support_departure(path, data['world'], attachment.supports)
    row = result['supports'][SUPPORT]
    assert result['collision_free']
    assert row['start_separation_m'][66] == pytest.approx(-.02121800164679713)
    assert row['floor_m'][66] == pytest.approx(-.02131800164679713)


def test_logged_goal_clearance_is_not_running_maximum():
    data, attachment, start, goal = request_geometry()
    # Small downward variation while fully clear of the support remains legal
    # under geometry.py / sweep.py's fixed measured-start floor.
    settled = goal.copy(); settled[:, 2] -= .001
    result = support_departure(np.array([start, goal, settled]), data['world'], attachment.supports)
    assert result['collision_free']
    deeper = start.copy(); deeper[:, 2] -= .0003
    result = support_departure(np.array([start, deeper]), data['world'], attachment.supports)
    assert not result['collision_free']
    assert result['support'] == SUPPORT and result['sample'] == 1
    assert 66 in result['sphere_indices']


def integrated_core(monkeypatch, *, checker_clear=True, exact_clear=True, profile=R4_20261027):
    cls, core, q, targets, recorded = fake_core(monkeypatch, .1)
    data, attachment, start, goal = request_geometry()
    class Tensor(np.ndarray):
        def detach(self): return self
        def cpu(self): return self
        def numpy(self): return np.asarray(self)
        def contiguous(self): return self
        def split(self, n): return [self[i:i+n] for i in range(0, len(self), n)]
    tensor = lambda v: np.asarray(v).view(Tensor)
    class Scene: pass
    scene = Scene()
    scene._support_names = attachment.supports
    scene._object_names = attachment.names
    scene._attached_indices = tuple(range(95))
    module = ModuleType('curobo_v2.service.curobo_service.contact_scene')
    module.CarriedSceneCollision = Scene
    monkeypatch.setitem(sys.modules, module.__name__, module)
    planner = next(iter(core._models.values()))[0]
    planner.scene_collision_checker = scene
    # A synthetic prismatic path through the real request's carried shapes;
    # no claim to reconstruct robot FK or re-run the real solver.
    planner.compute_kinematics = lambda state: NS(robot_spheres=tensor(
        start[None] + (state.position[:, 0] / .1)[:, None, None] * (goal - start)[None]))
    checker = NS(scene_model=scene, validate=lambda values: tensor([checker_clear] * len(values)))
    core._select = lambda *a, **kw: (planner, checker)
    core._state = lambda dense, planner: NS(position=tensor(dense))
    planner.ik_solver.solve_pose = lambda *a, **kw: NS(
        success=tensor([[True]]), solution=tensor([[[.1, 0, 0, 0, 0, 0, 0]]]))
    core.max_joint_step = .005
    core.execution_settings = profile
    core.attachment = attachment
    core._attachment_context = data['attachment']
    core._world_cuboids = data['world']
    calls = []
    def exact(samples, locks, *, measured_start):
        calls.append((len(samples), measured_start))
        return dict(collision_free=exact_clear)
    core.geometry = NS(validate=exact)
    core._models = {'test': (planner, checker, None, {})}
    core.validate = lambda *a, **kw: cls.validate(core, *a, **kw)
    return cls, core, q, targets, planner, checker, calls


@pytest.mark.parametrize('profile', [R3_20261025, R4_20261027])
def test_lift_runs_goal_dense_and_sweep_gates(monkeypatch, profile):
    cls, core, q, targets, _, _, calls = integrated_core(monkeypatch, profile=profile)
    assert cls.plan_ik_path(core, q, targets=targets, active_joints=core.joint_names[:7]) is not None
    assert calls[0] == (1, True) and calls[1] == (1, False)
    assert calls[2][0] > 2 and calls[2][1] is True
    assert core.last_validation['continuous_geometry']['result'] == 'PASS'
    assert ('support_departure' in core.last_validation) is (profile == R4_20261027)


@pytest.mark.parametrize('gate', ['checker', 'exact', 'exact_goal', 'exact_dense', 'missing_exact', 'sweep'])
def test_support_diagnostics_never_mask_a_real_gate(monkeypatch, gate):
    cls, core, q, targets, _, _, _ = integrated_core(
        monkeypatch, checker_clear=gate != 'checker', exact_clear=gate != 'exact')
    if gate == 'missing_exact': core.geometry = None
    if gate in {'exact_goal', 'exact_dense'}:
        core.geometry.validate = lambda samples, locks, measured_start: dict(
            collision_free=measured_start if gate == 'exact_goal' else len(samples) == 1)
    if gate == 'sweep':
        import curobo_v2.sweep as sweep
        monkeypatch.setattr(sweep, 'certify_trajectory', lambda *a, **kw: (
            dict(result='未验证', failure=dict(reason='fixture_collision')), dict(result='PASS')))
    assert cls.plan_ik_path(core, q, targets=targets, active_joints=core.joint_names[:7]) is None
    if gate == 'sweep':
        assert core.last_failure == 'service_continuous_command_validation_rejected'
    if gate == 'missing_exact':
        assert core.last_validation['reason'] == 'carried_support_exact_geometry_required'
    if gate == 'exact_goal': assert core.last_failure == 'ik_geometry_validation_failed'
    if gate == 'exact_dense': assert core.last_failure == 'trajectory_validation_failed'


def test_before_grasp_object_contact_is_still_checked(monkeypatch):
    cls, core, q, _, planner, checker, _ = integrated_core(monkeypatch, checker_clear=False)
    core.attachment = None
    core._attachment_context = None
    assert not cls.validate(core, q[None], planner, checker)
    assert 'support_departure' not in core.last_validation
    assert 'proxy_support_exemption' not in core.last_validation


def test_inflated_support_proxy_cannot_override_complete_shapes(monkeypatch):
    cls, core, q, _, planner, checker, _ = integrated_core(monkeypatch)
    compute = planner.compute_kinematics
    def extra_inflation(state):
        result = compute(state)
        # Increased sphere overapproximation at the goal can breach a per-sphere
        # floor even though the complete shape validator certifies departure.
        result.robot_spheres[..., 3] += (state.position[:, 0] / .1)[:, None] * .04
        return result
    planner.compute_kinematics = extra_inflation
    goal_q = q.copy(); goal_q[0] = .1
    assert cls.validate(core, np.array([q, goal_q]), planner, checker)
    report = core.last_validation['support_departure']
    assert not report['collision_free'] and not report['authoritative']
    assert core.last_validation['exact_geometry']['collision_free']
    assert core.last_validation['collision_free']
