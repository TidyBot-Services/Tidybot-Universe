"""R5 local planner windows in measured fixture geometry frames."""
from itertools import product
import numpy as np
from .fixture_distance import point_geom_distance
from .fixture_interaction import PRESS_PATTERNS,is_press_part

WHOLE_EXTENT_M = .03


def local_box(model, gid):
    from openarm365_dev.world import shape_box
    center, half = shape_box(model, gid)
    return center, half, np.asarray(list(product((-1., 1.), repeat=3)))*half+center


def neighbourhood_window(env, gid, neighbour, settings):
    m,d=env.sim.model,env.sim.data
    center,half,corners=local_box(m,gid)
    rotation=np.asarray(d.geom_xmat[gid]).reshape(3,3);origin=np.asarray(d.geom_xpos[gid])
    world=corners@rotation.T+origin
    extents=[dict(part_geom_id=p['part_geom_id'],part_name=p['part_name'],
        maximum_distance_m=max(point_geom_distance(m,d,p['part_geom_id'],v) for v in world))
        for p in neighbour['nearby_parts']]
    extent=min(extents,key=lambda p:p['maximum_distance_m'])
    audit=dict(neighbour,extent_half_size_m=half.tolist(),extent_center_local_m=center.tolist(),
        extent_samples=8,extent_part_distances=extents,maximum_extent_distance_m=extent['maximum_distance_m'],
        whole_extent_limit_m=WHOLE_EXTENT_M)
    if extent['maximum_distance_m']<=WHOLE_EXTENT_M:
        return [],dict(audit,action='dropped_whole',extent_part_geom_id=extent['part_geom_id'])
    if int(m.geom_type[gid])!=6:
        return None,dict(audit,action='kept_whole',reason='non_box')
    # One footprint avoids exempting the material between distinct interaction parts.
    pid=neighbour['part_geom_id'];_,_,pv=local_box(m,pid)
    press=is_press_part(neighbour)
    kind='press' if press else 'non_press'
    expansion=settings.fixture_press_window_m if press else settings.fixture_neighbourhood_window_m
    pr=np.asarray(d.geom_xmat[pid]).reshape(3,3)
    footprint=(pv@pr.T+np.asarray(d.geom_xpos[pid])-origin)@rotation
    thickness=int(np.argmin(half));axes=[i for i in range(3) if i!=thickness]
    lo=-half;hi=half
    low=np.maximum(lo,footprint.min(axis=0)-expansion)
    high=np.minimum(hi,footprint.max(axis=0)+expansion)
    low[thickness]=lo[thickness];high[thickness]=hi[thickness]
    if np.any(high<=low):return None,dict(audit,action='kept_whole',reason='empty_window')
    a,b=axes;bounds=[]
    for axis,start,end,other_low,other_high in (
        (a,lo[a],low[a],lo[b],hi[b]),(a,high[a],hi[a],lo[b],hi[b]),
        (b,lo[b],low[b],low[a],high[a]),(b,high[b],hi[b],low[a],high[a])):
        if end<=start:continue
        other=b if axis==a else a;l=lo.copy();h=hi.copy()
        l[axis]=start;h[axis]=end;l[other]=other_low;h[other]=other_high
        bounds.append(dict(center_local_m=((l+h)/2).tolist(),half_size=((h-l)/2).tolist()))
    return bounds,dict(audit,action='split',original_half_size=half.tolist(),
        window=dict(part_geom_id=pid,part_name=neighbour['part_name'],selection='nearest_part',thickness_axis=thickness,
            footprint_axes=axes,expansion_m=expansion,part_kind=kind,low_local_m=low.tolist(),high_local_m=high.tolist()),
        replacement_boxes=bounds)
