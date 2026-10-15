"""Complete conservative world from actual MuJoCo collision geoms.

Only bodies represented by the authenticated OpenArm URDF are omitted. The
stationary Tidyverse chassis, floor, articulated fixtures and all objects remain.
"""
import hashlib
import json
import numpy as np
from curobo_v2.sapien_world import WorldError, box_from_vertices, transformed_box
from .environment import mount_pose

def shape_box(model, gid, plane_extent=100.):
    import mujoco
    kind=int(model.geom_type[gid]);size=np.asarray(model.geom_size[gid],float)
    g=mujoco.mjtGeom
    if kind==int(g.mjGEOM_BOX): return np.zeros(3),size
    if kind==int(g.mjGEOM_SPHERE): return np.zeros(3),np.full(3,size[0])
    if kind==int(g.mjGEOM_ELLIPSOID): return np.zeros(3),size
    if kind==int(g.mjGEOM_CAPSULE): return np.zeros(3),np.array([size[0],size[0],size[1]+size[0]])
    if kind==int(g.mjGEOM_CYLINDER): return np.zeros(3),np.array([size[0],size[0],size[1]])
    if kind==int(g.mjGEOM_PLANE):
        return np.array([0.,0.,-plane_extent/2]),np.array([plane_extent,plane_extent,plane_extent/2])
    if kind==int(g.mjGEOM_MESH):
        mid=int(model.geom_dataid[gid]);start=int(model.mesh_vertadr[mid]);count=int(model.mesh_vertnum[mid])
        if mid<0 or count<=0: raise WorldError('Invalid actual MuJoCo mesh')
        return box_from_vertices(model.mesh_vert[start:start+count],np.ones(3))
    raise WorldError('Unsupported actual MuJoCo collision geometry: '+str(kind))

def is_descendant(model, bid, root):
    while bid>0:
        if bid==root:return True
        bid=int(model.body_parentid[bid])
    return root==0

def snapshot(env, *, chassis_binding=None):
    sim=env.sim;m=sim.model;root=m.body_name2id('robot0_openarm_body_link0')
    transform=np.linalg.inv(mount_pose(env));cuboids=[];inventory=[];excluded=[]
    for gid in range(m.ngeom):
        if not (m.geom_contype[gid] or m.geom_conaffinity[gid]):continue
        bid=int(m.geom_bodyid[gid]);name=m.geom_id2name(gid) or 'unnamed'
        chassis=False
        if chassis_binding and name==chassis_binding['native_geom']:
            expected_center=np.asarray(chassis_binding['center_in_planner_frame'],float)
            actual_center=(transform@np.r_[sim.data.geom_xpos[gid],1])[:3]
            if (int(m.geom_type[gid])!=6 or not np.array_equal(m.geom_size[gid],chassis_binding['half_size'])
                    or np.linalg.norm(actual_center-expected_center)>1e-5
                    or not np.allclose(transform[:3,:3]@sim.data.geom_xmat[gid].reshape(3,3),np.eye(3),atol=1e-5)):
                raise WorldError('Native chassis geometry differs from authenticated robot URDF')
            chassis=True
        if is_descendant(m,bid,root) or chassis:
            excluded.append({'geom_id':gid,'name':name,'reason':'authenticated OpenArm full robot model'})
            continue
        center,half=shape_box(m,gid)
        pose=np.eye(4);pose[:3,:3]=sim.data.geom_xmat[gid].reshape(3,3);pose[:3,3]=sim.data.geom_xpos[gid]
        box=transformed_box(f'mujoco:{gid}:{name}',center,half,pose,transform)
        cuboids.append(box);inventory.append({'name':box['name'],'geom_id':gid,'body_id':bid,
            'body_name':m.body_id2name(bid),'geom_type':int(m.geom_type[gid]),'source':'actual MuJoCo collision geometry'})
    if not cuboids or len(cuboids)>4096:raise WorldError('Collision world empty or exceeds authenticated endpoint bounds')
    if chassis_binding and sum(x['name']==chassis_binding['native_geom'] for x in excluded)!=1:
        raise WorldError('Authenticated chassis shape missing or ambiguous')
    identity={'cuboids':cuboids,'planner_from_world':transform.tolist()}
    return {**identity,'inventory':inventory,'excluded_robot_geoms':excluded,
        'complete_shape_count':len(cuboids),'world_sha256':hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()}
