"""Geometry export must conservatively contain the actual transformed shape."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from curobo_v2.sapien_world import box_from_vertices, transformed_box, WorldError


def test_scaled_mesh_box_contains_every_vertex_after_frame_transform():
    vertices = np.array([[-1., -2., -.1], [3., 1., .2], [.2, 4., .7]])
    scale = np.array([.02, .05, .3])
    center, half = box_from_vertices(vertices, scale)
    shape = np.eye(4);shape[:3,:3] = Rotation.from_euler('xyz',[.3,.1,-.8]).as_matrix();shape[:3,3] = [1,2,.3]
    frame = np.eye(4);frame[:3,:3] = Rotation.from_euler('z',.7).as_matrix();frame[:3,3] = [-.2,1,0]
    box = transformed_box('mesh',center,half,shape,frame)
    rotation = Rotation.from_quat(np.array(box['quat_wxyz'])[[1,2,3,0]]).as_matrix()
    actual = (frame @ shape @ np.c_[vertices*scale,np.ones(len(vertices))].T).T[:,:3]
    local = (actual-np.array(box['center'])) @ rotation
    assert np.all(abs(local) <= np.array(box['half_size'])+1e-7)


def test_world_to_mount_box_roundtrip():
    mount=np.eye(4);mount[:3,:3]=Rotation.from_euler('z',1.1).as_matrix();mount[:3,3]=[-2.,3.,.8]
    shape=np.eye(4);shape[:3,3]=[.3,.1,0]
    result=transformed_box('fixture',[0,0,0],[.1,.2,.3],mount@shape,np.linalg.inv(mount))
    np.testing.assert_allclose(result['center'],[.3,.1,0],atol=1e-7)
    np.testing.assert_allclose(np.abs(result['quat_wxyz']),[1,0,0,0],atol=1e-7)


@pytest.mark.parametrize('vertices', [[], [[1,2]], [[0,float('nan'),0]]])
def test_invalid_mesh_cannot_be_omitted(vertices):
    with pytest.raises((WorldError,ValueError)):
        box_from_vertices(vertices,[1,1,1])


def test_nonrigid_transform_rejected():
    transform=np.eye(4);transform[0,0]=2
    with pytest.raises(WorldError):
        transformed_box('shape',[0,0,0],[1,1,1],transform,np.eye(4))


def test_flat_mesh_keeps_collision_skin():
    _,half=box_from_vertices([[0,0,0],[1,1,0]],[1,1,1])
    assert half[2]>0
