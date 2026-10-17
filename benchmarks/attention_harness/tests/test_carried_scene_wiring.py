"""Small official cuRobo collision scenes; CPU Warp works without a GPU."""
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault('WARP_CACHE_PATH', '/tmp/warp-carried-tests')
curobo = pytest.importorskip('curobo')
import torch
from curobo.scene import Scene, Cuboid
from curobo.types import DeviceCfg
from curobo._src.geom.collision.buffer_collision import CollisionBuffer
from curobo._src.geom.collision.collision_scene import SceneCollision, SceneCollisionCfg
from curobo_v2.service.curobo_service.contact_scene import CarriedSceneCollision, configure_scene


def world():
    return Scene(cuboid=[
        Cuboid(name='support', pose=[0, 0, 0, 1, 0, 0, 0], dims=[1, 1, 1]),
        Cuboid(name='obstacle', pose=[2, 0, 0, 1, 0, 0, 0], dims=[1, 1, 1]),
    ])


def scene(cls=SceneCollision):
    device = DeviceCfg(device=torch.device('cpu'))
    cfg = SceneCollisionCfg(device_cfg=device, scene_model=world(), cache={'cuboid': 2})
    return cls.from_config(cfg), device


def cost(model, device, ordinary, carried, method='get_sphere_distance'):
    spheres = torch.tensor([[[[*(ordinary), .1], [*(carried), .1]]]], dtype=torch.float32)
    state = SimpleNamespace(robot_spheres=spheres)
    buffer = CollisionBuffer.from_shape(spheres.shape, device)
    return getattr(model, method)(state, buffer, torch.tensor([1.]),
                                  torch.tensor([0.])).detach().cpu()


def test_support_exemption_only_applies_to_carried_spheres():
    model, device = scene(CarriedSceneCollision)
    model.configure([1], 2, {'cuboid': 2})
    model.attachment(['object'], ['support'])
    model.load_collision_model(world())
    far = [5., 0., 0.]
    assert cost(model, device, far, [0., 0., 0.]).sum() == 0
    assert cost(model, device, far, [2., 0., 0.]).sum() > 0
    assert cost(model, device, [0., 0., 0.], far).sum() > 0
    assert cost(model, device, far, [0., 0., 0.], 'get_sphere_collision').sum() == 0


def test_no_exemption_uses_official_scene_result():
    baseline, device = scene()
    far = [5., 0., 0.]
    assert cost(baseline, device, far, [0., 0., 0.]).sum() > 0
    # The no-attachment and empty-support branches retain SceneCollision.
    assert type(baseline) is SceneCollision


@pytest.mark.parametrize('indices,total', [([], 2), ([2], 2), ([-1], 2), ([1, 1], 2)])
def test_invalid_sphere_mapping_fails_closed(indices, total):
    model, _ = scene(CarriedSceneCollision)
    with pytest.raises(ValueError, match='mapping'):
        model.configure(indices, total, {'cuboid': 2})


def test_unknown_support_and_changed_mapping_fail_closed():
    model, device = scene(CarriedSceneCollision)
    model.configure([1], 2, {'cuboid': 2})
    model.attachment(['object'], ['absent'])
    with pytest.raises(ValueError, match='geometry'):
        model.load_collision_model(world())
    model.attachment(['object'], ['support'])
    model.load_collision_model(world())
    spheres = torch.zeros((1, 1, 3, 4))
    with pytest.raises(ValueError, match='mapping'):
        model.get_sphere_distance(SimpleNamespace(robot_spheres=spheres),
                                  CollisionBuffer.from_shape(spheres.shape, device),
                                  torch.tensor([1.]), torch.tensor([0.]))


def test_changed_official_link_slot_fails_closed():
    model, _ = scene(CarriedSceneCollision)
    attachment = SimpleNamespace(link='carried', spheres=torch.tensor([[0., 0., 0., .1]]), sphere_count=1,
                                 names={'object'}, supports={'support'})
    def kinematics(indices):
        params = SimpleNamespace(total_spheres=2,
            link_spheres=torch.tensor([[[0., 0., 0., .1], [0., 0., 0., .1]]]),
            get_sphere_index_from_link_name=lambda link: torch.tensor(indices))
        return SimpleNamespace(config=SimpleNamespace(kinematics_config=params))
    configure_scene(model, kinematics([1]), attachment, {'cuboid': 2}, world())
    with pytest.raises(ValueError, match='mapping'):
        configure_scene(model, kinematics([0]), attachment, {'cuboid': 2}, world())


def test_measured_54_spheres_are_distinct_from_256_capacity():
    model, _ = scene(CarriedSceneCollision)
    declared = torch.zeros((256, 4))
    declared[:, 3] = -100.
    declared[:54, 3] = .01
    attachment = SimpleNamespace(link='carried', spheres=declared, sphere_count=54,
                                 names={'object'}, supports={'support'})

    def kinematics(link_slots=54, active=54, first=12, disabled_radius=-100.):
        total = first + link_slots + 3  # Other links' spheres belong to the same global list.
        spheres = torch.zeros((1, total, 4))
        spheres[..., 3] = disabled_radius
        spheres[:, :first, 3] = .1
        spheres[:, first:first + active, 3] = .01
        indices = torch.arange(first, first + link_slots)
        params = SimpleNamespace(total_spheres=total, link_spheres=spheres,
            get_sphere_index_from_link_name=lambda link: indices)
        return SimpleNamespace(config=SimpleNamespace(kinematics_config=params))

    assert configure_scene(model, kinematics(), attachment, {'cuboid': 2}, world()) == 54
    assert model._attached_indices == tuple(range(12, 66))
    # An official layout with reserved disabled slots must mask only its live spheres.
    padded, _ = scene(CarriedSceneCollision)
    assert configure_scene(padded, kinematics(link_slots=256), attachment,
                           {'cuboid': 2}, world()) == 54
    assert padded._attached_indices == tuple(range(12, 66))
    zero_padded, _ = scene(CarriedSceneCollision)
    assert configure_scene(zero_padded, kinematics(link_slots=256, disabled_radius=0.),
                           attachment, {'cuboid': 2}, world()) == 54
    for active in (53, 55):
        changed, _ = scene(CarriedSceneCollision)
        with pytest.raises(ValueError, match=r"link 'carried': expected active=54, actual active=(53|55).+expected indices=12\.\.65"):
            configure_scene(changed, kinematics(link_slots=256, active=active),
                            attachment, {'cuboid': 2}, world())
    with pytest.raises(ValueError, match=r'mapping differs for link \'carried\'.+actual active=54'):
        configure_scene(model, kinematics(first=13), attachment, {'cuboid': 2}, world())


def test_partially_constructed_carried_planner_can_be_destroyed():
    from curobo_v2.service.curobo_service.contact_scene import CarriedMotionPlanner
    planner = object.__new__(CarriedMotionPlanner)
    planner._destroyed = False
    calls = []
    planner.ik_solver = SimpleNamespace(destroy=lambda: calls.append('ik'))
    planner.destroy()
    planner.__del__()
    assert calls == ['ik']


def test_no_attachment_validate_matches_head_reference(monkeypatch):
    """HEAD's no-attachment validate fields and result on a synthetic world."""
    import hashlib
    import importlib.util
    import json
    from pathlib import Path
    import numpy as np
    import curobo_v2.identity as identity

    monkeypatch.setattr(identity, '_service_context', ({}, '/tmp/robot.yml'))
    source = Path(__file__).resolve().parents[3] / 'curobo_v2/service/curobo_service/planner_core.py'
    spec = importlib.util.spec_from_file_location(
        'curobo_v2.service.curobo_service._test_planner_core', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    core = object.__new__(module.CuroboPlanner)
    core.max_joint_step = .005
    core.limits = np.array([[-1., 1.]])
    core._world_cuboids = [dict(name='box', center=[2., 0., 0.], half_size=[.5, .5, .5])]
    core.attachment = None
    core.geometry = None
    core._state = lambda dense, planner: SimpleNamespace(position=torch.as_tensor(dense))
    checker = SimpleNamespace(validate=lambda batch: torch.ones(batch.shape[:2], dtype=torch.bool))
    assert core.validate(np.array([[0.], [.01]]), object(), checker)
    assert core.last_validation == {
        'samples': 3, 'max_joint_step_rad': .005, 'joint_limits': True,
        'collision_free': True, 'obstacle_count': 1,
        'world_sha256': hashlib.sha256(json.dumps(core._world_cuboids, sort_keys=True).encode()).hexdigest(),
        'geometry_accounting': {
            'declared_names': ['box'], 'static_names': ['box'], 'carried_names': [],
            'bijective': True, 'no_shape_dropped': True, 'carried_representation': None}}
