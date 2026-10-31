"""Offline D6c executor radius, audit and profile boundaries."""
from types import SimpleNamespace as NS
import numpy as np
import pytest
from curobo_v2 import execution_settings as es,fixture_interaction as fi,fixture_telemetry as ft
from openarm365_dev_v2.tests.test_fixture_phase1b import neighbourhood_executor
from openarm365_dev_v2.tests.test_fixture_phase1e import (
    PRESS_PARTS,OTHER_PARTS,test_window_settings_serialized_validated_hashed_and_r5_only as check_settings)
from openarm365_dev_v2.tests.test_fixture_interaction import (
    fixture_types,settings,r4_deployment,base,fake_mujoco_if_unavailable)


def appliance_executor(monkeypatch,fixture_types,settings,cls='Microwave',suffix='start_button',kind='finger',offset=.02):
    e,rows=neighbourhood_executor(monkeypatch,fixture_types,settings,kind,point=.05+offset)
    m=e.env.sim.model;f=fixture_types[cls]();f.name='appliance';f.root_body=m.body_id2name(4)
    e.env.fxtr=f;m.geoms[2]='appliance_'+suffix
    return e,rows


def check_record(row,kind,radius,distance):
    assert row['part_geom_id']==2 and row['part_name'].startswith('appliance_')
    assert row['part_kind']==kind and row['contact_radius_m']==radius
    assert row['contact_point_distance_m']==pytest.approx(distance)


@pytest.mark.parametrize('cls,suffix',PRESS_PARTS)
@pytest.mark.parametrize('kind',['finger','hand'])
@pytest.mark.parametrize('direct',[False,True])
def test_press_contact_audit_in_samples_physics_and_receipts(monkeypatch,fixture_types,settings,cls,suffix,kind,direct):
    e,rows=appliance_executor(monkeypatch,fixture_types,settings,cls,suffix,kind)
    if direct:
        e.env.sim.data.contact[0].geom2=2;e.env.sim.data.contact[0].pos=np.array([.05,0,0])
    e.monitor();contact_kind='part' if direct else 'neighbourhood';distance=0. if direct else .02
    sample=next(r for _,r in rows if r.get('event')=='fixture_contact_sample')
    physics=next(r for path,r in rows if path==e.physics)
    receipt=ft.receipt(e)['fixture_pilot']
    for record in (sample['contacts'][0],sample[contact_kind+'_contacts'][0],physics['fixture_contacts'][0],
            physics['fixture_'+contact_kind+'_contacts'][0],receipt['parts' if direct else 'neighbourhood'][0]):
        check_record(record,'press',.035,distance)


@pytest.mark.parametrize('cls,suffix',OTHER_PARTS)
@pytest.mark.parametrize('offset,allowed',[(.008,True),(.02,False)])
def test_nonpress_keeps_1cm_radius(monkeypatch,fixture_types,settings,cls,suffix,offset,allowed):
    e,rows=appliance_executor(monkeypatch,fixture_types,settings,cls,suffix,offset=offset)
    if allowed:
        e.monitor();sample=next(r for _,r in rows if r.get('event')=='fixture_contact_sample')
        check_record(sample['contacts'][0],'other',.01,offset)
        check_record(ft.receipt(e)['fixture_pilot']['neighbourhood'][0],'other',.01,offset)
    else:
        with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()


@pytest.mark.parametrize('kind',['finger','hand'])
def test_press_4cm_is_fatal(monkeypatch,fixture_types,settings,kind):
    e,_=appliance_executor(monkeypatch,fixture_types,settings,kind=kind,offset=.04)
    with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()


@pytest.mark.parametrize('kind',['arm','chassis'])
@pytest.mark.parametrize('direct',[False,True])
def test_arm_and_chassis_stay_fatal_within_1cm(monkeypatch,fixture_types,settings,kind,direct):
    e,_=appliance_executor(monkeypatch,fixture_types,settings,kind='arm',offset=.008)
    if kind=='chassis':e.env.sim.model.geoms[0]=e.planner.chassis_binding['native_geom']
    if direct:e.env.sim.data.contact[0].geom2=2
    with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()


def test_press_radius_does_not_expand_neighbourhood_membership(monkeypatch,fixture_types,settings):
    import mujoco
    e,_=appliance_executor(monkeypatch,fixture_types,settings,offset=.008)
    monkeypatch.setattr(mujoco,'mj_geomDistance',lambda *args:.0101)
    with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()


def test_each_contact_point_checked_independently(monkeypatch,fixture_types,settings):
    e,_=appliance_executor(monkeypatch,fixture_types,settings)
    e.env.sim.data.contact.append(NS(geom1=0,geom2=3,dist=-.001,pos=np.array([.09,0,0])))
    with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()
    assert e._fixture_motion['neighbourhood_contact_count']==1


def test_press_in_range_even_when_nonpress_is_closer(monkeypatch,fixture_types,settings):
    e,rows=appliance_executor(monkeypatch,fixture_types,settings,'Blender','power_button_main')
    m,d=e.env.sim.model,e.env.sim.data
    m.geoms[4]='appliance_knob_speed_main';d.geom_xpos[4]=[.005,0,0]
    e.monitor();sample=next(r for _,r in rows if r.get('event')=='fixture_contact_sample')
    check_record(sample['contacts'][0],'press',.035,.02)
    assert sample['contacts'][0]['pattern']==fi.part_pattern('Blender','power_button_main')


@pytest.mark.parametrize('profile',[None,es.R3_20261025,es.R4_20261027])
def test_press_exemption_remains_r5_only(monkeypatch,fixture_types,settings,profile):
    e,_=appliance_executor(monkeypatch,fixture_types,settings)
    e.execution_settings=profile
    assert fi.fixture_contacts(e.env,profile,e.robot_root)=={}
    with pytest.raises(RuntimeError,match='Unexpected physical'):e.monitor()


@pytest.mark.parametrize('field,expected',[('fixture_contact_radius_m',.01),('fixture_press_contact_radius_m',.035)])
def test_radius_settings_serialized_validated_hashed_and_r5_only(settings,field,expected):
    check_settings(settings,field,expected)
