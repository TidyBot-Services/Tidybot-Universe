"""Offline inventory checks for the read-only GPU diagnostic."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import yaml

from benchmarks.attention_harness.diagnostics.ik_path_bench import (
    diagnostic_files, precheck, print_summary, request_inputs, parse_ik_candidates,
)


@pytest.fixture
def offline_core(monkeypatch):
    """Construct only the planner's Python validation state, never its GPU model."""
    import curobo_v2.identity as identity
    monkeypatch.setattr(identity, '_service_context', ({}, '/tmp/offline.yml'))
    monkeypatch.setitem(sys.modules, 'torch', types.ModuleType('torch'))
    path = Path(__file__).parents[3] / 'curobo_v2/service/curobo_service/planner_core.py'
    spec = importlib.util.spec_from_file_location('curobo_v2.service.curobo_service._bench_offline_core', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    core = module.CuroboPlanner.__new__(module.CuroboPlanner)
    names = [f'openarm_{side}_joint{i}' for side in ('left', 'right') for i in range(1, 8)]
    core.joint_names = names
    core.limits = np.tile([-4., 4.], (14, 1))
    core.tool_frames = ['openarm_left_hand_tcp', 'openarm_right_hand_tcp']
    core.default_quaternion = [1., 0., 0., 0.]
    core._locks = {f'openarm_{side}_finger_joint1': 0. for side in ('left', 'right')}
    core._joints = {}
    for name in core._locks:
        node = ET.Element('joint')
        ET.SubElement(node, 'limit', lower='0', upper='1')
        core._joints[name] = node
    core._world_cuboids = []
    core._models = {}
    core.geometry = None
    core.attachment = None
    return core


def test_saved_certificate_rebuilds_native_request_and_passes_core_precheck(offline_core):
    fixture = json.loads((Path(__file__).parent / 'fixtures/ik_path_world.json').read_text())
    body, world, attachment, tool = request_inputs(offline_core, fixture, 'left')
    assert body['env_id'] == 'kitchen'
    assert set(body['locked_joints']) == set(offline_core._locks)
    assert not set(body['locked_joints']) & set(body['active_joints'])
    assert len(world) == 818
    q0 = precheck(offline_core, body, world, attachment, tool)
    assert q0.shape == (14,)
    target = {tool: {'position': [.1, .2, .3], 'quaternion_wxyz': [1., 0., 0., 0.]}}
    precheck(offline_core, body, world, attachment, tool, target)
    _, _, merged = offline_core.check_selection(q0, body['active_joints'], [tool], body['locked_joints'])
    assert all(name in merged for name in offline_core.joint_names[7:])
    with pytest.raises(ValueError, match='Only configured'):
        offline_core._select(q0, body['active_joints'], [tool], fixture['locked_joints'])


def test_precheck_rejects_bad_locks_world_and_target(offline_core):
    fixture = json.loads((Path(__file__).parent / 'fixtures/ik_path_world.json').read_text())
    body, world, attachment, tool = request_inputs(offline_core, fixture, 'left')
    bad = dict(body, locked_joints=dict(body['locked_joints'], openarm_right_joint1=0.))
    with pytest.raises(ValueError, match='Only configured'):
        precheck(offline_core, bad, world, attachment, tool)
    with pytest.raises(ValueError, match='unit wxyz'):
        precheck(offline_core, body, [dict(world[0], quat_wxyz=[0., 0., 0., 0.])], attachment, tool)
    precheck(offline_core, body, world, attachment, tool)
    with pytest.raises(ValueError, match='unit wxyz'):
        precheck(offline_core, body, world, attachment, tool,
                 {tool: {'position': [0., 0., 0.], 'quaternion_wxyz': [0., 0., 0., 0.]}})
    with pytest.raises(ValueError, match='seven active arm joints'):
        precheck(offline_core, dict(body, active_joints=body['active_joints'][:-1]),
                 world, attachment, tool)
    with pytest.raises(ValueError, match='positive and finite'):
        offline_core.check_ik_path_request({tool: {}}, body['active_joints'], max_joint_jump=0.)
    with pytest.raises(ValueError, match='same arm'):
        offline_core.check_ik_path_request({'openarm_right_hand_tcp': {}}, body['active_joints'])
    fixture['locked_joints']['openarm_right_joint1'] += .01
    with pytest.raises(ValueError, match='inactive joint lock differs'):
        request_inputs(offline_core, fixture, 'left')


def test_response_q0_is_reordered_and_summary_pairs_modes(offline_core, capsys):
    fixture = json.loads((Path(__file__).parent / 'fixtures/ik_path_world.json').read_text())
    fixture['response'] = {'joint_names': list(reversed(fixture['joint_names'])),
                           'trajectory': [list(reversed(fixture['q0']))]}
    body, _, _, _ = request_inputs(offline_core, fixture, 'left')
    assert body['current_q'] == pytest.approx(fixture['q0'])
    rows = [dict(case='up_1cm', mode='plan_pose', passed=True, total_seconds=.1)]
    rows += [dict(case='up_1cm', mode='ik_path', ik_candidates=k, passed=True,
                  total_seconds=.1, ik_selection_seconds=.02) for k in (1, 4, 8)]
    print_summary(rows)
    output = capsys.readouterr().out
    assert '| up_1cm | plan_pose | — | PASS' in output
    assert '| up_1cm | ik_path | 1 | PASS' in output
    assert '| up_1cm | ik_path | 4 | PASS' in output
    assert '| up_1cm | ik_path | 8 | PASS' in output
    assert 'select+check s' in output


def test_ik_candidate_parser_and_precheck(offline_core):
    assert parse_ik_candidates('1,4,8') == (1, 4, 8)
    for value in ('0', '-1', '1,x', '1,1', '65'):
        with pytest.raises(Exception):
            parse_ik_candidates(value)
    fixture = json.loads((Path(__file__).parent / 'fixtures/ik_path_world.json').read_text())
    body, world, attachment, tool = request_inputs(offline_core, fixture, 'left')
    precheck(offline_core, body, world, attachment, tool, ik_candidates=(1, 4, 8))
    with pytest.raises(ValueError, match='ik_candidates'):
        precheck(offline_core, body, world, attachment, tool, ik_candidates=(17,))


def test_request_model_candidate_bounds(monkeypatch, tmp_path):
    config = tmp_path / 'robot.yml'
    config.write_text('robot_cfg:\n  kinematics:\n    base_link: base\nservice: {}\n')
    prefix = 'curobo_v2.service.curobo_service.'
    config_module = types.ModuleType(prefix + 'config')
    config_module.Config = type('Config', (), dict(ROBOT_CFG=str(config), MAX_ENVS=1,
                                                  DEVICE='cuda:0', DEFAULT_ENV='default'))
    planner_module = types.ModuleType(prefix + 'planner')
    planner_module.PlannerAdapter = lambda **kwargs: object()
    world_module = types.ModuleType(prefix + 'world')
    world_module.WorldStore = lambda **kwargs: object()
    for name, module in [('config', config_module), ('planner', planner_module),
                         ('world', world_module)]:
        monkeypatch.setitem(sys.modules, prefix + name, module)
    path = Path(__file__).parents[3] / 'curobo_v2/service/curobo_service/server.py'
    spec = importlib.util.spec_from_file_location(prefix + '_offline_server', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    body = dict(current_q=[0.] * 14,
                targets={'openarm_left_hand_tcp': dict(position=[0., 0., 0.],
                         quaternion_wxyz=[1., 0., 0., 0.])},
                active_joints=[f'openarm_left_joint{i}' for i in range(1, 8)])
    assert module.IKPathRequest.model_validate(body).ik_candidates == 1
    assert module.IKPathRequest.model_validate(dict(body, ik_candidates=16)).ik_candidates == 16
    for invalid in (0, 17, True, 1.5, '4'):
        with pytest.raises(Exception):
            module.IKPathRequest.model_validate(dict(body, ik_candidates=invalid))
    config.write_text('robot_cfg:\n  kinematics:\n    base_link: base\nservice:\n  pose_search: measured_reference_high_penalty_v1\n')
    assert module.IKPathRequest.model_validate(dict(body, ik_candidates=64)).ik_candidates == 64
    with pytest.raises(Exception):
        module.IKPathRequest.model_validate(dict(body, ik_candidates=65))


def test_diagnostic_files_collects_urdf_meshes_and_reports_missing(tmp_path):
    config = tmp_path / 'robot.yml'
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'mesh.stl').write_bytes(b'collision')
    (assets / 'visual.dae').write_bytes(b'visual')
    (assets / 'spheres.yml').write_text('collision_spheres: {}')
    urdf = tmp_path / 'robot.urdf'
    urdf.write_text('<robot name="fake"><link name="base">'
                    '<collision><geometry><mesh filename="assets/mesh.stl"/></geometry></collision>'
                    '<visual><geometry><mesh filename="visual.dae"/></geometry></visual>'
                    '</link></robot>')
    config.write_text('robot_cfg: {}')
    document = {'robot_cfg': {'kinematics': {'urdf_path': 'robot.urdf',
                'asset_root_path': 'assets', 'collision_spheres': 'assets/spheres.yml'}},
                'service': {'exact_geometry': {'urdf_path': str(urdf)}}}
    files = diagnostic_files(config, assets, document)
    for path in (config, urdf, assets / 'mesh.stl', assets / 'visual.dae', assets / 'spheres.yml'):
        assert files[str(path.resolve())] == hashlib.sha256(path.read_bytes()).hexdigest()
    (assets / 'visual.dae').unlink()
    (assets / 'spheres.yml').unlink()
    with pytest.raises(FileNotFoundError) as error:
        diagnostic_files(config, assets, document)
    assert 'visual.dae' in str(error.value)
    assert 'spheres.yml' in str(error.value)


def test_deployed_urdf_all_meshes_exist_and_are_registered():
    project = Path('/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/'
                   'openarm-dev-integration/deployment-02/runtime/project')
    config = project / 'config/openarm_fixed.yml'
    assets = project / 'curobo_v2/service/curobo_service/assets'
    document = yaml.safe_load(config.read_text())
    # This deployment's URDF lives in openarm-model and references meshes under
    # the adjacent openarm_description tree.
    urdf = Path(document['robot_cfg']['kinematics']['urdf_path'])
    assert (project / 'config/openarm_description').is_dir()
    assert urdf.is_file()
    meshes = {str((urdf.parent / m.get('filename')).resolve())
              for m in ET.parse(urdf).getroot().findall('.//mesh')}
    assert meshes
    assert all(Path(path).is_file() for path in meshes)
    files = diagnostic_files(config, assets, document)
    assert meshes <= files.keys()
    assert all(files[path] == hashlib.sha256(Path(path).read_bytes()).hexdigest()
               for path in meshes)
