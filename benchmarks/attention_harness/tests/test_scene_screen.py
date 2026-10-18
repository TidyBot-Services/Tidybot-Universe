"""Hand-built data for the scene screen's pure decisions; no simulator or GPU."""
import math
import json
import time
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.diagnostics import scene_screen as screen


def test_dev_seed_gate_and_width():
    assert screen.parse_seeds('101,102,125') == (101, 102, 125)
    for raw in ('1001', '101,1001', '1000', '126', '102,102', ''):
        with pytest.raises(Exception):
            screen.parse_seeds(raw)
    assert screen.width_result(.0805)['graspable_by_width']
    assert screen.width_result(.0806)['width_margin_m'] == pytest.approx(-.0001)
    assert not screen.width_result(.0806)['graspable_by_width']


def test_grasp_search_cli_counts(capsys, tmp_path):
    base = ['--stage', 'ik', '--seeds', '102', '--out', str(tmp_path), '--dry-run']
    screen.main(base)
    default = json.loads(capsys.readouterr().out)
    assert default['grasp_base_shifts_per_seed'] == 4
    assert default['grasp_orientations_per_shift'] == 6
    assert default['coarse_grasp_ik_targets_max_per_seed'] == 48
    assert default['max_grasp_candidates'] == 3
    assert default['max_carry_cells'] == 80
    assert default['carry_targets_per_reachable_cell_max'] == 10
    assert default['carry_ik_targets_upper_bound_per_seed'] == 2400
    assert default['chassis_reject_before_ik']
    screen.main(base + ['--dy-list', '0.2,0.3', '--dyaw-list', '0,0.15', '--no-pitch-zero-only'])
    custom = json.loads(capsys.readouterr().out)
    assert custom['grasp_base_shifts_per_seed'] == 4
    assert custom['grasp_orientations_per_shift'] == 48
    assert custom['coarse_grasp_ik_targets_max_per_seed'] == 384
    screen.main(base + ['--dy-list', '0.2', '--full-grasp-search'])
    full = json.loads(capsys.readouterr().out)
    assert full['grasp_base_shifts_per_seed'] == 12
    assert full['grasp_orientations_per_shift'] == 48
    assert full['coarse_grasp_ik_targets_max_per_seed'] == 1152
    assert not full['chassis_reject_before_ik']
    with pytest.raises(SystemExit):
        screen.main(base + ['--dy-list', 'nan'])
    screen.main(base + ['--max-grasp-candidates', '2', '--max-carry-cells', '5'])
    limited = json.loads(capsys.readouterr().out)
    assert limited['carry_ik_targets_upper_bound_per_seed'] == 100
    with pytest.raises(SystemExit):
        screen.main(base + ['--max-carry-cells', '0'])


def test_carry_cache_reuses_positive_and_negative_endpoints_across_candidates():
    cache = screen.CarryIKCache(104)
    calls = []
    q = [1., 0., 0., 0.]
    def solve(point):
        calls.append(point)
        return (point == 0, .15 if point == 0 else None)
    for candidate in (1, 2):
        assert cache.check(q, (.1, .2), 0, lambda: solve(0))[0] == (True, .15)
        assert cache.check(q, (.1, .2), 1, lambda: solve(1))[0] == (False, None)
        if candidate == 2:
            assert len(calls) == 2  # second candidate made zero IK calls
    assert cache.hits == 2
    assert cache.check([-1., 0., 0., 0.], (.1, .2), 0, lambda: solve(0))[1] is True
    assert len(calls) == 2
    assert cache.check([1., 0., 0., .000002], (.1, .2), 0, lambda: solve(0))[1] is False
    assert cache.check(q, (.1, .2, .15), 0, lambda: solve(0))[1] is False


def test_carry_selection_uses_weakest_layer_and_unique_orientation():
    def item(number, q, margins):
        return {'candidate_number': number, 'quaternion_wxyz': q, 'grasp_margins': margins}
    passed = [item(1, [1, 0, 0, 0], [.5, .11, .5]),
              item(2, [0, 1, 0, 0], [.4, .4, .4]),
              item(3, [0, 1.0000001, 0, 0], [.7, .7, .7]),
              item(4, [0, 0, 1, 0], [.3, .3, .3]),
              item(5, [0, 0, 0, 1], [.2, .2, .2]),
              item(6, [0, -1, 0, 0], [.6, .6, .6])]
    assert [x['candidate_number'] for x in screen.select_carry_candidates(passed, 3)] == [3, 4, 5]


def test_carry_cell_budget_preserves_xy_order():
    shift_point = lambda point, shift: (point[0]-shift[0], point[1]-shift[1], point[2])
    cells = [(.3, 0.), (0., 0.), (.2, 0.), (.05, 0.)]
    assert screen.rank_carry_cells(cells, (.2, 0., 0.), (0., 0., 0.),
                                   (0., 0., 0.), 0., shift_point, 2) == [(.2, 0.), (.3, 0.)]


def test_release_rectangle_grid_and_any_point_margin():
    region = {'xy_aabb_world_m': [[.14, .52], [-1.97, -1.42]]}
    rect = screen.release_rectangle(region)
    assert rect[0] == pytest.approx([.20, .46])
    assert rect[1] == pytest.approx([-1.91, -1.48])
    points = screen.release_points(rect, .919, (.47, -1.85))
    assert len(points) == 10
    assert points[0] == pytest.approx([.46, -1.85, .999])
    assert all(.20 <= p[0] <= .46 and -1.91 <= p[1] <= -1.48 and p[2] == pytest.approx(.999)
               for p in points)
    assert screen.first_feasible_release([None, .099, .10, None]) == (2, .10)
    assert screen.first_feasible_release([None, .099]) is None
    assert screen.first_feasible_release([.11, None]) == (0, .11)
    narrow = screen.release_rectangle({'xy_aabb_world_m': [[0., .10], [0., .12]]})
    assert narrow[0] == pytest.approx([.05, .05])
    assert narrow[1] == pytest.approx([.06, .06])
    assert screen.release_points(narrow, 1., (0., 0.))[0] == pytest.approx([.05, .06, 1.08])


def test_bfs_uses_edges_and_bounds():
    # A hand-built wall blocks the direct east edge but leaves a route via north.
    blocked = {((0., 0.), (.05, 0.))}
    def edge(a, b):
        return (a, b) not in blocked
    seen = screen.bounded_grid_bfs((0., 0.), (0., .1, 0., .05), edge)
    assert (.05, 0.) in seen
    assert seen[(.05, 0.)] != (0., 0.)
    assert all(0 <= x <= .1 and 0 <= y <= .05 for x, y in seen)


def test_grid_cache_matches_uncached_and_reuses_directed_edges():
    bounds = (0., .1, 0., .05)
    blocked = {((0., 0.), (.05, 0.))}
    baseline_calls = []
    cached_calls = []

    def baseline_edge(a, b):
        baseline_calls.append((a, b))
        return (a, b) not in blocked

    def cached_edge(a, b):
        cached_calls.append((a, b))
        return (a, b) not in blocked

    starts = [(0., 0.), (0., 0.), (.05, 0.)]
    expected = [screen.bounded_grid_bfs(start, bounds, baseline_edge) for start in starts]
    cache = screen.GridSearchCache()
    observed = [cache.search(0., start, bounds, cached_edge) for start in starts]
    assert [row[0] for row in observed] == expected
    assert [row[1] for row in observed] == [False, True, False]
    assert observed[1][2] == 0
    assert len(cached_calls) == len(set(cached_calls)) < len(baseline_calls)
    other_yaw = cache.search(.15, (0., 0.), bounds, cached_edge)
    assert other_yaw[0] == expected[0] and not other_yaw[1]
    assert len(cached_calls) > len(cache.edges[0.])


def test_margin_verdict_and_markdown():
    assert screen.joint_margin([.2, 1.9], ['openarm_left_joint7', 'other'], [(0., 1.), (0., 2.)]) == pytest.approx(.2)
    reset = {'target_name': 'banana', 'graspable_by_width': True,
             'start_clearance_m': .004, 'start_clearance_ok': True}
    assert screen.verdict(reset) == 'unknown:ik_not_run'
    assert screen.verdict(dict(reset, graspable_by_width=False)) == 'infeasible:width_over_opening'
    assert screen.verdict(dict(reset, start_clearance_ok=False)) == 'infeasible:start_clearance_below_3mm'
    assert screen.verdict(reset, {'grasp_ik_feasible': False, 'carry_ik_feasible': False}) == 'unknown:grasp_ik_not_found'
    assert screen.verdict(reset, {'grasp_ik_feasible': True, 'carry_ik_feasible': False,
                                  'carry_candidates_tested': 3, 'carry_cells_tested': 240}) == 'unknown:carry_not_found'
    assert screen.verdict(reset, {'error': 'GPU unavailable', 'grasp_ik_feasible': None,
                                  'carry_ik_feasible': None}) == 'unknown:ik_error'
    assert screen.verdict(reset, {'grasp_ik_feasible': True, 'carry_ik_feasible': True}) == 'feasible'
    assert screen.verdict(reset, {'timeout': True, 'grasp_ik_feasible': None,
                                  'carry_ik_feasible': None}) == 'unknown:timeout'
    table = screen.format_table([{'seed': 102, 'reset': reset, 'ik': None}])
    assert '| 102 | banana | 是 | 4.00 | — | — | unknown:ik_not_run |' in table


def test_ik_timeout_persists_per_seed_and_skip_done(tmp_path, monkeypatch):
    reset = {'target_name': 'banana', 'graspable_by_width': True,
             'start_clearance_m': .004, 'start_clearance_ok': True}
    screen.write_json(tmp_path/'reset.json', {'rows': [dict(reset, seed=101), dict(reset, seed=102)]})
    calls = []

    def fake_ik(args, seed, _reset):
        calls.append(seed)
        if seed == 101:
            raise screen.SeedTimeout()
        else:
            rows = json.loads((tmp_path/'ik.json').read_text())['rows']
            assert rows[0]['seed'] == 101 and rows[0]['timeout']
        return {'seed': seed, 'grasp_ik_feasible': True,
                'carry_ik_feasible': True, 'physics_steps': 0}

    monkeypatch.setattr(screen, 'run_one_ik', fake_ik)
    args = SimpleNamespace(out=tmp_path, seeds=(101, 102), skip_done=False,
                           seed_timeout_seconds=1.)
    screen.run_ik(args)
    rows = json.loads((tmp_path/'ik.json').read_text())['rows']
    assert calls == [101, 102]
    assert rows[0]['timeout'] and rows[1]['carry_ik_feasible']
    assert 'unknown:timeout' in (tmp_path/'ik.md').read_text()
    assert 'feasible' in (tmp_path/'summary.md').read_text()
    args.skip_done = True
    screen.run_ik(args)
    assert calls == [101, 102]


def test_seed_deadline_interrupts_and_restores_timer():
    with pytest.raises(screen.SeedTimeout):
        with screen.seed_deadline(.01):
            time.sleep(.1)
    with screen.seed_deadline(.1):
        pass


def test_native_basin_points_and_rim_exclude_faucet():
    truth = {'position_world_m': [1., 0., .8],
        'sink_int_sites_world_m': {'basin': [[0., 0., .7], [.4, 0., .7], [0., .3, .7], [0., 0., .9]]},
        'sink_main_collision_boxes_world_m': [
            {'name': 'wall', 'center_world_m': [.2, .15, .82], 'half_size_world_m': [.2, .15, .09]},
            {'name': 'faucet', 'center_world_m': [.2, .15, .96], 'half_size_world_m': [.02, .02, .03]}]}
    result = screen.basin_geometry(truth)
    assert result['rim_source_collision_geom'] == 'wall'
    assert result['safe_object_center_world_m'][:2] == pytest.approx([.35, .05])
    assert result['safe_object_center_world_m'][2] < .9
    assert result['carry_tcp_world_m'][2] > result['rim_top_world_m']
