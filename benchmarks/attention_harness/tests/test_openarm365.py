"""OpenArm365 robot integration contracts; physical runs are separate evidence."""
from benchmarks.attention_harness.workspace_paths import get
import json
from pathlib import Path
import numpy as np
import pytest
from openarm365.model import build_model, ROOT
from openarm365.config import make_config, validate_config


def test_config_accepts_independent_full_task_budget():
    c=make_config("pick_place_counter_to_sink")
    assert validate_config(c)==c
    assert c["robot"]=="OpenArm365" and c["execution_arm"]=="left"
    assert c["horizon"]>=1000 and c["sdk_call_limit"]>=500
    assert c["safety_limits"]["max_arm_delta_m"]==.05
    c["robot"]="PandaOmron"
    with pytest.raises(ValueError): validate_config(c)


def test_legacy_frozen_config_unchanged():
    from benchmarks.attention_harness.robocasa365.config import validate_config as validate
    path=get('sim.configs_dir') / 'pick_place_counter_to_sink_seed101.json'
    c=json.loads(path.read_text()); assert validate(c)==c
    c["horizon"]=2000
    with pytest.raises(ValueError): validate(c)


def test_model_is_openarm_with_original_mass_limits_and_collision():
    import xml.etree.ElementTree as ET
    build_model()
    arm=ET.parse(ROOT/"model/robot.xml").getroot()
    assert len(arm.findall(".//joint"))==14
    for side in ("right","left"):
        assert arm.find(f".//body[@name='openarm_{side}_link7']") is not None
        assert arm.find(f".//motor[@joint='openarm_{side}_joint4']").get("ctrlrange")=="-27.0 27.0"
    assert len(arm.findall(".//geom[@group='0']"))>=16
    grip=ET.parse(ROOT/"model/gripper.xml").getroot()
    assert len(grip.findall("worldbody/.//joint"))==2
    assert all(float(j.get("range").split()[1])==.044 for j in grip.findall("worldbody/.//joint"))


def test_arm_mapping_is_left_and_world_to_base_roundtrip():
    from openarm365.frames import world_delta_to_base, base_delta_to_world
    q=[0,0,2**-.5,2**-.5]
    local=world_delta_to_base([.1,0,0],q)
    assert np.allclose(local,[0,-.1,0])
    assert np.allclose(base_delta_to_world(local,q),[.1,0,0])


def test_planar_base_maps_velocity_to_velocity_actuators():
    import xml.etree.ElementTree as ET
    root=ET.parse(ROOT/'model/base.xml').getroot()
    acts=root.findall('actuator/velocity')
    assert len(acts)==3, 'official JOINT_VELOCITY returns actuator commands, never motor force'
    assert [float(a.get('ctrlrange').split()[1]) for a in acts]==[.15,.15,.3]
    assert all(a.get('forcerange')=='-600.0 600.0' for a in acts)


def test_gripper_servo_holds_and_closes_without_limit_oscillation():
    import mujoco
    model=mujoco.MjModel.from_xml_path(str(ROOT/'model/gripper.xml'))
    # Isolate servo stability from legitimate finger-to-finger closed contacts.
    model.geom_contype[:]=0;model.geom_conaffinity[:]=0
    data=mujoco.MjData(model)
    data.qpos[:]=.044;data.ctrl[:]=.044
    for _ in range(250):
        mujoco.mj_step(model,data)
        assert np.max(np.abs(data.qpos-.044))<.001, 'open position must remain stable without contacts'
    data.ctrl[:]=0
    samples=[]
    for _ in range(250):
        mujoco.mj_step(model,data);samples.append(data.qpos.copy())
    assert np.min(samples)>-.001 and np.max(samples)<.045
    assert np.max(np.abs(data.qpos))<.001


def test_neutral_pose_command_retains_desired_base_relative_target():
    from openarm365.controller import install_pose_hold
    class Controller:
        control_dim=6
        def __init__(self): self.mode='achieved';self.achieved=np.array([.2,0,1.1]);self.goal=None
        def set_goal_update_mode(self,mode): self.mode=mode
        def set_goal(self,action): self.goal=(self.achieved if self.mode=='achieved' else self.goal)+np.asarray(action[:3])
    c=Controller();install_pose_hold(c)
    c.set_goal([.01,0,0,0,0,0]);expected=c.goal.copy()
    c.achieved[2]-=.03
    c.set_goal(np.zeros(6));assert np.allclose(c.goal,expected)
    c.set_goal([.02,0,0,0,0,0]);assert np.allclose(c.goal,c.achieved+[.02,0,0])
    assert c.mode=='achieved'


def test_zero_token_openarm_ui_never_schedules_optional_model_diagnosis():
    from benchmarks.attention_harness.ui_launch_worker import diagnostic_enabled
    assert not diagnostic_enabled({"scheduler_config":{"run_budget":{"schema_version":"attentionbench.openarm365-run-budget.v1","token_limit":0}}})
    assert diagnostic_enabled({"scheduler_config":{"run_budget":{"schema_version":"attentionbench.robocasa365-run-budget.v1","token_limit":8000}}})
    assert diagnostic_enabled({})
