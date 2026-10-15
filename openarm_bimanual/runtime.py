"""One physics owner, independent limbs, contact-gated physical handoff."""
from pathlib import Path
import io, json, math, time
import numpy as np
from .kitchen import OpenArmKitchen, PARAMETERS, accepted_module
from .planning import OpenArmPlanner, PlanningError
from .planning_scope import planning_sides, physical_holders
from .hold import bounded_idle_pose, idle_hold_metrics, idle_arm_goal
from .contact_policy import unexpected_contacts, permitted_target_sides
from .grasp import GraspCoordinator, GraspError, SCHEMA
from .grasp_feedback import loaded_limits, sample_feedback, apply_controller

foundation = accepted_module()


class Runtime(foundation.Runtime):
    def __init__(self, evidence, *, task_id, seed, execution_arm='both', fault_after_steps=None, parameters_path=PARAMETERS):
        from curobo_v2.deployment import retired_entry
        retired_entry('OpenArm legacy bimanual runtime')
        super().__init__(evidence, fault_after_steps)
        self.initialize_bimanual(task_id=task_id,seed=seed,execution_arm=execution_arm,parameters_path=parameters_path)

    def initialize_bimanual(self, *, task_id, seed, execution_arm, parameters_path):
        """State shared with the separately authenticated V2 owner."""
        self.task_id=task_id; self.seed=seed; self.execution_arm=execution_arm
        self.parameters_path=parameters_path; self.kitchen=None; self.planners={}
        self.object_stream=(self.evidence/'object_trajectory.jsonl').open('w');self.contact_stream=(self.evidence/'contacts.jsonl').open('w'); self.handoff=None; self.guard_fault=None; self.gripper_holds={}; self.grasp_receipts={s:None for s in ['left','right']};self.idle_arm_holds={}
        self.contact_states={s:{'proof':None,'stable_steps':0} for s in ['left','right']}
        self.grasp_control=None

    @property
    def allowed_sides(self):
        return ['left','right'] if self.execution_arm == 'both' else [self.execution_arm]

    def setup_camera(self):
        from openarm_robocasa.service import Runtime as Fixed
        Fixed.setup_camera(self)

    def init_rig(self):
        if self.kitchen:
            self.kitchen.reset_same_scene(); self.reset_steps=0
        else:
            self.kitchen=OpenArmKitchen(self.task_id,self.seed,self.execution_arm,parameters_path=self.parameters_path)
            self.rig=self.kitchen.rig
            self.planners={s:OpenArmPlanner(self.kitchen,bootstrap_side=s) for s in ['left','right']}
            self.planner=self.planners['left']
            self.setup_camera()
        self.handoff=None;self.guard_fault=None;self.gripper_holds={};self.grasp_receipts={s:None for s in ['left','right']};self.idle_arm_holds={}
        self.contact_states={s:{'proof':None,'stable_steps':0} for s in ['left','right']}
        self.initialize_grasp_control()
        self.kitchen.carrying_sides=[]
        self.kitchen.target_contact_sides=self.allowed_sides
        check=self.planner.check_path(np.array(self.rig.robot.qpos))
        if not check['collision_free']: raise RuntimeError('Bimanual installation collision: '+json.dumps(check))
        self.log('installation',robot=self.kitchen.selection.artifact(),mount=self.kitchen.mount,
                 collision_check=check,objects=self.kitchen.object_state())

    def initialize_grasp_control(self):
        limits,provenance=loaded_limits(self.rig)
        self.grasp_control=GraspCoordinator(limits,epoch=self.epoch,
            target_id=int(self.kitchen.target_entity.per_scene_id),stop_callback=self.grasp_stop,
            emit=lambda row:self.log('grasp_v2',grasp_event=row))
        self.log('grasp_v2_limits',schema=SCHEMA,provenance=provenance,formal_eligible=False)

    def grasp_identity_valid(self):
        # An authenticated dual-arm host must supply its existing identity watch.
        # Absence is failure; this module does not connect or replace a planner.
        alive=getattr(self,'planner_alive',None)
        try:return bool(callable(alive) and alive())
        except Exception:return False

    def grasp_stop(self, reason):
        # Original single-owner barrier, including when no action is running.
        self.guard_fault=reason
        if not getattr(self,'grasp_barrier_committed',False):
            if self.active and self.actions[self.active]['status']!='running':self.active=None
            self.cancel('failed');self.commit_hold();self.epoch+=1
        self.kitchen.carrying_sides=[]
        self.log('physical_guard_stop',reason=reason,formal_eligible=False)

    def sync_grasp_state(self):
        c=self.grasp_control
        self.handoff=c.handoff
        self.gripper_holds={s:f.goal_m for s,f in c.fingers.items() if f.holding}
        self.grasp_receipts={s:dict(epoch=c.epoch,status=(c.actions.get(f.action_id) or {}).get('status'),action_id=f.action_id,schema=SCHEMA,
            contact_hold_committed=f.holding,grasped=f.grasped,holding=f.holding,
            ownership=c.ownership) if f.holding else None for s,f in c.fingers.items()}
        self.contact_states={s:dict(proof=x.raw_contacts if x.bilateral else None,
            stable_steps=c.fingers[s].stable_steps,hold_valid=c.fingers[s].holding,
            schema=SCHEMA) if (x:=c.last.get(s)) is not None else dict(proof=None,stable_steps=0,hold_valid=False,schema=SCHEMA) for s in ['left','right']}
        self.kitchen.carrying_sides=[s for s,f in c.fingers.items() if f.holding]

    def health(self):
        result=super().health()
        result.update(api_version='openarm-bimanual/2.0',grasp_schema=SCHEMA,formal_eligible=False,task_id=self.task_id,
            robot={'model':'OpenArm','execution_arm':self.execution_arm},
            parameters_sha256=self.kitchen.parameters_sha256 if self.kitchen else None,
            grasp_limits={s:vars(l) for s,l in self.grasp_control.limits.items()} if self.grasp_control else None,
            native_target_id=int(self.kitchen.target_entity.per_scene_id) if self.kitchen else None,
            gripper_free_close_m=self.kitchen.parameters['gripper_free_close_m'] if self.kitchen else None,
            capabilities=['independent_arms','independent_grippers','physical_handoff','gripper_contact_hold'])
        return result

    def state(self):
        result=super().state()
        result.update(robot=self.kitchen.selection.artifact(),control_frame='world',
            mount_world_xyz_wxyz=self.kitchen.mount,
            tcp_world={s:self.rig.robot.find_link_by_name(f'openarm_{s}_hand_tcp').entity_pose.to_transformation_matrix() for s in ['left','right']},
            contacts=self.contact_states,handoff=self.handoff,grasp_receipts=self.grasp_receipts,
            grasp_control=self.grasp_control.snapshot() if self.grasp_control else None,
            ownership=self.grasp_control.ownership if self.grasp_control else None,formal_eligible=False,
            task_phase='placement' if self.handoff and self.handoff['phase']=='completed' else 'handoff' if self.handoff else 'pickup',
            arms={s:{'active':bool(self.active and s in self.actions[self.active].get('participating_arms',[])),
                     'action_id':self.active if self.active and s in self.actions[self.active].get('participating_arms',[]) else None,
                     'grasped':bool(self.grasp_control and self.grasp_control.fingers[s].grasped),
                     'holding':s in physical_holders(self.contact_states,self.gripper_holds,self.grasp_receipts,self.epoch),
                     'action_label':self.arm_action_label(s)} for s in ['left','right']})
        return result

    def arm_action_label(self, side):
        action=self.actions.get(self.active) or {};requested=action.get('requested') or {}
        if action.get('handoff') and self.handoff:
            return 'Release donor grip' if side==self.handoff['donor'] else 'Hold received object'
        if side in requested:return 'Move arm'
        if side+'_gripper' in requested:
            return 'Acquire object grip' if action.get('mode') in {'acquire','maintain'} else 'Position gripper'
        return None

    def observation(self):
        raw=super().observation()
        with np.load(io.BytesIO(raw),allow_pickle=False) as captured: arrays={n:captured[n].copy() for n in captured.files}
        arrays['state_json']=foundation.dump(self.state()).decode()
        buffer=io.BytesIO();np.savez_compressed(buffer,**arrays);return buffer.getvalue()

    def camera_snapshot(self):
        from openarm_robocasa.camera import pack_camera
        self.rig.scene.update_render();self.camera.take_picture()
        return pack_camera(np.array(self.camera.get_picture('Color'))[...,:3],json.loads(foundation.dump(self.state())))

    def check_action_path(self, goal, body):
        self.kitchen.carrying_sides=physical_holders(self.contact_states,self.gripper_holds,self.grasp_receipts,self.epoch)
        self.kitchen.target_contact_sides=permitted_target_sides(self.allowed_sides,self.handoff,
            {'grasp_arm':self.pending_grasp_arm} if getattr(self,'pending_grasp_arm',None) else self.actions.get(self.active))
        sides=planning_sides(body,self.kitchen.carrying_sides)
        deadline=time.monotonic()+8.
        try:checks={s:self.planners[s].check_path(goal,allow_target_contact=True,deadline=deadline) for s in sides}
        except PlanningError as exc:raise foundation.APIError(422,'planning_rejected',str(exc))
        return {'collision_free':all(c['collision_free'] for c in checks.values()),
            'carried_object_included':any(c['carried_object_included'] for c in checks.values()),
            'planning_sides':sides,'arm_checks':checks,'contacts':[contact for c in checks.values() for contact in c['contacts']],
            'inactive_arm_included':True,'noncontact_clearance_m':.005}

    def validate_action(self, body, ingress_epoch):
        if isinstance(body,dict):
            if any(s not in self.allowed_sides and {s,s+'_gripper'} & set(body) for s in ['left','right']):
                raise foundation.APIError(400,'inactive_arm_locked','Execution mode locks the other arm')
            if {'left_gripper','right_gripper'} & set(body) and not getattr(self,'grasp_dispatching',False):
                raise foundation.APIError(400,'grasp_protocol_required','All gripper commands require openarm.grasp/2')
        if self.guard_fault:raise foundation.APIError(409,"physical_guard_latched","Reset required after physical safety fault")
        goal=super().validate_action(body,ingress_epoch)
        slack=np.full(self.rig.robot.dof,foundation.CFG['tolerances']['arm_limit_slack_rad'])
        slack[self.rig.finger]=foundation.CFG['tolerances']['finger_limit_slack_m']
        current=bounded_idle_pose(np.array(self.rig.robot.qpos),self.rig.qlimits,slack=slack)
        # Capture each unused arm once, then preserve its actual hold across commands.
        goal,self.idle_arm_holds=idle_arm_goal(goal,current,self.rig.names,body,self.idle_arm_holds)
        for side in ['left','right']:
            if side+'_gripper' not in body and side not in self.gripper_holds:
                for i in [1,2]: goal[self.rig.idx[f'openarm_{side}_finger_joint{i}']]=current[self.rig.idx[f'openarm_{side}_finger_joint{i}']]
        check=self.check_action_path(goal,body)
        if not check['collision_free']:
            self.log('action_rejected_collision',collision_check=check)
            raise foundation.APIError(422,'collision_rejected','Action intersects kitchen or either arm')
        return goal

    def commit_hold(self):
        q=np.clip(np.array(self.rig.robot.qpos),self.rig.qlimits[:,0],self.rig.qlimits[:,1])
        for side in ['left','right']:
            if side in self.gripper_holds:
                q[self.rig.idx[f'openarm_{side}_finger_joint1']]=self.rig.goal[self.rig.idx[f'openarm_{side}_finger_joint1']]
        for m in self.rig.mimics:q[self.rig.idx[m.joint]]=m.multiplier*q[self.rig.idx[m.mimic]]+m.offset
        self.rig.goal=q.copy();self.rig.command=q.copy();self.rig.apply_drive()

    def execute(self, request):
        method,path,body=request.method,request.path,request.body
        custom={'/plan','/grasp','/handoff/begin','/handoff','/cancel','/task/info','/task/success','/perceive','/camera'}
        if path not in custom:
            if path in {'/actions','/base/actions'} and self.active:
                request.response=(409,'application/json',foundation.APIError(409,'action_busy','Await or cancel current action before dispatch'))
                request.event.set();return
            super().execute(request)
            if path == '/stop' and request.response[0]==200:
                if self.grasp_control and not self.grasp_control.suspended:
                    self.grasp_barrier_committed=True
                    try:self.grasp_control.abort('stop',step=self.total_steps,epoch=self.epoch,status='stopped')
                    finally:self.grasp_barrier_committed=False
                self.grasp_control.epoch=self.epoch
                self.sync_grasp_state()
                request.response=(200,'application/json',self.state())
            if path=='/actions' and request.response[0]==202:
                a=self.actions[self.active]; sides=[s for s in ['left','right'] if {s,s+'_gripper'} & set(body)]
                a.update(participating_arms=sides,coordination_group=self.handoff['id'] if self.handoff and self.handoff['phase'] in {'receiving','releasing'} else None,started_monotonic=time.monotonic(),started_unix=time.time(),hold_reference=np.array(self.rig.robot.qpos).tolist())
                self.log('limb_action_bound',action=a.copy())
                request.response=(202,'application/json',a.copy())
            return
        try:
            if request.canceled:raise foundation.APIError(503,'request_timeout','Canceled request')
            if path in {'/plan','/grasp','/handoff/begin','/handoff','/cancel'}:
                if method!='POST' or not isinstance(body,dict):raise foundation.APIError(400,'invalid_request','Expected POST JSON')
                if type(body.get('epoch')) is not int or body['epoch']!=self.epoch or request.epoch!=self.epoch:
                    raise foundation.APIError(409,'stale_epoch','Target belongs to old epoch')
            if path in {'/grasp','/handoff/begin','/handoff'} and not self.grasp_identity_valid():
                self.grasp_control.abort('runtime_identity_failed',step=self.total_steps)
                raise foundation.APIError(503,'runtime_identity_failed','Authenticated dual-arm V2 identity unavailable')
            if path=='/plan':
                side=body.get('arm')
                if side not in self.allowed_sides or set(body)-{'arm','position','orientation','epoch'}:
                    raise foundation.APIError(400,'invalid_arm','Explicit allowed arm required')
                ori=body.get('orientation')
                if ori is None:
                    # Current base yaw, not initial mount yaw, transforms local TCP orientation.
                    ori=self.rig.robot.find_link_by_name('base_link').entity_pose.to_transformation_matrix()[:3,:3] @ np.asarray(self.kitchen.parameters['tcp_orientation_mount'][side])
                ori=np.asarray(ori,float)
                if ori.shape!=(3,3) or not np.isfinite(ori).all() or not np.allclose(ori@ori.T,np.eye(3),atol=1e-6) or abs(np.linalg.det(ori)-1)>1e-6:
                    raise foundation.APIError(400,'invalid_orientation','Proper orthonormal world rotation required')
                try:result=self.planners[side].plan(body.get('position'),ori);result.update(reachable=True,status='planned')
                except PlanningError as e:result={'robot':self.kitchen.selection.artifact(),'reachable':False,'status':'rejected','reason':str(e),'action_dispatched':False}
                result.update(arm=side,epoch=self.epoch,physical_step=self.total_steps,planning_required=True);self.log('plan',result=result)
            elif path=='/grasp':
                if self.execution_arm!='both':raise foundation.APIError(400,'bimanual_only','Grasp v2 is an internal dual-arm component')
                c=self.grasp_control
                # Validate and duplicate-check before changing the original owner.
                c.parse(body)
                if body['request_id'] in c.requests:
                    result=c.command(body,step=self.total_steps)
                    request.response=(202 if result['status']=='running' else 200,'application/json',result);return
                if self.active:raise foundation.APIError(409,'action_busy','Await active action')
                goal=c.parse(body)[0]
                # Preserve native collision/limit/idle checks without invoking any old grasp logic.
                self.grasp_dispatching=True
                try:self.validate_action({body['arm']+'_gripper':goal,'epoch':self.epoch},request.epoch)
                finally:self.grasp_dispatching=False
                receipt=c.command(body,step=self.total_steps)
                action=c.actions[receipt['id']]
                action.update(grasp_request=action['requested'],requested={body['arm']+'_gripper':goal,'epoch':self.epoch},
                    grasp_v2=True,grasp_arm=body['arm'],goal=self.rig.make_q({body['arm']+'_gripper':goal},self.rig.goal).tolist(),
                    participating_arms=[body['arm']],hold_reference=np.array(self.rig.robot.qpos).tolist())
                self.actions[action['id']]=action;self.active=action['id'];apply_controller(self.rig,c)
                request.response=(202,'application/json',action.copy());return
            elif path in {'/handoff/begin','/handoff'}:
                donor,receiver=body.get('donor'),body.get('receiver')
                required={'epoch','donor','receiver'}|({'request_id'} if path=='/handoff' else set())
                if set(body)!=required or self.execution_arm!='both':raise foundation.APIError(400,'invalid_handoff','Explicit distinct roles/epoch required')
                c=self.grasp_control
                if path=='/handoff':
                    replay=c.replay_release(donor,receiver,epoch=self.epoch,request_id=body['request_id'])
                    if replay is not None:
                        request.response=(202 if replay['status']=='running' else 200,'application/json',replay);return
                if self.active:raise foundation.APIError(409,'action_busy','Await active action')
                if path=='/handoff/begin':result=c.begin(donor,receiver,epoch=self.epoch,step=self.total_steps)
                else:
                    # Native collision veto runs before issuing release permission.
                    self.grasp_dispatching=True
                    try:self.validate_action({donor+'_gripper':c.limits[donor].upper_m,'epoch':self.epoch},request.epoch)
                    finally:self.grasp_dispatching=False
                    receipt=c.release(donor,receiver,epoch=self.epoch,step=self.total_steps,request_id=body['request_id'])
                    action=c.actions[receipt['id']]
                    action.update(grasp_request=action['requested'],requested={donor+'_gripper':c.limits[donor].upper_m,'epoch':self.epoch},
                        grasp_v2=True,handoff=True,grasp_arm=donor,goal=self.rig.make_q({donor+'_gripper':c.limits[donor].upper_m},self.rig.goal).tolist(),
                        hold_reference=np.array(self.rig.robot.qpos).tolist(),participating_arms=[donor,receiver])
                    self.actions[action['id']]=action;self.active=action['id'];apply_controller(self.rig,c)
                    request.response=(202,'application/json',action.copy());return
                self.sync_grasp_state()
            elif path=='/cancel':
                if set(body)!={'epoch','action_id'} or body['action_id']!=self.active:raise foundation.APIError(409,'not_active','Cancellation must name active action')
                self.grasp_control.abort('cancel',step=self.total_steps,status='cancelled')
                self.sync_grasp_state();result=self.state()
            elif method=='GET' and path=='/camera':
                request.response=(200,'application/x-npz',self.camera_snapshot());return
            elif method=='GET' and path=='/task/info':result={'task':self.kitchen.spec.environment_id,'lang':self.kitchen.native.get_ep_meta().get('lang',''),'variation':self.kitchen.variation,'robot':self.kitchen.selection.artifact(),'cameras':['base_camera']}
            elif method=='GET' and path=='/task/success':
                result=self.kitchen.native_evaluate();result['success']=result['native_success'];self.log('native_evaluation',**result)
            elif method=='POST' and path=='/perceive' and body=={}:
                objects=[{'name':n,**p,'source':'sim_gt','frame':'world','confidence':1.} for n,p in self.kitchen.object_state().items()]
                destination=getattr(self.kitchen.native,self.kitchen.spec.destination_debug_key)
                objects.append({'name':self.kitchen.spec.destination_debug_key,'position':np.asarray(destination.pos).tolist(),'source':'sim_gt','frame':'world','confidence':1.})
                result={'objects':objects,'cameras':['base_camera'],'perception_mode':'sim_gt','robot':self.kitchen.selection.artifact()}
            else:raise foundation.APIError(400,'invalid_request','Unsupported arguments')
            request.response=(200,'application/json',result)
        except GraspError as e:request.response=(409 if e.code in {'stale_epoch','action_busy','duplicate_conflict','holding_conflict','handoff_busy','donor_not_grasped','receiver_not_confirmed','handoff_release_guard','physical_guard_latched'} else 400,'application/json',foundation.APIError(409 if e.code in {'stale_epoch','action_busy','duplicate_conflict','holding_conflict','handoff_busy','donor_not_grasped','receiver_not_confirmed','handoff_release_guard','physical_guard_latched'} else 400,e.code,str(e)))
        except foundation.APIError as e:request.response=(e.status,'application/json',e)
        except Exception as e:
            self.log('query_rejected',path=path,error=str(e));request.response=(422,'application/json',foundation.APIError(422,'query_rejected',str(e)))
        finally:request.event.set()

    def recover_stop(self, reason):
        from .lifecycle import stopped_components
        receipt={'reason':reason,'robot':self.kitchen.selection.artifact(),'physical_rest_confirmed':False}
        self.recovering=True;self.fault_after_steps=None
        try:
            self.cancel('failed');self.commit_hold();self.epoch+=1;self.guard_fault='simulation_error'
            if self.handoff and self.handoff['phase'] not in {'completed','aborted'}:self.handoff.update(phase='aborted',reason='simulation_error')
            for _ in range(240):
                self.physics_step()
                state=json.loads(foundation.dump(self.state()))
                stopped=stopped_components(state)
                if all(stopped.values()):
                    receipt.update(physical_rest_confirmed=True,stopped_components=stopped,stopped_state=state)
                    break
        except BaseException as exc:receipt['recovery_error']=str(exc)
        finally:
            self.recovering=False
            (self.evidence/'exception_stop.json').write_text(json.dumps(receipt,allow_nan=False)+'\n')
            self.log('exception_stop',receipt=receipt)

    def physics_step(self):
        active=self.actions.get(self.active)
        # The original executor advances physics. Its positional completion is
        # recorded as a comparison, never allowed to complete a v2 grasp.
        grasp_action=active if active and active.get('grasp_v2') else None
        if grasp_action:self.active=None
        try:super().physics_step()
        except BaseException as exc:
            if self.grasp_control and not self.grasp_control.suspended:
                self.grasp_control.abort('simulation_error',step=self.total_steps)
            if not getattr(self,'recovering',False):self.recover_stop(str(exc))
            raise
        if grasp_action and grasp_action['status']=='running':self.active=grasp_action['id']
        self.kitchen.steps=self.reset_steps
        c=self.grasp_control
        try:
            samples=sample_feedback(self.rig,target=self.kitchen.target_entity,epoch=self.epoch,step=self.total_steps,
                dt=foundation.CFG['physics']['dt'],identity_valid=self.grasp_identity_valid() and not bool(self.guard_fault))
            c.observe(samples)
        except Exception as exc:
            c.abort('feedback_invalid',step=self.total_steps)
            self.log('feedback_error',error=str(exc))
        self.sync_grasp_state()
        hold=idle_hold_metrics(np.array(self.rig.robot.qpos),np.asarray(active['hold_reference']),self.rig.names,active['requested']) if active and active.get('hold_reference') else {'violation':None}
        contacts=unexpected_contacts(self.rig.scene,sides=self.allowed_sides,target_id=self.kitchen.target_entity.per_scene_id,
            target_contact_sides=permitted_target_sides(self.allowed_sides,self.handoff,active))
        if (contacts or hold['violation']) and not c.suspended:
            c.abort(hold['violation'] or 'collision_stop',step=self.total_steps)
        if grasp_action and grasp_action['status']=='completed':
            q=np.array(self.rig.robot.qpos);v=np.array(self.rig.robot.qvel);tol=foundation.CFG['tolerances'];err=abs(q-self.rig.goal)
            ok=bool(np.max(err[self.rig.arm])<=tol['arm_position_rad'] and np.max(err[self.rig.finger])<=tol['finger_position_m'] and np.max(abs(v[self.rig.arm]))<=tol['arm_final_velocity_rad_s'] and np.max(abs(v[self.rig.finger]))<=tol['finger_final_velocity_m_s'])
            grasp_action.update(legacy_full_position_completion=ok,final_arm_error_rad=float(np.max(err[self.rig.arm])),
                final_finger_error_m=float(np.max(err[self.rig.finger])),final_arm_velocity_rad_s=float(np.max(abs(v[self.rig.arm]))),
                final_finger_velocity_m_s=float(np.max(abs(v[self.rig.finger]))))
            if not ok:
                grasp_action.update(status='failed',error={'code':'convergence_timeout','message':'Original full-group gates failed'})
                c.abort('convergence_timeout',step=self.total_steps)
        if self.active and self.actions[self.active]['status']!='running':self.active=None
        apply_controller(self.rig,c)
        self.kitchen.target_contact_sides=permitted_target_sides(self.allowed_sides,self.handoff,self.actions.get(self.active))
        self.object_stream.write(json.dumps({'epoch':self.epoch,'step':self.total_steps,'objects':self.kitchen.object_state(),'ownership':c.ownership,'formal_eligible':False})+'\n')
        self.contact_stream.write(json.dumps({'epoch':self.epoch,'step':self.total_steps,'action':self.active,'arms':self.contact_states,'handoff':self.handoff,'unexpected':contacts,'grasp_control':c.snapshot()})+'\n')
        if self.total_steps%240==0:self.object_stream.flush();self.contact_stream.flush()

    def run(self):
        try:super().run()
        finally:
            for planner in self.planners.values():planner.close()
            self.object_stream.close();self.contact_stream.close()
            if self.kitchen:self.kitchen.close()
