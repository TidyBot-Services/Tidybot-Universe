"""CPU-only T1b tests. No native reset, planner import, GPU, or model reads."""
from copy import deepcopy
import itertools
import json
import math
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace as NS

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from scripts.m11 import reach_sweep as sweep
from scripts.m11.make_cabinet_reference import generate_candidate, main as generate_main
from openarm365_dev.policy_validation import validate_generated_policy


def probe():
    return dict(task_id=sweep.TASK, seed=110, same_normalized_public_state=True,
        resets=[dict(normalized_sha256='a'*64, scene=dict(
            target=dict(aabb_world_m=[[.30, -.45, .92], [.36, -.39, .98]]),
            mount_pose_world=[[0., -1., 0., 0.], [1., 0., 0., -.84], [0., 0., 1., .6], [0., 0., 0., 1.]],
            distractors={'distr_cab': [.6, -.1, 1.42]},
            cabinet=dict(interior_regions={'level0': dict(
                sites_world_m=[[0., -.37, 1.41], [1., -.37, 1.41], [0., -.03, 1.41], [0., -.37, 1.68]],
                shelf_front_world_m=[.5, -.37, 1.41], inward_world=[0., 1., 0.], depth_m=.34)})))])


def candidate(**updates):
    row = next(sweep.candidates(probe()))
    row.update(updates)
    return row


class FakePlanner:
    def __init__(self, failure=None, margin=.2):
        self.failure, self.margin, self.events = failure, margin, []

    def begin(self, row):
        self.events = ['begin']

    def result(self, stage):
        self.events.append(stage)
        return dict(success=stage != self.failure, failure_reason='blocked' if stage == self.failure else None,
                    min_joint_margin_rad=self.margin, min_clearance_m=.03)

    def tuck(self, row):
        return self.result('tuck')

    def base_path(self, row):
        return self.result('base_path')

    def waypoint(self, stage, row):
        return self.result(stage)

    def attach(self, row):
        self.events.append('attach')


@pytest.mark.parametrize('order', ['base_first', 'grasp_first'])
@pytest.mark.parametrize('failure', [None, 'base_path', 'pregrasp', 'grasp', 'lift', 'carry', 'place', 'tuck'])
def test_stage_order_and_fail_closed(order, failure):
    planner = FakePlanner(failure)
    result = sweep.evaluate(candidate(order=order, tuck_other_arm=True), planner)
    expected = ['begin', 'tuck']
    for stage in result['stage_order']:
        if failure in expected:
            break
        expected.append(stage)
        if stage == 'grasp' and failure != 'grasp':
            expected.append('attach')
    assert planner.events == expected
    assert result['complete'] is (failure is None)
    assert all('min_joint_margin_rad' in r and 'min_clearance_m' in r for r in result['stages'].values())
    if failure:
        assert result['failure_stage'] == failure
        assert not any(r['success'] for r in result['stages'].values() if r.get('skipped'))


def test_ranking_stage_before_margin_and_stable_ties():
    shallow = sweep.evaluate(candidate(id=1), FakePlanner('grasp', .9))
    deep = sweep.evaluate(candidate(id=2), FakePlanner('place', .021))
    better_margin = sweep.evaluate(candidate(id=3), FakePlanner('place', .2))
    tied = deepcopy(better_margin); tied['id'] = 4
    assert [r['id'] for r in sorted([tied, shallow, better_margin, deep], key=sweep.rank_key, reverse=True)] == [3, 4, 2, 1]


def test_limit_is_output_only_and_summary_counts_all(monkeypatch):
    rows = [candidate(id=i) for i in range(1, 5)]
    monkeypatch.setattr(sweep, 'candidates', lambda *args, **kwargs: iter(rows))
    report = sweep.sweep(probe(), FakePlanner('lift'), ('right',), 2)
    assert report['total_candidates'] == 4 and len(report['candidates']) == 2
    assert report['pass_counts']['grasp'] == 4 and report['pass_counts']['lift'] == 0
    assert report['stage_blocking_reasons']['lift'] == {'blocked': 4}
    assert '| lift | 0 | blocked (4) |' in sweep.summary(report)
    json.dumps(report, allow_nan=False)


def test_grid_geometry_and_bounds():
    rows = list(sweep.candidates(probe()))
    assert len(rows) == 23040
    assert {r['pitch_deg'] for r in rows} == {90, 60, 45, 30}
    assert {r['closing_yaw_deg'] for r in rows} == set(range(0, 360, 30))
    assert {r['order'] for r in rows} == {'base_first', 'grasp_first'}
    for row in rows:
        assert all(math.hypot(*d[:2]) <= .19+1e-10 and abs(d[2]) <= .29+1e-10 for d in row['base_moves'])
        assert np.sum(row['base_moves'], axis=0).tolist() == pytest.approx(row['base_offset']) if row['base_moves'] else row['base_offset'] == [0., 0., 0.]
        rotation = Rotation.from_quat(row['grasp_quat_xyzw']).as_matrix()
        assert rotation[:, 2] == pytest.approx(row['approach_axis_world'])
        points = row['waypoints']
        assert np.array(points['grasp'])-np.array(points['pregrasp']) == pytest.approx(.1*rotation[:, 2])
        assert points['lift'][2] >= 1.41+.06+.03-1e-10
        assert points['place'][1]-(-.37) >= .12
        assert points['carry'][1] < -.37
        assert points['place'][0] == .25  # lane away from distr_cab


def report_for(row):
    return dict(schema='attentionbench.m11-reach-sweep.v1', diagnostic_only=True,
        task_id=sweep.TASK, seed=110, probe_normalized_sha256='a'*64,
        candidates=[sweep.evaluate(row, FakePlanner())])


def execute_policy(monkeypatch, source, arm, *, stall=False):
    position = [.33, -.6, 1.12]; quat = [0., 0., 0., 1.]; events = []
    joints = list(range(14))
    def observe():
        return {f'robot0_{arm}_eef_pos': position[:], f'robot0_{arm}_eef_quat': quat[:],
                'robot0_left_joint_pos': joints[:7], 'robot0_right_joint_pos': joints[7:]}
    def move(dx, dy, dz, rv=None, **kw):
        step = np.array([dx, dy, dz])
        position[:] = (np.array(position)+step).tolist()
        if rv is not None and not stall:
            quat[:] = (Rotation.from_rotvec(rv)*Rotation.from_quat(quat)).as_quat().tolist()
        events.append(('move', position[:], step.tolist(), kw, rv))
    def base(dx, dy, yaw):
        events.append(('base', [dx, dy, yaw]))
    sdk = ModuleType('robot_sdk')
    sdk.sensors = NS(get_observation=observe, find_objects=lambda: [{'name': 'obj'}, {'name': 'cabinet'}])
    sdk.base = NS(move_delta=base)
    sdk.bimanual = NS(move_joints=lambda q: events.append(('tuck', q)))
    setattr(sdk, arm+'_arm', NS(move_delta=move))
    setattr(sdk, arm+'_gripper', NS(open=lambda: events.append(('open',)), close=lambda: events.append(('close',))))
    monkeypatch.setitem(sys.modules, 'robot_sdk', sdk)
    scope = {}; exec(source, scope)
    return events, scope


@pytest.mark.parametrize('arm,order,stall', list(itertools.product(('right', 'left'), ('base_first', 'grasp_first'), (False, True))))
def test_candidate_policy_executes_correct_order_and_gentle_descent(monkeypatch, arm, order, stall):
    other = 'left' if arm == 'right' else 'right'
    row = candidate(arm=arm, order=order, base_moves=[[0., .15, 0.]],
                    tuck_other_arm=True, tuck_arm=other, tuck_q=sweep.TUCK_LEFT if other == 'left' else sweep.TUCK_RIGHT)
    source = generate_candidate(report_for(row), probe=probe())
    validate_generated_policy(source)
    events, scope = execute_policy(monkeypatch, source, arm, stall=stall)
    assert scope['calls'][0] <= 320
    tuck = next(e for e in events if e[0] == 'tuck')[1]
    start = 0 if other == 'left' else 7
    assert tuck[start:start+7] == row['tuck_q']
    retained = 7-start
    assert tuck[retained:retained+7] == list(range(14))[retained:retained+7]
    bi = next(i for i, e in enumerate(events) if e[0] == 'base')
    close = next(i for i, e in enumerate(events) if e[0] == 'close')
    assert (bi < close) is (order == 'base_first')
    if order == 'grasp_first':
        assert events[bi-1][1] == pytest.approx(row['waypoints']['lift'])
    search = [(i, e) for i, e in enumerate(events) if e[0] == 'move' and e[3]]
    assert len(search) == 1 and search[0][0] == close-1
    assert np.linalg.norm(search[0][1][2]) <= .015+1e-9
    # Every downward segment on the approach-to-grasp line is gentle.
    descent = [e for e in events[:close] if e[0] == 'move' and e[2][2] < 0 and e[1][2] < row['waypoints']['pregrasp'][2]-1e-8]
    assert descent and all(np.linalg.norm(e[2]) <= .015+1e-9 for e in descent)
    release = max(i for i, e in enumerate(events) if e[0] == 'open')
    assert events[release-1][1] == pytest.approx(row['waypoints']['place'])


def test_candidate_rank_cli_and_validation(tmp_path):
    report = report_for(candidate())
    second = deepcopy(report['candidates'][0]); second['id'] = 42
    report['candidates'].append(second)
    path = tmp_path/'candidates.json'; path.write_text(json.dumps(report))
    output = tmp_path/'policy.py'
    assert generate_main(['--candidate', str(path), '--rank', '2', '--out', str(output)]) == 0
    assert '# T1b sweep candidate 42' in output.read_text()
    for rank in (0, 3, -1):
        with pytest.raises(ValueError): generate_candidate(report, rank)
    bad = probe(); bad['seed'] = 111
    with pytest.raises(ValueError, match='mismatch'): generate_candidate(report, probe=bad)
    report['candidates'][0]['base_moves'] = [[.2, 0, 0]]
    with pytest.raises(ValueError, match='bound'): generate_candidate(report)


def test_native_aggregate_and_cache_are_stateful_without_native_import():
    from scripts.m11.reach_sweep_native import NativeSweep, aggregate
    n = NativeSweep({}, Path('probe'), Path('out'), Path('.'))
    n.env = NS(sim=NS(data=NS(qpos=np.array([0.]))))
    n.history = (); n.held = None; n.relative_object = None; n.retention = {'supports': None}
    n.forward = lambda: None
    calls = []
    def op():
        calls.append(1); n.env.sim.data.qpos[:] = 3.
        return {'success': True}
    n.cached(['prefix'], op)
    n.history = (); n.env.sim.data.qpos[:] = 0.
    n.cached(['prefix'], op)
    assert len(calls) == 1 and n.env.sim.data.qpos[0] == 3.
    result = aggregate([dict(success=False, failure_reason='collision', min_joint_margin_rad=.017)])
    assert result['failure_reason'] == 'collision' and result['min_joint_margin_rad'] == .017
    assert result['min_clearance_m'] is None


def test_native_trace_calls_real_adapter_interface_with_sampled_descent(monkeypatch):
    from scripts.m11.reach_sweep_native import NativeSweep
    from openarm365_dev import environment
    row = candidate()
    pose = np.eye(4); pose[:3, 3] = row['waypoints']['pregrasp']
    pose[:3, :3] = Rotation.from_quat(row['grasp_quat_xyzw']).as_matrix()
    monkeypatch.setattr(environment, 'tcp_pose', lambda *args: pose.copy())
    monkeypatch.setattr(environment, 'mount_pose', lambda *args: np.eye(4))
    n = NativeSweep({}, Path('probe'), Path('out'), Path('.'))
    n.env = NS(); n.config = {'ik_candidates': 8, 'ik_max_joint_jump': 1.0}
    requests = []
    def request(operation, active, **extra):
        requests.append(extra)
        target = extra['targets']['openarm_right_hand_tcp']
        pose[:3, 3] = target['position']
        pose[:3, :3] = Rotation.from_quat(np.array(target['quaternion_wxyz'])[[1, 2, 3, 0]]).as_matrix()
        return dict(success=True, failure_reason=None, min_joint_margin_rad=.1, min_clearance_m=None,
                    certified_clearance_m=.005, endpoint_q=[])
    n.request = request; n.set_endpoint = lambda r: r
    result = n.trace_waypoint('grasp', row)
    assert result['success'] and len(requests) == 7
    assert sum('grasp_search' in r for r in requests) == 1 and 'grasp_search' in requests[-1]
    assert requests[-1]['ik_candidates'] == 8 and requests[-1]['max_joint_jump'] == 1.0
    assert result['reached_world_m'] == pytest.approx(row['waypoints']['grasp'])
    assert result['certified_clearance_m'] == .005 and result['min_clearance_m'] is None


def test_exact_base_checker_receives_r4_and_held_object(monkeypatch):
    from scripts.m11.reach_sweep_native import NativeSweep
    from openarm365_dev import base_motion
    n = NativeSweep({}, Path('probe'), Path('out'), Path('.'))
    n.env = NS(sim=NS(data=NS(qpos=np.zeros(3))))
    n.history = (); n.held = {'target_id': 42}; n.relative_object = None; n.retention = {}
    n.base_indexes = [0, 1, 2]; n.base_initial = np.zeros(3); n.binding = {'native_geom': 'real-chassis'}
    n.settings = object(); n.world = lambda **kw: {'complete': kw['complete']}
    n.forward = lambda: None; n.move_held = lambda: None
    checks = []
    def check(env, world, binding, delta, initial, **kwargs):
        checks.append((world, delta, kwargs))
        return {'minimum_clearance_m': .012}
    monkeypatch.setattr(base_motion, 'check_exact_base_path', check)
    result = n.base_path(candidate(base_moves=[[.1, 0., .2], [0., .1, 0.]]))
    assert result['success'] and result['min_clearance_m'] == .012
    assert len(checks) == 2 and all(c[0]['complete'] for c in checks)
    assert all(c[2]['held_body_id'] == 42 and c[2]['execution_settings'] is n.settings for c in checks)
    assert n.env.sim.data.qpos == pytest.approx([.1-.1*math.sin(.2), .1*math.cos(.2), .2])


def test_pitched_candidate_policy_descent(monkeypatch):
    row = next(r for r in sweep.candidates(probe()) if r['pitch_deg'] == 45 and r['closing_yaw_deg'] == 90)
    source = generate_candidate(report_for(row))
    validate_generated_policy(source)
    events, scope = execute_policy(monkeypatch, source, row['arm'])
    close = next(i for i, e in enumerate(events) if e[0] == 'close')
    approach = row['waypoints']['pregrasp']
    reached = max(i for i, e in enumerate(events[:close]) if e[0] == 'move' and np.linalg.norm(np.array(e[1])-approach) < 1e-8)
    descent = [e for e in events[reached+1:close] if e[0] == 'move']
    assert len(descent) == 7 and all(np.linalg.norm(e[2]) <= .015+1e-8 for e in descent)
    assert descent[-1][3]['grasp_search']['object_name'] == 'obj'
    assert scope['calls'][0] <= 320


def test_tuck_rescue_comparison(monkeypatch):
    rows = [candidate(), candidate(id=2, tuck_other_arm=True, tuck_q=sweep.TUCK_LEFT)]
    monkeypatch.setattr(sweep, 'candidates', lambda *args, **kwargs: iter(rows))
    class Planner(FakePlanner):
        def begin(self, row):
            self.failure = None if row['tuck_other_arm'] else 'base_path'
            super().begin(row)
    report = sweep.sweep(probe(), Planner(), ('right',), 2)
    assert report['candidates'][0]['tuck_required_for_base_path'] is True
    assert report['candidates'][0]['untucked_base_path_passed'] is False


@pytest.mark.parametrize('side,indexes', [('left', [2, 3]), ('right', [0, 1])])
def test_open_endpoint_uses_physical_to_native_mapping(side, indexes):
    from scripts.m11.reach_sweep_native import NativeSweep
    n = NativeSweep({}, Path('probe'), Path('out'), Path('.'))
    n.env = NS(sim=NS(data=NS(qpos=np.full(4, .022))),
               robots=[NS(_ref_gripper_joint_pos_indexes={'left': [0, 1], 'right': [2, 3]})])
    n.config = {'gripper_command_endpoints_m': [.001, .043]}
    n.forward = lambda: None
    n.open_hand(side)
    assert n.env.sim.data.qpos[indexes] == pytest.approx([.043, .043])
    other = [i for i in range(4) if i not in indexes]
    assert n.env.sim.data.qpos[other] == pytest.approx([.022, .022])


@pytest.mark.parametrize('grid,offsets', [('none', 1), ('small', 8), ('full', 60)])
def test_narrow_grid(grid, offsets):
    rows = list(sweep.candidates(probe(), base_grid=grid, pitches=(90, 45), yaw_step_deg=45))
    assert len(rows) == 2*offsets*2*2*2*8
    for arm in ('right', 'left'):
        selected = [r for r in rows if r['arm'] == arm]
        assert len({tuple(r['base_offset']) for r in selected}) == offsets
        assert {r['pitch_deg'] for r in selected} == {90, 45}
        assert {r['closing_yaw_deg'] for r in selected} == set(range(0, 360, 45))
        assert all((r['base_offset'][1] >= 0 if arm == 'right' else r['base_offset'][1] <= 0) for r in selected)
    if grid == 'none':
        assert all(r['base_moves'] == [] for r in rows)


@pytest.mark.parametrize('cap,partial', [(3, True), (4, False), (8, False)])
def test_candidate_cap_and_complete_grid(cap, partial):
    progress = []
    report = sweep.sweep(probe(), FakePlanner('grasp'), ('right',), 2,
        lambda *args: progress.append(args), base_grid='none', pitches=(90,), yaw_step_deg=360,
        max_candidates=cap)
    assert report['total_candidates'] == min(4, cap)
    assert report['partial'] is partial
    assert report['stop_reason'] == ('max_candidates' if partial else None)
    assert len(progress) == min(4, cap) and all(p[3] >= 0 for p in progress)
    assert len(report['candidates']) == 2
    assert report['pass_counts']['pregrasp'] == min(4, cap)


def test_time_limit_finishes_candidate_and_counts_only_evaluated(monkeypatch):
    now = [0.]
    monkeypatch.setattr(sweep.time, 'monotonic', lambda: now[0])
    class Planner(FakePlanner):
        def begin(self, row):
            now[0] += 2.
            super().begin(row)
    report = sweep.sweep(probe(), Planner('lift'), ('right',), 20,
        base_grid='none', pitches=(90, 45), yaw_step_deg=45, time_limit_s=3.)
    assert report['total_candidates'] == 2
    assert report['elapsed_seconds'] == 4.
    assert report['partial'] and report['stop_reason'] == 'time_limit_s'
    assert all(r['elapsed_seconds'] == 2. for r in report['candidates'])
    assert report['stage_blocking_reasons']['lift'] == {'blocked': 2}
    assert 'PARTIAL' in sweep.summary(report)
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize('flag,value', [('--base-grid', 'tiny'), ('--pitches', ''),
    ('--pitches', '90,no'), ('--pitches', 'nan'), ('--pitches', 'inf'), ('--pitches', '91'),
    ('--pitches', '90,90'), ('--yaw-step-deg', '0'), ('--yaw-step-deg', '361'),
    ('--max-candidates', '0'), ('--time-limit-s', '0'), ('--time-limit-s', 'nan'),
    ('--time-limit-s', 'inf')])
def test_invalid_narrow_flags_fail_before_native_start(flag, value):
    with pytest.raises(SystemExit) as exc:
        sweep.main(['--probe', '/unused/probe.json', '--out', '/unused/out', flag, value])
    assert exc.value.code == 2


def test_cli_writes_partial_artifacts_with_narrow_options(tmp_path, monkeypatch, capsys):
    from scripts.m11 import reach_sweep_native
    class Planner(FakePlanner):
        receipt = {'fake': True}
        def __init__(self, *args):
            super().__init__('grasp')
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(sweep, 'refuse_keys_env', lambda: pytest.fail('in-process main must not install a permanent audit hook'))
    monkeypatch.setattr(reach_sweep_native, 'NativeSweep', Planner)
    monkeypatch.setattr(sweep, 'check_environment', lambda: None)
    source = tmp_path/'probe.json'; source.write_text(json.dumps(probe()))
    out = tmp_path/'out'
    assert sweep.main(['--probe', str(source), '--out', str(out), '--arms', 'right',
        '--base-grid', 'none', '--pitches', '90,45', '--yaw-step-deg', '45',
        '--max-candidates', '3', '--time-limit-s', '300', '--limit', '2']) == 0
    report = json.loads((out/'candidates.json').read_text())
    assert report['partial'] and report['total_candidates'] == 3
    assert len(report['candidates']) == 2 and report['runtime'] == Planner.receipt
    assert report['grid'] == dict(arms=['right'], base_grid='none', pitches=[90, 45], yaw_step_deg=45)
    assert 'PARTIAL' in (out/'summary.md').read_text()
    assert capsys.readouterr().out.count('candidate=') == 3


def test_isolated_bootstrap_uses_owner_registry_and_rejects_tampering(tmp_path):
    """Real frozen identity/dev_local modules, synthetic manifests, no runtime/GPU import."""
    import subprocess
    # Keep this in -I so an already imported owner identity cannot hide the bug.
    script = r'''
import hashlib, json, runpy, shutil, sys
from pathlib import Path
root, out = map(Path, sys.argv[1:])
frozen = out/'frozen'
(frozen/'curobo_v2').mkdir(parents=True)
for name in ('__init__.py', 'identity.py', 'dev_local.py', 'official_normalization.py', 'local_dev_trust.py'):
    shutil.copyfile(root/'curobo_v2'/name, frozen/'curobo_v2'/name)
sys.path.insert(0, str(frozen))
from curobo_v2 import identity as trust, dev_local
assert Path(trust.__file__).parent == frozen/'curobo_v2'
assert not trust.DEPLOYMENT_REGISTRY.exists()
def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return trust.digest(path)
parent_path = out/'official.json'
parent = dict(schema='attentionbench.curobo-v2.runtime.v1', generation=2,
    prefix='/synthetic/python', python='/synthetic/python/bin/python', library_root='/synthetic/library',
    library_revision=trust.OFFICIAL_COMMIT, library_base=trust.OFFICIAL_COMMIT,
    distribution_version='synthetic', module_version='synthetic',
    files={'/synthetic/external': 'unchanged'}, dependencies={'synthetic': {}}, distributions={},
    robot_configs={}, project_root='/synthetic/official/project', service_root='/synthetic/official/service',
    official_only=True, provenance={})
parent_sha = write(parent_path, parent)
trust.OFFICIAL_PARENT_LOCK = parent_path
trust.TRUSTED_LOCKS = {parent_sha: trust.OFFICIAL_COMMIT}
base_path = out/'base.json'
base = dict(parent, project_root='/synthetic/base/project', service_root='/synthetic/base/service')
base_sha = write(base_path, base)
manifest = {'adapter.py': 'synthetic'}
key = dev_local._key(base_sha, manifest)
dev_local.DEV_LOCAL_ROOT = out/'dev-local'
lock_path = dev_local.DEV_LOCAL_ROOT/key/'runtime-lock.json'
lock = dict(base, project_root=str(lock_path.parent/'runtime/project'), provenance=dict(
    deployment_kind='dev-local', formal_eligible=False, snapshot_key=key, source_manifest=manifest,
    base_deployment_lock_path=str(base_path), base_deployment_lock_sha256=base_sha))
lock_sha = write(lock_path, lock)
# The frozen identity module is already loaded, just as in the real worker.
load = runpy.run_path(str(root/'scripts/m11/reach_sweep_worker.py'))['load_bootstrap']
def rejected(call):
    try: call()
    except trust.IdentityError: return
    raise AssertionError('identity failure was accepted')
rejected(lambda: trust.load_lock(lock_path, tier='dev', boundary='dev_loop'))
registry = out/'owner/trusted_deployments.json'
entry = dict(lock_path=str(base_path), official_commit=trust.OFFICIAL_COMMIT,
    parent_sha256=parent_sha, allowed_tiers=['dev'])
registry_sha = write(registry, {base_sha: entry})
bootstrap = dict(lock_path=str(lock_path), lock_sha256=lock_sha,
    trust_registry=str(registry), trust_registry_sha256=registry_sha)
assert load(bootstrap) == (lock, lock_sha)
assert trust.DEPLOYMENT_REGISTRY == registry and trust._registry_digest == registry_sha
rejected(lambda: load(dict(bootstrap, lock_sha256='wrong')))
rejected(lambda: load(dict(bootstrap, trust_registry='relative.json')))
write(registry, {})
rejected(lambda: load(bootstrap))  # changed registry bytes
rejected(lambda: load(dict(bootstrap, trust_registry_sha256=trust.digest(registry))))  # no approval
write(registry, {base_sha: dict(entry, allowed_tiers=['formal'])})
rejected(lambda: load(dict(bootstrap, trust_registry_sha256=trust.digest(registry))))
assert not any(n == 'curobo' or n.startswith('curobo.') for n in sys.modules)
print('authenticated frozen identity; rejected pin, registry, approval and tier mismatches')
'''
    result = subprocess.run([sys.executable, '-B', '-I', '-c', script, str(sweep.ROOT), str(tmp_path)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert 'authenticated frozen identity' in result.stdout


@pytest.mark.parametrize('module', ['reach_sweep', 'reach_sweep_worker'])
def test_key_guard_is_private_to_script_process(module):
    import ast
    import subprocess
    import sys
    from pathlib import Path
    source = Path('scripts/m11') / (module + '.py')
    entry = ast.parse(source.read_text()).body[-1]
    assert isinstance(entry, ast.If)
    assert ast.unparse(entry.body[0]) == 'refuse_keys_env()'
    code = f"""
import sys
from scripts.m11 import {module} as script
# Import alone must not install a key guard. Audit events do not open a file.
sys.audit('open', '/synthetic/keys.env', 'r', 0)
script.refuse_keys_env()
try:
    sys.audit('open', '/synthetic/keys.env', 'r', 0)
except PermissionError:
    pass
else:
    raise AssertionError('script process did not refuse keys.env')
"""
    subprocess.run([sys.executable, '-B', '-c', code], check=True, timeout=30)
