"""Epoch-bound SDK transport with explicit left/right action routing."""
import math, time
import numpy as np
from openarm_mobile.backend import MobileOpenArmActionBackend
from benchmarks.attention_harness.robocasa_native.agent_actions import ReadOnlyArmPlanRejected
from tidybot_sdk import CapabilityNotAvailableError
from .lifecycle import stopped_components


class BimanualActionBackend(MobileOpenArmActionBackend):
    _supports_bimanual = True
    def _call(self, method, path, body=None, timeout=None):
        if method=='POST' and path in {'/handoff','/handoff/begin'}:
            from curobo_v2.native_session import NativeV2Service
            from curobo_v2.deployment import retired_entry
            owner=getattr(self,'owner',None)
            if (not isinstance(owner,NativeV2Service) or owner.url!=self.base_url
                    or owner.process is None or owner.process.poll() is not None):
                retired_entry('unmanaged OpenArm SDK handoff transport')
        return super()._call(method,path,body,timeout)

    def __init__(self,*args,execution_arm='both',**kwargs):
        super().__init__(*args,execution_arm=execution_arm,**kwargs)
        if not {'independent_arms','independent_grippers','physical_handoff'} <= set(self._attested_health.get('capabilities',[])):
            raise CapabilityNotAvailableError('Bimanual task service capability attestation missing')

    def _side(self, side):
        if side not in {'left','right'} or self.selection.execution_arm not in {'both',side}:
            raise ValueError('Explicit arm is not allowed by locked execution mode')
        return side

    def observe(self):
        import io,json
        data=self._call('GET','/observation')
        with np.load(io.BytesIO(data),allow_pickle=False) as o:
            state=json.loads(str(o['state_json']))
            if state.get('robot')!=self.selection.artifact() or state['epoch']!=int(o['epoch']) or state['total_steps']!=int(o['total_steps']):
                raise RuntimeError('Bimanual observation identity or clock mismatch')
            primary='left' if self.selection.execution_arm=='both' else self.selection.execution_arm
            pose=np.asarray(state['tcp_world'][primary])
            result={'robot0_eef_pos':pose[:3,3],'robot0_eef_rot_mat':pose[:3,:3],
                'robot0_joint_pos':np.asarray(state[primary]['qpos']),
                'robot0_gripper_qpos':np.array([state[primary+'_gripper']['driver_position_m']]),
                'base_camera_image':o['rgb'].copy(),'base_camera_depth':o['depth_m'].copy(),
                'base_camera_intrinsics':o['K'].copy(),'base_camera_pose_mat':o['T_world_optical'].copy(),
                'physical_step':np.array(state['total_steps']),'epoch':np.array(state['epoch'])}
            for s in ['left','right']:
                result[s+'_eef_pos']=np.asarray(state['tcp_world'][s])[:3,3]
                result[s+'_joint_pos']=np.asarray(state[s]['qpos'])
                result[s+'_gripper_qpos']=np.array([state[s+'_gripper']['driver_position_m']])
            return result

    def plan_limb(self, side, x,y,z, *, orientation=None):
        self._side(side)
        if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in [x,y,z]):
            raise ValueError('Position requires finite world xyz')
        state=self._state();body={'arm':side,'position':[x,y,z],'epoch':state['epoch']}
        if orientation is not None:body['orientation']=np.asarray(orientation,float).tolist()
        plan=self._call('POST','/plan',body)
        if plan.get('robot')!=self.selection.artifact() or plan.get('arm')!=side or plan.get('epoch')!=state['epoch']:
            raise RuntimeError('Plan identity/arm/epoch mismatch')
        if plan.get('reachable') is not False and not plan.get('collision_check',{}).get('collision_free'):
            raise RuntimeError('Plan lacks both-arm collision attestation')
        self.plan_query_receipts.append(plan);return plan

    def move_limb(self, side, x,y,z, *, tolerance=.004,max_steps=100,orientation=None):
        self._side(side)
        if isinstance(tolerance,bool) or not isinstance(tolerance,(int,float)) or not math.isfinite(tolerance) or not 0<tolerance<=.004 or type(max_steps) is not int or max_steps<1:
            raise ValueError('Motion requires preserved <=4mm tolerance and positive waypoint budget')
        plan=self.plan_limb(side,x,y,z,orientation=orientation)
        if plan.get('reachable') is False:raise ReadOnlyArmPlanRejected()
        state=self._state()
        if state['epoch']!=plan['epoch']:raise RuntimeError('Plan belongs to old epoch')
        indices=[state['joint_order'].index(f'openarm_{side}_joint{i}') for i in range(1,8)]
        start=np.asarray(state[side]['qpos']);points=[]
        for waypoint in plan.get('joint_waypoints',[plan['target_qpos']]):
            goal=np.asarray(waypoint)[indices];count=max(1,math.ceil(float(np.max(abs(goal-start)))))
            points.extend(start+(goal-start)*i/count for i in range(1,count+1));start=goal
        if len(points)>max_steps:raise RuntimeError('Motion exceeds waypoint budget before dispatch')
        receipts=[]
        for point in points:
            receipts.append(self._wait_action(self._call('POST','/actions',{side:point.tolist(),'epoch':state['epoch']})))
            observer=getattr(self,'segment_observer',None)
            if observer:observer()
        actual=np.asarray(self._state()['tcp_world'][side]);error=float(np.linalg.norm(actual[:3,3]-[x,y,z]))
        receipt={'arm':side,'participating_arms':[side],'plan':plan,'final_tcp_error_m':error,'actions':receipts}
        self.planned_motion_receipts.append(receipt)
        if error>tolerance:
            self.stop();raise RuntimeError(f'{side} TCP error {error} exceeds {tolerance}')
        return receipt

    def grasp(self, side, *, mode='acquire', jaw_gap_m=None, finger_displacement_m=None,
              desired_contact_force_n=2., drive_force_limit_n=20., request_id=None):
        import uuid
        from .grasp import SCHEMA
        self._side(side)
        health=self._attested_health
        if health.get('grasp_schema')!=SCHEMA:raise CapabilityNotAvailableError('Grasp v2 service required')
        state=self._state()
        if jaw_gap_m is None and finger_displacement_m is None:
            finger_displacement_m=health['gripper_free_close_m'] if mode=='acquire' else health['grasp_limits'][side]['upper_m']
        body=dict(schema=SCHEMA,arm=side,epoch=state['epoch'],request_id=request_id or str(uuid.uuid4()),
                  mode=mode,drive_force_limit_n=drive_force_limit_n)
        if jaw_gap_m is not None:body['jaw_gap_m']=jaw_gap_m
        if finger_displacement_m is not None:body['finger_displacement_m']=finger_displacement_m
        if mode=='acquire':body.update(target_id=health['native_target_id'],desired_contact_force_n=desired_contact_force_n)
        # Client uses the same unit/limit validator; service remains authoritative.
        from .grasp import GraspCoordinator, Limits
        check=GraspCoordinator({s:Limits(**health['grasp_limits'][s]) for s in ['left','right']},
            epoch=state['epoch'],target_id=health['native_target_id'],stop_callback=lambda reason:None)
        q,cap,desired=check.parse(body)
        needed=self._stable_requirement(side)
        from benchmarks.attention_harness.core.control import EmergencyInterrupt
        try:receipt=self._call('POST','/grasp',body)
        except EmergencyInterrupt:raise
        except Exception:
            try:self.stop()
            finally:raise
        self._grasp_receipt(receipt,body)
        action_id=receipt['id']
        try:action=self._wait_action(receipt)
        except EmergencyInterrupt:raise
        except Exception:
            try:self.stop()
            finally:raise
        self._grasp_receipt(action,body,action_id=action_id,completed=True)
        state=self._grasp_control_state(state['epoch'])
        grippers=state['grasp_control'].get('grippers');f=grippers.get(side) if isinstance(grippers,dict) else None
        acquire=mode=='acquire'
        if (not isinstance(f,dict) or f.get('holding') is not acquire or f.get('grasped') is not acquire
                or not all(type(f.get(k)) in (int,float) and math.isfinite(f[k]) and f[k]==v
                           for k,v in [('drive_force_limit_n',cap),('desired_contact_force_n',desired)])):
            self._grasp_protocol_violation('Grasp receipt disagrees with current holding or force contract')
        if acquire:
            if (f.get('mode')!='hold' or f.get('stable_confirmed') is not True
                    or type(f.get('stable_steps')) is not int or f['stable_steps']<needed
                    or type(f.get('target_id')) is not int or f['target_id']!=body['target_id']):
                self._grasp_protocol_violation('Grasp lacks current stable designated-target holding')
        elif (f.get('mode')!='position' or f.get('action_id')!=action_id
                or type(f.get('goal_m')) not in (int,float) or not math.isfinite(f['goal_m'])
                or not math.isclose(f['goal_m'],q,rel_tol=0,abs_tol=1e-12)):
            self._grasp_protocol_violation('Position grasp receipt is historical or has a different live target')
        return action

    def _stable_requirement(self,side):
        try:needed=self._attested_health['grasp_limits'][side]['stable_steps']
        except (KeyError,TypeError):self._grasp_protocol_violation('Missing grasp stability contract')
        if type(needed) is not int or needed<12:self._grasp_protocol_violation('Invalid grasp stability contract')
        return needed

    def _grasp_receipt(self,a,body,*,action_id=None,completed=False):
        from .grasp import SCHEMA
        acquire=body['mode']=='acquire'
        req=(a.get('grasp_request') or a.get('requested')) if isinstance(a,dict) else None
        if (not isinstance(a,dict) or a.get('schema')!=SCHEMA or type(a.get('epoch')) is not int
                or a['epoch']!=body['epoch'] or a.get('arm')!=body['arm'] or req!=body
                or not isinstance(a.get('id'),str) or not a['id'] or (action_id is not None and a['id']!=action_id)
                or a.get('mode') not in ({'acquire','maintain'} if acquire else {'position'})
                or a.get('status') not in ({'completed'} if completed else {'running','queued','completed'})
                or (completed and (a.get('grasped') is not acquire or a.get('holding') is not acquire
                    or a.get('contact_hold_committed') is not acquire or a.get('error') is not None))):
            self._grasp_protocol_violation('Grasp action receipt is not bound to the current v2 request and certificate')

    def set_limb_gripper(self, side, value, *, settle_steps=80):
        self._side(side)
        if isinstance(value,bool) or value not in {-1.,1.} or type(settle_steps) is not int or settle_steps<1:
            raise ValueError('Gripper requires open/close and positive settle steps')
        # Experimental explicit default 2 N/finger, cap 20 N/driver. This is not a
        # recommendation derived from the 333 N URDF maximum. Budget stays 720.
        return self.grasp(side,mode='acquire' if value>0 else 'position')

    def _grasp_protocol_violation(self,message):
        try:self.stop()
        except Exception as exc:raise RuntimeError(message+'; Stop barrier failed: '+type(exc).__name__) from exc
        raise RuntimeError(message)

    def _grasp_control_state(self,epoch=None):
        from .grasp import SCHEMA
        from benchmarks.attention_harness.core.control import EmergencyInterrupt
        try:state=self._state()
        except EmergencyInterrupt:raise
        except Exception:self._grasp_protocol_violation('Grasp control state unavailable or identity invalid')
        c=state.get('grasp_control') if isinstance(state,dict) else None
        if (not isinstance(c,dict) or type(state.get('epoch')) is not int
                or (epoch is not None and state['epoch']!=epoch) or type(c.get('epoch')) is not int
                or c['epoch']!=state['epoch'] or c.get('schema')!=SCHEMA
                or c.get('controller_suspended') is not False or c.get('fault') is not None
                or c.get('ownership')!=state.get('ownership') or c.get('handoff')!=state.get('handoff')):
            self._grasp_protocol_violation('Grasp control state schema, epoch or controller evidence invalid')
        return state

    def _handoff_binding(self,state,donor,receiver,group=None):
        from .grasp import SCHEMA
        h=state.get('handoff')
        if (not isinstance(h,dict) or h.get('schema')!=SCHEMA
                or h.get('donor')!=donor or h.get('receiver')!=receiver
                or not isinstance(h.get('id'),str) or not h['id']
                or (group is not None and h['id']!=group) or h.get('phase')=='aborted'):
            self._grasp_protocol_violation('Handoff identity or roles changed')
        return h

    def _release_receipt(self,a,donor,epoch,group,request_id,*,action_id=None,completed=False):
        from .grasp import SCHEMA
        req=(a.get('grasp_request') or a.get('requested')) if isinstance(a,dict) else None
        if (not isinstance(a,dict) or a.get('schema')!=SCHEMA or type(a.get('epoch')) is not int
                or a['epoch']!=epoch or a.get('arm')!=donor or a.get('mode')!='position'
                or a.get('handoff') is not True or a.get('coordination_group')!=group
                or not isinstance(a.get('id'),str) or not a['id'] or (action_id is not None and a['id']!=action_id)
                or a.get('status') not in ({'completed'} if completed else {'running','queued','completed'})
                or not isinstance(req,dict) or req.get('schema')!=SCHEMA or type(req.get('epoch')) is not int
                or req['epoch']!=epoch or req.get('arm')!=donor or req.get('mode')!='position' or req.get('request_id')!=request_id):
            self._grasp_protocol_violation('Release action receipt is not bound to this handoff request')

    def handoff(self,donor,receiver,*,begin=False):
        from .grasp import SCHEMA
        from benchmarks.attention_harness.core.control import EmergencyInterrupt
        if {donor,receiver}!={'left','right'} or self.selection.execution_arm!='both':raise ValueError('Handoff requires distinct arms in Both arms mode')
        if self._attested_health.get('grasp_schema')!=SCHEMA:raise CapabilityNotAvailableError('Grasp v2 service required')
        needed=self._stable_requirement(receiver)
        import uuid
        state=self._grasp_control_state();epoch=state['epoch'];deadline=time.monotonic()+2.;request_id=str(uuid.uuid4())
        group=None if begin else self._handoff_binding(state,donor,receiver)['id']
        while True:
            try:
                receipt=self._call('POST','/handoff/begin' if begin else '/handoff',{'donor':donor,'receiver':receiver,'epoch':epoch,**({} if begin else {'request_id':request_id})})
                break
            except EmergencyInterrupt:raise  # The inherited SDK already committed Stop.
            except Exception as exc:
                gate='donor_not_grasped' if begin else 'receiver_not_confirmed'
                if not isinstance(exc,RuntimeError) or gate not in str(exc):
                    try:self.stop()
                    finally:raise
                if time.monotonic()>=deadline:raise
                time.sleep(.01);state=self._grasp_control_state(epoch)
                if not begin:self._handoff_binding(state,donor,receiver,group)
        if begin:
            if (not isinstance(receipt,dict) or receipt.get('schema')!=SCHEMA
                    or receipt.get('donor')!=donor or receipt.get('receiver')!=receiver
                    or not isinstance(receipt.get('id'),str) or not receipt['id']
                    or receipt.get('phase') not in {'receiving','shared'}):
                self._grasp_protocol_violation('Handoff begin receipt lacks current roles or schema')
            self._handoff_binding(self._grasp_control_state(epoch),donor,receiver,receipt.get('id'))
            return receipt
        self._release_receipt(receipt,donor,epoch,group,request_id)
        action_id=receipt['id']
        try:action=self._wait_action(receipt)
        except EmergencyInterrupt:raise
        except Exception:
            try:self.stop()
            finally:raise
        self._release_receipt(action,donor,epoch,group,request_id,action_id=action_id,completed=True)
        until=time.monotonic()+2.
        while True:
            current=self._grasp_control_state(epoch);h=self._handoff_binding(current,donor,receiver,group)
            if h.get('release_action')!=action_id or h.get('phase') not in {'releasing','receiver_independent','completed'}:
                self._grasp_protocol_violation('Current handoff does not match the release action')
            if h['phase']=='completed':
                c=current['grasp_control'];grippers=c.get('grippers') or {}
                if not isinstance(grippers,dict):self._grasp_protocol_violation('Invalid handoff gripper feedback')
                d=grippers.get(donor);r=grippers.get(receiver)
                if not isinstance(d,dict) or not isinstance(r,dict):self._grasp_protocol_violation('Invalid handoff participant feedback')
                if (current.get('ownership')!=receiver or h.get('ownership')!=receiver
                        or r.get('holding') is not True or r.get('grasped') is not True or r.get('stable_confirmed') is not True
                        or type(r.get('stable_steps')) is not int or r['stable_steps']<needed
                        or type(r.get('target_id')) is not int or r['target_id']!=self._attested_health['native_target_id']
                        or d.get('holding') is not False or d.get('grasped') is not False or d.get('action_id')!=action_id
                        or type(h.get('independent_steps')) is not int or h['independent_steps']<needed):
                    self._grasp_protocol_violation('Completed handoff lacks current independent receiver holding and ownership')
                return dict(action,handoff_completed=True,ownership=receiver)
            if time.monotonic()>=until:self._grasp_protocol_violation('Receiver independent holding not confirmed')
            time.sleep(.01)

    def reset(self):
        previous=self._state()['epoch'];result=self._call('POST','/reset',{})
        if result.get('robot')!=self.selection.artifact() or result.get('epoch',previous)<=previous or result.get('active_action'):
            raise RuntimeError('Reset barrier identity/epoch disagrees with service')
        return result

    def cancel(self,action_id):
        state=self._state();receipt=self._call('POST','/cancel',{'epoch':state['epoch'],'action_id':action_id})
        return self._confirm_rest(receipt)

    def _confirm_rest(self,receipt):
        deadline=time.monotonic()+1.
        while time.monotonic()<deadline:
            state=self._transport('GET','/state',None,2.)
            stopped=stopped_components(state)
            if all(stopped.values()):
                receipt.update(physical_rest_confirmed=True,stopped_components=stopped,stopped_state=state);return receipt
            time.sleep(.02)
        receipt.update(physical_rest_confirmed=False,stopped_components=stopped);return receipt

    def stop(self):
        return self._confirm_rest(super().stop())

    def move_arm_to_position(self,*a,**kw):
        if self.selection.execution_arm=='both':raise CapabilityNotAvailableError('Both arms mode requires left_arm or right_arm')
        return self.move_limb(self.selection.execution_arm,*a,**kw)

    def set_gripper(self,*a,**kw):
        if self.selection.execution_arm=='both':raise CapabilityNotAvailableError('Both arms mode requires independent gripper')
        return self.set_limb_gripper(self.selection.execution_arm,*a,**kw)


MobileOpenArmActionBackend = BimanualActionBackend
