"""C21 public SDK forwarding with existing executor/planner fakes."""
import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import sys

import numpy as np
import pytest

from curobo_v2 import execution_settings as es
from curobo_v2.client import PlanningRejected
from openarm365_dev import executor as ex, planner as native
from openarm365_dev_v2.tests.test_mujoco_ik_mode import planner
from openarm365_dev_v2.tests.test_motion_tracking_diagnostics import fake_apply
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment


def option():return dict(object_name='obj',lift_position=[.1,.2,.4],place_position=[.5,.2,.4])


def test_planner_forwards_lookahead_with_world_axis(planner,monkeypatch,r3_deployment):
    planner.execution_settings=replace(es.R4_20261027,planner_deployment=r3_deployment.planner_deployment)
    planner.env=NS(obj_body_id={'obj':7})
    planner.check_goal_margin=lambda *a:{}
    monkeypatch.setattr(native,'receipt_fields',lambda settings:{})
    ctx=dict(holder='left',target_id=7)
    monkeypatch.setattr(sys.modules['openarm365_dev.attachment'],'planner_context',lambda env,sig,*a:ctx if sig else None)
    original=planner.client.call
    def call(path,body):
        value=original(path,body)
        if path.startswith('/plan'):value.update(trajectory=[[0.]*14],heading_used_rad=.1)
        return value
    planner.client.call=call
    result=planner.plan_pose([.1,.2,.3],np.eye(3),side='left',epoch=7,grasp_search=option())
    body=planner.client.calls[-1][1]
    assert body['attachment'] is None
    assert body['grasp_search']['attachment']==ctx
    assert body['grasp_search']['world_vertical']==[0,0,1]
    assert body['grasp_search']['lift_targets']['openarm_left_hand_tcp']['position']==[.1,.2,.4]
    assert 'grasp_search' not in planner.client.calls[-2][1]  # warmup selectors only
    assert planner._heading_world_target==7
    assert result['heading_used_rad']==.1


@pytest.mark.parametrize('held,grip,dz,contact',[(None,-1.,-.01,True),({'target_id':7},-1.,-.01,False),(None,1.,-.01,False),(None,-1.,.01,False)])
def test_search_never_bypasses_grasp_or_contact_gate(monkeypatch,held,grip,dz,contact):
    obj=NS(check_live=lambda:None,execution_settings=es.R4_20261027,
        held=held,grips={'left':grip},any_object_contact=lambda:contact,env=NS(obj_body_id={'obj':7}))
    with pytest.raises((ValueError,PlanningRejected)):
        ex.NativeExecutor.move_delta.__wrapped__(obj,'left',0,0,dz,request_id='test',grasp_search=option())


def test_apply_and_worker_keep_public_heading(monkeypatch):
    obj,_,events,_=fake_apply(monkeypatch,position=[.011,.02])
    original=obj.planner.claim
    obj.planner.claim=lambda *a:dict(original(*a),heading_used_rad=-.1,headings_tried_rad=[0,.1,-.1],heading_candidates=[])
    receipt=ex.NativeExecutor.apply(obj,{'plan_id':'test'},request_id='test')
    assert receipt['heading_used_rad']==-.1
    assert events[-1]['heading_used_rad']==-.1
    # Execute the real worker arm dispatch branch without importing a simulator.
    tree=ast.parse(Path('openarm365_dev_v2/worker.py').read_text())
    branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="group in {'left_arm', 'right_arm'}")
    scope=dict(group='left_arm',request={'args':[0,0,-.01],'kwargs':{}},rid='test',value=None,
               executor=NS(move_delta=lambda *a,**kw:receipt))
    exec(compile(ast.Module(body=branch.body,type_ignores=[]),'worker','exec'),scope)
    assert scope['value']['heading_used_rad']==-.1
    assert 'after_qpos' not in scope['value']


def test_sdk_prompt_profile_scoped():
    from benchmarks.attention_harness.dev_tasks import dev_sdk_prompt
    base=dict(sdk_call_limit=320,horizon=9600,attempt_wall_seconds=1920)
    legacy=dev_sdk_prompt(base)
    assert dev_sdk_prompt(dict(base,execution_settings={'profile':'r3-20261025'}))==legacy
    prompt=dev_sdk_prompt(dict(base,execution_settings={'profile':'r4-20261027'}))
    assert prompt.startswith(legacy) and 'grasp_search' in prompt and 'heading_used_rad' in prompt
