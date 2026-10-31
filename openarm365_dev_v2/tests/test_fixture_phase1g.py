"""Offline R5 partial opening through executor, policy and transport boundaries."""
import ast
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import time
import traceback

import numpy as np
import pytest
from curobo_v2 import fixture_interaction as fi,fixture_telemetry as ft
from curobo_v2.fixture_gripper import MIN_WIDTH_M,OPEN_WIDTH_M,GripperWidthRejected
from openarm365_dev import executor as em,planner as pm,policy_validation as pv
from openarm365_dev_v2 import agent_sdk,fixture_sdk,agent_policy_worker,worker
from openarm365_dev_v2.agent_policy import validate_source
from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
from openarm365_dev_v2.tests.test_fixture_phase1c import contact_action
from openarm365_dev_v2.tests.test_fixture_interaction import settings,fixture_types,r4_deployment,base,fake_mujoco_if_unavailable,GOLDEN


def width_executor(monkeypatch,fixture_types,settings,*,offset=0.,agent=False):
    e,rows,invalid,_=contact_action(monkeypatch,fixture_types,settings,[0,0,0],contact=False,agent=agent)
    e.env.sim.data.contact=[]
    e.grips={'left':0.,'right':0.}
    e.env.sim.data.qpos=np.r_[np.zeros(3),[.022]*4]
    e.env.sim.data.qvel=np.zeros(7)
    e.env.robots[0]._ref_gripper_joint_pos_indexes={'right':[3,4],'left':[5,6]}
    e.env.sim.model.get_joint_qvel_addr=lambda name:{'g1':3,'g2':4,'g3':5,'g4':6}[name]
    e.planner.lock_addresses={'openarm_left_finger_joint1':3,'openarm_right_finger_joint1':5}
    e.planner.env=e.env;e.planner.execution_settings=settings
    e.planner.addresses=[];e.planner.names=['joint'];requests=[]
    def step():
        e.sequence+=1
        for side,index in [('left',3),('right',5)]:
            e.env.sim.data.qpos[index:index+2]=(.044*(1-e.grips[side])/2)+offset
        e.monitor()
    e.control_step=step
    def plan(*a,**k):
        requests.append(pm.MujocoV2Planner.body(e.planner,[]));return {'plan_id':'p','trajectory':[[0.],[.02]]}
    e.planner.plan_pose=plan
    e.planner.state=lambda:e.goals.copy()
    return e,rows,invalid,requests


@pytest.mark.parametrize('side',['left','right'])
@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('width',[MIN_WIDTH_M,.006,OPEN_WIDTH_M])
def test_reaches_holds_no_grasp_next_plan_measured_locks(monkeypatch,fixture_types,settings,side,agent,width):
    e,rows,invalid,requests=width_executor(monkeypatch,fixture_types,settings,offset=.0002,agent=agent)
    e.grasp_counts[side]={7:20}
    e.any_object_contact=lambda:pytest.fail('move_to must not enter empty close')
    result=e.gripper_move_to(side,width,request_id='width')
    measured=[width+.0002]*2
    assert result['command']=='move_to' and result['target_width_m']==width
    assert result['measured_finger_positions_m']==pytest.approx(measured)
    assert result['settle_steps']==20 and result['sequence']==20
    assert 'empty_close' not in result and e.held is None
    assert e.grips[side]==pytest.approx(1-2*width/.044)
    assert not e.planner.prepared and invalid==['gripper_width_changed']
    e.any_object_contact=lambda:False
    e.move_delta(side,.01,0,0,request_id='arm')
    assert requests[-1]['locked_joints'][f'openarm_{side}_finger_joint1']==pytest.approx(measured[0])
    assert e.grips[side]==pytest.approx(1-2*width/.044) and e.held is None and not e.failed
    receipt=next(r for _,r in rows if r.get('command')=='move_to' and r['event']=='receipt')
    assert receipt['settle_steps']==20 and receipt['measured_finger_positions_m']==pytest.approx(measured)


@pytest.mark.parametrize('width',[-1.,0.,.003,.004,MIN_WIDTH_M-1e-9,.0220001,float('nan'),float('inf'),10**400,None,'0.006',True,[],{}])
def test_invalid_recoverable_without_execution(monkeypatch,fixture_types,settings,width):
    e,rows,invalid,_=width_executor(monkeypatch,fixture_types,settings)
    before=e.grips.copy()
    with pytest.raises(GripperWidthRejected,match='gripper_width_out_of_range') as caught:
        e.gripper_move_to('left',width,request_id='bad')
    assert e.sequence==0 and not e.failed and e.grips==before and not invalid
    assert caught.value.public()['rejected']
    json.dumps(rows,allow_nan=False)
    assert rows[-2][1]['command']=='move_to'


@pytest.mark.parametrize('holder',['left','right'])
def test_holding_side_refused_other_side_allowed(monkeypatch,fixture_types,settings,holder):
    e,rows,invalid,_=width_executor(monkeypatch,fixture_types,settings)
    held=e.held={'holder':holder,'target_id':7}
    with pytest.raises(GripperWidthRejected,match='gripper_width_while_holding'):
        e.gripper_move_to(holder,.006,request_id='holding')
    assert e.sequence==0 and not e.failed and e.held is held
    other='right' if holder=='left' else 'left'
    e.control_step=lambda:setattr(e,'sequence',e.sequence+1)
    with pytest.raises(GripperWidthRejected,match='gripper_width_not_reached'):
        e.gripper_move_to(other,.006,request_id='other')
    assert e.sequence==20 and e.held is held and not e.failed


@pytest.mark.parametrize('offset',[.000999,.001001])
def test_measured_tolerance_recoverable_after_settle(monkeypatch,fixture_types,settings,offset):
    e,rows,invalid,_=width_executor(monkeypatch,fixture_types,settings,offset=offset)
    if offset<.001:e.gripper_move_to('left',.006,request_id='near')
    else:
        with pytest.raises(GripperWidthRejected,match='gripper_width_not_reached') as exc:
            e.gripper_move_to('left',.006,request_id='far')
        assert exc.value.diagnostics['settle_steps']==20
    assert not e.failed and e.sequence==20 and e.grips['left']==pytest.approx(1-2*.006/.044)
    assert invalid==['gripper_width_changed'] and e.active_request is None and not e.gripping


@pytest.mark.parametrize('guard',['mimic','limit','collision'])
def test_existing_guards_still_fatal(monkeypatch,fixture_types,settings,guard):
    e,_,invalid,_=width_executor(monkeypatch,fixture_types,settings)
    def step():
        e.sequence+=1
        if guard=='mimic':e.env.sim.data.qpos[3:5]=[.006,.008]
        elif guard=='limit':e.env.sim.data.qpos[3:5]=[.045,.045]
        else:raise RuntimeError('Unexpected physical robot/world contact')
        e.monitor()
    e.control_step=step
    with pytest.raises(RuntimeError):e.gripper_move_to('left',.006,request_id='guard')
    assert e.failed and invalid[-1]=='gripper_failure'


@pytest.mark.parametrize('case',list(json.loads(GOLDEN.read_text())['cases']))
def test_non_r5_sdk_validation_allowlists_golden(case):
    from openarm365_dev import policy_worker
    c=json.loads(GOLDEN.read_text())['cases'][case]['config']
    expected=json.loads(GOLDEN.read_text())['operations']
    assert {k:sorted(v) for k,v in pv.operations(c).items()}==expected
    for builder in (agent_sdk.build_sdk,fixture_sdk.build_sdk):
        sdk=builder(NS(),lambda r:None,config=c)
        for side in ('left','right'):assert not hasattr(getattr(sdk,side+'_gripper'),'move_to')
    for ops in (pv.OPERATIONS,policy_worker.OPERATIONS,agent_policy_worker.OPERATIONS):
        assert {k:sorted(v) for k,v in ops.items()}==expected
    for side in ('left','right'):
        code=f'from robot_sdk import {side}_gripper\n{side}_gripper.move_to(0.004)'
        for validator in (validate_source,pv.validate_generated_policy):
            with pytest.raises(Exception,match='forbidden method'):validator(code,config=c)


@pytest.mark.parametrize('builder',[agent_sdk.build_sdk,fixture_sdk.build_sdk])
@pytest.mark.parametrize('side',['left','right'])
def test_r5_sdk_source_validation_and_nan_wire(builder,side):
    c={'execution_settings':{'profile':fi.PROFILE}};calls=[];events=[]
    def rpc(*a):calls.append(a);json.dumps(a,allow_nan=False);return {'ok':True}
    sdk=builder(NS(rpc=rpc),events.append,config=c)
    for validator in (validate_source,pv.validate_generated_policy):
        validator(f'from robot_sdk import {side}_gripper\n{side}_gripper.move_to(0.004)',config=c)
    getattr(sdk,side+'_gripper').move_to(.006)
    getattr(sdk,side+'_gripper').move_to(float('nan'))
    assert calls[0]==(side+'_gripper','move_to',(.006,),{}) and calls[1][2]==('nan',)
    json.dumps(events,allow_nan=False)


@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('route',['left_gripper','owner'])
@pytest.mark.parametrize('width,offset,reason',[(.006,0.,None),(.004,0.,'gripper_width_out_of_range'),('nan',0.,'gripper_width_out_of_range'),(.006,.002,'gripper_width_not_reached')])
@pytest.mark.parametrize('collaboration',['none','c1'])
def test_actual_worker_rpc_rejection_receipt_and_replay(monkeypatch,tmp_path,fixture_types,settings,agent,route,width,offset,reason,collaboration):
    e,rows,invalid,_=width_executor(monkeypatch,fixture_types,settings,offset=offset,agent=agent)
    e.bimanual_collaboration=collaboration
    if collaboration=='c1':
        e.held={'left':None,'right':None};e.held_transform={'left':None,'right':None}
        e.held_reference={side:dict(mode='fixed') for side in ('left','right')}
    from openarm365_dev_v2 import agent_probe
    monkeypatch.setattr(agent_probe,'state_fingerprint',lambda *a:None)
    main=next(n for n in ast.parse(Path(worker.__file__).read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
    loop=next(n for n in ast.walk(main) if isinstance(n,ast.For) and ast.unparse(n.iter)=='sys.stdin')
    request=dict(request_id='width',group=route,operation='gripper_move_to' if route=='owner' else 'move_to',
        args=['left',width] if route=='owner' else [width],kwargs={})
    sent=[]
    scope=dict(__name__=worker.__name__,__package__=worker.__package__,c=dict(bimanual_collaboration=collaboration),json=json,os=os,time=time,traceback=traceback,
        sys=NS(stdin=[json.dumps(request)]*2),stop=NS(is_set=lambda:False),
        args=NS(agent_mode=agent,deadline=e.deadline,evidence=tmp_path),receipt_cache={},executor=e,env=e.env,
        execution_settings=settings,fixture_enabled=fi.enabled,allowed_operations={'left_gripper':['move_to']},
        send=sent.append,override=None,tier=None,receipt_fields=lambda *a:{},RecoverablePlanRejection=RecoverablePlanRejection,
        FixtureContactBaseDisplacementRejected=ft.FixtureContactBaseDisplacementRejected)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[loop],type_ignores=[])),str(worker.__file__),'exec'),scope)
    assert len(sent)==2 and sent[0]==sent[1] and sent[0]['ok'] and not e.failed
    if collaboration=='c1':
        assert sent[0]['bimanual_collaboration']=='c1'
        assert sent[0]['held_by_arm']=={'left':None,'right':None}
    value=sent[0]['value'];details=value['diagnostics'] if reason else value
    assert details['command']=='move_to' and details['target_width_m']==(repr(width) if isinstance(width,str) else width)
    assert details['settle_steps']==(0 if reason=='gripper_width_out_of_range' else 20)
    if reason:assert value['reason']==reason and value['rejected']
    assert len((tmp_path/'rpc-receipts.jsonl').read_text().splitlines())==1


@pytest.mark.parametrize('profile',[None,'r3-20261025','r4-20261027',fi.PROFILE])
def test_owner_gates_and_routes(monkeypatch,tmp_path,profile):
    from openarm365_dev.owner import NativeOwner
    from openarm365_dev_v2.owner import NativeOwnerV2
    owner=NativeOwnerV2.__new__(NativeOwnerV2);owner.config=tmp_path/'config.json'
    owner.config.write_text(json.dumps({'execution_settings':{'profile':profile}}))
    calls=[]
    def inherited(self,group,op,args=(),kwargs=None,**kw):
        if group!='owner' and op not in pv.OPERATIONS[group]:raise ValueError('Unauthorized public SDK operation')
        calls.append((group,op,args,kwargs));return 'receipt'
    monkeypatch.setattr(NativeOwner,'rpc',inherited)
    if profile==fi.PROFILE:
        assert owner.rpc('left_gripper','move_to',(.006,))=='receipt'
        assert calls==[('owner','gripper_move_to',('left',.006),{})]
    else:
        with pytest.raises(ValueError,match='Unauthorized'):owner.rpc('left_gripper','move_to',(.006,))


def test_actual_agent_policy_transport(tmp_path):
    from openarm365_dev_v2.agent_sandbox import execute_agent_policy
    c={'execution_settings':{'profile':fi.PROFILE}};calls=[]
    sdk=NS(left_gripper=NS(move_to=lambda width:calls.append(width) or {'ok':True}))
    code='from robot_sdk import left_gripper\nleft_gripper.move_to(0.004)\nleft_gripper.move_to(float("nan"))'
    result=execute_agent_policy(code=code,sdk=sdk,context={},deadline=time.monotonic()+10,
        stderr_path=tmp_path/'err.txt',source_config=c,unsafe_test_transport=True)
    assert result.status=='completed' and calls==[.004,'nan'] and result.call_count==2
