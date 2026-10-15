"""Existing SDK protocol with authenticated, one-use V2 trajectory execution."""
from __future__ import annotations
import json
import math
import urllib.error
import numpy as np
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend
from benchmarks.attention_harness.robocasa_native.agent_actions import ReadOnlyArmPlanRejected
from .identity import IdentityError
from .native_session import NativeV2Service


class V2ReadOnlyPlanRejected(ReadOnlyArmPlanRejected):
    def __init__(self, detail, stop_receipt):
        super().__init__()
        self.detail,self.stop_receipt=detail,stop_receipt
        self.args=('cuRoboV2 rejected before physical dispatch: '+detail,)


def checked_transport(owner,method,path,body,timeout):
    try:return owner.request(method,path,body,timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail=exc.read().decode('utf-8',errors='replace')
        try:code=json.loads(detail).get('error',{}).get('code')
        except (ValueError,AttributeError):code=None
        if method=='POST'and path in {'/plan','/plan/joint'}and exc.code==422 and code=='v2_plan_rejected_no_motion':
            # Only this authenticated typed planning response proves no motion.
            # Finish the original physical Stop barrier before exposing rejection.
            stop=owner.request('POST','/stop',{},timeout=10.)
            raise V2ReadOnlyPlanRejected(detail,stop)from exc
        raise RuntimeError(f'Authenticated OpenArm V2 HTTP {exc.code}: {detail}')from exc


class V2OpenArmActionBackend(OpenArmActionBackend):
    def __init__(self, owner, *, execution_arm, timeout_seconds=90., interrupt_check=None):
        if not isinstance(owner,NativeV2Service)or owner.process is None:
            raise IdentityError('V2 SDK requires the authenticated native service owner')
        identity=owner.attest()
        if not identity['ready']or identity['arm']!=execution_arm:
            raise IdentityError('V2 SDK owner is not ready for the declared arm')
        self.owner=owner
        def transport(method,path,body,timeout):
            return checked_transport(owner,method,path,body,timeout)
        super().__init__(owner.url,execution_arm=execution_arm,transport=transport,
                         timeout_seconds=timeout_seconds,interrupt_check=interrupt_check)
        health=self._call('GET','/health')
        if health.get('planner_generation')!=2 or health.get('trajectory_protocol')!='authenticated-v2-one-use/2':
            raise IdentityError('Existing SDK endpoint does not implement authenticated V2 trajectories')

    def move_arm_to_position(self,x,y,z,*,tolerance,max_steps):
        if (isinstance(tolerance,bool)or not isinstance(tolerance,(int,float))or not math.isfinite(tolerance)
                or tolerance<=0 or type(max_steps)is not int or max_steps<1):
            raise ValueError('Invalid SDK tolerance or action budget')
        plan=self.plan_arm_to_position(x,y,z)
        state=self._state()
        if (plan.get('planner_generation')!=2 or plan.get('planner_nonce')!=self.owner.identity['planner_identity']['nonce']
                or plan.get('epoch')!=state['epoch'] or not isinstance(plan.get('plan_id'),str)):
            self.stop();raise IdentityError('SDK plan is not the current authenticated one-use V2 trajectory')
        receipt=self._call('POST','/trajectory/actions',{'plan_id':plan['plan_id'],'epoch':state['epoch']})
        self._wait_action(receipt)
        actual=np.asarray(self._state()['tcp_world'][self.selection.execution_arm])[:3,3]
        error=float(np.linalg.norm(actual-[x,y,z]));self.planned_motion_receipts.append(dict(plan=plan,final_tcp_error_m=error))
        if error>tolerance:
            self.stop();raise RuntimeError(f'Original physical TCP convergence error {error}m exceeds {tolerance}m')
