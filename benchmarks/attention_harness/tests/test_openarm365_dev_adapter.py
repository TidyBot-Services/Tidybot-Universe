import copy
import sys
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pytest
import mujoco

from openarm365_dev import world
from openarm365_dev.environment import joint_address, PHYSICAL_TO_NATIVE, tcp_pose

@pytest.mark.parametrize('kind,size,expected',[
    (mujoco.mjtGeom.mjGEOM_BOX,[1,2,3],[1,2,3]),
    (mujoco.mjtGeom.mjGEOM_SPHERE,[2,0,0],[2,2,2]),
    (mujoco.mjtGeom.mjGEOM_CAPSULE,[2,3,0],[2,2,5]),
    (mujoco.mjtGeom.mjGEOM_CYLINDER,[2,3,0],[2,2,3]),
    (mujoco.mjtGeom.mjGEOM_ELLIPSOID,[1,2,3],[1,2,3])])
def test_native_primitive_bounds(kind,size,expected):
    center,half=world.shape_box(NS(geom_type=[kind],geom_size=[size]),0)
    assert np.array_equal(center,[0,0,0]);assert np.array_equal(half,expected)

def test_mesh_uses_compiled_vertices_not_visual_bounds():
    m=NS(geom_type=[mujoco.mjtGeom.mjGEOM_MESH],geom_size=[[0,0,0]],geom_dataid=[0],
        mesh_vertadr=[1],mesh_vertnum=[2],mesh_vert=np.array([[999]*3,[-1,-2,-3],[3,4,5],[999]*3]))
    center,half=world.shape_box(m,0)
    assert np.array_equal(center,[1,1,1]);assert np.array_equal(half,[2,3,4])

def test_unknown_collision_shape_fails_closed():
    with pytest.raises(world.WorldError,match='Unsupported'):
        world.shape_box(NS(geom_type=[mujoco.mjtGeom.mjGEOM_HFIELD],geom_size=[[1]*3]),0)

def test_floor_contains_negative_halfspace():
    center,half=world.shape_box(NS(geom_type=[mujoco.mjtGeom.mjGEOM_PLANE],geom_size=[[0]*3]),0)
    assert center[2]+half[2]==0
    assert center[2]-half[2]==-100

def test_complete_inventory_keeps_base_objects_fixture_and_floor(monkeypatch):
    m=NS(ngeom=7,geom_contype=[1,1,1,1,1,1,0],geom_conaffinity=[1,1,1,1,1,0,0],
        geom_bodyid=[0,1,2,3,4,5,6],body_parentid=[0,0,1,0,0,0,0],
        geom_id2name=lambda i:['floor','body','finger','chassis','cup','door','visual'][i],
        body_id2name=lambda i:'body'+str(i),body_name2id=lambda n:1,
        geom_type=[mujoco.mjtGeom.mjGEOM_BOX]*7,geom_size=np.ones((7,3)))
    sim=NS(model=m,data=NS(geom_xmat=np.tile(np.eye(3).ravel(),(7,1)),geom_xpos=np.zeros((7,3))))
    monkeypatch.setattr(world,'mount_pose',lambda env:np.eye(4))
    w=world.snapshot(NS(sim=sim))
    assert {x['geom_id'] for x in w['inventory']}=={0,3,4,5}
    assert {x['geom_id'] for x in w['excluded_robot_geoms']}=={1,2}
    assert w['complete_shape_count']==4

def test_missing_or_ambiguous_joint_is_rejected():
    for names in [[],['robot0_openarm_left_joint1','other_openarm_left_joint1']]:
        with pytest.raises(ValueError,match='Ambiguous'):
            joint_address(NS(model=NS(joint_names=names)),'openarm_left_joint1')

def test_physical_left_and_right_use_distinct_native_endpoints():
    assert PHYSICAL_TO_NATIVE=={'left':'right','right':'left'}
    env=NS(robots=[NS(eef_site_id={'right':0,'left':1})],
        sim=NS(data=NS(site_xmat=np.tile(np.eye(3).ravel(),(2,1)),site_xpos=np.array([[1,2,3],[4,5,6]]))))
    assert np.array_equal(tcp_pose(env,'left')[:3,3],[1,2,3])
    assert np.array_equal(tcp_pose(env,'right')[:3,3],[4,5,6])
    with pytest.raises(ValueError):tcp_pose(env,'both')
