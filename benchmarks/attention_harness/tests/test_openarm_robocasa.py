"""Robot identity must be selected once and must never fall back to Panda."""
from dataclasses import FrozenInstanceError

import pytest

from benchmarks.attention_harness.robocasa_native.robot_config import (
    RobotSelection, robot_selection,
)


def test_legacy_franka_config_remains_compatible():
    old = {"schema_version": "attentionbench.robocasa-formal-config.v3", "suite": "robocasa"}
    assert robot_selection(old) == RobotSelection("Franka", "single")
    assert "robot" not in old


@pytest.mark.parametrize("arm", ["left", "right"])
def test_openarm_selects_explicit_execution_side(arm):
    selected = robot_selection({"suite": "robocasa", "robot": {"model": "OpenArm", "execution_arm": arm}})
    assert selected.model == "OpenArm"
    assert selected.execution_arm == arm
    assert selected.inactive_arm == ("right" if arm == "left" else "left")


def test_openarm_default_is_left_and_selection_is_immutable():
    selected = robot_selection({"suite": "robocasa", "robot": {"model": "OpenArm"}})
    assert selected.execution_arm == "left"
    with pytest.raises(FrozenInstanceError):
        selected.execution_arm = "right"


@pytest.mark.parametrize("robot", [
    {"model": "Unknown"}, {"model": "OpenArm", "execution_arm": "both"},
    {"model": "Franka", "execution_arm": "left"}, {}, None,
])
def test_invalid_robot_never_falls_back(robot):
    with pytest.raises(ValueError):
        robot_selection({"suite": "robocasa", "robot": robot})


def test_unknown_history_does_not_acquire_a_robot_identity():
    with pytest.raises(ValueError):
        robot_selection({"suite": "robocasa"})


def test_other_suites_are_not_openarm_routes():
    with pytest.raises(ValueError):
        robot_selection({"suite": "robocasa365", "robot": {"model": "OpenArm"}})


def test_native_kitchen_adapter_exists_without_franka_service_import():
    from openarm_robocasa.kitchen import OpenArmKitchen
    assert OpenArmKitchen.robot_model == "OpenArm"


def test_sdk_attests_selected_model_and_arm_before_any_command():
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    calls = []
    def transport(method, path, body, timeout):
        calls.append((method, path, body))
        return {"ready": True, "robot": {"model": "Franka", "execution_arm": "single"}}
    with pytest.raises(RuntimeError, match="identity"):
        OpenArmActionBackend("http://127.0.0.1:8872", execution_arm="left", transport=transport)
    assert calls == [("GET", "/health", None)]


def test_openarm_sdk_rejects_external_server_and_invalid_arm():
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    for url, arm in [("https://example.com", "left"), ("http://127.0.0.1:8872", "both")]:
        with pytest.raises(ValueError):
            OpenArmActionBackend(url, execution_arm=arm)


def test_openarm_planning_error_does_not_dispatch_an_action():
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    calls = []
    def transport(method, path, body, timeout):
        calls.append(path)
        if path == "/health":
            return {"ready": True, "robot": {"model": "OpenArm", "execution_arm": "right"}}
        raise RuntimeError("unreachable selected OpenArm TCP")
    backend = OpenArmActionBackend("http://127.0.0.1:8872", execution_arm="right", transport=transport)
    with pytest.raises(RuntimeError, match="unreachable"):
        backend.move_arm_to_position(1, 2, 3, tolerance=.004, max_steps=100)
    assert calls == ["/health", "/plan"]


@pytest.mark.parametrize("arm", ["left", "right"])
@pytest.mark.parametrize("value", [-1., 1.])
def test_gripper_uses_selected_driver_and_loaded_limits(arm, value):
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    calls = []
    identity = {"model": "OpenArm", "execution_arm": arm}
    def transport(method, path, body, timeout):
        calls.append((path, body))
        if path == "/health": return {"ready": True, "robot": identity}
        if path == "/state": return {"robot": identity, "epoch": 7, arm + "_gripper": {"limits_m": [0, .04399999976158142]}}
        if path == "/actions": return {"id": "7:1"}
        if path.startswith("/actions/"): return {"id": "7:1", "status": "completed"}
        raise AssertionError(path)
    sdk = OpenArmActionBackend("http://127.0.0.1:8872", execution_arm=arm, transport=transport)
    sdk.set_gripper(value, settle_steps=10)
    expected = .04399999976158142 if value < 0 else 0
    assert ("/actions", {arm + "_gripper": expected, "epoch": 7}) in calls
    assert not any("franka" in path.lower() for path, body in calls)


def test_fixed_mount_and_unimplemented_rotation_report_capability_error():
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    from tidybot_sdk import CapabilityNotAvailableError
    sdk = OpenArmActionBackend("http://127.0.0.1:8872", transport=lambda *_: {
        "ready": True, "robot": {"model": "OpenArm", "execution_arm": "left"}})
    with pytest.raises(CapabilityNotAvailableError, match="fixed mount"):
        sdk.move_base_delta(.1, 0, 0)
    with pytest.raises(CapabilityNotAvailableError, match="rotation"):
        sdk.move_arm_delta(0, 0, .01, (.1, 0, 0))


def test_observation_uses_one_physics_step_for_image_joints_and_tcp():
    import io,json,numpy as np
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    robot={'model':'OpenArm','execution_arm':'left'}
    pose=np.eye(4);pose[:3,3]=[3.,-1.,1.]
    state={'robot':robot,'total_steps':42,'epoch':2,'tcp_world':{'left':pose.tolist()},
           'left':{'qpos':[.1]*7},'right':{'qpos':[.2]*7},'left_gripper':{'driver_position_m':.03}}
    buffer=io.BytesIO();np.savez(buffer,rgb=np.zeros((2,3,3)),depth_m=np.ones((2,3)),K=np.eye(3),
         T_world_optical=np.eye(4),total_steps=42,epoch=2,state_json=json.dumps(state))
    calls=[]
    def transport(method,path,body,timeout):
        calls.append(path)
        if path=='/health':return {'ready':True,'robot':robot}
        if path=='/observation':return buffer.getvalue()
        pytest.fail('Observation must not fetch joints from a different /state step')
    sdk=OpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    obs=sdk.observe()
    assert int(obs['physical_step'])==42 and int(obs['epoch'])==2
    assert np.allclose(obs['robot0_eef_pos'],[3,-1,1])
    assert np.allclose(obs['right_joint_pos'],[.2]*7)
    assert calls==['/health','/observation']


def test_unreachable_plan_is_read_only_and_never_dispatched():
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    from benchmarks.attention_harness.robocasa_native.agent_actions import ReadOnlyArmPlanRejected
    robot={'model':'OpenArm','execution_arm':'left'};calls=[]
    def transport(method,path,body,timeout):
        calls.append(path)
        if path=='/health':return {'ready':True,'robot':robot}
        if path=='/plan':return {'robot':robot,'reachable':False,'planning_required':True,'status':'rejected',
            'reason':'outside bounded workspace','epoch':2}
        pytest.fail('Unreachable plan dispatched a command')
    sdk=OpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    assert sdk.plan_arm_to_position(20,0,0)['reachable'] is False
    with pytest.raises(ReadOnlyArmPlanRejected):sdk.move_arm_to_position(20,0,0,tolerance=.004,max_steps=10)
    assert calls==['/health','/plan','/plan']


def test_cancel_and_expired_deadline_commit_stop_before_query():
    import time
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    from benchmarks.attention_harness.core.control import EmergencyInterrupt
    robot={'model':'OpenArm','execution_arm':'left'};calls=[]
    def transport(method,path,body,timeout):
        calls.append(path)
        if path=='/health':return {'ready':True,'robot':robot}
        if path=='/stop':return {'robot':robot,'active_action':None,'epoch':8}
        pytest.fail('Cancelled query dispatched')
    sdk=OpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    sdk.set_interrupt_check(lambda:True)
    with pytest.raises(EmergencyInterrupt):sdk.find_objects()
    sdk.set_interrupt_check(lambda:False);sdk.set_episode_deadline(time.monotonic()-1)
    with pytest.raises(TimeoutError):sdk.find_objects()
    assert calls==['/health','/stop','/stop']


def test_openarm_camera_uses_attested_http_observation(monkeypatch):
    import io,json,numpy as np
    from benchmarks.attention_harness import ui_server
    robot={'model':'OpenArm','execution_arm':'right'}
    buffer=io.BytesIO();np.savez(buffer,rgb_u8=np.full((2,3,3),127,dtype=np.uint8),state_json=json.dumps({'robot':robot}))
    calls=[]
    def fetch(url,timeout):calls.append(url);return io.BytesIO(buffer.getvalue())
    monkeypatch.setattr(ui_server,'urlopen',fetch)
    station={'origin':'http://127.0.0.1:12345','robot':robot,'camera_transport':'openarm_http'}
    frame=ui_server.openarm_camera_frame(station)
    assert frame.startswith(b'\x89PNG') and calls==['http://127.0.0.1:12345/camera']
    with pytest.raises(ValueError,match='identity'):
        ui_server.openarm_camera_frame(station|{'robot':robot|{'execution_arm':'left'}})


def test_camera_packet_preserves_native_pixels_and_physics_stamp():
    import io,json,numpy as np
    from openarm_robocasa.camera import pack_camera
    raw=pack_camera(np.ones((2,3,3),dtype=np.float32)*.5,{'epoch':3,'total_steps':100,'robot':{'model':'OpenArm','execution_arm':'left'}})
    with np.load(io.BytesIO(raw),allow_pickle=False) as data:
        assert data['rgb_u8'].shape==(2,3,3) and data['rgb_u8'].dtype==np.uint8
        assert (data['rgb_u8']==127).all() and int(data['total_steps'])==100
        assert json.loads(str(data['state_json']))['epoch']==3


def test_recording_can_bound_openarm_render_load_without_changing_franka_default(tmp_path):
    from benchmarks.attention_harness.ui_media import AttemptCameraRecording
    ordinary=AttemptCameraRecording(tmp_path/'franka',lambda:b'',suite='robocasa')
    selected=AttemptCameraRecording(tmp_path/'openarm',lambda:b'',suite='robocasa',fps=1)
    assert ordinary.recorder.fps==5 and selected.recorder.fps==1
    assert selected.receipt['requested_fps']==1


def test_reset_reuses_native_scene_and_restores_only_robot_at_reset():
    import numpy as np
    from types import SimpleNamespace as S
    from openarm_robocasa.kitchen import OpenArmKitchen
    kitchen=OpenArmKitchen.__new__(OpenArmKitchen);calls=[]
    kitchen.seed=101;kitchen.parameters={'initial':{'left':[0]*7}}
    robot=S(dof=18,set_qpos=lambda q:calls.append(('qpos',q.copy())),set_qvel=lambda q:None)
    kitchen.rig=S(robot=robot,make_q=lambda initial:np.zeros(18),apply_drive=lambda:calls.append(('drive',None)))
    kitchen.env=S(reset=lambda **kw:calls.append(('native_reset',kw)));kitchen.steps=200
    kitchen.reset_same_scene()
    assert kitchen.steps==0
    assert ('native_reset',{'seed':101,'options':{'reconfigure':False}}) in calls
    assert len([row for row in calls if row[0]=='qpos'])==1


def _launch_fixture(tmp_path, monkeypatch):
    import hashlib, json
    from benchmarks.attention_harness import ui_launch
    from benchmarks.attention_harness.core.store import AttentionStore
    code = tmp_path / 'policy.py'; code.write_text('pass\n')
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'suite': 'robocasa', 'robot': {'model': 'OpenArm', 'execution_arm': 'left'}}))
    profile = {'id': 'openarm-left', 'suite': 'robocasa', 'task': 'counter_to_sink', 'seed': 101,
        'execution_target': 'robocasa_sim', 'code': str(code), 'config': str(config),
        'approved_policy_sha256': hashlib.sha256(code.read_bytes()).hexdigest(),
        'approved_config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
        'sim_source_root': str(tmp_path), 'agent_source_root': str(tmp_path), 'task_source_root': str(tmp_path),
        'sim_python': str(tmp_path), 'agent_python': str(tmp_path),
        'robot': {'model': 'OpenArm', 'execution_arm': 'left'}}
    selection = {'profile_id': profile['id'], 'seed': 101, 'attention_policy': 'reactive_help',
        'execution_target': 'robocasa_sim', 'assistance_mode': 'live_human_first',
        'max_attempts': 1, 'assistance_credits': 1, 'token_limit': 1000,
        'human_deadline_seconds': 30, 'overall_deadline_seconds': 300,
        'robot_model': 'OpenArm', 'execution_arm': 'left'}
    monkeypatch.setattr(ui_launch, 'inspect_formal_entry', lambda **_: ({'sha256': 'locked'}, None))
    calls = []
    from types import SimpleNamespace
    def popen(command, **kwargs): calls.append(command); return SimpleNamespace(pid=999999)
    return ui_launch, profile, selection, AttentionStore(tmp_path / 'attention.sqlite3'), calls, popen


def test_public_options_exposes_attested_robot_selection(tmp_path, monkeypatch):
    module, profile, *_ = _launch_fixture(tmp_path, monkeypatch)
    assert module.public_options([profile])['profiles'][0]['robot'] == profile['robot']


def test_launch_locks_robot_and_rejects_switch_before_side_effects(tmp_path, monkeypatch):
    module, profile, selection, store, calls, popen = _launch_fixture(tmp_path, monkeypatch)
    for changes in [{'robot_model': 'Franka'}, {'execution_arm': 'right'}, {'robot_model': 'Unknown'}]:
        with pytest.raises(ValueError, match='robot|arm'):
            module.launch_formal_run(selection | changes, profiles=[profile], store=store,
                                    artifact_root=tmp_path / 'bad', popen=popen)
    assert calls == [] and not store.events()
    launch = module.launch_formal_run(selection, profiles=[profile], store=store,
                                      artifact_root=tmp_path / 'good', popen=popen)
    assert launch['locked']['robot'] == profile['robot']
    assert launch['locked']['approved_config_sha256'] == profile['approved_config_sha256']
    assert len(calls) == 1


def test_launch_robot_identity_is_read_from_approved_config_not_profile_label(tmp_path, monkeypatch):
    module, profile, selection, store, calls, popen = _launch_fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='robot|arm'):
        module.launch_formal_run(selection | {'execution_arm': 'right'},
            profiles=[profile | {'robot': {'model': 'OpenArm', 'execution_arm': 'right'}}],
            store=store, artifact_root=tmp_path / 'bad', popen=popen)
    assert calls == []


def test_joint_planner_routes_around_obstacle_and_preserves_bounds():
    import numpy as np
    from openarm_robocasa.planning import joint_path
    def edge(a, b):
        points = a + np.linspace(0, 1, 80)[:, None] * (b - a)
        return not np.any((abs(points[:, 0]) < .2) & (abs(points[:, 1]) < .35))
    start = np.array([-.8, 0.]); goal = np.array([.8, 0.]); limits = np.array([[-1., 1.], [-1., 1.]])
    path = joint_path(start, goal, limits, edge, deadline_seconds=3.)
    assert np.allclose(path[0], start) and np.allclose(path[-1], goal)
    assert all(edge(a, b) for a, b in zip(path, path[1:]))
    assert all(np.all(point >= limits[:, 0]) and np.all(point <= limits[:, 1]) for point in path)
    assert len(path) >= 3


def test_single_openarm_service_cleanup_is_verified_without_inventing_an_agent():
    from benchmarks.attention_harness.service_recovery import service_stop_confirmed
    stop = {'mechanism': 'dedicated_openarm_kitchen_process_group', 'reason': 'normal_exit',
        'robot': {'model': 'OpenArm', 'execution_arm': 'left'},
        'services': {'openarm': {'leader_reaped': True, 'process_group_gone': True}}}
    assert service_stop_confirmed(stop, 'robocasa')
    assert not service_stop_confirmed(stop | {'robot': {'model': 'Franka', 'execution_arm': 'single'}}, 'robocasa')
    assert not service_stop_confirmed(stop | {'services': {'openarm': {'leader_reaped': True, 'process_group_gone': False}}}, 'robocasa')


def test_changed_robot_parameters_are_rejected_before_loading_physics(tmp_path):
    from openarm_robocasa.kitchen import load_parameters
    import hashlib
    path = tmp_path / 'parameters.json'; path.write_text('{}')
    path.with_suffix('.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    path.write_text('{"altered":true}')
    with pytest.raises(ValueError, match='pinned hash'): load_parameters(path)


def test_live_collision_policy_allows_only_selected_fingers_and_exact_target():
    import numpy as np
    from types import SimpleNamespace as S
    from openarm_robocasa.contact_policy import unexpected_contacts
    def contact(name, other_id):
        entities = [S(name=name, per_scene_id=1), S(name='object', per_scene_id=other_id)]
        return S(bodies=[S(entity=e) for e in entities], points=[S(position=np.zeros(3), separation=-.001, impulse=np.ones(3))])
    contacts = [contact('openarm_left_left_finger', 99), contact('openarm_right_left_finger', 99),
                contact('openarm_left_right_finger', 100), contact('openarm_left_link6', 99)]
    rows = unexpected_contacts(S(get_contacts=lambda: contacts), side='left', target_id=99)
    assert [row['bodies'][0] for row in rows] == ['openarm_right_left_finger', 'openarm_left_right_finger', 'openarm_left_link6']


def test_run_identity_persists_to_ui_without_rewriting_legacy_history(tmp_path):
    from dataclasses import replace
    from benchmarks.attention_harness.tests.test_attention_store import records
    from benchmarks.attention_harness.core.store import AttentionStore
    from benchmarks.attention_harness.ui_server import dashboard_snapshot
    run, *_ = records(); store = AttentionStore(tmp_path / 'attention.sqlite3')
    identity = {'robot': {'model': 'OpenArm', 'execution_arm': 'right'},
                'model_sha256': 'a' * 64, 'config_sha256': 'b' * 64}
    store.create_run(replace(run, suite='robocasa', task_id='counter_to_sink', robot_configuration=identity))
    actual = dashboard_snapshot(AttentionStore(store.path), run.run_id)
    assert actual['run']['robot'] == identity['robot']
    assert actual['run']['robot_configuration'] == identity
    from benchmarks.attention_harness.core.models import RunStatus
    store.transition_run(run.run_id,RunStatus.RUNNING,event_key='robot-start')
    store.transition_run(run.run_id,RunStatus.FAILED,event_key='robot-finish')
    assert dashboard_snapshot(AttentionStore(store.path),run.run_id)['run']['robot_configuration']==identity
    legacy = replace(run, run_id='run:legacy', suite='robocasa', task_id='counter_to_sink')
    store.create_run(legacy)
    assert dashboard_snapshot(store, legacy.run_id)['run']['robot'] is None


def _openarm_request(tmp_path):
    import json, hashlib
    from pathlib import Path
    from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
    root=Path(__file__).resolve().parents[3]
    old=json.loads((root/'benchmarks/attention_harness/protocol/v2/review_packages/seven_primary_guidance_v1_1_dev_2026-10-01/configs/robocasa_counter_to_sink_seed101.json').read_text())
    params=root/'openarm_robocasa/configs/robot_parameters_v2.json'
    value={k:old[k] for k in ['suite','task_id','seed','perception_mode','scene_id','object_set_id','camera_names','task_prompt','sim_runtime','safety_limits']}
    from benchmarks.attention_harness.robocasa_native.openarm_config import _REQUIRED_SOURCE
    value.update(schema_version='attentionbench.robocasa-openarm-formal-config.v1',
       robot={'model':'OpenArm','execution_arm':'left'},model_sha256='ae5f37c7f0c509df81d01229c13f0f624d619e253542cedfb0008aaa6d827456',
       robot_parameters={'path':str(params),'sha256':hashlib.sha256(params.read_bytes()).hexdigest()},
       task_source=old['task_source']|{'root':'/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks'},
       service_files={f:hashlib.sha256((root/f).read_bytes()).hexdigest() for f in _REQUIRED_SOURCE})
    value['camera_names']=['base_camera']
    code=tmp_path/'policy.py';code.write_text('def policy(robot_sdk, context):\n    return {}\n')
    cfg=tmp_path/'config.json';cfg.write_text(json.dumps(value))
    request=FormalRunRequest(suite='robocasa',task_id='counter_to_sink',seed=101,policy_code_path=code,
        policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),config_path=cfg,
        config_sha256=hashlib.sha256(cfg.read_bytes()).hexdigest(),artifact_root=tmp_path/'out',overall_deadline_seconds=300)
    return request,value


def test_approved_source_lock_covers_native_coupling_sdk_and_runner():
    from benchmarks.attention_harness.robocasa_native.openarm_config import _REQUIRED_SOURCE
    assert {'openarm_robocasa/mimic.py','openarm_robocasa/contact_controller.py','openarm_robocasa/variation.py',
        'benchmarks/attention_harness/robocasa_native/openarm_backend.py',
        'benchmarks/attention_harness/robocasa_native/openarm_runner.py'} <= _REQUIRED_SOURCE


def test_live_catalog_has_independent_openarm_sides_and_original_franka_hash():
    from pathlib import Path
    from benchmarks.attention_harness.ui_launch import load_catalog
    root=Path(__file__).resolve().parents[3]
    profiles=load_catalog(root/'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json')
    sink=[p for p in profiles if p['suite']=='robocasa' and p['task']=='counter_to_sink']
    assert {tuple(sorted(p['robot'].items())) for p in sink} >= {
       (('execution_arm','single'),('model','Franka')),
       (('execution_arm','left'),('model','OpenArm')),
       (('execution_arm','right'),('model','OpenArm'))}
    original=next(p for p in sink if p['robot']['model']=='Franka')
    assert original['approved_config_sha256']=='42eabef568bd5b16f62fe17f3a78b6e56037cb7204d50f5062e98fe9b08a28de'


def test_openarm_independent_config_accepts_exact_source_and_rejects_mutations(tmp_path):
    import json,hashlib
    from dataclasses import replace
    from benchmarks.attention_harness.robocasa_native.formal_runner import _config
    request,value=_openarm_request(tmp_path)
    assert _config(request)['robot']==value['robot']
    for change in [ {'robot':{'model':'Franka','execution_arm':'single'}},
          {'safety_limits':value['safety_limits']|{'max_delta_m':.5}},
          {'robot_parameters':value['robot_parameters']|{'sha256':'0'*64}},
          {'service_files':value['service_files']|{'openarm_robocasa/service.py':'0'*64}},
          {'model_sha256':'0'*64}, {'seed':102}]:
        request.config_path.write_text(json.dumps(value|change))
        altered=replace(request,config_sha256=hashlib.sha256(request.config_path.read_bytes()).hexdigest())
        with pytest.raises(ValueError):_config(altered)


def test_formal_runner_selects_openarm_without_constructing_franka_services(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness.robocasa_native import formal_runner,openarm_runner
    request,value=_openarm_request(tmp_path);seen=[]
    class Runner:
        def __init__(self,**kwargs):seen.append(kwargs)
        def execute(self,req):assert req==request;return {'routed':'OpenArm'}
    monkeypatch.setattr(openarm_runner,'OpenArmFormalSuiteRunner',Runner)
    monkeypatch.setattr(formal_runner,'DedicatedRobocasaServices',lambda **_:pytest.fail('Franka services constructed'))
    runner=formal_runner.RobocasaFormalSuiteRunner(sim_source_root=tmp_path,agent_source_root=tmp_path,
        task_source_root=tmp_path,sim_python=tmp_path/'python',agent_python=tmp_path/'python')
    assert runner.execute(request)=={'routed':'OpenArm'} and len(seen)==1


def test_contact_grasp_sdk_uses_explicit_contact_mode_not_free_space_zero_goal():
    from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
    identity={'model':'OpenArm','execution_arm':'left'};calls=[]
    def transport(method,path,body,timeout):
        calls.append((path,body))
        if path=='/health':return {'ready':True,'robot':identity,'capabilities':['gripper_contact_hold']}
        if path=='/state':return {'robot':identity,'epoch':2,'left_gripper':{'limits_m':[0,.044]}}
        if path=='/grasp':return {'id':'2:3'}
        if path=='/actions/2%3A3':return {'id':'2:3','status':'completed','grasp_mode':'contact_hold','contact_hold_committed':True}
        raise AssertionError(path)
    sdk=OpenArmActionBackend('http://127.0.0.1:8872',transport=transport)
    result=sdk.set_gripper(1.,settle_steps=80)
    assert result['contact_hold_committed'] and ('/grasp',{'epoch':2}) in calls
    assert not any(path=='/actions' for path,body in calls)


def test_bilateral_grasp_requires_both_selected_fingers_on_exact_target():
    import numpy as np
    from types import SimpleNamespace as S
    from openarm_robocasa.contact_policy import bilateral_target_contacts
    def contact(finger,other=99):
        return S(bodies=[S(entity=S(name='openarm_left_'+finger+'_finger',per_scene_id=1)),
                        S(entity=S(name='object',per_scene_id=other))],
                 points=[S(separation=-.0001,impulse=np.ones(3),position=np.zeros(3))])
    one=contact('left');wrong=contact('right',100);right=contact('right')
    assert not bilateral_target_contacts(S(get_contacts=lambda:[one,wrong]),side='left',target_id=99)
    assert bilateral_target_contacts(S(get_contacts=lambda:[one,right]),side='left',target_id=99)


def test_openarm_public_camera_handoff_is_run_bound_and_does_not_accept_other_robots(tmp_path):
    from benchmarks.attention_harness.public_station import publish_public_station, read_public_station
    run_dir=tmp_path/'attention-robocasa-counter_to_sink-seed101-new';run_id='run:'+run_dir.name
    attempt='attempt:'+run_dir.name+':0';identity={'model':'OpenArm','execution_arm':'left'}
    publish_public_station(run_dir=run_dir,run_id=run_id,attempt_id=attempt,suite='robocasa',
       origin='http://127.0.0.1:8872',device_id='openarm_base_camera',camera_transport='openarm_http',robot=identity)
    station=read_public_station(tmp_path,run_id,'robocasa',{attempt})
    assert station['robot']==identity and station['camera_transport']=='openarm_http'
    assert read_public_station(tmp_path,run_id,'robocasa',{'attempt:another:0'}) is None
    with pytest.raises(ValueError):
        publish_public_station(run_dir=run_dir,run_id=run_id,attempt_id=attempt,suite='robocasa',
           origin='http://127.0.0.1:8872',device_id='openarm_base_camera',camera_transport='openarm_http',
           robot={'model':'Franka','execution_arm':'single'})


def test_contact_controller_keeps_native_follower_without_position_servo():
    import xml.etree.ElementTree as ET
    from types import SimpleNamespace as S
    from openarm_robocasa.contact_controller import apply_contact_controller
    calls={}
    class Joint:
        def __init__(self,name):self.name=name
        def set_drive_properties(self,*args,**kwargs):calls[self.name]=(args,kwargs)
    names=['openarm_left_finger_joint1','openarm_left_finger_joint2']
    xml={name:ET.fromstring('<joint><limit effort="333"/></joint>') for name in names}
    rig=S(joints=[Joint(n) for n in names],idx={n:i for i,n in enumerate(names)},jxml=xml)
    receipt=apply_contact_controller(rig,'left',stiffness=2000,driver_damping=50,follower_damping=150,mode='force')
    assert calls[names[0]]==((2000,50,333.),{'mode':'force'})
    assert calls[names[1]]==((0,150,333.),{'mode':'force'})
    assert receipt['follower_position_stiffness']==0 and receipt['coupling']=='native_fixed_tendon'


def test_damped_native_mimic_keeps_accepted_coefficients_and_stiffness():
    from types import SimpleNamespace as S
    from openarm_robocasa.mimic import create_native_mimics
    root=object();follower=object();driver=object();calls=[]
    joints={'follow':S(parent_link=root,child_link=follower),'driver':S(parent_link=root,child_link=driver)}
    robot=S(find_joint_by_name=joints.get,create_fixed_tendon=lambda *args,**kw:calls.append((args,kw)))
    create_native_mimics(robot,[S(joint='follow',mimic='driver',multiplier=1.,offset=0.)],damping=100.)
    assert calls==[(([root,follower,driver],[0,-1.,1],[0,-1.,1]),{'rest_length':0.,'stiffness':1e5,'damping':100.})]


def test_source_lock_includes_run_persistence_ui_handoff_and_recording():
    from benchmarks.attention_harness.robocasa_native.openarm_config import _REQUIRED_SOURCE
    assert {
        'benchmarks/attention_harness/core/models.py',
        'benchmarks/attention_harness/core/store.py',
        'benchmarks/attention_harness/formal_attention_run.py',
        'benchmarks/attention_harness/ui_launch.py',
        'benchmarks/attention_harness/ui_media.py',
        'benchmarks/attention_harness/ui_server.py',
        'benchmarks/attention_harness/public_station.py',
        'benchmarks/attention_harness/service_recovery.py',
        'tidybot_sdk/facade.py',
    } <= _REQUIRED_SOURCE


def test_sink_policy_starts_with_reachable_task_approach_not_high_singular_pose(monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace as S
    initial=[3.81386,-1.8014,.97]
    first=[]
    class ReachedFirstMove(Exception):pass
    def plan(x,y,z):
        first.append([x,y,z]);raise ReachedFirstMove()
    sdk=S(sensors=S(find_objects=lambda _: [{'position':initial}]),
          arm=S(move_to_position=lambda x,y,z,**_:plan(x,y,z),
                plan_to_position=lambda *_:pytest.fail('Duplicate policy-level planning query')),
          gripper=S(open=lambda **_:None))
    monkeypatch.setitem(sys.modules,'robot_sdk',sdk)
    code=(Path(__file__).resolve().parents[3]/'openarm_robocasa/policies/counter_to_sink.py').read_text()
    with pytest.raises(ReachedFirstMove):
        exec(compile(code,'approved_openarm_policy','exec'),{'context':{'task_id':'counter_to_sink','robot':{'model':'OpenArm'}}})
    assert first==[[initial[0],initial[1],initial[2]+.15]]


def test_openarm_guidance_rejects_unsupported_controls_before_any_robot_action(monkeypatch):
    import sys
    from pathlib import Path
    from types import SimpleNamespace as S
    calls=[]
    sdk=S(sensors=S(find_objects=lambda _:calls.append('perception') or [{'position':[3.8,-1.8,.97]}]),
          arm=S(move_to_position=lambda *a,**k:calls.append('motion')),
          gripper=S(open=lambda **k:calls.append('gripper'),close=lambda **k:None))
    monkeypatch.setitem(sys.modules,'robot_sdk',sdk)
    code=(Path(__file__).resolve().parents[3]/'openarm_robocasa/policies/counter_to_sink.py').read_text()
    with pytest.raises(RuntimeError,match='OpenArm guidance does not support'):
        exec(compile(code,'approved_openarm_policy','exec'),{'context':{'task_id':'counter_to_sink',
            'robot':{'model':'OpenArm'},'attention_input':{'advisor_guidance':'AB_CONTROL_V1 base_forward_m=0.11;'}}})
    assert calls==[]


def test_openarm_grasp_hint_uses_own_geometry_bounds():
    import ast
    from pathlib import Path
    code=(Path(__file__).resolve().parents[3]/'openarm_robocasa/policies/counter_to_sink.py').read_text()
    tree=ast.parse(code);functions=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'locate','text_parameters'}],type_ignores=[])
    values={};exec(compile(functions,'OpenArm_text_parser','exec'),values)
    parse=values['text_parameters']
    assert parse('AB_CONTROL_V1 grasp_offset_m=0.005;','robocasa')=={'grasp_offset_m':.005}
    assert parse('AB_CONTROL_V1 grasp_offset_m=0.045;','robocasa')=={}
    with pytest.raises(RuntimeError,match='OpenArm guidance does not support'):
        parse('AB_CONTROL_V1 base_forward_m=99;','robocasa')


def test_openarm_preview_matches_policy_capabilities_without_claiming_adoption():
    from benchmarks.attention_harness.hint_compilation import prepare_human_guidance
    robot={'model':'OpenArm','execution_arm':'left'}
    value=prepare_human_guidance('AB_CONTROL_V1 grasp_offset_m=0.005;',suite='robocasa',task_id='counter_to_sink',robot=robot)
    assert value['status']=='supported' and value['parameters']=={'grasp_offset_m':.005}
    assert 'adopted' not in value
    for text in ['AB_CONTROL_V1 base_forward_m=0.11;','AB_CONTROL_V1 close_settle_steps=30;']:
        value=prepare_human_guidance(text,suite='robocasa',task_id='counter_to_sink',robot=robot)
        assert value['status']=='unsupported' and value['parameters']=={} and value['compiled']==text
    assert prepare_human_guidance('AB_CONTROL_V1 grasp_offset_m=0.045;',suite='robocasa',task_id='counter_to_sink',robot=robot)['status']=='unrecognized'


def test_openarm_http_hint_preview_uses_locked_robot_and_does_not_write(tmp_path):
    import json,re,sqlite3,threading,time
    from dataclasses import replace
    from urllib.request import Request,urlopen
    from benchmarks.attention_harness.core.runtime import AttentionRuntime
    from benchmarks.attention_harness.core.store import AttentionStore
    from benchmarks.attention_harness.attention_modes import AssistanceMode
    from benchmarks.attention_harness.tests.test_attention_store import records
    from benchmarks.attention_harness.ui_server import create_server
    store=AttentionStore(tmp_path/'preview.sqlite3');run,attempt,trace,hint=records()
    store.create_run(replace(run,suite='robocasa',task_id='counter_to_sink',assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,
        robot_configuration={'robot':{'model':'OpenArm','execution_arm':'right'}}))
    store.create_attempt(attempt);store.put_trace(trace)
    hint=replace(hint,mode=AssistanceMode.LIVE_HUMAN_FIRST,deadline_at=time.time()+60)
    AttentionRuntime(store,None,clock=time.time).open_request(hint)
    server=create_server(store=store,port=0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    def dump():
        with sqlite3.connect(store.path) as db:return '\n'.join(db.iterdump())
    try:
        with urlopen(base+'/ui/') as response:token=re.search(r'data-control-token="([^"]+)"',response.read().decode()).group(1)
        before=dump()
        for text,status,parameters in [('AB_CONTROL_V1 grasp_offset_m=0.005;','supported',{'grasp_offset_m':.005}),
                ('AB_CONTROL_V1 base_forward_m=0.11;','unsupported',{})]:
            with urlopen(Request(base+'/api/requests/'+hint.request_id+'/guidance-preview',method='POST',
                    data=json.dumps({'content':text}).encode(),headers={'Content-Type':'application/json','X-Attention-Token':token})) as response:
                value=json.load(response)
            assert value['status']==status and value['parameters']==parameters
            assert dump()==before
        assert store.get_request(hint.request_id).state.value=='pending'
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


def test_source_lock_includes_robot_specific_guidance_compiler():
    from benchmarks.attention_harness.robocasa_native.openarm_config import _REQUIRED_SOURCE
    assert 'benchmarks/attention_harness/hint_compilation.py' in _REQUIRED_SOURCE


def test_actual_openarm_launch_policy_passes_unchanged_sdk_sandbox():
    from pathlib import Path
    from benchmarks.attention_harness.robocasa_native.policy_sandbox import validate_generated_policy
    code = (Path(__file__).resolve().parents[3] / 'openarm_robocasa/policies/counter_to_sink.py').read_text()
    validate_generated_policy(code)
