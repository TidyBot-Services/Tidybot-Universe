"""Pure geometry and dry-run checks; no simulator creation."""
import json
import math
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from benchmarks.attention_harness.diagnostics import object_proxy_measure as measure


@pytest.mark.parametrize('seed', [101, 102, 103])
def test_main_sets_cache_before_import_and_checks_sim_after_reset(tmp_path, monkeypatch, seed):
    events = []
    output = tmp_path/'measurement'

    class Identity:
        def __init__(self, *_args):
            for variable, child in (('XDG_CACHE_HOME', 'cache'),
                                    ('NUMBA_CACHE_DIR', 'numba-cache')):
                assert Path(os.environ[variable]) == output/child
                assert (output/child).is_dir()
            events.append('identity')

        def verify_identity(self):
            return {'verified': True}

    def reset():
        assert env.sim is None
        events.append('reset')
        env.sim = SimpleNamespace(model=object(), data=object())

    env = SimpleNamespace(sim=None, reset=reset, close=lambda: events.append('close'), robots=[])

    def create_env(name, **kwargs):
        assert name == 'PickPlaceCounterToSink'
        assert kwargs == dict(robots='OpenArm365', seed=seed,
                              obj_instance_split='pretrain', layout_and_style_ids=[(11, 34)],
                              camera_widths=128, camera_heights=128, randomize_cameras=False,
                              generative_textures=None, control_freq=20, horizon=2400,
                              scene_reset_seed=seed)
        events.append('create')
        return env

    monkeypatch.setitem(sys.modules, 'openarm365_dev.identity',
                        SimpleNamespace(OfficialEnvironmentIdentity=Identity))
    monkeypatch.setitem(sys.modules, 'openarm365_dev_v2.environment',
                        SimpleNamespace(create_env=create_env))
    monkeypatch.setattr(measure, 'check_sim_interfaces',
                        lambda actual: (events.append('check_sim'),
                                        actual.sim.model, actual.sim.data))
    monkeypatch.setattr(measure, 'measure',
                        lambda actual, *_args, **_kwargs: (events.append('measure'), {'task': 'test'})[1])
    monkeypatch.setattr(measure, 'render_markdown', lambda _result: '# test\n')
    @contextmanager
    def shadow(actual_out):
        assert actual_out == output
        events.append('shadow_enter')
        try:
            yield
        finally:
            events.append('shadow_exit')

    monkeypatch.setattr(measure, 'writable_mjcf_assets', shadow)
    measure.main(['--seed', str(seed), '--out', str(output)])
    assert events == ['identity', 'create', 'shadow_enter', 'reset',
                      'shadow_exit', 'check_sim', 'measure', 'close']
    assert json.loads((output/'results.json').read_text())['identity'] == {'verified': True}


def test_physical_left_mujoco_names_are_distinct_from_urdf_links():
    names = ['world', 'gripper0_right_hand', 'gripper0_right_openarm_left_hand',
             'gripper0_right_openarm_left_right_finger',
             'gripper0_right_openarm_left_left_finger',
             'gripper0_left_hand', 'gripper0_left_openarm_right_hand',
             'gripper0_left_openarm_right_right_finger',
             'gripper0_left_openarm_right_left_finger']
    model = SimpleNamespace(body_names=names)
    assert measure.physical_left_body_names(model) == tuple(names[3:5] + names[2:3])
    model.body_names = [name for name in names if name != names[3]]
    with pytest.raises(ValueError, match='available body names') as error:
        measure.physical_left_body_names(model)
    assert names[4] in str(error.value)
    model.body_names = names + [names[3]]
    with pytest.raises(ValueError, match='found'):
        measure.physical_left_body_names(model)


def test_physical_left_joint_and_tcp_use_native_right_namespace():
    joint_names = ['gripper0_right_openarm_left_finger_joint1',
                   'gripper0_right_openarm_left_finger_joint2',
                   'gripper0_left_openarm_right_finger_joint1']
    model = SimpleNamespace(joint_names=joint_names,
                            get_joint_qpos_addr=lambda name: joint_names.index(name),
                            site_names=['gripper0_left_grip_site', 'gripper0_right_grip_site'])
    assert measure.physical_left_finger_joint_addresses(model) == [0, 1]
    env = SimpleNamespace(sim=SimpleNamespace(model=model),
                          robots=[SimpleNamespace(eef_site_id={'right': 1, 'left': 0})])
    assert measure.physical_left_tcp_site_id(env) == 1
    env.robots[0].eef_site_id['right'] = 0
    with pytest.raises(ValueError, match='available site names'):
        measure.physical_left_tcp_site_id(env)
    model.joint_names = joint_names[1:]
    with pytest.raises(ValueError, match='available joint names'):
        measure.physical_left_finger_joint_addresses(model)


def test_handwritten_cuboid_hull_obb_width_ratios():
    corners = np.array([[x, y, z] for x in (-1., 1.) for y in (-.5, .5)
                        for z in (-.25, .25)])
    theta = math.radians(30)
    rotation = np.array([[math.cos(theta), -math.sin(theta), 0.],
                         [math.sin(theta), math.cos(theta), 0.], [0., 0., 1.]])
    points = corners @ rotation.T
    assert measure.hull_volume(points) == pytest.approx(1.)
    obb = measure.oriented_bounds(points)
    assert sorted(obb['dimensions_m']) == pytest.approx([.5, 1., 2.], abs=1e-5)
    assert obb['volume_m3'] == pytest.approx(1., abs=1e-5)
    assert measure.projection_width(points, rotation[:, 0]) == pytest.approx(2.)
    assert measure.projection_width(points, rotation[:, 1]) == pytest.approx(1.)
    ratio = measure.ratios(2., 1., [4., 2., 1.], [2., 1., .5])
    assert ratio == {'proxy_volume_over_real_hull': 2.,
                     'axis_dimension_ratios': [2., 2., 2.]}


def test_handwritten_proxy_vertices_and_dry_run(capsys):
    box = {'center': [1., 2., 3.], 'half_size': [.5, 1., 2.],
           'quat_wxyz': [1., 0., 0., 0.]}
    assert measure.axis_bounds(measure.box_vertices(box)) == pytest.approx([1., 2., 4.])
    measure.main(['--dry-run', '--out', 'unused'])
    result = json.loads(capsys.readouterr().out)
    assert result['seed'] == 101
    assert result['task'] == 'PickPlaceCounterToSink'
    assert result['physics_steps'] == result['policy_runs'] == 0
    assert result['finger_kinematic_positions_m_each'] == [.001, .005, .01, .015, .02, .025, .03, .035, .04, .043]
    with pytest.raises(SystemExit):
        measure.main(['--dry-run', '--seed', '1001'])


def test_layered_widths_handwritten_cuboid():
    points = np.array([[x, y, z] for x in (-.05, .05) for y in (-.03, .03)
                       for z in (0., .01, .02)])
    result = measure.layered_horizontal_widths(points, .01, .08, .0875)
    populated = [row for row in result['layers'] if row['vertex_count'] >= 4]
    assert populated
    for row in populated:
        assert row['narrowest_width_m'] == pytest.approx(.06)
        assert 90 in row['angles_within_pad_gap_deg']
        assert 0 not in row['angles_within_sphere_gap_deg']
    assert result['object_vertical_height_m'] == pytest.approx(.02)
    assert result['pad_comparison_threshold_m'] == .08


def test_layered_widths_handwritten_narrow_top_step():
    bottom = [[x, y, z] for x in (-.06, .06) for y in (-.05, .05)
              for z in (0., .01)]
    top = [[x, y, z] for x in (-.03, .03) for y in (-.025, .025)
           for z in (.03, .04)]
    result = measure.layered_horizontal_widths(bottom + top, .01, .08, .0875)
    bottom_row = min(result['layers'], key=lambda row: abs(row['center_world_z_m'] - .005))
    top_row = min(result['layers'], key=lambda row: abs(row['center_world_z_m'] - .035))
    assert bottom_row['narrowest_width_m'] == pytest.approx(.10)
    assert bottom_row['angles_within_pad_gap_deg'] == []
    assert top_row['narrowest_width_m'] == pytest.approx(.05)
    assert 90 in top_row['angles_within_pad_gap_deg']
    empty = min(result['layers'], key=lambda row: abs(row['center_world_z_m'] - .02))
    assert empty['vertex_count'] == 0
    assert result['narrowest_local']['width_m'] == pytest.approx(.05)
    assert top_row['center_world_z_m'] in result['interior_layers_within_pad_gap']
    assert max(row['center_world_z_m'] for row in result['layers']) == pytest.approx(.04)


@pytest.mark.parametrize('wrapped', [False, True])
def test_pose_access_accepts_native_and_robosuite_data(wrapped):
    position = np.array([[1., 2., 3.]])
    rotation = np.eye(3).reshape(1, 9)
    shared = dict(geom_xpos=position + 1, geom_xmat=rotation,
                  site_xpos=position + 2, site_xmat=rotation)
    if wrapped:
        data = SimpleNamespace(body_xpos=position, body_xmat=rotation, **shared)
    else:
        data = SimpleNamespace(xpos=position, xmat=rotation, **shared)
    for reader, expected in ((measure.body_pose, position[0]),
                             (measure.geom_pose, shared['geom_xpos'][0]),
                             (measure.site_pose, shared['site_xpos'][0])):
        actual_position, actual_rotation = reader(data, 0)
        assert actual_position == pytest.approx(expected)
        assert actual_rotation == pytest.approx(np.eye(3))


def test_pose_access_reports_missing_array():
    with pytest.raises(TypeError, match='xmat'):
        measure.body_pose(SimpleNamespace(xpos=np.zeros((1, 3))), 0)
    with pytest.raises(TypeError, match='missing attributes:.*geom_xmat'):
        measure._check_attributes('fake data', SimpleNamespace(geom_xpos=[]),
                                  ('geom_xpos', 'geom_xmat'))


def test_copied_mujoco_forward_keeps_reset_state():
    mujoco = pytest.importorskip('mujoco')
    xml = '''<mujoco><worldbody><body name="hand">
      <body name="finger1"><joint name="gripper0_right_openarm_left_finger_joint1" type="slide" axis="0 1 0"/>
        <geom type="box" size="0.01 0.01 0.01"/></body>
      <body name="finger2"><joint name="gripper0_right_openarm_left_finger_joint2" type="slide" axis="0 -1 0"/>
        <geom type="box" size="0.01 0.01 0.01"/></body>
      </body></worldbody></mujoco>'''
    native = mujoco.MjModel.from_xml_string(xml)
    original = mujoco.MjData(native)
    original.qpos[:] = [.02, .02]
    mujoco.mj_forward(native, original)
    class Wrapper:
        _model = native
        joint_names = ['gripper0_right_openarm_left_finger_joint1',
                       'gripper0_right_openarm_left_finger_joint2']
        def get_joint_qpos_addr(self, name):
            return int(native.jnt_qposadr[mujoco.mj_name2id(native, mujoco.mjtObj.mjOBJ_JOINT, name)])
        def __getattr__(self, name):
            return getattr(native, name)
    env = SimpleNamespace(sim=SimpleNamespace(model=Wrapper(),
                                              data=SimpleNamespace(_data=original)))
    opened, indexes = measure.kinematic_data_at(env, .043)
    assert opened.qpos[indexes] == pytest.approx([.043, .043])
    assert original.qpos[:] == pytest.approx([.02, .02])


def test_real_native_pose_arrays_survive_data_copy():
    mujoco = pytest.importorskip('mujoco')
    from copy import copy
    xml = '''<mujoco><worldbody>
      <body name="base" pos="1 2 3">
        <body name="moving" pos="0 1 0">
          <joint name="slide" type="slide" axis="1 0 0"/>
          <geom name="block" type="box" pos="0 0 .3" size=".1 .1 .1"/>
          <site name="tip" pos="0 .2 0" size=".01"/>
        </body>
      </body>
    </worldbody></mujoco>'''
    model = mujoco.MjModel.from_xml_string(xml)
    original = mujoco.MjData(model)
    original.qpos[0] = .2
    mujoco.mj_forward(model, original)
    bid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, 'moving')
    gid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, 'block')
    sid = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, 'tip')
    for reader, obj_id, position in ((measure.body_pose, bid, [1.2, 3., 3.]),
                                     (measure.geom_pose, gid, [1.2, 3., 3.3]),
                                     (measure.site_pose, sid, [1.2, 3.2, 3.])):
        actual_position, actual_rotation = reader(original, obj_id)
        assert actual_position == pytest.approx(position)
        assert actual_rotation == pytest.approx(np.eye(3))
    # Python MuJoCo 3.3.1 exposes MjData.__copy__, not a module-level mj_copyData.
    copied = copy(original)
    copied.qpos[0] = .4
    mujoco.mj_forward(model, copied)
    assert measure.body_pose(copied, bid)[0] == pytest.approx([1.4, 3., 3.])
    assert measure.geom_pose(copied, gid)[0] == pytest.approx([1.4, 3., 3.3])
    assert measure.site_pose(copied, sid)[0] == pytest.approx([1.4, 3.2, 3.])
    assert measure.body_pose(original, bid)[0] == pytest.approx([1.2, 3., 3.])

    class ModelWrapper:
        _model = model
        body_names = ('world', 'base', 'moving')
        joint_names = ('slide',)
        site_names = ('tip',)
        body_name2id = lambda self, name: mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, name)
        body_id2name = lambda self, obj_id: mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, obj_id)
        geom_id2name = lambda self, obj_id: mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_GEOM, obj_id)
        get_joint_qpos_addr = lambda self, name: int(model.jnt_qposadr[
            mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, name)])

        def __getattr__(self, name):
            return getattr(model, name)

    class DataWrapper:
        _data = original

        def __getattr__(self, name):
            if name == 'body_xpos':
                return original.xpos
            if name == 'body_xmat':
                return original.xmat
            return getattr(original, name)

    env = SimpleNamespace(sim=SimpleNamespace(model=ModelWrapper(), data=DataWrapper()))
    measure.check_sim_interfaces(env)
