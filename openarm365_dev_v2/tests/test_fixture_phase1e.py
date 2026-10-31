"""Offline press-window geometry, settings identity and unchanged contact boundary."""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace as NS
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from curobo_v2 import execution_settings as es,fixture_interaction as fi,fixture_world as fw
from openarm365_dev import world as wm
from openarm365_dev.base_motion import _box,_obb_clearance
from openarm365_dev.planner import MujocoV2Planner
from openarm365_dev_v2.tests.test_fixture_phase1b import neighbourhood_executor
from openarm365_dev_v2.tests.test_fixture_phase1d import panel_env,inside
from openarm365_dev_v2.tests.test_fixture_interaction import (
    fixture_types,settings,r4_deployment,base,fake_mujoco_if_unavailable)

PRESS_PARTS=[('Microwave','start_button'),('Microwave','stop_button'),
    ('CoffeeMachine','start_button_0'),('CoffeeMachine','start_button_12'),
    ('Blender','power_button_main'),('StandMixer','button_head_lock_main'),
    ('ElectricKettle','button_lid_main'),('ElectricKettle','switch_main'),
    ('Toaster','sideL_button_cancel_main'),('Toaster','sideR_button_cancel_main')]
OTHER_PARTS=[('Microwave','door_handle_main'),('Blender','knob_speed_main'),
    ('Blender','pitcher_handle_1'),('StandMixer','knob_speed_main'),
    ('ElectricKettle','handle_main'),('Toaster','sideL_knob_doneness_main'),
    ('Toaster','sideR_lever_handle'),('Stove','knob_front_left_main'),
    ('Drawer','door_handle_g1'),('Sink','spout_main')]


def appliance_panel(monkeypatch,fixture_types,cls='Microwave',suffix='start_button',angles=(0,0,0)):
    env,rotation,origin=panel_env(monkeypatch,fixture_types,angles)
    f=fixture_types[cls]();f.name='appliance';f.root_body='fixture'
    env.fxtr=f;env.init_robot_base_ref=f;env.fixture_refs={'alias':(f,{})}
    m=env.sim.model;m.geoms[3]='appliance_panel';m.geoms[4]='appliance_'+suffix
    m.geom_size[4]=[.0135,.001,.0095]
    return env,rotation,origin


@pytest.mark.parametrize('cls,suffix',PRESS_PARTS+OTHER_PARTS)
def test_window_kind_follows_whitelisted_part(monkeypatch,fixture_types,settings,cls,suffix):
    env,_,_=appliance_panel(monkeypatch,fixture_types,cls,suffix)
    world=wm.snapshot(env,execution_settings=settings)
    window=world['fixture_contact_exemption']['neighbourhood_split'][0]['window']
    press=(cls,suffix) in PRESS_PARTS;expansion=.035 if press else .01
    assert window['part_kind']==('press' if press else 'non_press')
    assert window['expansion_m']==expansion
    assert np.allclose(window['low_local_m'],[.02-.0135-expansion,-.02,.05-.0095-expansion])
    assert np.allclose(window['high_local_m'],[.02+.0135+expansion,.02,.05+.0095+expansion])
    assert fw.PRESS_PATTERNS==frozenset(p for patterns in fi.PART_PATTERNS.values()
        for p in patterns if 'button' in p or 'switch' in p)


@pytest.mark.parametrize('angles',[(0,0,0),(.2,.5,.8),(-1.,.3,1.2)])
@pytest.mark.parametrize('finger_width',[.06,.061])
def test_uploaded_press_window_material_and_finger_clearance(monkeypatch,tmp_path,fixture_types,settings,angles,finger_width):
    env,rotation,origin=appliance_panel(monkeypatch,fixture_types,angles=angles)
    mount=np.eye(4);mount[:3,:3]=Rotation.from_euler('z',.4).as_matrix();mount[:3,3]=[.1,.2,.3]
    monkeypatch.setattr(wm,'mount_pose',lambda env:mount)
    p=MujocoV2Planner.__new__(MujocoV2Planner)
    p.env=env;p.evidence=tmp_path;p.sequence=0;p.chassis_binding=None;p.execution_settings=settings
    p.contact_exemption_mode=p.contact_exemption_configured='none';sent=[]
    p.client=NS(call=lambda path,body:sent.append((path,body)) or {})
    world,receipt=p.upload();uploaded=sent[0][1]['cuboids']
    assert sent==[('/world/cuboids',dict(env_id='robocasa365',cuboids=world['cuboids']))]
    audit=world['fixture_contact_exemption'];split=audit['neighbourhood_split'][0]
    assert receipt['fixture_contact_exemption']==audit
    assert json.loads((tmp_path/'world-0001.json').read_text())==world
    assert split['window']['expansion_m']==.035 and split['window']['part_kind']=='press'
    boxes=[b for b in uploaded if ':fixture-window:' in b['name']];assert len(boxes)==4
    transform=np.linalg.inv(mount)
    def planner(local):return (transform@np.r_[origin+rotation@local,1])[:3]
    low=np.array([-.0285,-.02,.0055]);high=np.array([.0685,.02,.0945])
    for x in np.linspace(-.079,.079,49):
        for z in np.linspace(-.229,.229,65):
            expected=not (low[0]<x<high[0] and low[2]<z<high[2])
            assert any(inside(b,planner([x,0,z])) for b in boxes)==expected
    for axis in (0,2):
        for edge in (low[axis],high[axis]):
            for offset in (-.0001,.0001):
                point=np.array([.02,0,.05]);point[axis]=edge+offset
                expected=not (low[axis]<point[axis]<high[axis])
                assert any(inside(b,planner(point)) for b in boxes)==expected
    assert any(inside(b,planner([.02,0,-.10])) for b in boxes)
    # End face reaches 2 mm past the button's front surface; require 5 mm planner clearance.
    finger_center=planner([.02,-.0214-.001+.002-.005,.05])
    finger_rotation=transform[:3,:3]@rotation;finger_half=np.array([finger_width/2,.005,.0175])
    assert all(_obb_clearance(finger_center,finger_rotation,finger_half,*_box(b))>.005 for b in uploaded)
    old=wm.snapshot(env,exempt_body_ids=set(),execution_settings=replace(settings,fixture_press_window_m=.01))
    assert any(_obb_clearance(finger_center,finger_rotation,finger_half,*_box(b))<.005 for b in old['cuboids'])
    full=wm.snapshot(env,exempt_body_ids=set(),execution_settings=settings)
    assert full['unexempted_cuboids']==old['unexempted_cuboids']
    assert audit['world_sha256_after']!=old['world_sha256']


@pytest.mark.parametrize('kind,small,action',[(6,True,'dropped_whole'),(2,True,'dropped_whole'),
    (2,False,'kept_whole'),(6,False,'split')])
def test_press_keeps_existing_small_and_nonbox_rules(monkeypatch,fixture_types,settings,kind,small,action):
    env,_,_=appliance_panel(monkeypatch,fixture_types);m,d=env.sim.model,env.sim.data
    m.geom_type[3]=kind
    if small:
        m.geom_size[3]=[.003]*3;d.geom_xpos[3]=d.geom_xpos[4]+[.01,0,0]
    audit=wm.snapshot(env,execution_settings=settings)['fixture_contact_exemption']
    rows=audit['neighbourhood_'+('dropped_whole' if action=='dropped_whole' else 'kept_whole' if action=='kept_whole' else 'split')]
    assert len(rows)==1 and rows[0]['action']==action
    if action=='kept_whole':assert rows[0]['reason']=='non_box'


@pytest.mark.parametrize('distance,split',[(.009,True),(.01,True),(.011,False)])
def test_press_neighbour_qualification_stays_1cm(monkeypatch,fixture_types,settings,distance,split):
    import mujoco
    env,_,_=appliance_panel(monkeypatch,fixture_types)
    monkeypatch.setattr(mujoco,'mj_geomDistance',lambda *args:distance)
    world=wm.snapshot(env,execution_settings=settings)
    assert bool(world['fixture_contact_exemption']['neighbourhood_split']) is split
    assert (3 in [r['geom_id'] for r in world['inventory']])


@pytest.mark.parametrize('kind,offset,allowed',[('finger',.008,True),('hand',.008,True),
    ('finger',.02,True),('hand',.02,True),('arm',.008,False)])
def test_monitor_press_contact_radius_inside_press_window(monkeypatch,fixture_types,settings,kind,offset,allowed):
    e,rows=neighbourhood_executor(monkeypatch,fixture_types,settings,kind)
    m,d=e.env.sim.model,e.env.sim.data
    f=fixture_types['Microwave']();f.name='microwave';f.root_body=m.body_id2name(4)
    e.env.fxtr=f;m.geoms[2]='microwave_start_button'
    m.geom_size[2]=[.0135,.001,.0095];m.geom_size[3]=[.08,.02,.23]
    d.geom_xpos[2]=[0,-.021,0]
    d.contact[0].pos=np.array([.0135+offset,-.02,0])
    parts=fi.interaction_geoms(e.env,settings);neighbours=fi.neighbourhood_geoms(e.env,settings,parts)
    _,split=fw.neighbourhood_window(e.env,3,neighbours[3],settings)
    window=split['window'];assert window['part_kind']=='press' and window['expansion_m']==.035
    assert np.all(d.contact[0].pos>=window['low_local_m']) and np.all(d.contact[0].pos<=window['high_local_m'])
    if allowed:
        e.monitor()
        sample=next(r for _,r in rows if r.get('event')=='fixture_contact_sample')
        assert sample['contacts'][0]['contact_point_distance_m']==pytest.approx(offset)
        assert sample['contacts'][0]['part_kind']=='press' and sample['contacts'][0]['contact_radius_m']==.035
    else:
        with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()


@pytest.mark.parametrize('field,expected',[('fixture_neighbourhood_window_m',.01),('fixture_press_window_m',.035)])
def test_window_settings_serialized_validated_hashed_and_r5_only(settings,field,expected):
    wire=settings.to_dict();assert wire[field]==expected
    assert es.from_config(wire)==settings
    assert json.loads(settings.canonical_json())[field]==expected
    assert hashlib.sha256(settings.canonical_json().encode()).hexdigest()==settings.sha256
    changed=replace(settings,**{field:expected+.001})
    assert changed.sha256!=settings.sha256
    assert es.request_fields(settings)['execution_settings'][field]==expected
    assert es.receipt_fields(settings)[field]==expected
    for value in (None,True,0,expected+.001,str(expected),float('nan'),float('inf')):
        invalid=dict(wire);invalid[field]=value
        with pytest.raises(ValueError,match=field):es.from_config(invalid)
    invalid=dict(wire);del invalid[field]
    with pytest.raises(ValueError,match=field):es.from_config(invalid)
    for profile in (es.R3_20261025,es.R4_20261027):
        prior=replace(profile,planner_deployment=settings.planner_deployment).to_dict()
        assert field not in prior
        prior[field]=expected
        with pytest.raises(ValueError,match=field):es.from_config(prior)
    assert field not in es.request_fields(None)
