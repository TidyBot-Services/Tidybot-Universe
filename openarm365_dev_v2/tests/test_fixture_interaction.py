"""Offline R5 boundary tests. MuJoCo contacts and fixture getters are fakes."""
import copy
from dataclasses import fields,replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace as NS

import numpy as np
import pytest

from curobo_v2 import execution_settings as es,fixture_interaction as fi
from openarm365_dev import world as wm,planner as pm,executor as em
from openarm365_dev_v2 import dev_config as dc
from openarm365_dev_v2.tests.test_contact_exemption import (
    environment,contact_executor,fake_mujoco_if_unavailable)
from openarm365_dev_v2.tests.test_motion_tracking_diagnostics import fake_apply
from benchmarks.attention_harness.tests.c21_fixtures import r4_deployment
from benchmarks.attention_harness.tests.test_dev_local_deployment import base

ROOT=Path(__file__).resolve().parents[2]
GOLDEN=Path(__file__).with_name('fixtures')/'fixture_legacy_golden.json'
PROBES=Path(__file__).with_name('fixtures')/'fixture_parts_probe.json'


@pytest.fixture
def settings(r4_deployment):
    return es.from_config(replace(es.R5_FIXTURE_20261031,
        planner_deployment=r4_deployment.planner_deployment).to_dict())


@pytest.fixture
def fixture_types(monkeypatch):
    class Fixture:pass
    module=ModuleType('robocasa.models.fixtures');module.Fixture=Fixture
    monkeypatch.setitem(sys.modules,'robocasa.models.fixtures',module)
    return {cls:type(cls,(Fixture,),{}) for cls in fi.PART_PATTERNS}


def fixture_env(monkeypatch,fixture_types):
    import mujoco
    env=environment();m=env.sim.model
    m._model=m;env.sim.data._data=env.sim.data
    env.sim.data.geom_xpos[3]=[1.,.4,.5]
    def distance(model,data,g1,g2,maximum,segment):
        return float(np.linalg.norm(np.maximum(abs(data.geom_xpos[g1]-data.geom_xpos[g2])-model.geom_size[g1]-model.geom_size[g2],0)))
    monkeypatch.setattr(mujoco,'mj_geomDistance',distance,raising=False)
    m.geoms=['robot','obj','obj_child','cab_left_door_g0','cab_left_door_handle_g0']
    f=fixture_types['HingeCabinet']();f.name='cab';f.root_body='fixture'
    f.get_door_state=lambda env:{'left_door':0.,'right_door':.1}
    env.fxtr=f;env.init_robot_base_ref=f;env.fixture_refs={'alias':(f,{})}
    m.body_jntadr=np.array(m.body_jntadr,copy=True);m.body_jntnum=np.array(m.body_jntnum,copy=True)
    m.body_jntadr[4]=2;m.body_jntnum[4]=1;m.jnt_type=np.array([0,0,3])
    m.jnt_range=np.array([[0,0],[0,0],[-1.57,0.]])
    m.jnt_qposadr=np.array([0,1,2]);m.joint_id2name=lambda jid:'cab_leftdoorhinge'
    env.sim.data.xaxis=np.array([[0,0,1.]]*3);env.sim.data.xanchor=np.zeros((3,3))
    env.sim.data.qpos=np.zeros(3)
    monkeypatch.setattr(wm,'mount_pose',lambda env:np.eye(4))
    return env


@pytest.mark.parametrize('case',list(json.loads(GOLDEN.read_bytes())['cases']))
def test_legacy_config_sdk_prompt_golden(monkeypatch,case):
    from benchmarks.attention_harness import dev_tasks as dt
    from curobo_v2 import identity
    prior=json.loads(GOLDEN.read_bytes())['cases'][case];task,profile=case.split(':')
    pin={'lock_path':'/tmp/fixture-test-deployment/runtime-lock.json','sha256':'fake-sha'}
    settings=None if profile=='legacy' else replace(es.R3_20261025 if profile=='r3' else es.R4_20261027,planner_deployment=pin)
    monkeypatch.setattr(es,'_require_dev_local',lambda pin:None)
    monkeypatch.setattr(es,'_deployment_pin',lambda:dict(pin))
    monkeypatch.setattr(identity,'load_lock',lambda *a,**k:({'provenance':{'deployment_kind':'dev-local'}},'fake-sha'))
    monkeypatch.setattr(dc,'verify_lock',lambda *a,**k:None)
    source={'kind':'dev-local','source_root':str(dc.ROOT)} if profile=='r4' else None
    c=dc._make_dev_config(task,102,{'path':'fixture','sha256':'fixture'},execution_settings=settings,planner_source=source)
    dc.validate_dev_config(c)
    if source:c['planner_source']['source_root']='<ROOT>'
    assert json.dumps(c,sort_keys=True)==json.dumps(prior['config'],sort_keys=True)
    assert dt.dev_sdk(c)==prior['sdk'] and dt.dev_sdk_prompt(c).encode()==prior['prompt'].encode()
    assert dt.dev_sdk_seal(c)==prior['seal']
    from benchmarks.attention_harness.dev_planner_memory import sdk_interface_sha256
    assert sdk_interface_sha256(dict(contract=dt.dev_sdk(c),prompt=dt.dev_sdk_prompt(c)))==prior['seal']['dev_sdk_sha256']


def test_operations_legacy_and_profile_gating():
    from openarm365_dev import policy_validation as pv,policy_worker
    from openarm365_dev_v2 import agent_policy_worker as aw
    expected=json.loads(GOLDEN.read_bytes())['operations']
    for ops in (pv.OPERATIONS,policy_worker.OPERATIONS,aw.OPERATIONS):
        assert {k:sorted(v) for k,v in ops.items()}==expected
    source='from robot_sdk import sensors\nvalues=sensors.find_fixtures()\n'
    for profile in (None,'r3-20261025','r4-20261027'):
        config={} if profile is None else {'execution_settings':{'profile':profile}}
        with pytest.raises(Exception,match='forbidden method'):pv.validate_generated_policy(source,config=config)
        assert 'find_fixtures' not in pv.operations(config)['sensors']
    config={'execution_settings':{'profile':fi.PROFILE}}
    pv.validate_generated_policy(source,config=config)
    assert pv.operations(config)['sensors']==set(expected['sensors']) | {'find_fixtures'}


@pytest.mark.parametrize('field',[f.name for f in fields(es.R5_FIXTURE_20261031)])
def test_profile_missing_invalid_fields(settings,field):
    value=settings.to_dict();del value[field]
    with pytest.raises(ValueError):es.from_config(value)
    value=settings.to_dict();value[field]=None
    with pytest.raises(ValueError):es.from_config(value)


def test_profile_dev_local_and_receipts(settings,monkeypatch):
    assert es.from_config(settings.to_dict())==settings
    assert es.receipt_fields(settings)['fixture_contact_mode']=='record_only'
    assert es.receipt_fields(settings)['formal_eligible'] is False
    value=settings.to_dict();value['unreviewed']=True
    with pytest.raises(ValueError,match='extra field'):es.from_config(value)
    value=settings.to_dict();value['planner_deployment']['sha256']='0'*64
    with pytest.raises(Exception):es.from_config(value)


@pytest.mark.parametrize('task',fi.DEFAULT_PILOT_TASKS)
def test_pilot_config_registry_and_resources(monkeypatch,settings,task):
    from benchmarks.attention_harness import dev_tasks as dt
    from openarm365_dev_v2.fixture_contract import resource_paths
    monkeypatch.setattr(dc,'verify_lock',lambda *a,**k:None)
    source={'kind':'dev-local','source_root':str(dc.ROOT)}
    c=dc.make_dev_config(task,101,{'path':'fixture','sha256':'fixture'},execution_settings=settings,planner_source=source)
    assert dc.validate_dev_config(c) is c
    assert c['scene_reset_seed']==101
    assert c['fixture_scene_contract'] and c['fixture_success_condition']
    assert c['dev_sdk_sha256']==dt.dev_sdk_seal(c)['dev_sdk_sha256']
    assert all(p.is_file() for p in resource_paths())
    for key in ('fixture_scene_contract','fixture_success_condition','scene_reset_seed','dev_sdk_sha256'):
        invalid=copy.deepcopy(c);del invalid[key]
        with pytest.raises(ValueError):dc.validate_dev_config(invalid)
    for tier,boundary in [('legacy','dev_loop'),('dev','formal')]:
        with pytest.raises(ValueError):dc._make_dev_config(task,101,c['dev_lock'],execution_settings=settings,planner_source=source,budget_tier=tier,budget_boundary=boundary)
    if task!='open_cabinet':
        with pytest.raises(ValueError,match='requires r5'):dc._make_dev_config(task,101,c['dev_lock'])
    assert list(dt.TASKS)==['pick_place_counter_to_sink','open_cabinet']
    policy=ROOT/'openarm365_dev_v2/policies'/('fixture_open_cabinet.py' if task=='open_cabinet' else task+'.py')
    from openarm365_dev_v2.agent_policy import validate_source
    validate_source(policy.read_text(),config=c)


def test_whitelist_real_screening_names():
    probes=json.loads(PROBES.read_bytes());seen=set();accepted=denied=0
    for task in probes.values():
        for f in task.get('fixtures',{}).values():
            cls=f['type'];seen.add(cls)
            for raw in f['parts']:
                name=raw.replace('#','1');allow=fi.part_pattern(cls,name)
                expected=any(token in name for token in ('handle','button','knob','switch','lever','spout')) and 'reg_' not in name and '_reg_' not in name
                assert bool(allow)==expected,(cls,name,allow)
                accepted+=bool(allow);denied+=not bool(allow)
    assert len(fi.PART_PATTERNS)==15 and len(seen)==12
    assert accepted>50 and denied>10
    for cls in fi.PART_PATTERNS:
        for name in ('door_main','door_panel','body_main','counter_top','wall_main','floor','door_handle_reg_main','knob_body','start_button_bad'):
            assert fi.part_pattern(cls,name) is None
    assert fi.WHITELIST_SHA256==hashlib.sha256(json.dumps(dict(fi.PART_PATTERNS),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def test_world_only_task_parts_and_audit(monkeypatch,fixture_types,settings,tmp_path):
    env=fixture_env(monkeypatch,fixture_types)
    full=wm.snapshot(env,exempt_body_ids={2,5})
    slim=wm.snapshot(env,exempt_body_ids={2,5},execution_settings=settings)
    assert [r['geom_id'] for r in slim['inventory']]==[3]
    audit=slim['fixture_contact_exemption'];assert [p['geom_id'] for p in audit['parts']]==[4]
    assert audit['parts'][0]['fixture']=='cab' and audit['parts'][0]['pattern']
    assert audit['world_sha256_before']==full['world_sha256']
    assert audit['world_sha256_after']==slim['world_sha256']!=full['world_sha256']
    del env.fxtr;env.fixture_refs={}
    assert not fi.interaction_geoms(env,settings) # base reference alone is not task scope
    assert wm.snapshot(env,execution_settings=None)==wm.snapshot(env)


def test_find_fixtures_getters_extent_axes_and_gating(monkeypatch,fixture_types,settings):
    env=fixture_env(monkeypatch,fixture_types)
    rows=fi.find_fixtures(env,settings);assert len(rows)==1
    row=rows[0];assert row['fixture_class']=='HingeCabinet'
    assert row['state']['door_open_fraction']=={'left_door':0.,'right_door':.1}
    part=row['interaction_parts'][0]
    assert part['center']==env.sim.data.geom_xpos[4].tolist()
    assert part['extent']==[.1,.1,.2]
    assert part['axes'][0]['kind']=='hinge' and part['axes'][0]['axis_world']==[0,0,1]
    env.sim.model.jnt_type[2]=2
    assert fi.find_fixtures(env,settings)[0]['interaction_parts'][0]['axes'][0]['kind']=='slide'
    for settings in (None,es.R3_20261025,es.R4_20261027):
        with pytest.raises(ValueError,match='requires'):fi.find_fixtures(env,settings)


def monitor_fixture(monkeypatch,fixture_types,settings,kind='finger',other='handle'):
    e,rows=contact_executor(monkeypatch,0,2,'movable_objects')
    m=e.env.sim.model;m.geoms=list(m.geoms)
    m.geoms[0]='robot0_openarm_left_left_finger_collision0' if kind=='finger' else 'robot0_openarm_left_hand_collision0' if kind=='hand' else 'robot0_openarm_left_link6_collision0'
    m.geoms[2]='cab_left_door_handle_g0' if other=='handle' else 'cab_left_door_g0' if other=='panel' else 'counter_top'
    m.ngeom=len(m.geoms);m.geom_contype=np.ones(m.ngeom);m.geom_conaffinity=np.ones(m.ngeom)
    m.geom_type=np.full(m.ngeom,6);m.geom_size=np.full((m.ngeom,3),.05)
    e.env.sim.data.geom_xpos=np.zeros((m.ngeom,3));e.env.sim.data.geom_xmat=np.tile(np.eye(3).ravel(),(m.ngeom,1))
    for c in e.env.sim.data.contact:c.pos=np.zeros(3)
    m.jnt_type=np.array(m.jnt_type,copy=True);m.jnt_type[0]=3
    f=fixture_types['HingeCabinet']();f.name='cab';f.root_body=m.body_id2name(4)
    e.env.fxtr=f;e.env.obj_body_id={'distr_counter':6,'distr_sink':7,'counter':8}
    e.execution_settings=settings;e.goals=np.array([.02]);e._execution_receipt={}
    from curobo_v2 import fixture_telemetry
    fixture_telemetry.begin(e)
    return e,rows


@pytest.mark.parametrize('kind,other,allowed',[('finger','handle',True),('hand','handle',True),('arm','handle',False),('finger','panel',False),('finger','counter',False)])
def test_monitor_boundary_force_error_and_mimic(monkeypatch,fixture_types,settings,kind,other,allowed):
    e,rows=monitor_fixture(monkeypatch,fixture_types,settings,kind,other)
    if allowed:e.monitor()
    else:
        with pytest.raises(RuntimeError,match='Unexpected physical robot/world contact'):e.monitor()
    samples=[r for p,r in rows if r.get('event')=='fixture_contact_sample']
    assert bool(samples)==allowed
    if allowed:
        assert samples[0]['contacts'][0]['contact_force_n']==2.5
        assert samples[0]['joint_tracking_error_rad']=={'joint':-.02}
        assert e._fixture_motion['contact_steps']==1
        e.env.sim.data.qpos[3]=.009;e.monitor()
        e.env.sim.data.qpos[3]=.011
        e.monitor()
        assert e._fixture_motion['maximum_gripper_mimic_error_m']['left']==.011


@pytest.mark.parametrize('contact,profile,relaxed',[(True,fi.PROFILE,True),(False,fi.PROFILE,False),(True,'r4-20261027',False),(True,None,False)])
def test_endpoint_only_current_motion_fixture_contact(monkeypatch,contact,profile,relaxed):
    e,_,rows,_=fake_apply(monkeypatch,position=[.03,.02])
    e.execution_settings=None if profile is None else NS(profile=profile)
    monkeypatch.setattr(em,'receipt_fields',lambda settings:{})
    monkeypatch.setattr(em,'held_envelope_receipt',lambda settings:{})
    e._fixture_motion={'contact_steps':99} # previous motion never authorizes this one
    def step():
        e.sequence+=1
        if contact and profile==fi.PROFILE:e._fixture_motion['contact_steps']+=1
    e.control_step=step
    if relaxed:
        result=em.NativeExecutor.apply(e,{'plan_id':'p'},request_id='fixture')
        assert result['fixture_pilot']['endpoint_bound_enforced'] is False
        assert result['maximum_endpoint_joint_error_rad']==pytest.approx(.02)
        assert rows[-2]['mode']=='record_only'
    else:
        with pytest.raises(RuntimeError,match='4mrad endpoint'):em.NativeExecutor.apply(e,{'plan_id':'p'},request_id='fixture')
        assert e.failed


def test_probe_gating():
    from openarm365_dev_v2.agent_probe import validate
    with pytest.raises(ValueError,match='unsupported'):validate('find_fixtures',{})
    config={'execution_settings':{'profile':fi.PROFILE}}
    validate('find_fixtures',{},config=config)
    with pytest.raises(ValueError):validate('find_fixtures',{'names':[]},config=config)


@pytest.mark.parametrize('prefix',['','robot0_','gripper0_left_','gripper0_right_'])
def test_real_robosuite_gripper_prefixes(prefix):
    assert fi.gripper_side(prefix+'openarm_left_left_finger_collision0')=='left'
    assert fi.gripper_side(prefix+'openarm_right_hand_collision0')=='right'
    assert fi.gripper_side(prefix+'openarm_left_link6_collision0') is None


def test_agent_sandbox_sensor_roundtrip_and_legacy_denial(tmp_path):
    import time
    from openarm365_dev_v2.agent_sandbox import execute_agent_policy
    code='from robot_sdk import sensors\nprint(sensors.find_fixtures())\n'
    sdk=NS(sensors=NS(find_fixtures=lambda:[{'name':'task_fixture'}]))
    c={'execution_settings':{'profile':fi.PROFILE}}
    result=execute_agent_policy(code=code,sdk=sdk,context={},deadline=time.monotonic()+10,
        stderr_path=tmp_path/'err.txt',source_config=c,unsafe_test_transport=True)
    assert result.status=='completed' and result.call_count==1
    assert 'task_fixture' in (tmp_path/'policy-stdout.txt').read_text()
    with pytest.raises(Exception,match='forbidden method'):
        execute_agent_policy(code=code,sdk=sdk,context={},deadline=time.monotonic()+10,
            stderr_path=tmp_path/'legacy.txt',unsafe_test_transport=True)


def test_scope_excludes_scene_inventory_counters_and_wrong_body(monkeypatch,fixture_types,settings):
    env=fixture_env(monkeypatch,fixture_types)
    other=fixture_types['HingeCabinet']();other.name='unreferenced';other.root_body='fixture'
    env.fixtures={'unreferenced':other}
    counter=type('Counter',(fixture_types['HingeCabinet'].__bases__[0],),{})()
    counter.name='counter';env.counter=counter
    assert list(fi.task_fixtures(env))==['cab']
    env.sim.model.geom_bodyid=np.array(env.sim.model.geom_bodyid,copy=True)
    env.sim.model.geom_bodyid[4]=1
    assert not fi.interaction_geoms(env,settings)


def test_upload_contains_geom_level_audit(monkeypatch,fixture_types,settings,tmp_path):
    env=fixture_env(monkeypatch,fixture_types)
    p=pm.MujocoV2Planner.__new__(pm.MujocoV2Planner)
    p.env=env;p.execution_settings=settings;p.chassis_binding=None;p.attachment_provider=lambda:None
    p.contact_exemption_mode='movable_objects';p.contact_exemption_configured='movable_objects'
    p.sequence=0;p.evidence=tmp_path;p.client=NS(call=lambda *a:{'server':'receipt'})
    world,receipt=p.upload()
    assert receipt['fixture_contact_exemption']==world['fixture_contact_exemption']
    assert receipt['fixture_contact_exemption']['parts'][0]['geom_id']==4
    assert receipt['contact_exemption']['world_sha256']==world['world_sha256']


def test_fixture_close_uses_physics_without_attachment(monkeypatch,fixture_types,settings):
    from curobo_v2.client import PlanningRejected
    e,rows=monitor_fixture(monkeypatch,fixture_types,settings)
    e.check_live=lambda:None;e.sequence=0;e.grips={'left':-1.,'right':-1.}
    e.held_trace_enabled=False;e.held_reference={'mode':'fixed'};e.epoch='pilot'
    e.planner.ledger=NS(invalidate=lambda *a:None);e.planner.prepared=set()
    monkeypatch.setattr(em,'receipt_fields',lambda settings:{})
    def step():e.sequence+=1;e.monitor()
    e.control_step=step
    result=em.NativeExecutor.gripper.__wrapped__(e,'left',1.,request_id='close')
    assert e.held is None and result['attached_object'] is None
    assert result['fixture_pilot']['contact_steps']==20
    e.env.sim.data.contact=[]
    result=em.NativeExecutor.gripper.__wrapped__(e,'left',1.,request_id='empty-close')
    assert result['empty_close'] is True and result['attached_object'] is None and e.held is None


@pytest.mark.parametrize('cls,method,expected',[
    ('Microwave','get_state',{'turned_on':False}),('CoffeeMachine','get_state',{'turned_on':True}),
    ('Sink','get_handle_state',{'water_on':True}),('Stove','get_knobs_state',{'front_left':1.5}),
    ('Toaster','get_state',{'left':{'turned_on':False}}),('ElectricKettle','get_state',{'switch':.5}),
    ('StandMixer','get_state',{'button_head_lock':.2}),('Dishwasher','get_state',{'door':.4}),
    ('Blender','get_state',{'turned_on':False}),('ToasterOven','get_state',{'door':.6}),
    ('Oven','get_state',{'timer':.3})])
def test_getter_signatures(fixture_types,cls,method,expected):
    f=fixture_types[cls]();env=object()
    if cls in ('Microwave','CoffeeMachine','Blender','ToasterOven','Oven'):
        setattr(f,method,lambda:expected)
    else:
        def getter(actual):
            assert actual is env
            return expected
        setattr(f,method,getter)
    state=fi.fixture_state(env,f)
    assert state==({'knob_angles_rad':expected} if cls=='Stove' else expected)


@pytest.mark.parametrize('profile',[None,'r3-20261025','r4-20261027',fi.PROFILE])
def test_owner_pipe_sensor_gate(tmp_path,profile):
    import io,time
    from openarm365_dev_v2.owner import NativeOwnerV2
    path=tmp_path/'config.json';path.write_text(json.dumps({'execution_settings':{'profile':profile}}))
    owner=NativeOwnerV2(python='unused',config=path,evidence=tmp_path,deadline=time.monotonic()+5,cancel_check=lambda:False)
    owner.process=NS(stdin=io.BytesIO())
    owner.read=lambda:dict(kind='response',request_id='fixture',ok=True,value=[{'name':'handle_fixture'}])
    if profile==fi.PROFILE:
        assert owner.rpc('sensors','find_fixtures',request_id='fixture')==[{'name':'handle_fixture'}]
        assert json.loads(owner.process.stdin.getvalue())['operation']=='find_fixtures'
    else:
        with pytest.raises(ValueError,match='Unauthorized'):owner.rpc('sensors','find_fixtures',request_id='fixture')
        assert owner.process.stdin.getvalue()==b''


@pytest.mark.parametrize('task,reference',[('turn_on_microwave','microwave'),('turn_off_stove','stove'),('open_drawer','drawer'),('open_cabinet','fxtr')])
def test_pilot_camera_needs_only_its_fixture(task,reference):
    from openarm365_dev_v2.fixture_contract import configure_camera,camera_target
    position=np.array([.2,.3,.7]);forward=[]
    m=NS(camera_name2id=lambda name:0,cam_pos=np.zeros((1,3)),cam_quat=np.zeros((1,4)))
    env=NS(sim=NS(model=m,forward=lambda:forward.append(True)),**{reference:NS(pos=position)})
    configure_camera(env,{'task_id':task})
    np.testing.assert_allclose(m.cam_pos[0],position+[1.7,-1.2,1.25])
    np.testing.assert_array_equal(camera_target(env,{'task_id':task}),position)
    np.testing.assert_array_equal(position,[.2,.3,.7]);assert forward==[True]


def test_all_contact_points_for_same_geom_pair_are_recorded(monkeypatch,fixture_types,settings):
    e,rows=monitor_fixture(monkeypatch,fixture_types,settings)
    e.env.sim.data.contact*=2
    e.monitor()
    sample=next(row for path,row in rows if row.get('event')=='fixture_contact_sample')
    assert len(sample['contacts'])==2
    assert [c['contact_index'] for c in sample['contacts']]==[0,1]
    assert [c['contact_force_n'] for c in sample['contacts']]==[2.5,2.5]
    assert e._fixture_motion['contact_count']==2


def test_contact_sample_survives_an_unchanged_joint_guard(monkeypatch,fixture_types,settings):
    e,rows=monitor_fixture(monkeypatch,fixture_types,settings)
    e.planner.state=lambda:np.array([.02])
    e.limits=np.array([[-.01,.01]])
    with pytest.raises(RuntimeError,match='joint limit'):e.monitor()
    sample=next(row for path,row in rows if row.get('event')=='fixture_contact_sample')
    assert sample['contacts'][0]['contact_force_n']==2.5
    assert sample['joint_tracking_error_rad']=={'joint':0.0}


@pytest.mark.parametrize('profile',[None,fi.PROFILE])
def test_one_call_fixture_extension_preserves_legacy_actions(profile):
    from openarm365_dev_v2.fixture_sdk import build_sdk
    calls=[];events=[]
    def rpc(group,operation,*args,**kwargs):
        calls.append((group,operation))
        return [{'name':'task_fixture'}] if group=='sensors' else None
    sdk=build_sdk(NS(rpc=rpc),events.append,config={'execution_settings':{'profile':profile}})
    assert sdk.left_arm.move_delta(0,0,.01) is None
    assert hasattr(sdk.sensors,'find_fixtures') is (profile==fi.PROFILE)
    if profile==fi.PROFILE:
        assert sdk.sensors.find_fixtures()==[{'name':'task_fixture'}]
        assert events[-1]['operation']=='find_fixtures' and events[-1]['status']=='completed'
        with pytest.raises(TypeError):sdk.sensors.find_fixtures('unexpected')


def test_zero_motion_rpc_never_reuses_previous_contact_summary(monkeypatch,fixture_types,settings):
    from curobo_v2 import fixture_telemetry
    e,rows=monitor_fixture(monkeypatch,fixture_types,settings)
    e.monitor();before=e.physics_steps
    assert fixture_telemetry.receipt(e,after_physics_step=before)=={}
    assert fixture_telemetry.receipt(e,after_physics_step=before-1)['fixture_pilot']['contact_count']==1
