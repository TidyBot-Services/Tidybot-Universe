"""SDK mobile actions require explicit capability and use real joint drives."""
import math
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
from tidybot_sdk import CapabilityNotAvailableError


class MobileOpenArmActionBackend(OpenArmActionBackend):
    def _call(self, method, path, body=None, timeout=None):
        result=super()._call(method,path,body,timeout)
        if path=='/health': self._attested_health=result
        return result

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        health=self._attested_health
        if 'mobile_base_joint_drive' not in health.get('capabilities',[]):
            raise CapabilityNotAvailableError('Mobile OpenArm requires an attested joint-driven chassis')

    def move_base_delta(self, dx, dy, dtheta, *, frame='local'):
        values=[dx,dy,dtheta]
        if any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in values):
            raise ValueError('Base delta requires finite dx/dy meters and yaw radians')
        if math.hypot(dx,dy)>.3 or abs(dtheta)>.5:
            raise ValueError('Base delta exceeds the preserved safety envelope')
        if frame not in {'local','global'}:raise ValueError('Base frame must be local or global')
        state=self._state()
        if frame=='local' and state.get('base',{}).get('world_pose') is not None:
            import numpy as np
            world=np.asarray(state['base']['world_pose'])[:2,:2]@np.array([dx,dy])
            values=[*world.tolist(),dtheta]
        receipt=self._call('POST','/base/actions',{'delta':values,'epoch':state['epoch']})
        return self._wait_action(receipt)

    def stop(self):
        import time
        receipt=super().stop()
        limit=time.monotonic()+1.
        while time.monotonic()<limit:
            state=self._transport("GET","/state",None,2.)
            base=state.get("base")
            if not base:break
            if max(abs(v) for v in base["qvel"])<=.005:
                receipt["base_physical_rest_confirmed"]=True
                receipt["base_stopped_state"]=base
                return receipt
            time.sleep(.02)
        receipt["base_physical_rest_confirmed"]=False
        return receipt
