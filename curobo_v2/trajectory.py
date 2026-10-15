"""One-use V2 trajectories bound to physical state, world, owner and epoch."""
from __future__ import annotations
from copy import deepcopy
import math
import secrets
import time
import numpy as np
from .client import PlanningRejected


def world_matches(expected, current, *, position_tolerance=1e-4, angle_tolerance=1e-4):
    """Geometry and names stay exact; only bounded floating-point pose drift is allowed."""
    a = {c['name']:c for c in expected}; b = {c['name']:c for c in current}
    if len(a)!=len(expected) or len(b)!=len(current) or set(a)!=set(b):return False
    for name,x in a.items():
        y=b[name]
        if not np.array_equal(x['half_size'],y['half_size']):return False
        ca=np.asarray(x['center'],float);cb=np.asarray(y['center'],float)
        if ca.shape!=(3,)or cb.shape!=(3,)or not np.isfinite(ca).all()or not np.isfinite(cb).all():return False
        if np.linalg.norm(ca-cb)>position_tolerance:return False
        qa=np.asarray(x.get('quat_wxyz',[1,0,0,0]),float);qb=np.asarray(y.get('quat_wxyz',[1,0,0,0]),float)
        if qa.shape!=(4,)or qb.shape!=(4,)or not np.isfinite(qa).all() or not np.isfinite(qb).all():return False
        if abs(np.linalg.norm(qa)-1)>1e-5 or abs(np.linalg.norm(qb)-1)>1e-5:return False
        angle=2*math.acos(min(1.,abs(float(qa@qb))/(np.linalg.norm(qa)*np.linalg.norm(qb))))
        if angle>angle_tolerance:return False
    return True


class TrajectoryLedger:
    def __init__(self, *, clock=time.monotonic, lifetime=8.):
        self.clock,self.lifetime=clock,lifetime
        self.plans={}

    def invalidate(self, reason):
        for row in self.plans.values():
            if row['status']=='ready':row.update(status='invalid',reason=reason)

    def issue(self, response, *, epoch, state, world, identity, attachment):
        from .sweep import verify_certificate
        verify_certificate(response)
        if (response.get('status')!='success' or not response.get('validation',{}).get('collision_free')
                or not response.get('validation',{}).get('joint_limits')
                or identity.get('runtime',{}).get('generation')!=2 or not identity.get('nonce')):
            raise PlanningRejected('Only a validated authenticated V2 trajectory may be issued')
        names=response['joint_names'];trajectory=np.asarray(response['trajectory'],float);start=np.asarray(state,float)
        if (len(names)!=len(set(names)) or trajectory.ndim!=2 or len(trajectory)<2
                or trajectory.shape[1]!=len(names) or start.shape!=(len(names),)
                or not np.isfinite(trajectory).all() or np.max(abs(start-trajectory[0]))>1e-4):
            raise PlanningRejected('V2 trajectory lacks a consistent authoritative start/joint mapping')
        pid=secrets.token_hex(16)
        self.plans[pid]=dict(status='ready',issued=self.clock(),epoch=epoch,start=start.copy(),
            response=deepcopy(response),world=deepcopy(world),identity=deepcopy(identity),attachment=deepcopy(attachment))
        return pid

    def claim(self, plan_id, *, epoch, state, world, identity, attachment):
        row=self.plans.get(plan_id)
        if not row or row['status']!='ready':raise PlanningRejected('Unknown, consumed or invalidated V2 trajectory')
        # Every attempt consumes the capability, including a stale rejection.
        row['status']='consumed'
        q=np.asarray(state,float)
        reasons=[]
        if epoch!=row['epoch']:reasons.append('epoch')
        if self.clock()-row['issued']>self.lifetime:reasons.append('deadline')
        if q.shape!=row['start'].shape or not np.isfinite(q).all()or np.max(abs(q-row['start']))>.005:reasons.append('joint_state')
        if identity!=row['identity']:reasons.append('service_identity')
        if attachment!=row['attachment']:reasons.append('attachment')
        if not world_matches(row['world'],world):reasons.append('kitchen_world')
        if reasons:
            row.update(status='invalid',reason=','.join(reasons))
            raise PlanningRejected('V2 trajectory is stale ('+','.join(reasons)+'); stop and request a new plan')
        from .sweep import verify_certificate
        verify_certificate(row['response'])
        return deepcopy(row['response'])
