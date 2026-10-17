import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from curobo_v2.client import PlanningRejected
from openarm365_dev.base_motion import (BASE_SETTLE_CONSECUTIVE_STEPS, BASE_SETTLE_MAX_STEPS,
    BasePathRejected, base_is_stationary, check_chassis_path, guard_base_and_arms,
    validate_delta, robot_boxes_from_geoms)
from openarm365_dev.owner import build_sdk
from openarm365_dev.executor import NativeExecutor
from openarm365_dev.policy_validation import validate_generated_policy
from openarm365_dev.policy_worker import OPERATIONS as WORKER_OPERATIONS
from openarm365_dev.policy_validation import OPERATIONS as VALIDATION_OPERATIONS
from openarm365_dev.sandbox import _meter_sdk_call
from benchmarks.attention_harness.diagnostics.base_motion_probe import TRIALS, markdown_table, policy_source, main, summarize


BINDING = {'center_in_planner_frame': [0., 0., 0.5], 'half_size': [0.3, 0.2, 0.3]}
SEED101_BINDING = {'center_in_planner_frame': [-0.198, 0., -0.291],
                   'half_size': [0.3665, 0.2615, 0.303]}
SEED101_WORLD = Path(__file__).resolve().parents[2] / 'benchmarks/attention_harness/tests/fixtures/ik_path_world.json'


def world(*boxes):
    return {'cuboids': [dict(name=f'box{i}', center=list(center), half_size=list(half),
                              quat_wxyz=[1., 0., 0., 0.]) for i, (center, half) in enumerate(boxes)],
            'inventory': [dict(geom_type=6) for _ in boxes], 'world_sha256': 'test'}


def box(name, center, half):
    return dict(name=name, center=list(center), half_size=list(half), quat_wxyz=[1., 0., 0., 0.])


def full_robot_scene(obstacle_y):
    binding = {'center_in_planner_frame': [0., 0., .5], 'half_size': [.2, .16, .2]}
    robot = [box('chassis', [0., 0., .5], [.2, .16, .2]),
             box('left_finger', [.45, 0., .7], [.04, .03, .04])]
    obstacle = box('cabinet', [.45, obstacle_y, .7], [.05, .04, .1])
    measured = dict(cuboids=[obstacle], inventory=[dict(name='cabinet')], world_sha256='full')
    return measured, binding, robot


def test_pure_robot_box_source_uses_current_pose_and_mesh_bounds():
    from scipy.spatial.transform import Rotation
    pose=np.eye(4);pose[:3,:3]=Rotation.from_euler('z',.5).as_matrix();pose[:3,3]=[1.,2.,3.]
    mount=np.eye(4);mount[:3,3]=[-.2,0,0]
    rows=[dict(name='mesh_finger',local_center=[.1,0,0],half_size=[.04,.02,.01],world_from_geom=pose)]
    result=robot_boxes_from_geoms(rows,mount)
    assert len(result)==1 and result[0]['name']=='mesh_finger'
    assert np.allclose(result[0]['center'],pose[:3,:3]@[.1,0,0]+[.8,2,3])
    assert result[0]['half_size']==[.04,.02,.01]


def test_full_robot_sweep_rejects_finger_and_clear_path():
    measured,binding,robot=full_robot_scene(.23)
    # Chassis alone passes while the extended finger enters the cabinet.
    assert check_chassis_path(measured,binding,(0,.15,0))['status']=='clear'
    with pytest.raises(PlanningRejected,match='left_finger versus cabinet'):
        check_chassis_path(measured,binding,(0,.15,0),robot)
    clear,binding,robot=full_robot_scene(.50)
    receipt=check_chassis_path(clear,binding,(0,.15,0),robot)
    assert receipt['status']=='clear' and receipt['robot_geoms_checked']==2
    assert receipt['minimum_clearance_pair']['robot_geom'] in ('chassis','left_finger')
    assert receipt['minimum_clearance_pair']['obstacle']=='cabinet'
    assert receipt['minimum_clearance_m']>.02


def test_origin_check_and_full_world_including_exemptable_object():
    measured,binding,robot=full_robot_scene(.08)
    with pytest.raises(PlanningRejected,match='sample 0/1') as caught:
        check_chassis_path(measured,binding,(0,0,0),robot)
    audit=caught.value.collision_check
    assert audit['robot_geoms_checked']==2
    assert audit['minimum_clearance_pair']['sample_index']==0
    assert audit['minimum_obstacle_clearance_m']<.02
    # The base check sees the complete world, even if an arm planner omits an object.
    measured['cuboids'][0]['name']='movable_object'
    with pytest.raises(PlanningRejected,match='movable_object'):
        check_chassis_path(measured,binding,(0,0,0),robot)


def test_limits_and_finite():
    assert validate_delta(.2, 0, .3).tolist() == [.2, 0, .3]
    for value in ((.2001, 0, 0), (.15, .15, 0), (0, 0, .301), (float('nan'), 0, 0)):
        with pytest.raises(PlanningRejected): validate_delta(*value)


def test_path_clear_edge_penetration_and_yaw_sweep():
    assert check_chassis_path(world(([0., .60, .5], [.05, .05, .1])), BINDING, [0, .1, 0])['status'] == 'clear'
    for obstacle, delta in [
        (([0., .37, .5], [.05, .05, .1]), [0, .1, 0]), # 2 cm skin grazes
        (([0., .30, .5], [.05, .05, .1]), [0, .1, 0]), # penetration
        (([.24, .29, .5], [.02, .02, .1]), [0, 0, .3]), # swept corner
    ]:
        with pytest.raises(PlanningRejected, match='clearance'):
            check_chassis_path(world(obstacle), BINDING, delta)


def seed101_snapshot():
    """Wrap the stored cuboids in the inventory schema returned by world.snapshot."""
    cuboids = json.loads(SEED101_WORLD.read_text())['world']
    inventory = []
    for box in cuboids:
        geom_id, native_name = box['name'].split(':', 2)[1:]
        inventory.append(dict(name=box['name'], geom_id=int(geom_id), body_id=-1,
                              body_name=native_name, geom_type=6,
                              source='actual MuJoCo collision geometry'))
    return dict(cuboids=cuboids, inventory=inventory, world_sha256='stored-seed-101')


def test_seed101_floor_and_chassis_paths():
    measured = seed101_snapshot()
    floor = next(box for box in measured['cuboids'] if box['name'].endswith(':floor_1_room_g0'))
    floor_item = measured['inventory'][measured['cuboids'].index(floor)]
    assert floor_item['geom_type'] == 6
    assert check_chassis_path(dict(measured, cuboids=[floor], inventory=[floor_item]),
                              SEED101_BINDING, (0, .1, 0))['obstacles_checked'] == 0
    for delta in ((0, .1, 0), (0, .2, 0), (-.1, 0, 0), (0, 0, .2)):
        assert check_chassis_path(measured, SEED101_BINDING, delta)['status'] == 'clear'
    with pytest.raises(PlanningRejected, match='stack_1_left_group'):
        check_chassis_path(measured, SEED101_BINDING, (.2, 0, 0))
    with pytest.raises(PlanningRejected, match='right_door_g1'):
        check_chassis_path(measured, SEED101_BINDING, (0, 0, .3))


def test_support_top_uses_rotation_and_does_not_exempt_counter():
    from scipy.spatial.transform import Rotation
    binding = {'center_in_planner_frame': [0., 0., 0.], 'half_size': [.3, .2, .3]}
    tilted = dict(name='tilted_counter', center=[0., 0., -.4],
                  half_size=[.3, .2, .01],
                  quat_wxyz=Rotation.from_euler('y', .5).as_quat()[[3, 0, 1, 2]].tolist())
    measured = dict(cuboids=[tilted], inventory=[dict(geom_type=0)], world_sha256='test')
    with pytest.raises(PlanningRejected, match='tilted_counter'):
        check_chassis_path(measured, binding, (0, .1, 0))


def test_guard_window():
    guard_base_and_arms([.2, 0, 0], [0, 0, 0], [0], [0])
    guard_base_and_arms([.001, 0, 0], [0, 0, 0], [0])
    with pytest.raises(RuntimeError, match='Uncommanded'): guard_base_and_arms([.0011, 0, 0], [0, 0, 0], [0])
    with pytest.raises(RuntimeError, match='Arm joint drift'): guard_base_and_arms([.2, 0, 0], [0, 0, 0], [.0041], [0])


def test_sdk_chain_and_trace():
    assert WORKER_OPERATIONS['base'] == VALIDATION_OPERATIONS['base'] == {'move_delta'}
    validate_generated_policy(policy_source('base_y_plus'))
    class Owner:
        def __init__(self): self.calls=[]
        def rpc(self, *args): self.calls.append(args);return None
    owner=Owner();events=[];sdk=build_sdk(owner,events.append)
    sdk.base.move_delta(0, .1, 0)
    assert owner.calls[0][:2] == ('base','move_delta')
    assert events[0]['source']=='base' and events[0]['status']=='completed'
    assert events[0]['arguments']=={'dx':0,'dy':.1,'dyaw':0}
    assert _meter_sdk_call(0,1)==1
    with pytest.raises(RuntimeError,match='SDK call limit'): _meter_sdk_call(1,1)


def test_base_receipt_journal_and_step_count(monkeypatch):
    import openarm365_dev.world as world_module
    import openarm365_dev.executor as executor_module
    monkeypatch.setattr(world_module, 'snapshot', lambda *args, **kwargs: world())
    monkeypatch.setattr(executor_module, 'check_exact_base_path',
                        lambda *args: dict(status='clear',robot_geoms_checked=1,
                            obstacle_candidates_checked=2,distance_calculations=3,elapsed_seconds=.01,
                            start_minimum_clearance_m=.04,minimum_clearance_pair=None))
    executor=object.__new__(NativeExecutor)
    data=SimpleNamespace(qpos=np.zeros(3))
    robot=SimpleNamespace(_ref_base_joint_pos_indexes=np.array([0,1,2]))
    executor.env=SimpleNamespace(sim=SimpleNamespace(data=data),robots=[robot],horizon=2400,
                                 control_timestep=.05)
    class Planner:
        chassis_binding=BINDING
        prepared={('left',)}
        ledger=SimpleNamespace(invalidate=lambda reason: None)
        def state(self): return np.zeros(14)
    executor.planner=Planner();executor.base_initial=np.zeros(3);executor.base_hold=np.zeros(3)
    executor.sequence=0;executor.physics_steps=0;executor.failed=False;executor.epoch='test'
    executor.check_live=lambda: None
    events=[];executor.log=lambda path, row: events.append(row);executor.journal=Path('/unused')
    def step():
        if executor.sequence>=executor.env.horizon: raise RuntimeError('Native control-step budget exhausted')
        c=executor.base_command;theta=data.qpos[2]
        local=np.array([np.cos(theta)*c[0]+np.sin(theta)*c[1],
                        -np.sin(theta)*c[0]+np.cos(theta)*c[1]])
        data.qpos[:2]+=.05*.15*local;data.qpos[2]+=.05*.3*c[2]
        executor.sequence+=1;executor.physics_steps+=1
    executor.control_step=step
    result=executor.base_move(0,.1,0,request_id='r1')
    assert result['request_id']=='r1' and result['start_sequence']==0
    assert result['sequence']==executor.sequence==executor.physics_steps
    assert abs(result['base_error'][1])<=.005 and result['collision_check']['status']=='clear'
    assert result['collision_check']['robot_geoms_checked']==1
    assert result['collision_check']['distance_calculations']==3
    assert events[0]['collision_check']['start_minimum_clearance_m']==.04
    assert 'minimum_clearance_pair' in events[-1]['collision_check']
    assert result['arm_joint_change']==[0.]*14
    assert result['base_settle_steps']==BASE_SETTLE_CONSECUTIVE_STEPS
    assert result['base_settle_displacement']==[0.]*3
    assert [row['event'] for row in events]==['base_intent','base_tracking','receipt']
    assert executor.planner.prepared==set()
    rejected=dict(status='rejected',robot_geoms_checked=2,minimum_clearance_m=.01,
                  minimum_obstacle_clearance_m=.01,
                  minimum_clearance_pair=dict(robot_geom='left_finger',obstacle='cabinet',sample_index=1))
    def reject(*args): raise BasePathRejected('finger versus cabinet',rejected)
    monkeypatch.setattr(executor_module,'check_exact_base_path',reject)
    with pytest.raises(PlanningRejected,match='finger versus cabinet'):
        executor.base_move(0,.01,0,request_id='collision')
    assert events[-1]['event']=='base_collision_rejected'
    assert events[-1]['collision_check']==rejected
    monkeypatch.setattr(executor_module,'check_exact_base_path',
                        lambda *args: dict(status='clear',robot_geoms_checked=1,minimum_clearance_pair=None))
    executor.env.horizon=executor.sequence
    with pytest.raises(RuntimeError, match='budget'): executor.base_move(0,.01,0,request_id='r2')
    assert executor.failed


def fake_settling_executor(monkeypatch, positions, arm_change_step=None):
    import openarm365_dev.world as world_module
    import openarm365_dev.executor as executor_module
    monkeypatch.setattr(world_module, 'snapshot', lambda *args, **kwargs: world())
    monkeypatch.setattr(executor_module, 'check_exact_base_path',
                        lambda *args: dict(status='clear'))
    executor=object.__new__(NativeExecutor)
    data=SimpleNamespace(qpos=np.zeros(3))
    robot=SimpleNamespace(_ref_base_joint_pos_indexes=np.array([0,1,2]))
    executor.env=SimpleNamespace(sim=SimpleNamespace(data=data),robots=[robot],horizon=2400,
                                 control_timestep=.05)
    class Planner:
        chassis_binding=BINDING
        prepared=set()
        ledger=SimpleNamespace(invalidate=lambda reason: None)
        arm=np.zeros(14)
        def state(self): return self.arm.copy()
    executor.planner=Planner();executor.base_initial=np.zeros(3);executor.base_hold=np.zeros(3)
    executor.sequence=0;executor.physics_steps=0;executor.failed=False;executor.epoch='test'
    executor.check_live=lambda: None
    events=[];executor.log=lambda path, row: events.append(row);executor.journal=Path('/unused')
    def step():
        if executor.sequence>=executor.env.horizon: raise RuntimeError('Native control-step budget exhausted')
        assert np.array_equal(executor.base_command,np.zeros(3))
        data.qpos[:]=positions[min(executor.sequence,len(positions)-1)]
        executor.sequence+=1;executor.physics_steps+=1
        if executor.sequence==arm_change_step: executor.planner.arm[0]=.0041
    executor.control_step=step
    return executor,events


def test_base_stationary_rate_boundaries():
    assert base_is_stationary([0,0,0],[1e-5,-1e-5,1e-5],.05)
    assert not base_is_stationary([0,0,0],[1.01e-5,0,0],.05)
    assert not base_is_stationary([0,0,0],[0,0,1.01e-5],.05)


def test_base_creeps_one_mm_then_holds_at_settled_position(monkeypatch):
    positions=[[0,y,0] for y in (.00025,.0005,.00075,.001)] + [[0,.001,0]]*5
    executor,events=fake_settling_executor(monkeypatch,positions)
    receipt=executor.base_move(0,0,0,request_id='creep')
    assert receipt['base_settle_steps']==len(positions)
    assert receipt['base_settle_displacement']==pytest.approx([0,.001,0])
    assert receipt['after_base_qpos']==pytest.approx([0,.001,0])
    assert executor.base_hold==pytest.approx([0,.001,0])
    assert executor.sequence==executor.physics_steps==len(positions)
    assert events[-1]['base_settle_steps']==len(positions)


def test_base_settle_timeout_is_public_failure(monkeypatch):
    positions=[[0,(i+1)*.00002,0] for i in range(BASE_SETTLE_MAX_STEPS)]
    executor,events=fake_settling_executor(monkeypatch,positions)
    with pytest.raises(RuntimeError,match=f'Base did not settle within {BASE_SETTLE_MAX_STEPS} control steps'):
        executor.base_move(0,0,0,request_id='sliding')
    assert executor.sequence==executor.physics_steps==BASE_SETTLE_MAX_STEPS
    assert events[-1]['event']=='base_failure'
    assert events[-1]['base_settle_steps']==BASE_SETTLE_MAX_STEPS
    assert executor.failed


def test_base_settle_rechecks_final_arrival_error(monkeypatch):
    positions=[[0,.001*(i+1),0] for i in range(6)] + [[0,.006,0]]*5
    executor,events=fake_settling_executor(monkeypatch,positions)
    with pytest.raises(RuntimeError,match='Base motion failed to converge within 5mm/5mrad'):
        executor.base_move(0,0,0,request_id='overshoot')
    assert executor.sequence==len(positions)
    assert events[-2]['event']=='base_tracking'
    assert events[-2]['base_error']==pytest.approx([0,-.006,0])
    assert events[-1]['event']=='base_failure'


def test_base_already_stationary_uses_exactly_k_budget_steps(monkeypatch):
    executor,events=fake_settling_executor(monkeypatch,[[0,0,0]])
    receipt=executor.base_move(0,0,0,request_id='still')
    assert receipt['base_settle_steps']==BASE_SETTLE_CONSECUTIVE_STEPS
    assert executor.sequence==executor.physics_steps==BASE_SETTLE_CONSECUTIVE_STEPS
    assert events[-1]['event']=='receipt'


def test_base_settle_keeps_arm_guard(monkeypatch):
    executor,events=fake_settling_executor(monkeypatch,[[0,0,0]],arm_change_step=2)
    with pytest.raises(RuntimeError,match='Arm joint drift during base motion exceeded 4mrad'):
        executor.base_move(0,0,0,request_id='arm-drift')
    assert executor.sequence==2 and executor.failed
    assert events[-1]['event']=='base_failure'


def test_base_settle_steps_consume_remaining_horizon(monkeypatch):
    executor,events=fake_settling_executor(monkeypatch,[[0,0,0]])
    executor.env.horizon=BASE_SETTLE_CONSECUTIVE_STEPS-1
    with pytest.raises(RuntimeError,match='Native control-step budget exhausted'):
        executor.base_move(0,0,0,request_id='budget')
    assert executor.sequence==BASE_SETTLE_CONSECUTIVE_STEPS-1
    assert events[-1]['event']=='base_failure'


def test_world_and_pose_target_follow_moved_mount(monkeypatch):
    from scipy.spatial.transform import Rotation
    import openarm365_dev.world as world_module
    import openarm365_dev.planner as planner_module
    class Model:
        ngeom=2
        geom_contype=np.array([1,1]);geom_conaffinity=np.array([1,1]);geom_bodyid=np.array([2,3])
        geom_type=np.array([6,6]);geom_size=np.array([[.1,.1,.1],BINDING['half_size']])
        body_parentid=np.array([0,0,0,0])
        def body_name2id(self,name): return 1
        def geom_id2name(self,gid): return ['fixture','chassis_collision'][gid]
        def body_id2name(self,bid): return ['','robot','fixture','chassis'][bid]
    class Env: pass
    env=Env();env.mount=np.eye(4)
    geo=SimpleNamespace(geom_xpos=np.array([[1.,0.,.5],[0.,0.,.5]]),
        geom_xmat=np.repeat(np.eye(3)[None],2,axis=0).reshape(2,9))
    env.sim=SimpleNamespace(model=Model(),data=geo)
    monkeypatch.setattr(world_module,'mount_pose',lambda e:e.mount)
    monkeypatch.setattr(world_module,'shape_box',lambda model,gid:(np.zeros(3),model.geom_size[gid]))
    before=world_module.snapshot(env,chassis_binding={**BINDING,'native_geom':'chassis_collision'})
    env.mount[:3,:3]=Rotation.from_euler('z',.2).as_matrix();env.mount[:2,3]=[.1,0]
    geo.geom_xpos[1]=env.mount[:3,:3]@np.array(BINDING['center_in_planner_frame'])+env.mount[:3,3]
    geo.geom_xmat[1]=env.mount[:3,:3].ravel()
    after=world_module.snapshot(env,chassis_binding={**BINDING,'native_geom':'chassis_collision'})
    expected=(np.linalg.inv(env.mount)@np.array([1.,0.,.5,1.]))[:3]
    assert np.allclose(before['cuboids'][0]['center'],[1.,0.,.5])
    assert np.allclose(after['cuboids'][0]['center'],expected)
    monkeypatch.setattr(planner_module,'mount_pose',lambda e:e.mount)
    planner=object.__new__(planner_module.MujocoV2Planner)
    planner.env=env;planner.names=[f'openarm_left_joint{i}' for i in range(1,8)]
    caught={}
    planner.plan=lambda path,extra,active,epoch:caught.update(extra)
    planner.plan_pose(np.array([1.,0.,.5]),np.eye(3),side='left',epoch='test')
    assert np.allclose(caught['targets']['openarm_left_hand_tcp']['position'],expected)


def test_probe_dry_run_and_table(capsys):
    assert main(['--dry-run']) == 0
    output=capsys.readouterr().out
    assert all(name in output for name in TRIALS)
    rows=[dict(trial='base_y_plus',public_status='completed',base_change=[0,.1,0],
               arrival_error=[0,.001,0],arm_joint_change=[0.001],elapsed_seconds=1.5)]
    table=markdown_table(rows)
    assert '| base_y_plus | completed |' in table and '0.00100' in table
    assert len(TRIALS)==6


def test_probe_reads_public_error_without_event_source(tmp_path):
    version = tmp_path/'artifacts/devexp/cells/cell/v1'
    episode = version/'executions/openarm365-dev-test'
    episode.mkdir(parents=True)
    message = 'RuntimeError: PlanningRejected: Base path clearance below 0.02m at sample 0/5: mujoco:392:floor_1_room_g0'
    (version/'public-feedback.json').write_text(json.dumps(dict(execution_status='failed', public_error=message,
        action_trace=[dict(operation='move_delta', status='failed', error={'message': message})])))
    (episode/'result.json').write_text(json.dumps({'status': 'failed', 'error': message}))
    row = summarize('base_y_plus', tmp_path, 49.)
    assert row['public_status'] == 'failed'
    assert row['public_error'] == message
    assert message in markdown_table([row])
