"""Recorded seed 102 second lift: CPU geometry, no robot models or native replay."""
import itertools
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from curobo_v2.attachment import SingleArmAttachment, cover_cuboid, cuboid_pose
from curobo_v2.execution_settings import R3_20261025, R4_20261027
from curobo_v2.proxy_departure import support_departure
from test_c21b_support_departure import integrated_core

SUPPORT = 'mujoco:1214:counter_1_left_group_top_left_1'
# Zero-based carried sphere, source shape suffix, cell index, signed gap in mm.
CONTACTS = [
    (0, 1683, 0, 3.248682301738), (1, 1683, 1, .731462362355),
    (2, 1683, 2, -1.902454369275), (3, 1683, 3, -4.586157388217),
    (8, 1683, 8, 3.886469511700), (9, 1683, 9, 1.590317188625),
    (10, 1683, 10, -.898240438071), (11, 1683, 11, -3.484834114873),
    (16, 1684, 0, 1.102702724811), (17, 1684, 1, 3.258486538777),
    (18, 1684, 2, 4.778191880317), (22, 1684, 6, 1.004939814540),
    (23, 1684, 7, 3.483423135386), (28, 1685, 0, 3.946641504082),
    (38, 1688, 0, -3.068871776511), (39, 1688, 1, -.416325899832),
    (40, 1688, 2, 2.156714476866), (41, 1688, 3, 4.425607338275),
    (46, 1688, 8, -1.975201781913), (47, 1688, 9, .564354856297),
    (48, 1688, 10, 2.938951806933), (49, 1688, 11, 4.893366348885),
]


def recorded_geometry():
    data = json.loads((Path(__file__).parent / 'fixtures/c21c_seed102_second_lift.json').read_text())
    context = data['request']['attachment']
    attachment = SingleArmAttachment(context, data['world'], [context['tool_frame']],
                                     execution_settings=R4_20261027)
    tcp = attachment.initial_pose @ np.linalg.inv(attachment.transform)
    spheres = attachment.spheres[:attachment.sphere_count].copy()
    spheres[:, :3] = spheres[:, :3] @ tcp[:3, :3].T + tcp[:3, 3]
    return data, attachment, spheres


def test_recorded_start_pairs_against_all_789_static_cuboids():
    data, attachment, spheres = recorded_geometry()
    assert len(data['world']) == 795 and attachment.sphere_count == 54
    assert not attachment.supports
    assert SUPPORT in data['previous_attachment']['support_names']
    mapping = []
    for shape in attachment.cuboids:
        cells = cover_cuboid(shape, attachment.transform @ np.linalg.inv(attachment.initial_pose))
        mapping.extend((shape['name'], i) for i in range(len(cells)))
    # Enumerating all static bodies here is diagnostic, not declaring supports.
    report = support_departure(spheres[None], data['world'],
                               {c['name'] for c in data['world']} - attachment.names)
    near = {name: np.flatnonzero(np.array(row['start_separation_m']) < .005).tolist()
            for name, row in report['supports'].items()
            if min(row['start_separation_m']) < .005}
    assert near == {SUPPORT: [row[0] for row in CONTACTS]}
    gaps = report['supports'][SUPPORT]['start_separation_m']
    for index, shape, cell, mm in CONTACTS:
        assert mapping[index] == (f'entity:257:shape:{shape}', cell)
        assert gaps[index] == pytest.approx(mm / 1000, abs=1e-12)
    assert sum(gap < 0 for gap in gaps) == 7
    other = min((min(row['start_separation_m']), name)
                for name, row in report['supports'].items() if name != SUPPORT)
    assert other[1] == 'mujoco:1252:dishwasher_left_group_g6'
    assert other[0] == pytest.approx(.03090577073793755)


def test_recorded_complete_shapes_are_clear_of_original_support():
    data, attachment, _ = recorded_geometry()
    support = next(c for c in data['world'] if c['name'] == SUPPORT)
    pose = cuboid_pose(support)
    gaps = []
    for shape in attachment.cuboids:
        vertices = np.array(list(itertools.product(*[[-h, h] for h in shape['half_size']])))
        shape_pose = cuboid_pose(shape)
        world = vertices @ shape_pose[:3, :3].T + shape_pose[:3, 3]
        local = (world - pose[:3, 3]) @ pose[:3, :3]
        bottom = local[np.argmin(local[:, 2])]
        # Closest vertex projects inside the top face: vertical gap is the
        # Euclidean cuboid separation, not merely an infinite-plane lower bound.
        assert np.all(abs(bottom[:2]) < support['half_size'][:2])
        gaps.append(bottom[2] - support['half_size'][2])
    assert gaps == pytest.approx([.019224075881966354, .021132682104239867,
                                 .029560456998946508, .030855392007604523,
                                 .023133537960036213, .019651103739465672])
    validation = data['logged_start_validation']
    assert validation['joint_limits'] and validation['exact_geometry']['collision_free']
    assert not validation['proxy_collision_free']
    assert data['logged_first_lift']['continuous_geometry_result'] == 'PASS'
    assert attachment.initial_pose[2, 3] - data['previous_attachment']['object_pose'][2][3] == pytest.approx(.0241584665967989)


@pytest.mark.parametrize('profile', [R3_20261025, R4_20261027, None])
def test_empty_support_declaration_cannot_override_start_checker(monkeypatch, profile):
    cls, core, q, targets, _, _, calls = integrated_core(monkeypatch, profile=profile)
    data, attachment, _ = recorded_geometry()
    core.attachment = attachment
    core._attachment_context = data['request']['attachment']
    core._world_cuboids = data['world']
    _, checker = core._select(q)
    checker.validate = lambda values: np.zeros(len(values), dtype=bool).view(type(core._state(q[None], None).position))
    assert cls.plan_ik_path(core, q, targets=targets, active_joints=core.joint_names[:7]) is None
    assert core.last_failure == 'start_state_collision_or_constraint_violation'
    assert calls == [(1, True)]
    assert 'support_departure' not in core.last_validation
    assert core.last_validation['exact_geometry']['collision_free']


@pytest.mark.parametrize('gate', ['clear', 'checker', 'exact_start', 'joint_limit', 'sweep'])
def test_declared_original_support_start_uses_exact_and_sweep(monkeypatch, gate):
    cls, core, q, targets, planner, checker, calls = integrated_core(
        monkeypatch, checker_clear=gate != 'checker', exact_clear=gate != 'exact_start')
    data, _, spheres = recorded_geometry()
    # Explicit conditional case requested by #12. The historical request has
    # no declaration; do not infer or silently grant one in production.
    context = deepcopy(data['request']['attachment'])
    context['support_names'] = data['previous_attachment']['support_names']
    attachment = SingleArmAttachment(context, data['world'], [context['tool_frame']],
                                     execution_settings=R4_20261027)
    core.attachment = attachment
    core._attachment_context = context
    core._world_cuboids = data['world']
    scene = checker.scene_model
    scene._support_names = attachment.supports
    scene._object_names = attachment.names
    scene._attached_indices = tuple(range(attachment.sphere_count))
    tensor_type = type(core._state(q[None], planner).position)
    def kinematics(state):
        values = np.repeat(spheres[None], len(state.position), axis=0)
        values[:, :, 2] += (state.position[:, 0] / .1)[:, None] * .025
        return NS(robot_spheres=values.view(tensor_type))
    planner.compute_kinematics = kinematics
    if gate == 'joint_limit':
        core.limits[0, 0] = .01
    if gate == 'sweep':
        import curobo_v2.sweep as sweep
        monkeypatch.setattr(sweep, 'certify_trajectory', lambda *a, **kw: (
            dict(result='未验证', failure=dict(reason='fixture_collision')), dict(result='PASS')))
    result = cls.plan_ik_path(core, q, targets=targets, active_joints=core.joint_names[:7])
    assert (result is not None) is (gate == 'clear')
    if gate == 'clear':
        assert calls[0] == (1, True) and calls[1] == (1, False)
        assert calls[2][0] > 2 and calls[2][1] is True
        assert core.last_validation['continuous_geometry']['result'] == 'PASS'
        row = core.last_validation['support_departure']['supports'][SUPPORT]
        assert row['start_separation_m'][3] == pytest.approx(-.00458615738821657)
    elif gate != 'sweep':
        assert core.last_failure == 'start_state_collision_or_constraint_violation'
    else:
        assert core.last_failure == 'service_continuous_command_validation_rejected'
