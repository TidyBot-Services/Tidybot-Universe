"""Pure command construction checks; no simulation, planner, or model."""
import math
from pathlib import Path
import shlex

import pytest

from benchmarks.attention_harness.diagnostics import probe_from_screen as probe
from benchmarks.attention_harness.diagnostics import scene_screen as screen


def sample_rows():
    q = (math.sin(math.radians(35/2)), math.cos(math.radians(35/2)), 0., 0.)
    reset = {'seed': 102, 'target_z_range_world_m': [.92, .952],
             'sink': {'rim_top_world_m': .919, 'rim_source_collision_geom': 'basin_wall',
                      'chosen_region': 'basin', 'regions': [{'name': 'basin',
                          'xy_aabb_world_m': [[.14, .52], [-1.972, -1.424]]}]}}
    ik = {'seed': 102, 'grasp_ik_feasible': True, 'carry_ik_feasible': True,
          'grasp_base_shift': [0., .30, 0.], 'carry_base_cell': [0., -.15],
          'selected_candidate_number': 3, 'grasp_quaternion_world_xyzw': q,
          'grasp_theta_deg': 55., 'object_height_m': .032,
          'target_z_range_world_m': [.92, .952],
          'release_point_world_m': [.46, -1.85, .999]}
    return reset, ik


def test_theta_and_world_quaternion_convention():
    from benchmarks.attention_harness.diagnostics.reachability_map import grasp_orientation
    for theta in (55., 70., -125.):
        q = grasp_orientation(theta, 1, 0, 1)['quaternion_world_xyzw']
        expected = (math.sin(math.radians((90-theta)/2)),
                    math.cos(math.radians((90-theta)/2)), 0., 0.)
        assert q == pytest.approx(expected)
        assert screen.theta_from_world_quaternion(q) == pytest.approx(theta)
        assert screen.theta_from_world_quaternion([-v for v in q]) == pytest.approx(theta)
    reversed_axis = grasp_orientation(55, -1, 0, 1)['quaternion_world_xyzw']
    assert screen.theta_from_world_quaternion(reversed_axis) == pytest.approx(-125.)
    assert screen.theta_from_world_quaternion([.5, .5, .5, .5]) is None


def test_clearance_and_command_assembly():
    assert probe.clearance_for_height(.032) == .08
    assert probe.clearance_for_height(.20) == pytest.approx(.14)
    with pytest.raises(ValueError, match='exceeds 0.20'):
        probe.clearance_for_height(.34)
    reset, ik = sample_rows()
    params = probe.assemble_parameters(reset, ik, seed=102, out='probe-102')
    assert params['carry_base_shift'] == pytest.approx((0., -.45, 0.))
    assert params['carry_sink_xy'] == (.46, -1.85)
    assert params['clearance_m'] == .08
    command = probe.shell_command(params)
    tokens = shlex.split(command)
    assert tokens[0] == 'ATTN_PLANNER_MODE=ik_path'
    assert '--retreat-base=-0.30,0,0' in tokens
    assert [float(v) for v in tokens[tokens.index('--grasp-quat')+1].split(',')] == pytest.approx((.3007057995, .9537169507, 0., 0.))
    assert [float(v) for v in tokens[tokens.index('--carry-base-shift')+1].split(',')] == pytest.approx((0., -.45, 0.))
    assert tokens[tokens.index('--lift-height-m')+1] == '0.06'
    assert tokens[tokens.index('--budget-scale')+1] == '4'


def test_old_ik_requires_rerun():
    reset, ik = sample_rows()
    del ik['grasp_quaternion_world_xyzw']
    with pytest.raises(ValueError, match='old ik.json lacks grasp_quaternion_world_xyzw; rerun scene_screen --stage ik'):
        probe.assemble_parameters(reset, ik, seed=102, out='unused')


def test_seed102_recorded_geometry_matches_successful_command_scale():
    folder = Path(__file__).parent/'fixtures/scene_screen_seed102'
    reset = probe.selected_row(folder/'reset.json', 102)
    ik = probe.selected_row(folder/'ik.json', 102)
    # The recorded old-format row lacked the orientation added to this fixture.
    old_ik = {key: value for key, value in ik.items() if key not in probe.REQUIRED_IK}
    with pytest.raises(ValueError, match='rerun scene_screen --stage ik'):
        probe.assemble_parameters(reset, old_ik, seed=102, out='unused')
    params = probe.assemble_parameters(reset, ik, seed=102, out='probe-102')
    assert params['base_shift'] == pytest.approx((0., .30, 0.))
    assert params['carry_base_shift'][1] == pytest.approx(-.45)
    assert params['carry_sink_xy'][0] == pytest.approx(.47, abs=.02)
    assert params['carry_sink_xy'][1] == pytest.approx(-1.85, abs=.07)
    assert params['carry_rim_top'] == pytest.approx(.918, abs=.002)
    assert params['clearance_m'] == .08
    assert reset['sink']['regions'][0]['xy_aabb_world_m'][0][0] <= params['carry_sink_xy'][0] <= reset['sink']['regions'][0]['xy_aabb_world_m'][0][1]
    assert reset['sink']['regions'][0]['xy_aabb_world_m'][1][0] <= params['carry_sink_xy'][1] <= reset['sink']['regions'][0]['xy_aabb_world_m'][1][1]
