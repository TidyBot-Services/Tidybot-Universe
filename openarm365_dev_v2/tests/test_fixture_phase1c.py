"""Offline contact re-hold checks through action and RPC boundaries."""
import ast
from dataclasses import fields,replace
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace as NS
import time
import traceback

import numpy as np
import pytest
from curobo_v2 import execution_settings as es,fixture_interaction as fi,fixture_telemetry as ft
from openarm365_dev import executor as em
from openarm365_dev_v2.agent_executor import AgentExecutor
from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
from openarm365_dev_v2.tests.test_fixture_interaction import (
    monitor_fixture,settings,fixture_types,r4_deployment,base,fake_mujoco_if_unavailable)


def contact_action(monkeypatch,fixture_types,settings,displacement,*,contact=True,agent=False,peak=None):
    e,rows=monitor_fixture(monkeypatch,fixture_types,settings)
    if agent:e.__class__=AgentExecutor
    e.sequence=0;e.failed=False;e.epoch='test';e.moving=False
    e.deadline=time.monotonic()+60;e.cancel_check=lambda:False
    e.goals=np.zeros(1);e.grips={'left':-1.,'right':-1.}
    e.held_trace_enabled=False;e.held_reference={'mode':'fixed'}
    e.env.control_timestep=.01
    e.planner.state=lambda:e.goals.copy()
    e.planner.spec={'robot_cfg':{'kinematics':{'cspace':{'max_acceleration':1,'max_jerk':1}}}}
    e.planner.prepared={'stale'};invalid=[];plans=[]
    e.planner.ledger=NS(invalidate=invalid.append)
    e.planner.claim=lambda *args:{'plan_id':'p','time_from_start_seconds':[.02]}
    def plan(*args,**kwargs):
        plans.append((e.base_hold.copy(),e.env.sim.data.qpos[:3].copy()))
        return {'plan_id':'p'}
    e.planner.plan_joints=plan
    monkeypatch.setattr(em,'sample_for_original_drive',lambda *a,**k:(np.array([[0.],[.01],[.02]]),{}))
    monkeypatch.setattr(em,'receipt_fields',lambda *args:{})
    monkeypatch.setattr(em,'held_envelope_receipt',lambda *args:{})
    original_contacts=list(e.env.sim.data.contact)
    def step():
        e.sequence+=1
        e.env.sim.data.qpos[:3]=peak if peak is not None and e.sequence==1 else displacement
        e.env.sim.data.contact=original_contacts if contact and e.sequence==1 else []
        e.monitor()
    e.control_step=step
    return e,rows,invalid,plans


def run_action(e,action,request='contact'):
    if action=='arm':return e.move_joints([.02],side='left',request_id=request)
    return e.gripper('left',1. if action=='close' else -1.,request_id=request)


@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('action',['arm','open','close'])
def test_contact_11mm_reholds_once_and_following_noncontact_passes(monkeypatch,fixture_types,settings,action,agent):
    measured=np.array([.011,0,.002])
    e,rows,invalid,plans=contact_action(monkeypatch,fixture_types,settings,measured,agent=agent)
    result=run_action(e,action)
    np.testing.assert_array_equal(e.base_hold,measured)
    assert result['base_rehold']==dict(before=[0.,0.,0.],after=measured.tolist(),translation_m=.011,yaw_rad=.002)
    np.testing.assert_array_equal(result['base_drift']['reference_base_qpos'],measured)
    assert result['base_drift']['max_translation_m']==.011
    assert not e.failed and e.planner.prepared==set() and invalid==['fixture_contact_rehold']
    events=[r for _,r in rows if r.get('event')=='fixture_base_rehold']
    assert len(events)==1 and events[0]['request_id']=='contact'
    assert events[0]['base_rehold']==result['base_rehold']
    assert next(r for _,r in rows if r.get('event')=='receipt')['base_rehold']==result['base_rehold']
    ft.rehold(e)
    assert len([r for _,r in rows if r.get('event')=='fixture_base_rehold'])==1
    following=run_action(e,'arm','retreat')
    assert 'base_rehold' not in following and following['base_drift']['max_translation_m']==0
    assert following['fixture_pilot']['contact_count']==0 and not e.failed
    np.testing.assert_array_equal(plans[-1][0],measured)
    np.testing.assert_array_equal(plans[-1][1],measured)


@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('action',['arm','open','close'])
@pytest.mark.parametrize('displacement',[[.06,0,0],[0,0,.11],[.04,.04,0]])
def test_excessive_contact_displacement_rejects_without_rehold(monkeypatch,fixture_types,settings,action,agent,displacement):
    e,rows,invalid,_=contact_action(monkeypatch,fixture_types,settings,displacement,agent=agent)
    with pytest.raises(ft.FixtureContactBaseDisplacementRejected) as exc:run_action(e,action)
    assert exc.value.public()['reason']=='fixture_contact_base_displacement_excessive'
    assert exc.value.public()['rejected'] and not exc.value.public()['ok']
    assert not e.failed and e.epoch=='test' and e.sequence>0
    np.testing.assert_array_equal(e.base_hold,np.zeros(3))
    assert not e.planner.prepared and invalid==['fixture_contact_base_displacement_excessive']
    assert not e.moving and not e.gripping and e.active_request is None
    assert not any(r.get('event')=='fixture_base_rehold' or 'base_rehold' in r for _,r in rows)
    rejected=next(r for _,r in rows if r.get('event')=='receipt')
    assert rejected['status']=='rejected' and rejected['reason']==exc.value.reason


@pytest.mark.parametrize('action',['arm','open','close'])
@pytest.mark.parametrize('peak',[[.06,0,0],[0,0,.11],[.04,.04,0]])
def test_peak_shove_rejects_even_after_base_returns(monkeypatch,fixture_types,settings,action,peak):
    e,_,_,_=contact_action(monkeypatch,fixture_types,settings,[.001,0,0],peak=peak)
    with pytest.raises(ft.FixtureContactBaseDisplacementRejected) as exc:run_action(e,action)
    assert exc.value.diagnostics['translation_m']==.001
    assert exc.value.diagnostics['maximum_translation_m']>.05 or exc.value.diagnostics['maximum_yaw_rad']>.10
    np.testing.assert_array_equal(e.base_hold,np.zeros(3))


@pytest.mark.parametrize('action',['arm','open','close'])
def test_no_contact_retains_fatal_3mm_guard(monkeypatch,fixture_types,settings,action):
    e,rows,_,_=contact_action(monkeypatch,fixture_types,settings,[.011,0,0],contact=False)
    with pytest.raises(RuntimeError,match='base drift'):run_action(e,action)
    assert e.failed and e.epoch!='test'
    np.testing.assert_array_equal(e.base_hold,np.zeros(3))
    assert not any(r.get('event')=='fixture_base_rehold' or 'base_rehold' in r for _,r in rows)


@pytest.mark.parametrize('displacement',[[.05,0,.10],[0,-.05,-.10],[0,0,0]])
def test_caps_are_inclusive(monkeypatch,fixture_types,settings,displacement):
    e,_,_,_=contact_action(monkeypatch,fixture_types,settings,displacement)
    result=run_action(e,'arm')
    assert 'base_rehold' in result and not e.failed


@pytest.mark.parametrize('profile',[None,'r3-20261025','r4-20261027'])
def test_other_profiles_ignore_stale_contact_stats(profile):
    e=NS(execution_settings=None if profile is None else NS(profile=profile),
        _fixture_motion={'guard_record_only_steps':10})
    ft.rehold(e)
    assert vars(e).keys()=={'execution_settings','_fixture_motion'} and ft.receipt(e)=={}


def test_caps_are_r5_only_frozen_hashed_settings(settings):
    new={'fixture_contact_base_rehold_max_translation_m':.05,'fixture_contact_base_rehold_max_yaw_rad':.10}
    assert all(not set(new)&{f.name for f in fields(s)} for s in (es.R3_20261025,es.R4_20261027))
    for name,value in new.items():
        block=settings.to_dict();assert block[name]==value
        block[name]=value*2
        with pytest.raises(ValueError,match=name):es.from_config(block)
    block=settings.to_dict();old={k:v for k,v in block.items() if k not in new}
    encoded=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    assert settings.sha256==hashlib.sha256(encoded(block)).hexdigest()
    assert settings.sha256!=hashlib.sha256(encoded(old)).hexdigest()


@pytest.mark.parametrize('agent',[False,True])
@pytest.mark.parametrize('action',['arm','close'])
@pytest.mark.parametrize('reject',[False,True])
@pytest.mark.parametrize('collaboration',['none','c1'])
def test_actual_worker_dispatch_receipt_and_cached_replay(monkeypatch,tmp_path,fixture_types,settings,agent,action,reject,collaboration):
    from openarm365_dev_v2 import worker
    from openarm365_dev_v2 import agent_probe
    displacement=[.06 if reject else .011,0,0]
    e,_,_,_=contact_action(monkeypatch,fixture_types,settings,displacement,agent=agent)
    e.bimanual_collaboration=collaboration
    if collaboration=='c1':
        e.held={'left':None,'right':None};e.held_transform={'left':None,'right':None}
        e.held_reference={side:dict(mode='fixed') for side in ('left','right')}
    e.planner.plan_pose=lambda *a,**k:{'trajectory':[[0.],[.02]],'plan_id':'p'}
    monkeypatch.setattr(agent_probe,'state_fingerprint',lambda env,executor:(executor.sequence,tuple(executor.base_hold)))
    main=next(n for n in ast.parse(Path(worker.__file__).read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
    loop=next(n for n in ast.walk(main) if isinstance(n,ast.For) and ast.unparse(n.iter)=='sys.stdin')
    group='right_arm' if action=='arm' else 'left_gripper'
    request=dict(request_id='rpc',group=group,operation='move_delta' if action=='arm' else 'close',
        args=[.01,0,0] if action=='arm' else [],kwargs={})
    sent=[]
    scope=dict(__name__=worker.__name__,__package__=worker.__package__,c=dict(bimanual_collaboration=collaboration),
        json=json,os=os,time=time,traceback=traceback,sys=NS(stdin=[json.dumps(request)]*2),
        stop=NS(is_set=lambda:False),args=NS(agent_mode=agent,deadline=e.deadline,evidence=tmp_path),
        receipt_cache={},capture=lambda *a,**k:None,executor=e,env=e.env,execution_settings=settings,fixture_enabled=fi.enabled,
        allowed_operations={group:[request['operation']]},send=sent.append,override=None,tier=None,
        receipt_fields=lambda settings:{},RecoverablePlanRejection=RecoverablePlanRejection,
        FixtureContactBaseDisplacementRejected=ft.FixtureContactBaseDisplacementRejected)
    exec(compile(ast.fix_missing_locations(ast.Module(body=[loop],type_ignores=[])),str(worker.__file__),'exec'),scope)
    assert len(sent)==2 and sent[0]==sent[1] and sent[0]['ok']
    if collaboration=='c1':
        assert sent[0]['bimanual_collaboration']=='c1'
        assert sent[0]['held_by_arm']=={'left':None,'right':None}
    assert sent[0]['start_sequence']==0 and sent[0]['sequence']==e.sequence and e.sequence>0
    assert not e.failed
    if reject:
        assert sent[0]['value']['rejected'] and sent[0]['value']['reason']==ft.FixtureContactBaseDisplacementRejected.reason
        assert 'base_rehold' not in sent[0]
    else:
        assert sent[0]['base_rehold']['translation_m']==.011
    assert len((tmp_path/'rpc-receipts.jsonl').read_text().splitlines())==1


def test_r5_prompt_describes_post_execution_rejection():
    from benchmarks.attention_harness.dev_tasks import dev_sdk,dev_sdk_prompt
    config=dict(execution_settings={'profile':fi.PROFILE},sdk_call_limit=320,horizon=9600,attempt_wall_seconds=1920)
    metadata=dev_sdk(config)['fixture_interaction'];prompt=dev_sdk_prompt(config)
    assert metadata['base_rehold_after_contact']
    assert metadata['base_rehold_max_translation_m']==.05 and metadata['base_rehold_max_yaw_rad']==.10
    assert 'fixture_contact_base_displacement_excessive' in prompt and 'after physical execution' in prompt
