"""Offline panel geometry, screened registrations and probe receipts."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import ModuleType,SimpleNamespace as NS
import sys
import subprocess
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from curobo_v2 import fixture_interaction as fi,fixture_world as fw,execution_settings as es
from openarm365_dev import world as wm
from openarm365_dev_v2 import fixture_pilot as pilot,dev_config as dc
from openarm365_dev_v2.tests.test_fixture_interaction import (
    fixture_env,fixture_types,settings,r4_deployment,base,fake_mujoco_if_unavailable)

ROOT=Path(__file__).resolve().parents[2]
SCREENED={
    'open_cabinet':(101,102,103),'close_blender_lid':(101,102,103),
    'open_drawer':(101,102,103),'turn_on_microwave':(101,102,103),
    'turn_off_stove':(101,102,103),'open_stand_mixer_head':(101,102,103),
    'coffee_setup_mug':(101,103,104),'prepare_coffee':(102,103,104),
    'kettle_boiling':(101,102,103),'heat_kebab_sandwich':(103,105,106),
    'pick_place_counter_to_cabinet':(101,102,107),'categorize_condiments':(102,103,105),
    'weigh_ingredients':(101,102,103),'arrange_tea':(104,105,106)}


def panel_env(monkeypatch,fixture_types,angles=(0,0,0)):
    env=fixture_env(monkeypatch,fixture_types);m,d=env.sim.model,env.sim.data
    m.geom_size=np.array(m.geom_size,copy=True);m.geom_type=np.array(m.geom_type,copy=True)
    m.geom_size[3]=[.08,.02,.23];m.geom_size[4]=[.005,.001,.006]
    rotation=Rotation.from_euler('xyz',angles).as_matrix();origin=np.array([1.,.4,.5])
    for gid in (3,4):d.geom_xmat[gid]=rotation.ravel()
    d.geom_xpos[3]=origin;d.geom_xpos[4]=origin+rotation@np.array([.02,-.0214,.05])
    import mujoco
    monkeypatch.setattr(mujoco,'mj_geomDistance',lambda *args:.0004)
    return env,rotation,origin


def inside(box,point):
    rotation=Rotation.from_quat(np.array(box['quat_wxyz'])[[1,2,3,0]]).as_matrix()
    return bool(np.all(np.abs(rotation.T@(point-box['center']))<=np.array(box['half_size'])+1e-12))


@pytest.mark.parametrize('angles',[(0,0,0),(.2,.5,.8),(-1.,.3,1.2)])
def test_panel_uploaded_with_local_window_and_clear_approach(monkeypatch,fixture_types,settings,angles):
    env,rotation,origin=panel_env(monkeypatch,fixture_types,angles)
    mount=np.eye(4);mount[:3,:3]=Rotation.from_euler('z',.4).as_matrix();mount[:3,3]=[.1,.2,.3]
    monkeypatch.setattr(wm,'mount_pose',lambda env:mount)
    world=wm.snapshot(env,exempt_body_ids=set(),execution_settings=settings)
    audit=world['fixture_contact_exemption'];split=audit['neighbourhood_split'][0]
    boxes=[b for b in world['cuboids'] if ':fixture-window:' in b['name']]
    assert len(boxes)==4 and split['original_half_size']==[.08,.02,.23]
    assert not audit['neighbourhood_dropped_whole'] and not audit['neighbourhood_kept_whole']
    window=split['window'];assert window['thickness_axis']==1 and window['expansion_m']==.01
    assert np.allclose(window['low_local_m'],[.005,-.02,.034])
    assert np.allclose(window['high_local_m'],[.035,.02,.066])
    transform=np.linalg.inv(mount)
    def planner(local):return (transform@np.r_[origin+rotation@local,1])[:3]
    for x in np.linspace(-.079,.079,33):
        for z in np.linspace(-.229,.229,45):
            expected=not (.005<x<.035 and .034<z<.066)
            assert any(inside(b,planner([x,0,z])) for b in boxes)==expected
    # The formerly traversable point 15 cm below the button is solid panel.
    assert any(inside(b,planner([.02,0,-.10])) for b in boxes)
    tip=planner([.02,-.0374,.05])
    for box in boxes:
        r=Rotation.from_quat(np.array(box['quat_wxyz'])[[1,2,3,0]]).as_matrix()
        distance=np.linalg.norm(np.maximum(abs(r.T@(tip-box['center']))-box['half_size'],0))
        assert distance>.005
    assert len(world['unexempted_cuboids'])==4 and any(b['name'].startswith('mujoco:3:') for b in world['unexempted_cuboids'])
    assert split['distance_m']==.0004 and split['maximum_extent_distance_m']>.03
    for box,replacement in zip(boxes,split['replacement_boxes']):
        assert box['center']==replacement['center'] and box['half_size']==replacement['half_size']
    assert audit['world_sha256_after']==world['world_sha256']!=audit['world_sha256_before']


@pytest.mark.parametrize('kind',[6,2])
def test_small_neighbour_dropped_with_exact_extent(monkeypatch,fixture_types,settings,kind):
    env,rotation,origin=panel_env(monkeypatch,fixture_types)
    m,d=env.sim.model,env.sim.data;m.geom_size[3]=[.003,.003,.003];m.geom_type[3]=kind
    d.geom_xpos[3]=d.geom_xpos[4]+[.01,0,0]
    world=wm.snapshot(env,execution_settings=settings);audit=world['fixture_contact_exemption']
    row=audit['neighbourhood_dropped_whole'][0]
    assert row['action']=='dropped_whole' and row['distance_m']==.0004
    assert row['maximum_extent_distance_m']<=.03 and row['extent_samples']==8
    assert row['extent_half_size_m']==[.003]*3 and row['extent_part_distances'][0]['part_geom_id']==4
    assert audit['neighbourhood']==[row] and not audit['neighbourhood_split']
    assert 3 not in [i['geom_id'] for i in world['inventory']]


@pytest.mark.parametrize('kind',[2,3,4,5,7])
def test_large_nonbox_kept_whole(monkeypatch,fixture_types,settings,kind):
    env,_,_=panel_env(monkeypatch,fixture_types);env.sim.model.geom_type[3]=kind
    if kind==7:
        from itertools import product
        m=env.sim.model;m.geom_dataid=np.zeros(m.ngeom,dtype=int);m.mesh_vertadr=[0];m.mesh_vertnum=[8]
        m.mesh_vert=np.array(list(product((-.08,.08),(-.02,.02),(-.23,.23))))
    world=wm.snapshot(env,execution_settings=settings);audit=world['fixture_contact_exemption']
    row=audit['neighbourhood_kept_whole'][0]
    assert row['geom_id']==3 and row['action']=='kept_whole' and row['reason']=='non_box'
    assert row['maximum_extent_distance_m']>.03 and row['distance_m']==.0004
    assert not audit['neighbourhood_dropped_whole'] and not audit['neighbourhood_split']
    assert 3 in [i['geom_id'] for i in world['inventory']]


def test_split_world_is_uploaded_and_audited(monkeypatch,tmp_path,fixture_types,settings):
    from openarm365_dev.planner import MujocoV2Planner
    env,_,_=panel_env(monkeypatch,fixture_types);p=MujocoV2Planner.__new__(MujocoV2Planner)
    p.env=env;p.evidence=tmp_path;p.sequence=0;p.chassis_binding=None;p.execution_settings=settings
    p.contact_exemption_mode=p.contact_exemption_configured='none';sent=[]
    p.client=NS(call=lambda path,body:(sent.append((path,body)) or {}))
    world,receipt=p.upload()
    assert sent==[('/world/cuboids',dict(env_id='robocasa365',cuboids=world['cuboids']))]
    assert receipt['fixture_contact_exemption']==world['fixture_contact_exemption']
    assert len(receipt['fixture_contact_exemption']['neighbourhood_split'][0]['replacement_boxes'])==4
    assert json.loads((tmp_path/'world-0001.json').read_text())==world


@pytest.mark.parametrize('x',[-.08,.08])
def test_panel_edge_window_is_clipped(monkeypatch,fixture_types,settings,x):
    env,_,origin=panel_env(monkeypatch,fixture_types);env.sim.data.geom_xpos[4]=origin+[x,-.0214,.05]
    world=wm.snapshot(env,execution_settings=settings)
    row=world['fixture_contact_exemption']['neighbourhood_split'][0]
    assert len(row['replacement_boxes'])==3
    assert row['window']['low_local_m'][0]>=-.08 and row['window']['high_local_m'][0]<=.08
    for b in row['replacement_boxes']:
        c=np.array(b['center_local_m']);h=np.array(b['half_size'])
        assert np.all(h>0) and np.all(abs(c)+h<=np.array([.08,.02,.23])+1e-12)


@pytest.mark.parametrize('profile',[None,'r3','r4'])
def test_non_r5_world_bytes_unchanged(monkeypatch,fixture_types,settings,profile):
    env,_,_=panel_env(monkeypatch,fixture_types)
    old='3ce1a3a:openarm365_dev/world.py'
    namespace={'__name__':'openarm365_dev.phase1d_before_world','__package__':'openarm365_dev'}
    exec(compile(subprocess.check_output(['git','show',old],cwd=ROOT),old,'exec'),namespace)
    namespace['mount_pose']=lambda env:np.eye(4)
    chosen=None if profile is None else replace(es.R3_20261025 if profile=='r3' else es.R4_20261027,planner_deployment=settings.planner_deployment)
    assert json.dumps(wm.snapshot(env,execution_settings=chosen),sort_keys=True)==json.dumps(namespace['snapshot'](env),sort_keys=True)


@pytest.mark.parametrize('task,seed',[(t,s) for t,seeds in SCREENED.items() for s in seeds])
def test_all_screened_prepare_only_configs(monkeypatch,tmp_path,settings,task,seed):
    from benchmarks.attention_harness import dev_tasks as dt
    monkeypatch.setattr(dc,'new_execution_settings',lambda **kwargs:settings)
    monkeypatch.setattr(dc,'make_lock',lambda *args,**kwargs:{'path':'offline','sha256':'offline'})
    monkeypatch.setattr(dc,'verify_lock',lambda *args,**kwargs:None)
    policy=tmp_path/'custom.py';policy.write_text('from robot_sdk import sensors\nprint(sensors.find_fixtures())\n')
    out=tmp_path/'prepared'
    assert pilot.main(['--task',task,'--seed',str(seed),'--policy',str(policy),'--prepare-only','--out',str(out)])==0
    config=json.loads((out/'config.json').read_text());assert dc.validate_dev_config(config) is config
    assert config['seed']==config['scene_reset_seed']==seed and config['execution_settings']['profile']==fi.PROFILE
    assert config['budget_tier']=='dev' and config['runner_boundary']=='dev_loop'
    assert dt.FIXTURE_TASKS[task].seeds==SCREENED[task]
    assert config['task_prompt']==dt.FIXTURE_TASKS[task].task_prompt
    assert config['initial_joint_targets']==__import__('openarm365_dev_v2.environment',fromlist=['initial_joints']).initial_joints('pick_place_counter_to_sink')
    assert list(dt.TASKS)==['pick_place_counter_to_sink','open_cabinet']
    assert (out/'policy.py').read_bytes()==policy.read_bytes()


@pytest.mark.parametrize('task',SCREENED)
def test_unscreened_seed_rejected_before_writes(tmp_path,task):
    out=tmp_path/'not-created'
    with pytest.raises(SystemExit):pilot.main(['--task',task,'--seed','125','--probe-start','--prepare-only','--out',str(out)])
    assert not out.exists()


@pytest.mark.parametrize('task',[t for t in SCREENED if t not in fi.DEFAULT_PILOT_TASKS])
def test_added_tasks_require_explicit_policy(tmp_path,task):
    with pytest.raises(SystemExit):pilot.main(['--task',task,'--prepare-only','--out',str(tmp_path/'none')])


@pytest.mark.parametrize('task',[t for t in SCREENED if t not in ('open_cabinet','pick_place_counter_to_cabinet')])
@pytest.mark.parametrize('profile',[None,'r3','r4'])
def test_added_tasks_refuse_non_r5_config(settings,task,profile):
    chosen=None if profile is None else replace(es.R3_20261025 if profile=='r3' else es.R4_20261027,planner_deployment=settings.planner_deployment)
    with pytest.raises(ValueError,match='requires r5'):
        dc._make_dev_config(task,SCREENED[task][0],{},execution_settings=chosen)


def test_probe_source_validation_and_motion(monkeypatch):
    from openarm365_dev_v2.agent_policy import validate_source
    from openarm365_dev.policy_validation import validate_generated_policy
    code=(ROOT/'openarm365_dev_v2/policies/fixture_start_probe.py').read_text()
    config={'execution_settings':{'profile':fi.PROFILE}}
    validate_source(code,config=config);validate_generated_policy(code,config=config)
    sdk=ModuleType('robot_sdk');calls=[]
    start=[-.4,-.8,0,.8,0,0,0,.4,.8,0,.8,0,0,0]
    sdk.sensors=NS(get_observation=lambda:dict(robot0_left_joint_pos=start[:7],robot0_right_joint_pos=start[7:]))
    sdk.bimanual=NS(move_joints=lambda q:calls.append(list(q)))
    monkeypatch.setitem(sys.modules,'robot_sdk',sdk);exec(code,{})
    expected=list(start);expected[0]+=.05;expected[7]+=.05
    assert calls==[expected,start]
    calls.clear()
    def reject(q):calls.append(list(q));raise RuntimeError('rejected')
    sdk.bimanual.move_joints=reject;exec(code,{})
    assert calls==[expected,start]


@pytest.mark.parametrize('agent',[False,True])
def test_probe_runner_and_exact_contact_receipt(monkeypatch,tmp_path,settings,agent):
    monkeypatch.setattr(dc,'new_execution_settings',lambda **kwargs:settings)
    monkeypatch.setattr(dc,'make_lock',lambda *args,**kwargs:{'path':'offline','sha256':'offline'})
    monkeypatch.setattr(dc,'verify_lock',lambda *args,**kwargs:None)
    module=ModuleType('openarm365_dev_v2.runner')
    class Runner:
        def __init__(self,**kwargs):assert kwargs['agent_mode'] is agent
        def execute(self,request):
            episode=request.artifact_root/'openarm365-dev-offline';episode.mkdir()
            bodies=['openarm_tidyverse_chassis','world:mujoco:677:stack_2_main_group_1_door_handle_g1']
            rows=[dict(request=dict(request_id=str(i),group='bimanual',operation='move_joints',args=[[0]*14]),response=dict(ok=True,value=dict(ok=False,rejected=True,reason='start_state_collision_or_constraint_violation',diagnostics=dict(validation=dict(exact_geometry=dict(contacts=[dict(bodies=bodies)])))))) for i in range(2)]
            rows.append(dict(request=dict(group='owner',operation='evaluate'),response=dict(ok=False,error='fatal_error')))
            (episode/'rpc-receipts.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            return dict(status='completed')
    module.OpenArm365DevRunnerV2=Runner;monkeypatch.setitem(sys.modules,module.__name__,module)
    out=tmp_path/'probe';argv=['--task','arrange_tea','--probe-start','--out',str(out)]
    if agent:argv+=['--agent-mode']
    assert pilot.main(argv)==0
    report=json.loads((out/'start_probe.json').read_text());assert report['move_rpc_count']==2
    assert report['rpcs'][0]['ok'] is False and report['rpcs'][0]['reason']=='start_state_collision_or_constraint_violation'
    assert report['rpcs'][0]['exact_geometry_contact_bodies'][0][0]=='openarm_tidyverse_chassis'
    assert report['rpcs'][2]['reason']=='fatal_error'
    assert json.loads((out/'config.json').read_text())['seed']==104


def test_screened_fixture_whitelist_coverage():
    from benchmarks.attention_harness import dev_tasks as dt
    probes=json.loads((ROOT/'openarm365_dev_v2/tests/fixtures/fixture_parts_probe.json').read_text())
    assert dict(fi.PILOT_SEEDS)==SCREENED
    for task in dt.FIXTURE_TASKS.values():
        fixtures=probes[task.official_env]['fixtures']
        assert fixtures
        for f in fixtures.values():
            assert f['type'] in fi.PART_PATTERNS
            assert any(fi.part_pattern(f['type'],raw.replace('#','1')) for raw in f['parts'])
    assert fi.WHITELIST_SHA256=='3485ea3bcdab11c4ca848484b5853e9e3614c982fa92e7573dc5c92c376490d1'
