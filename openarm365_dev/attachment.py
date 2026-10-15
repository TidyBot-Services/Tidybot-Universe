"""Measured bilateral grasp and complete carried-object geometry, owner only."""
import numpy as np
import mujoco
from .world import is_descendant
from .environment import tcp_pose

def bilateral_contacts(env):
    sim=env.sim;objects=set(getattr(env,'obj_body_id',{}).values());result={'left':{},'right':{}}
    for index,c in enumerate(sim.data.contact):
        if c.dist>0:continue
        force=np.zeros(6);mujoco.mj_contactForce(sim.model._model,sim.data._data,index,force)
        if force[0]<=0:continue
        for fg,og in [(int(c.geom1),int(c.geom2)),(int(c.geom2),int(c.geom1))]:
            name=sim.model.geom_id2name(fg) or '';other=int(sim.model.geom_bodyid[og])
            for side in ('left','right'):
                if f'openarm_{side}_' not in name:continue
                finger=next((f for f in ('left','right') if f'_{f}_finger_' in name),None)
                if finger is None:continue
                for obj in objects:
                    if is_descendant(sim.model,other,obj):result[side].setdefault(obj,set()).add(finger)
    return {side:{obj for obj,fingers in values.items() if fingers=={'left','right'}} for side,values in result.items()}

def object_pose(env,bid):
    t=np.eye(4);t[:3,:3]=env.sim.data.body_xmat[bid].reshape(3,3);t[:3,3]=env.sim.data.body_xpos[bid];return t

def planner_context(env,signature,world,tools):
    if signature is None:return None
    side=signature['holder'];bid=signature['target_id']
    from .environment import mount_pose
    local=np.linalg.inv(mount_pose(env));target=object_pose(env,bid);tcp=tcp_pose(env,side)
    support_ids=set();m=env.sim.model
    for c in env.sim.data.contact:
        if c.dist>0:continue
        bodies=[int(m.geom_bodyid[g]) for g in (c.geom1,c.geom2)]
        for first,other in [bodies,bodies[::-1]]:
            if is_descendant(m,first,bid) and not is_descendant(m,other,bid):support_ids.add(other)
    context=dict(signature,object_pose=(local@target).tolist(),tcp_object_transform=(np.linalg.inv(tcp)@target).tolist(),
        tool_frame=f'openarm_{side}_hand_tcp',
        shape_names=[c['name'] for c in world['inventory'] if is_descendant(m,c['body_id'],bid)],
        support_names=[c['name'] for c in world['inventory'] if c['body_id'] in support_ids])
    from curobo_v2.attachment import SingleArmAttachment
    SingleArmAttachment(context,world['cuboids'],tools)
    return context
