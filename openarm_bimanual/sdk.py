"""Explicit limbs share the SDK emitter, safety monitor and action owner."""
from tidybot_sdk import TidyBotSDK
from benchmarks.attention_harness.robocasa_native.mobile_sdk import _BaseFacade
from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend
import numpy as np


class DualSafetyMonitor(SafetyMonitorBackend):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self._positions={};self.backend.segment_observer=self.observe

    def observe(self):
        value=super().observe()
        for s in ['left','right']:
            p=np.asarray(value[s+'_eef_pos']);j=np.asarray(value[s+'_joint_pos'])
            if p.shape!=(3,) or not np.isfinite(p).all() or not np.isfinite(j).all():self._violate('invalid_limb_state',arm=s)
            if s in self._positions and np.linalg.norm(p-self._positions[s])>self.max_observed_step_m:self._violate('observed_step_exceeds_limit',arm=s,distance_m=float(np.linalg.norm(p-self._positions[s])))
            self._positions[s]=p.copy()
            self.events.append({'kind':'limb_state_sample','arm':s,'eef_position_m':p.tolist()})
        return value


class Limb:
    def __init__(self,side,monitor,emitter):self.side=side;self.monitor=monitor;self.emitter=emitter
    def call(self,op,arguments,callback,*,query=False,gripper=False):
        state=self.monitor.backend._state();handoff=state.get('handoff') or {}
        details={**arguments,'participating_arms':[self.side],'coordination_group':handoff.get('id') if handoff.get('phase') in {'receiving','releasing'} else None,'handoff_phase':handoff.get('phase')}
        wrapped=callback if query else lambda:self.monitor._execute(op,callback,details)
        if not self.emitter:return wrapped()
        return self.emitter.call(source='robot_sdk.'+self.side+('_gripper' if gripper else '_arm'),
            event_type='sdk.arm_plan_query' if query else 'sdk.gripper_command' if gripper else 'sdk.arm_command',
            operation=op,arguments=details,callback=wrapped)
    def plan_to_position(self,x,y,z,*,orientation=None):
        return self.call('plan_to_position',{'target':[x,y,z],'orientation':orientation},lambda:self.monitor.backend.plan_limb(self.side,x,y,z,orientation=orientation),query=True)
    def move_to_position(self,x,y,z,*,tolerance=.004,max_steps=100,orientation=None):
        return self.call('move_to_position',{'target':[x,y,z],'tolerance':tolerance,'max_steps':max_steps,'orientation':orientation},lambda:self.monitor.backend.move_limb(self.side,x,y,z,tolerance=tolerance,max_steps=max_steps,orientation=orientation))
    def move_joints(self,q):
        return self.call('move_joints',{'joint_target':q},lambda:self.monitor.backend.move_joint_group(**{self.side:q}))


class LimbGripper(Limb):
    def grasp(self, *, mode='acquire', jaw_gap_m=None, finger_displacement_m=None,
              desired_contact_force_n=2., drive_force_limit_n=20., request_id=None):
        args=dict(mode=mode,jaw_gap_m=jaw_gap_m,finger_displacement_m=finger_displacement_m,
                  desired_contact_force_n=desired_contact_force_n,drive_force_limit_n=drive_force_limit_n,request_id=request_id)
        return self.call('grasp',args,lambda:self.monitor.backend.grasp(self.side,**args),gripper=True)
    def open(self,*,settle_steps=80):
        return self.call('open',{'settle_steps':settle_steps},lambda:self.monitor.backend.set_limb_gripper(self.side,-1.,settle_steps=settle_steps),gripper=True)
    def close(self,*,settle_steps=80):
        return self.call('close',{'settle_steps':settle_steps},lambda:self.monitor.backend.set_limb_gripper(self.side,1.,settle_steps=settle_steps),gripper=True)


class Bimanual:
    def __init__(self,monitor,emitter):self.monitor=monitor;self.emitter=emitter
    def _call(self,operation,arguments,callback,*,action=False):
        state=self.monitor.backend._state();handoff=state.get('handoff') or {}
        details={**arguments,'participating_arms':['left','right'],'coordination_group':handoff.get('id'),'handoff_phase':handoff.get('phase')}
        wrapped=(lambda:self.monitor._execute(operation,callback,details)) if action else callback
        if not self.emitter:return wrapped()
        return self.emitter.call(source='robot_sdk.bimanual',event_type='sdk.handoff' if operation in {'begin_handoff','handoff'} else 'sdk.bimanual_control',operation=operation,arguments=details,callback=wrapped)
    def state(self):return self._call('state',{},self.monitor.backend._state)
    def move_joints(self,*,left=None,right=None):
        return self._call('move_joints',{'left':left,'right':right},lambda:self.monitor.backend.move_joint_group(left=left,right=right),action=True)
    def begin_handoff(self,donor,receiver):return self._call('begin_handoff',{'donor':donor,'receiver':receiver},lambda:self.monitor.backend.handoff(donor,receiver,begin=True))
    def handoff(self,donor,receiver):return self._call('handoff',{'donor':donor,'receiver':receiver},lambda:self.monitor.backend.handoff(donor,receiver),action=True)
    def stop(self):return self._call('stop',{},self.monitor.backend.stop)
    def cancel(self,action_id):return self._call('cancel',{'action_id':action_id},lambda:self.monitor.backend.cancel(action_id))
    def reset(self):
        def callback():
            receipt=self.monitor.backend.reset();self.monitor._positions={};self.monitor._last_position=None;return receipt
        return self._call('reset',{},callback)


class BimanualSDK(TidyBotSDK):
    def __init__(self,perception,monitor,event_sink,clock):
        super().__init__(perception,event_sink=event_sink,clock=clock)
        emitter=self.arm._emitter
        for s in ['left','right']:
            setattr(self,s+'_arm',Limb(s,monitor,emitter));setattr(self,s+'_gripper',LimbGripper(s,monitor,emitter))
        self.base=_BaseFacade(monitor,emitter);self.bimanual=Bimanual(monitor,emitter)
    def describe(self):
        value=super().describe()
        for s in ['left','right']:
            value[s+'_arm']=('plan_to_position','move_to_position','move_joints');value[s+'_gripper']=('open','close','grasp')
        value['bimanual']=('state','move_joints','begin_handoff','handoff','stop','cancel','reset');value['base']=('move_delta',)
        return value
