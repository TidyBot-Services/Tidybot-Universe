"""Measured bilateral grasp and complete carried-object geometry, owner only."""
import json
import os
import time
from pathlib import Path
import numpy as np
import mujoco
from .world import is_descendant
from .environment import tcp_pose

# Resting objects often hover 1.5–6 mm above a support in MuJoCo's contact margin.
SUPPORT_PROXIMITY_M = 0.010
# MuJoCo returns distmax when shapes are farther apart; keep it above the rule.
SUPPORT_DISTANCE_QUERY_MAX_M = 0.011

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

def attachment_dump_enabled():
    value=os.environ.get('ATTN_DIAG_ATTACHMENT_DUMP')
    if value not in (None,'1'):
        raise ValueError('ATTN_DIAG_ATTACHMENT_DUMP must be 1 when set')
    return value=='1'


def nearest_distances(rows,limit=10):
    """Stable nearest-first order for measured geom pairs (pure diagnostic data)."""
    return sorted(rows,key=lambda row:(row['distance_m'],row['object_geom_id'],row['other_geom_id']))[:limit]


def proximity_support_distances(inventory,object_body_id,robot_body_id,
                                geom_bodyid,body_parentid,body_jntnum,distance):
    """Return nearest eligible fixed-body distances, ordered by body id."""
    def descendant(bid,root):
        while bid>0:
            if bid==root:return True
            bid=int(body_parentid[bid])
        return root==0

    def fixed(bid):
        while bid>0:
            if body_jntnum[bid]:return False
            bid=int(body_parentid[bid])
        return True

    object_geoms=sorted({shape['geom_id'] for shape in inventory
        if descendant(int(geom_bodyid[shape['geom_id']]),object_body_id)})
    if not object_geoms:return {}
    result={}
    for shape in sorted(inventory,key=lambda row:(row['body_id'],row['geom_id'])):
        bid=int(shape['body_id'])
        if (descendant(bid,object_body_id) or descendant(bid,robot_body_id)
                or not fixed(bid)):
            continue
        gid=shape['geom_id']
        nearest=min(distance(obj,gid) for obj in object_geoms)
        if nearest<=SUPPORT_PROXIMITY_M:
            result[bid]=min(nearest,result.get(bid,nearest))
    return dict(sorted(result.items()))


def support_decisions(inventory,contacts,support_names,proximity_distances=None):
    """Explain contact and fixed-body proximity support selection."""
    proximity_distances=proximity_distances or {}
    selected=set(support_names);result=[]
    for shape in inventory:
        matches=[c for c in contacts if c['other_body_id']==shape['body_id']]
        eligible=[c for c in matches if c['distance_m']<=0]
        distance=proximity_distances.get(shape['body_id'])
        if shape['name'] in selected and eligible:reason='nonpositive_contact_with_object'
        elif shape['name'] in selected and distance is not None:reason='proximity_within_10mm'
        elif eligible:reason='nonpositive_contact_but_body_not_selected'
        elif matches:reason='positive_distance_contacts_filtered'
        else:reason='no_contact_with_object'
        result.append(dict(name=shape['name'],body_id=shape['body_id'],
            included=shape['name'] in selected,reason=reason,
            contact_indices=[c['contact_index'] for c in matches],
            proximity_distance_m=distance))
    return result


def dump_attachment(env,signature,world,context,attachment,evidence,proximity_distances):
    sim=env.sim;m=sim.model;native_model=m._model;native_data=sim.data._data
    bid=signature['target_id'];robot=m.body_name2id('robot0_openarm_body_link0')
    object_gids=[gid for gid in range(m.ngeom) if is_descendant(m,int(m.geom_bodyid[gid]),bid)]
    object_set=set(object_gids)
    robot_gids={gid for gid in range(m.ngeom) if is_descendant(m,int(m.geom_bodyid[gid]),robot)}
    robot_gids.update(item['geom_id'] for item in world.get('excluded_robot_geoms',()))
    def geom(gid):
        body_id=int(m.geom_bodyid[gid])
        return dict(geom_id=gid,geom_name=m.geom_id2name(gid),
                    body_id=body_id,body_name=m.body_id2name(body_id))
    contacts=[]
    for index,c in enumerate(sim.data.contact):
        g1,g2=int(c.geom1),int(c.geom2)
        if g1 not in object_set and g2 not in object_set:continue
        force=np.zeros(6)
        mujoco.mj_contactForce(native_model,native_data,index,force)
        others=[g for g in (g1,g2) if g not in object_set]
        contacts.append(dict(contact_index=index,geom1=geom(g1),geom2=geom(g2),
            other_body_id=int(m.geom_bodyid[others[0]]) if len(others)==1 else None,
            distance_m=float(c.dist),contact_force_n=float(force[0]),
            contact_force_6d=force.tolist(),
            support_rule='filtered_positive_distance' if c.dist>0 else
                         'eligible_external_body' if len(others)==1 else 'object_internal_contact'))
    distances=[];per_geom=[]
    for gid in object_gids:
        pairs=[]
        for other in range(m.ngeom):
            if other in object_set or other in robot_gids:continue
            distance=float(mujoco.mj_geomDistance(native_model,native_data,gid,other,.05,None))
            pairs.append(dict(object_geom_id=gid,object_geom_name=m.geom_id2name(gid),
                other_geom_id=other,other_geom_name=m.geom_id2name(other),
                other_body_name=m.body_id2name(int(m.geom_bodyid[other])),
                distance_m=distance,at_query_limit=distance>=.05))
        distances.extend(pairs)
        per_geom.append(dict(object_geom=geom(gid),nearest_10=nearest_distances(pairs)))
    payload=dict(event='attachment_dump',holder=signature['holder'],
        target_body=dict(body_id=bid,body_name=m.body_id2name(bid)),
        object_geoms=[geom(gid) for gid in object_gids],contacts=contacts,
        distance_query_limit_m=.05,nearest_10=nearest_distances(distances),
        nearest_10_per_object_geom=per_geom,
        support_names=context['support_names'],
        support_decisions=support_decisions(world['inventory'],contacts,context['support_names'],proximity_distances),
        attachment_geometry=attachment.artifact(),overlapping_cover_cells=None)
    evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    path=evidence/f'attachment-dump-{time.time_ns()}.json'
    path.write_text(json.dumps(payload,indent=2,allow_nan=False)+'\n')
    return path


def retain_declared_supports(context, retention, inventory):
    """Freeze only the first attached declaration; never grow it on later calls.

    ``retention`` belongs to the executor's grasp, shared by its shallow signature
    copies. Returned request evidence is detached from that mutable owner state.
    """
    from copy import deepcopy
    by_name={row['name']:row for row in inventory}
    recomputed=list(context['support_names'])
    if retention['supports'] is None:
        retention['supports']=[dict(name=name, body_id=int(by_name[name]['body_id']),
                                   body_name=by_name[name]['body_name']) for name in recomputed]
    for support in retention['supports']:
        current=by_name.get(support['name'])
        if (current is None or current['body_id']!=support['body_id']
                or current['body_name']!=support['body_name']):
            raise ValueError('Retained support identity changed during attachment')
    result=deepcopy(context)
    result['support_names']=list(dict.fromkeys(recomputed+[row['name'] for row in retention['supports']]))
    result['recomputed_support_names']=recomputed
    result['support_retention']=deepcopy(retention)
    return result


def planner_context(env,signature,world,tools,evidence=None):
    dump=attachment_dump_enabled()
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
    proximity_distances=proximity_support_distances(
        world['inventory'],bid,m.body_name2id('robot0_openarm_body_link0'),
        m.geom_bodyid,m.body_parentid,m.body_jntnum,
        lambda obj,other:float(mujoco.mj_geomDistance(m._model,env.sim.data._data,
            obj,other,SUPPORT_DISTANCE_QUERY_MAX_M,None)))
    support_ids.update(proximity_distances)
    context=dict(signature,object_pose=(local@target).tolist(),tcp_object_transform=(np.linalg.inv(tcp)@target).tolist(),
        tool_frame=f'openarm_{side}_hand_tcp',
        shape_names=[c['name'] for c in world['inventory'] if is_descendant(m,c['body_id'],bid)],
        support_names=[c['name'] for c in sorted(world['inventory'],key=lambda row:(row['body_id'],row['geom_id']))
            if c['body_id'] in support_ids])
    from curobo_v2.attachment import SingleArmAttachment
    attachment=SingleArmAttachment(context,world['cuboids'],tools)
    if dump:
        if evidence is None:raise ValueError('Attachment dump requires an evidence directory')
        dump_attachment(env,signature,world,context,attachment,evidence,proximity_distances)
    return context
