"""Bimanual SDK transport for the certified one-use V2 owner."""
import math
import numpy as np
from .identity import IdentityError
from .native_session import NativeV2Service
from .openarm_sdk import checked_transport
from openarm_bimanual.backend import BimanualActionBackend
from benchmarks.attention_harness.robocasa_native.openarm_backend import OpenArmActionBackend


class V2BimanualActionBackend(BimanualActionBackend):
    def __init__(self,owner,*,execution_arm='both',timeout_seconds=90.,interrupt_check=None):
        if not isinstance(owner,NativeV2Service)or owner.process is None:
            raise IdentityError('Bimanual SDK requires its authenticated native owner')
        identity=owner.attest()
        if not identity['ready']or identity['arm']!=execution_arm or identity.get('grasp_schema')!='openarm.grasp/2':
            raise IdentityError('Bimanual owner selection or grasp identity differs')
        self.owner=owner
        OpenArmActionBackend.__init__(self,owner.url,execution_arm=execution_arm,
            timeout_seconds=timeout_seconds,interrupt_check=interrupt_check,
            transport=lambda m,p,b,t:checked_transport(owner,m,p,b,t))
        if (self._attested_health.get('native_adapter')!='openarm-bimanual-v2/1'
                or self._attested_health.get('trajectory_protocol')!='authenticated-v2-one-use/2'):
            raise IdentityError('Bimanual native trajectory protocol unavailable')

    def execute_plan(self,plan):
        state=self._state()
        if (plan.get('planner_generation')!=2 or plan.get('epoch')!=state['epoch']
                or plan.get('planner_nonce')!=self.owner.identity['planner_identity']['nonce']
                or not isinstance(plan.get('plan_id'),str)):
            self.stop();raise IdentityError('Plan belongs to an unrelated owner or epoch')
        receipt=self._call('POST','/trajectory/actions',dict(plan_id=plan['plan_id'],epoch=state['epoch']))
        return self._wait_action(receipt)

    def move_limb(self,side,x,y,z,*,tolerance=.004,max_steps=100,orientation=None):
        self._side(side)
        if (isinstance(tolerance,bool)or not isinstance(tolerance,(int,float))
                or not math.isfinite(tolerance)or not 0<tolerance<=.004 or type(max_steps)is not int or max_steps<1):
            raise ValueError('Preserved 4mm tolerance and positive action budget required')
        plan=self.plan_limb(side,x,y,z,orientation=orientation)
        action=self.execute_plan(plan)
        error=float(np.linalg.norm(np.asarray(self._state()['tcp_world'][side])[:3,3]-[x,y,z]))
        result=dict(arm=side,plan=plan,actions=[action],final_tcp_error_m=error)
        self.planned_motion_receipts.append(result)
        if error>tolerance:
            self.stop();raise RuntimeError('Native TCP convergence exceeds preserved tolerance')
        return result

    def move_joint_group(self,*,left=None,right=None):
        selected={s:q for s,q in [('left',left),('right',right)]if q is not None}
        if not selected:raise ValueError('At least one explicit arm target required')
        for side in selected:self._side(side)
        state=self._state();names=self._attested_health['planner_joint_names']
        target=[state['qpos'][state['joint_order'].index(n)]for n in names]
        from .native_requests import finite_vector
        for side,q in selected.items():
            q=finite_vector(q,7,side)
            for i,v in enumerate(q,1):target[names.index(f'openarm_{side}_joint{i}')]=float(v)
        plan=self._call('POST','/plan/joint',dict(arm='both'if len(selected)==2 else next(iter(selected)),
            epoch=state['epoch'],joint_names=names,target_qpos=target))
        action=self.execute_plan(plan)
        result=dict(plan=plan,actions=[action],participating_arms=sorted(selected))
        self.planned_motion_receipts.append(result);return result

    def move_base_delta(self,*args,**kwargs):
        raise ValueError('This deployment freezes the chassis in place')
