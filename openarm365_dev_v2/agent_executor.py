"""Local authenticated pre-dispatch checks, without weakening motion guards."""
import math
from openarm365_dev.executor import NativeExecutor
from openarm365_dev.base_motion import BasePathRejected
from .agent_rejection import RecoverablePlanRejection
from .agent_probe import state_fingerprint


class AgentExecutor(NativeExecutor):
    def reject(self, reason, diagnostics=None):
        self.check_live()
        self.planner.client.attest()
        self.planner.ledger.invalidate('pre_dispatch_rejection')
        raise RecoverablePlanRejection(dict(reason=reason, validation=diagnostics or {}))

    def move_delta(self, side, dx=0., dy=0., dz=0., rotation_delta=None, *, request_id, grasp_search=None, speed=1.):
        rotation=[0.,0.,0.] if rotation_delta is None else rotation_delta
        if (not isinstance(rotation,(tuple,list)) or len(rotation)!=3
                or any(type(x) not in (int,float) or not math.isfinite(x) for x in [dx,dy,dz,*rotation])):
            return self.reject('invalid_delta')
        if any(abs(v)>getattr(self,'arm_delta_limit',.05) for v in (dx,dy,dz)) or any(abs(v)>.5 for v in rotation):
            return self.reject('action_delta_limit')
        if speed!=1 and (getattr(self,'arm_motion','short')!='long' or type(speed) not in (int,float) or not math.isfinite(speed) or not .25<=speed<=1):
            return self.reject('invalid_speed')
        return super().move_delta(side,dx,dy,dz,rotation_delta=rotation,request_id=request_id,grasp_search=grasp_search,speed=speed)

    def base_move(self, dx, dy, dyaw, *, request_id):
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (dx,dy,dyaw))
                or math.hypot(dx,dy)>getattr(self,'base_limits',(.20,.30))[0] or abs(dyaw)>getattr(self,'base_limits',(.20,.30))[1]):
            return self.reject('base_delta_limit')
        before=state_fingerprint(self.env,self)
        try:return super().base_move(dx,dy,dyaw,request_id=request_id)
        except BasePathRejected as exc:
            if self.failed or before!=state_fingerprint(self.env,self):raise
            return self.reject('base_path_rejected',getattr(exc,'collision_check',{}))
