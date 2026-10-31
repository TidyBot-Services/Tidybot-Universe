"""Offline composition of fixture actions with main's C1 and policy-v2 paths."""
import pytest
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.dev_agent_success import load,DEV_RULE
from curobo_v2.fixture_gripper import GripperWidthRejected
from openarm365_dev_v2.tests.test_fixture_phase1g import (
    width_executor,fixture_types,settings,r4_deployment,base,fake_mujoco_if_unavailable)


def c1(executor):
    executor.bimanual_collaboration='c1'
    executor.held={'left':None,'right':None}
    executor.held_transform={'left':None,'right':None}
    executor.held_reference={side:dict(mode='fixed') for side in ('left','right')}


@pytest.mark.parametrize('side',['left','right'])
def test_c1_r5_empty_close_with_empty_slots(monkeypatch,fixture_types,settings,side):
    e,_,_,_=width_executor(monkeypatch,fixture_types,settings)
    c1(e)
    result=e.gripper(side,1.,request_id='close')
    assert result['empty_close'] is True
    assert result['held_by_arm']==e.held=={'left':None,'right':None}
    assert result['held_reference']=={'mode':'fixed'}
    assert e.gripping_side is None and not e.failed


@pytest.mark.parametrize('side',['left','right'])
def test_c1_r5_width_holds_opposite_slot_and_rejects_own(monkeypatch,fixture_types,settings,side):
    e,_,_,_=width_executor(monkeypatch,fixture_types,settings)
    c1(e)
    held=dict(holder=side,target_id=7);e.held[side]=held
    with pytest.raises(GripperWidthRejected,match='gripper_width_while_holding'):
        e.gripper_move_to(side,.006,request_id='own')
    assert e.sequence==0 and not e.failed
    other='right' if side=='left' else 'left'
    # Isolate routing/state retention; existing monitor tests cover held transforms.
    def settle():
        assert e.gripping_side==other
        e.sequence+=1
        index=e.env.robots[0]._ref_gripper_joint_pos_indexes[{'left':'right','right':'left'}[other]]
        e.env.sim.data.qpos[index]=.006
    e.control_step=settle
    result=e.gripper_move_to(other,.006,request_id='other')
    assert result['held_by_arm'][side]==held and e.held[side] is held
    assert e.held[other] is None and e.gripping_side is None
    assert result['settle_steps']==20 and not e.failed


@pytest.mark.parametrize('with_rule',[False,True])
@pytest.mark.parametrize('policy_id',['trace_aware_hint_only','full_trace_aware_attention_planner'])
def test_r5_policy_v2_tools_and_success_rule(tmp_path,with_rule,policy_id):
    config=dict(budget_tier='dev',runner_boundary='dev_loop',sdk_call_limit=320,horizon=9600,attempt_wall_seconds=1920,agent_policy_version='agent-policy-v2',
        agent_policy_id=policy_id,execution_settings={'profile':'r5-fixture-20261031'})
    condition=load('pick_place_counter_to_sink',102) if with_rule else None
    ws=Workspace(tmp_path/'agent').prepare({'task_id':'fixture','task_prompt':'Move safely.'},config,condition)
    tools=(ws.ws/'docs/tools.md').read_text()
    assert 'find_fixtures: {}' in tools
    assert ('This hint_only run has no Memory input' in tools)==(policy_id=='trace_aware_hint_only')
    assert (DEV_RULE in tools)==with_rule
    assert 'version' in tools and 'summary' in tools
