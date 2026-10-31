"""Distance to collision solids in the measured geom frame."""
import numpy as np
from scipy.optimize import brentq
from scipy.spatial import ConvexHull


def point_geom_distance(model, data, gid, point):
    import mujoco
    p=np.asarray(data.geom_xmat[gid]).reshape(3,3).T@(np.asarray(point,float)-data.geom_xpos[gid])
    size=np.asarray(model.geom_size[gid],float);kind=int(model.geom_type[gid]);g=mujoco.mjtGeom
    if not np.isfinite(p).all():raise RuntimeError('Nonfinite fixture contact point')
    if kind==int(g.mjGEOM_BOX):return float(np.linalg.norm(np.maximum(abs(p)-size,0)))
    if kind==int(g.mjGEOM_SPHERE):return max(0.,float(np.linalg.norm(p)-size[0]))
    if kind==int(g.mjGEOM_CAPSULE):
        p[2]-=np.clip(p[2],-size[1],size[1])
        return max(0.,float(np.linalg.norm(p)-size[0]))
    if kind==int(g.mjGEOM_CYLINDER):
        return float(np.linalg.norm(np.maximum([np.linalg.norm(p[:2])-size[0],abs(p[2])-size[1]],0)))
    if kind==int(g.mjGEOM_ELLIPSOID):
        if np.sum((p/size)**2)<=1:return 0.
        s2=size*size
        t=brentq(lambda t:np.sum((size*p/(t+s2))**2)-1,0.,float(np.linalg.norm(size*p)))
        return float(np.linalg.norm(p-s2*p/(t+s2)))
    if kind==int(g.mjGEOM_MESH):
        mid=int(model.geom_dataid[gid]);start=int(model.mesh_vertadr[mid]);count=int(model.mesh_vertnum[mid])
        vertices=np.asarray(model.mesh_vert[start:start+count],float)
        hull=ConvexHull(vertices)
        if np.max(hull.equations[:,:3]@p+hull.equations[:,3])<=0:return 0.
        minimum=float('inf')
        for a,b,c in vertices[hull.simplices]:
            ab=b-a;ac=c-a;normal=np.cross(ab,ac);normal/=np.linalg.norm(normal)
            projection=p-normal*np.dot(p-a,normal)
            uv=np.linalg.solve([[ab@ab,ab@ac],[ab@ac,ac@ac]],[ab@(projection-a),ac@(projection-a)])
            if min(uv)>=0 and sum(uv)<=1:minimum=min(minimum,float(np.linalg.norm(p-projection)))
            for x,y in ((a,b),(b,c),(c,a)):
                edge=y-x;closest=x+np.clip((p-x)@edge/(edge@edge),0,1)*edge
                minimum=min(minimum,float(np.linalg.norm(p-closest)))
        return minimum
    raise RuntimeError('Unsupported fixture part collision geometry: '+str(kind))
