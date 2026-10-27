"""C21d: uninterrupted grasp provenance, recorded CPU geometry and native doubles."""
from copy import deepcopy
from types import SimpleNamespace as NS

import numpy as np
import pytest

from curobo_v2.attachment import SingleArmAttachment
from curobo_v2.execution_settings import R3_20261025, R4_20261027
from curobo_v2.proxy_departure import support_departure
from curobo_v2.service.curobo_service.geometry import ExactGeometryValidator
from openarm365_dev import attachment as native, executor as ex, planner as plan
from openarm365_dev_v2.tests.test_held_reference import FakeExecutor
from openarm365_dev_v2.tests.test_motion_tracking_diagnostics import fake_apply
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment
from test_c21b_support_departure import integrated_core
from test_c21c_start_departure import recorded_geometry, SUPPORT


def inventory(data):
    # Synthetic body ids, explicitly supplied as inventory (not parsed from geom
    # ids in world names). The recorded geometry is real; no native model read.
    return [dict(name=c['name'], body_id=i+100, body_name=f'body-{i}')
            for i, c in enumerate(data['world'])]


def retained_context(*, first_empty=False):
    data, _, spheres = recorded_geometry()
    state = dict(attach_request_id='stable-close-102', supports=None)
    first = deepcopy(data['previous_attachment'])
    if first_empty:
        first['support_names'] = []
    native.retain_declared_supports(first, state, inventory(data))
    context = native.retain_declared_supports(data['request']['attachment'], state, inventory(data))
    return data, context, state, spheres


def test_freeze_first_declaration_merge_without_growing_and_identity_check():
    data, context, state, _ = retained_context()
    assert context['recomputed_support_names'] == []
    assert context['support_names'] == data['previous_attachment']['support_names']
    assert len(state['supports']) == 12
    assert state['attach_request_id'] == 'stable-close-102'
    other = next(c['name'] for c in data['world'] if 'dishwasher' in c['name'])
    later = deepcopy(data['request']['attachment']); later['support_names'] = [other]
    merged = native.retain_declared_supports(later, state, inventory(data))
    assert other in merged['support_names']  # Existing per-request rule is unchanged.
    assert other not in {row['name'] for row in state['supports']}
    again = native.retain_declared_supports(data['request']['attachment'], state, inventory(data))
    assert other not in again['support_names']
    context['support_retention']['supports'].clear()
    assert len(state['supports']) == 12  # Evidence cannot mutate the executor.
    changed = inventory(data)
    row = next(row for row in changed if row['name'] == SUPPORT)
    row['body_id'] += 10000
    with pytest.raises(ValueError, match='identity changed'):
        native.retain_declared_supports(later, state, changed)


def test_empty_first_declaration_is_frozen_even_if_later_nearby():
    data, context, state, _ = retained_context(first_empty=True)
    assert context['support_names'] == [] and state['supports'] == []
    native.retain_declared_supports(data['previous_attachment'], state, inventory(data))
    assert state['supports'] == []
    assert native.retain_declared_supports(data['request']['attachment'], state,
                                          inventory(data))['support_names'] == []


@pytest.mark.parametrize('profile', [R3_20261025, None])
def test_retention_wire_is_r4_only(profile):
    data, context, _, _ = retained_context()
    with pytest.raises(ValueError, match='explicit measured attachment'):
        SingleArmAttachment(context, data['world'], [context['tool_frame']], execution_settings=profile)


def test_provenance_cannot_silently_add_an_undeclared_support():
    data, context, _, _ = retained_context()
    context['support_names'].append(next(c['name'] for c in data['world'] if 'dishwasher' in c['name']))
    with pytest.raises(ValueError, match='exactly account'):
        SingleArmAttachment(context, data['world'], [context['tool_frame']], execution_settings=R4_20261027)


def request_models(source, name, monkeypatch, config):
    """Load actual HTTP schemas without constructing the service or its models."""
    import ast
    import sys
    from types import ModuleType
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom, ast.ClassDef, ast.FunctionDef))
                 and not (isinstance(node, ast.ImportFrom) and node.level)
                 and getattr(node, 'name', '') != 'create_app']
    module = ModuleType(name); module.Config = NS(ROBOT_CFG=str(config))
    monkeypatch.setitem(sys.modules, name, module)
    exec(compile(tree, name, 'exec'), module.__dict__)
    return module


def test_http_retention_roundtrip_and_r3_legacy_bytes(monkeypatch, tmp_path, r4_deployment):
    from pathlib import Path
    import subprocess
    from pydantic import ValidationError
    from benchmarks.attention_harness.tests.r3_fixtures import fake_deployment
    config = tmp_path/'robot.yml'; config.write_text('robot_cfg:\n  kinematics:\n    base_link: base\nservice: {}\n')
    path = 'curobo_v2/service/curobo_service/server.py'
    new = request_models(Path(path).read_text(), 'c21d_server', monkeypatch, config)
    old = request_models(subprocess.check_output(['git', 'show', 'b553472:'+path], text=True),
                         'c21d_old_server', monkeypatch, config)
    data, context, _, _ = retained_context()
    bodies = [
        ('WarmupRequest', dict(current_q=[0.]*7)),
        ('JointPlanRequest', dict(current_q=[0.]*7, target_qpos=[.1]*7)),
        ('PlanRequest', dict(current_q=[0.]*7, target_pose=[0., 0., .1])),
        ('IKPathRequest', dict(current_q=[0.]*7, active_joints=[f'openarm_left_joint{i}' for i in range(1, 8)],
                               targets={'openarm_left_hand_tcp': dict(position=[0., 0., .1])})),
    ]
    r3 = fake_deployment(monkeypatch, tmp_path)
    for name, body in bodies:
        schema = getattr(new, name)
        req = schema.model_validate(dict(body, attachment=context, execution_settings=r4_deployment.to_dict()))
        assert req.attachment.model_dump() == context
        assert req.model_dump()['attachment'] == context
        for settings in (None, r3.to_dict()):
            wire = dict(body, attachment=data['request']['attachment'])
            if settings is not None:
                wire['execution_settings'] = settings
            assert schema.model_validate(wire).model_dump_json() == getattr(old, name).model_validate(wire).model_dump_json()
            with pytest.raises(ValidationError, match='require r4'):
                schema.model_validate(dict(wire, attachment=context))
    partial = deepcopy(context); partial.pop('recomputed_support_names')
    with pytest.raises(ValidationError, match='complete provenance'):
        new.SingleAttachmentRequest.model_validate(partial)


def fake_executor(monkeypatch, settings):
    obj = FakeExecutor([0], 'fixed'); obj.execution_settings = settings
    obj.base_hold = np.zeros(3)
    obj.env = NS(sim=NS(data=NS(qpos=np.zeros(3))), robots=[NS(_ref_base_joint_pos_indexes=[0, 1, 2])])
    obj.record_drift_sample = ex.NativeExecutor.record_drift_sample.__get__(obj)
    obj.drift_receipt = ex.NativeExecutor.drift_receipt.__get__(obj)
    monkeypatch.setattr(ex, 'tcp_pose', lambda *a: np.eye(4))
    monkeypatch.setattr(native, 'object_pose', lambda *a: np.eye(4))
    return obj


def test_executor_release_and_replacement_clear_retention(monkeypatch, r4_deployment):
    obj = fake_executor(monkeypatch, r4_deployment)
    close = ex.NativeExecutor.gripper(obj, 'left', 1., request_id='close-1')
    state = obj.held['support_retention']
    assert state == dict(attach_request_id='close-1', supports=None)
    state['supports'] = [dict(name='old', body_id=10, body_name='counter')]
    assert close['attached_object']['support_retention']['supports'] is None
    # Replacing the attachment without an intervening open also starts fresh.
    ex.NativeExecutor.gripper(obj, 'left', 1., request_id='close-2')
    assert obj.held['support_retention'] == dict(attach_request_id='close-2', supports=None)
    released = ex.NativeExecutor.gripper(obj, 'left', -1., request_id='release')
    assert obj.held is None and obj.held_transform is None
    assert released['attached_object'] is None
    ex.NativeExecutor.gripper(obj, 'left', 1., request_id='close-3')
    assert obj.held['support_retention'] == dict(attach_request_id='close-3', supports=None)


@pytest.mark.parametrize('profile', [R3_20261025, R4_20261027, None])
def test_planner_consecutive_requests_and_receipts(monkeypatch, tmp_path, r4_deployment, profile):
    from benchmarks.attention_harness.tests.r3_fixtures import fake_deployment
    settings = r4_deployment if profile == R4_20261027 else fake_deployment(monkeypatch, tmp_path) if profile else None
    data, _, _ = recorded_geometry()
    held = dict(holder='left', target_id=257)
    if profile == R4_20261027:
        held['support_retention'] = dict(attach_request_id='close-102', supports=None)
    contexts = iter([data['previous_attachment'], data['request']['attachment']])
    def measured(env, signature, *args):
        assert signature == dict(holder='left', target_id=257)
        return deepcopy(next(contexts))
    monkeypatch.setattr(native, 'planner_context', measured)
    requests = []; issued = []
    def call(path, body):
        requests.append((path, deepcopy(body)))
        return dict(trajectory=[[0.], [.1]])
    owner = NS(execution_settings=settings, env=NS(), tools=[], evidence=tmp_path, sequence=1,
               prepared={('joint',)}, attachment_provider=lambda: held.copy(),
               upload=lambda: (dict(cuboids=data['world'], inventory=inventory(data)), {}),
               body=lambda active: dict(current_q=[0.]), guard=lambda op: op(),
               check_goal_margin=lambda *a: {}, client=NS(call=call, identity={}),
               ledger=NS(issue=lambda response, **kw: issued.append(deepcopy(kw)) or 'pid'))
    for _ in range(2):
        response = plan.MujocoV2Planner.plan(owner, '/plan/joint', {}, ['joint'], 'epoch')
    final = requests[-1][1]['attachment']
    assert requests[-2][0] == '/warmup'
    assert requests[-2][1]['attachment'] == final
    if profile == R4_20261027:
        assert final['recomputed_support_names'] == []
        assert final['support_names'] == data['previous_attachment']['support_names']
        assert response['support_retention'] == held['support_retention']
        assert issued[-1]['attachment'] == held
        obj, _, events, _ = fake_apply(monkeypatch, position=[.01, .02])
        obj.execution_settings = settings
        obj.planner.claim = lambda *a: dict(response, time_from_start_seconds=[.02])
        receipt = ex.NativeExecutor.apply(obj, {'plan_id': 'pid'}, request_id='lift-2')
        assert receipt['support_retention'] == held['support_retention']
        assert events[-1]['support_retention'] == held['support_retention']
    else:
        assert final == data['request']['attachment']
        assert 'support_retention' not in response and 'support_retention' not in held


def recorded_core(monkeypatch, *, first_empty=False, profile=R4_20261027, dz=.025, other_contact=False):
    cls, core, q, targets, planner, checker, calls = integrated_core(monkeypatch, profile=profile)
    data, context, state, spheres = retained_context(first_empty=first_empty)
    if profile != R4_20261027:
        context = deepcopy(data['request']['attachment'])
    attachment = SingleArmAttachment(context, data['world'], [context['tool_frame']], execution_settings=profile)
    core.attachment = attachment; core._attachment_context = context; core._world_cuboids = data['world']
    scene = checker.scene_model
    scene._support_names = attachment.supports; scene._object_names = attachment.names
    scene._attached_indices = tuple(range(54))
    tensor_type = type(core._state(q[None], planner).position)
    def kinematics(state):
        values = np.repeat(spheres[None], len(state.position), axis=0)
        values[:, :, 2] += (state.position[:, 0] / .1)[:, None] * dz
        return NS(robot_spheres=values.view(tensor_type))
    planner.compute_kinematics = kinematics
    # Real CPU sphere/cuboid distances against EVERY non-exempt static fixture.
    static = {c['name'] for c in data['world']} - attachment.names - attachment.supports
    def checked(values):
        proxy = kinematics(NS(position=values[:, 0])).robot_spheres
        report = support_departure(proxy, data['world'], static)
        clear = all(min(row['minimum_separation_m']) >= .005 for row in report['supports'].values())
        return np.full(len(values), clear).view(tensor_type)
    checker.validate = checked
    # Synthetic contacts drive the production exact gate. The real starting
    # gap comes from all six complete shapes reconstructed in C21c.
    current = {}
    other_fixture = next(c['name'] for c in data['world'] if 'dishwasher' in c['name'])
    def pose(robot):
        value = attachment.initial_pose.copy(); value[2, 3] += current['q'][0] / .1 * dz
        return value
    attachment.pose = pose
    def contacts():
        gap = .019224075881966354 + current['q'][0] / .1 * dz
        return [NS(bodies=[NS(entity=NS(name='carried')), NS(entity=NS(name='world:'+SUPPORT))],
                   points=[NS(separation=gap, position=np.zeros(3))])] + (
            [NS(bodies=[NS(entity=NS(name='carried')), NS(entity=NS(name='world:'+other_fixture))],
                points=[NS(separation=-.001, position=np.zeros(3))])] if other_contact else [])
    geom = NS(execution_settings=profile, attachment=attachment, joint_names=core.joint_names,
              names=core.joint_names, indices={n: i for i, n in enumerate(core.joint_names)}, joints={},
              clearance=.005, fixed_groups={}, robot=NS(set_qpos=lambda q: current.update(q=q.copy()),
              set_qvel=lambda q: None, set_qf=lambda q: None),
              sapien=NS(Pose=lambda p: p, physx=NS(PhysxRigidDynamicComponent=object)),
              carried_actor=NS(name='carried', find_component_by_type=lambda cls: NS()),
              scene=NS(step=lambda: None, get_contacts=contacts))
    core.geometry = NS(native_fixture=geom,
                       validate=lambda *a, **kw: ExactGeometryValidator.validate_single_attachment(geom, *a, **kw))
    return cls, core, q, targets, planner, checker


@pytest.mark.parametrize('profile,first_empty,accepted', [
    (R4_20261027, False, True), (R3_20261025, False, False), (R4_20261027, True, False)])
def test_seed102_second_lift_retained_support(monkeypatch, profile, first_empty, accepted):
    cls, core, q, targets, _, _ = recorded_core(monkeypatch, profile=profile, first_empty=first_empty)
    assert core.attachment.sphere_count == 54
    result = cls.plan_ik_path(core, q, targets=targets, active_joints=core.joint_names[:7])
    assert (result is not None) == accepted
    if accepted:
        evidence = core.last_validation
        assert evidence['exact_geometry']['collision_free']
        assert evidence['continuous_geometry']['result'] == 'PASS'
        assert evidence['attachment']['recomputed_support_names'] == []
        assert evidence['attachment']['support_retention']['attach_request_id'] == 'stable-close-102'
        row = evidence['support_departure']['supports'][SUPPORT]
        assert row['start_separation_m'][3] == pytest.approx(-.00458615738821657)
        assert evidence['support_departure']['authoritative'] is False
    else:
        assert core.last_failure == 'start_state_collision_or_constraint_violation'


@pytest.mark.parametrize('gate', ['reapproach', 'other_proxy', 'other_exact', 'robot', 'joint', 'constraint', 'sweep'])
def test_retained_support_preserves_all_vetoes(monkeypatch, gate):
    cls, core, q, targets, planner, checker = recorded_core(
        monkeypatch, dz=-.025 if gate == 'reapproach' else .025, other_contact=gate == 'other_exact')
    if gate == 'other_proxy':
        fixture = deepcopy(next(c for c in core._world_cuboids if c['name'] == SUPPORT))
        # Move an existing, non-support fixture into proxy contact, keeping its
        # identity in the checker's explicit static set.
        other = next(c for c in core._world_cuboids if 'dishwasher' in c['name'])
        fixture['name'] = other['name']; other.update(fixture)
    if gate == 'robot':
        checker.validate = lambda v: np.zeros(len(v), bool).view(type(core._state(q[None], planner).position))
    if gate == 'joint':
        core.limits[0, 0] = .01
    if gate == 'constraint':
        pose = core.attachment.pose
        def inconsistent(robot):
            value = pose(robot); value[0, 3] += .011
            return value
        core.attachment.pose = inconsistent
    if gate == 'sweep':
        import curobo_v2.sweep as sweep
        def reject(response, **kw):
            assert kw['attachment']['support_retention'] == core._attachment_context['support_retention']
            return dict(result='未验证', failure=dict(reason='fixture_collision')), dict(result='PASS')
        monkeypatch.setattr(sweep, 'certify_trajectory', reject)
    assert cls.plan_ik_path(core, q, targets=targets, active_joints=core.joint_names[:7]) is None
    if gate == 'reapproach':
        assert core.last_failure == 'ik_geometry_validation_failed'
        assert not core.last_validation['exact_geometry']['collision_free']
    elif gate == 'sweep':
        assert core.last_failure == 'service_continuous_command_validation_rejected'
    else:
        assert core.last_failure == 'start_state_collision_or_constraint_violation'
        if gate == 'constraint':
            assert core.last_validation['exact_geometry']['reason'] == 'measured_attachment_inconsistent'


def test_retained_support_exact_floor_is_measured_anew(monkeypatch):
    cls, core, q, _, planner, checker = recorded_core(monkeypatch)
    # Drive the production exact contact gate with explicit native contact
    # doubles. TCP rises; a lower point re-approaches (e.g. during rotation).
    validate = core.geometry.validate
    geom = core.geometry.native_fixture
    def run(gaps):
        rows = iter(gaps)
        geom.scene.get_contacts = lambda: [NS(
            bodies=[NS(entity=NS(name='carried')), NS(entity=NS(name='world:'+SUPPORT))],
            points=[NS(separation=next(rows), position=np.zeros(3))])]
        end = q.copy(); end[0] = .1
        return validate(np.array([q, end]), {}, measured_start=True)
    assert run([-.004, -.003])['collision_free']
    # First call's -4.1 mm floor must not survive into a request starting clear.
    result = run([.019224075881966354, -.0003])
    assert not result['collision_free'] and result['sample'] == 1
    assert result['support_departure']['start_separation_m'] == {}
