"""V2 trajectories on the existing kitchen HTTP owner and physical executor.

This migration entry reuses native task evaluation, contacts, controller, camera,
Stop barriers, original 720-step actions and the main SDK protocol.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import signal
import sys
import threading
import time
import numpy as np
from .identity import IdentityError,load_lock,verify_runtime,verify_files,install_import_guard,digest
from .integrity import IntegrityWatch
from .sapien_planner import SapienV2Planner
from .command_curve import sample_for_original_drive
from .sapien_monitor import KitchenWorldMonitor
from .sapien_validator import KitchenCollisionValidator
from .client import PlanningRejected


def runtime_class():
    # Authenticate before importing any simulator/executor module.
    from openarm_robocasa.service import Runtime as OriginalRuntime,foundation
    class Runtime(OriginalRuntime):
        def __init__(self,*args,lock_path,robot_config,deadline,**kwargs):
            lock,_=load_lock(lock_path);verify_runtime(lock,owner=True)
            super().__init__(*args,**kwargs)
            self.v2_lock,self.v2_config,self.v2_deadline=lock_path,robot_config,deadline
            self.v2=None;self.planning_interrupt=threading.Event();self.v2_dispatching=False;self.dispatch_gate=threading.Lock()
            self.integrity=IntegrityWatch(lambda:verify_files(lock),interval=.5,max_age=3.)

        def init_rig(self):
            if self.v2:self.v2.close()
            self.planning_interrupt.clear()
            # Reuse the original kitchen, accepted rig and reset/controller. The
            # planning object here can only veto geometry; it cannot generate a path.
            if self.kitchen is None:
                from openarm_robocasa.kitchen import OpenArmKitchen
                self.kitchen=OpenArmKitchen(self.task_id,self.seed,self.execution_arm,parameters_path=self.parameters_path)
                self.rig=self.kitchen.rig;self.reset_steps=0
                self.planner=KitchenCollisionValidator(self.kitchen)
                physical_close=self.rig.close
                def close_with_native_evaluation():
                    path=self.evidence/'native_final_evaluation.json'
                    if not path.exists():
                        try:result=dict(self.kitchen.native_evaluate(),evaluated=True,epoch=self.epoch,
                            task_id=self.task_id,seed=self.seed,source_context='original evaluator before physical owner cleanup')
                        except Exception as exc:result=dict(evaluated=False,error=f'{type(exc).__name__}: {exc}')
                        path.write_text(json.dumps(result,indent=2)+'\n')
                    physical_close()
                self.rig.close=close_with_native_evaluation
            else:
                from openarm_robocasa.contact_controller import apply_contact_controller
                control=foundation.sim.CFG['controller']
                for side in ['left','right']:
                    apply_contact_controller(self.rig,side,stiffness=control['finger_stiffness'],
                        driver_damping=control['finger_damping'],follower_damping=0,mode=control['mode'])
                self.kitchen.reset_same_scene();self.reset_steps=0
            check=self.planner.check_path(np.array(self.rig.robot.qpos))
            if not check['collision_free']:raise RuntimeError('Original native installation collides: '+json.dumps(check))
            self.log('installation',robot=self.kitchen.selection.artifact(),mount=self.kitchen.mount,
                     collision_check=check,objects=self.kitchen.object_state())
            self.setup_camera()
            def attachment():
                from openarm_robocasa.contact_policy import bilateral_target_contacts
                proof=bilateral_target_contacts(self.rig.scene,side=self.execution_arm,
                    target_id=self.kitchen.target_entity.per_scene_id)
                return None if not proof else {'holder':self.execution_arm,'target_id':self.kitchen.target_entity.per_scene_id}
            self.v2=SapienV2Planner(self.kitchen,lock_path=self.v2_lock,robot_config=self.v2_config,
                evidence=self.evidence/f'v2-epoch-{self.epoch}',deadline=self.v2_deadline,
                cancel_check=lambda:self.closing.is_set()or self.planning_interrupt.is_set(),
                attachment_provider=attachment)
            self.v2.prepare([f'openarm_{self.execution_arm}_joint{i}'for i in range(1,8)],
                            target_tools=[f'openarm_{self.execution_arm}_hand_tcp'])
            # Long native scene construction can hold Python's interpreter lock.
            # Reverify synchronously before the original owner declares readiness.
            self.integrity.refresh()

        def validate_action(self,body,ingress_epoch):
            if self.v2_dispatching:
                # The complete V2 curve has just passed the native mesh veto.
                # Retain original limits/epoch validation without substituting
                # the old straight line between its first and last positions.
                return foundation.Runtime.validate_action(self,body,ingress_epoch)
            return super().validate_action(body,ingress_epoch)

        def health(self):
            result=super().health()
            result.update(planner_generation=2,planner_identity=self.v2.client.identity if self.v2 else None,
                          planner_ready=self.planner_alive()and not self.planning_interrupt.is_set(),
                          planner_joint_names=self.v2.names if self.v2 else None,
                          planning_request_defaults={'position_frame':'world','length_unit':'m','angular_unit':'rad'},
                          trajectory_protocol='authenticated-v2-one-use/2',
                          command_interpolation='quintic-Hermite of public q/v/a at unchanged official timestamps')
            return result

        def planner_alive(self):
            try:self.integrity.check(require_fresh=self.ready)
            except IdentityError:return False
            return bool(self.v2 and not self.v2.failed and not self.v2.client.failed
                        and self.v2.owner.process and self.v2.owner.process.poll()is None)

        def submit(self,method,path,body):
            if path in {'/stop','/cancel'}:
                # Do this before the original owner's lock: interrupt a blocked
                # GPU call so its result cannot be dispatched before the barrier.
                with self.dispatch_gate:self.planning_interrupt.set()
            if path not in {'/plan','/plan/joint'}:
                return super().submit(method,'/stop'if path=='/cancel'else path,body)
            # Explicit model preparation and planning share the unchanged
            # episode deadline. Other native requests retain their old budget.
            with self.lock:
                if not self.ready:raise foundation.APIError(503,'not_ready',self.error or 'Simulation starting')
                request=foundation.Request(method,path,body,self.epoch)
                self.sequence+=1;self.queue.put((1,self.sequence,request))
            remaining=max(0.,min(75.,self.v2_deadline-time.monotonic()))
            if not request.event.wait(remaining):
                request.canceled=True
                with self.dispatch_gate:self.planning_interrupt.set()
                raise foundation.APIError(503,'request_timeout','V2 preparation/planning deadline expired; no capability may be issued')
            status,ctype,result=request.response
            if isinstance(result,Exception):raise result
            return status,ctype,result

        def execute(self,request):
            path=request.path;body=request.body
            if path in {'/stop','/reset'} and self.v2:self.v2.invalidate(path)
            if (request.method=='POST'and path not in {'/stop','/reset'}and self.v2
                    and (not self.planner_alive()or self.planning_interrupt.is_set())):
                request.response=(503,'application/json',foundation.APIError(503,'v2_stopped','V2 stopped or failed; reset required'))
                request.event.set();return
            if path not in {'/plan','/trajectory/actions','/plan/joint'}:
                if path=='/actions'and isinstance(body,dict)and {'left','right'}&set(body):
                    request.response=(422,'application/json',foundation.APIError(422,'v2_trajectory_required','Arm actions require a one-use V2 trajectory'))
                    request.event.set();return
                if request.method=='POST'and path in {'/actions','/grasp'}:
                    try:
                        self.v2.client.attest()
                        with self.dispatch_gate:
                            if self.planning_interrupt.is_set():raise InterruptedError('Native gripper dispatch canceled')
                            return super().execute(request)
                    except Exception as exc:
                        self.planning_interrupt.set();self.v2.close()
                        request.response=(503,'application/json',foundation.APIError(503,'v2_unavailable',str(exc)))
                        request.event.set();return
                return super().execute(request)
            try:
                if request.canceled:raise foundation.APIError(503,'request_timeout','Canceled request')
                if self.active:raise foundation.APIError(409,'action_busy','Await or cancel the current physical action')
                if request.method!='POST'or not isinstance(body,dict):raise foundation.APIError(400,'invalid_request','Expected POST JSON')
                if request.epoch!=self.epoch:raise foundation.APIError(409,'stale_epoch','Request belongs to old epoch')
                if path in {'/plan','/plan/joint'}:
                    if path=='/plan':
                        from .native_requests import position_request
                        try:position=position_request(body)
                        except ValueError as exc:raise foundation.APIError(422,'invalid_plan',str(exc))from exc
                        orientation=self.rig.robot.root_pose.to_transformation_matrix()[:3,:3]@np.asarray(self.kitchen.parameters['tcp_orientation_mount'][self.execution_arm])
                        plan=self.v2.plan_pose(position,orientation,side=self.execution_arm,epoch=self.epoch)
                    else:
                        from .native_requests import joint_request
                        try:target=joint_request(body,names=self.v2.names,epoch=self.epoch,limits=self.rig.qlimits[self.v2.indices])
                        except LookupError as exc:raise foundation.APIError(409,'stale_epoch',str(exc))from exc
                        except ValueError as exc:raise foundation.APIError(422,'invalid_plan',str(exc))from exc
                        inactive=[i for i,n in enumerate(self.v2.names)if not n.startswith(f'openarm_{self.execution_arm}_joint')]
                        if inactive and np.max(abs(np.array(target)[inactive]-self.v2.state()[inactive]))>1e-6:
                            raise foundation.APIError(422,'invalid_plan','Target changes an inactive native joint')
                        plan=self.v2.plan_joints(target,active_joints=[f'openarm_{self.execution_arm}_joint{i}'for i in range(1,8)],epoch=self.epoch)
                    if request.canceled or self.planning_interrupt.is_set()or self.closing.is_set()or time.monotonic()>=self.v2_deadline:
                        self.v2.invalidate('request_expired');self.planning_interrupt.set()
                        raise foundation.APIError(503,'request_timeout','Expired query cannot issue an execution capability')
                    target=np.asarray(self.rig.robot.qpos).copy()
                    target[self.v2.indices]=plan['trajectory'][-1]
                    result=dict(plan,target_qpos=target.tolist(),reachable=True,planning_required=True,status='planned',
                        robot=self.kitchen.selection.artifact(),physical_step=self.total_steps,
                        collision_check=plan['validation'])
                    self.log('v2_plan',result=result);request.response=(200,'application/json',result)
                else:
                    from .native_requests import trajectory_request
                    try:pid=trajectory_request(body,self.epoch)
                    except LookupError as exc:raise foundation.APIError(409,'stale_epoch',str(exc))from exc
                    except ValueError as exc:raise foundation.APIError(422,'invalid_trajectory',str(exc))from exc
                    plan=self.v2.claim(pid,epoch=self.epoch)
                    c=foundation.CFG['controller'];dt=foundation.CFG['physics']['dt'];budget=foundation.CFG['execution']['action_steps']
                    samples,timing=sample_for_original_drive(plan,joint_names=plan['joint_names'],
                        maximum_speed={n:c['target_slew_arm_rad_s']for n in plan['joint_names']},
                        position_limits=self.rig.qlimits[self.v2.indices],**self.v2.motion_limits,
                        dt=dt,step_budget=budget,settle_steps=120)
                    native_path=np.repeat(np.asarray(self.rig.robot.qpos)[None,:],len(samples),axis=0)
                    native_path[:,self.v2.indices]=samples
                    native_validation=self.planner.validate_trajectory(native_path,allow_target_contact=True,
                        cancel_check=lambda:self.planning_interrupt.is_set()or self.closing.is_set()or time.monotonic()>=self.v2_deadline)
                    if not native_validation['collision_free']:
                        raise PlanningRejected('Original complete kitchen meshes rejected V2 trajectory: '+json.dumps(native_validation))
                    target=np.asarray(self.rig.robot.qpos).copy();target[self.v2.indices]=plan['trajectory'][-1]
                    side=self.execution_arm
                    action_body={side:[float(target[self.rig.idx[f'openarm_{side}_joint{i}']])for i in range(1,8)],'epoch':self.epoch}
                    monitor=KitchenWorldMonitor(self.kitchen.scene,self.rig.robot,
                        attachment=self.v2.attachment_provider(),attachment_provider=self.v2.attachment_provider)
                    with self.dispatch_gate:
                        if self.planning_interrupt.is_set()or self.closing.is_set()or time.monotonic()>=self.v2_deadline:
                            raise InterruptedError('V2 action canceled or expired before physical dispatch')
                        if not self.planner_alive():
                            raise PlanningRejected('Authenticated V2 child exited before physical dispatch')
                        self.v2_dispatching=True
                        internal=foundation.Request('POST','/actions',action_body,request.epoch)
                        internal.canceled=request.canceled
                        try:super().execute(internal)
                        finally:self.v2_dispatching=False
                        request.response=internal.response
                    if request.response[0]==202:
                        action=self.actions[self.active]
                        action.update(plan_id=body['plan_id'],planner_generation=2,planner_nonce=self.v2.client.nonce,
                            trajectory_timing=timing,native_trajectory_validation=native_validation,trajectory_joint_names=plan['joint_names'],
                            trajectory_samples=samples.tolist(),world_revision=plan['world_revision'],world_sha256=plan['world_sha256'])
                        self.world_monitor=monitor
                        self.log('v2_trajectory_bound',action=action.copy())
                        request.response=(202,'application/json',action.copy())
            except foundation.APIError as exc:request.response=(exc.status,'application/json',exc)
            except Exception as exc:
                was_canceled=request.canceled or self.planning_interrupt.is_set()or self.closing.is_set()or time.monotonic()>=self.v2_deadline
                self.planning_interrupt.set()
                if self.v2:self.v2.close()
                self.log('v2_rejected',path=path,error=f'{type(exc).__name__}: {exc}',physical_dispatches=0)
                from .native_errors import classify_failure
                status,code=(503,'v2_request_canceled')if was_canceled else classify_failure(exc,planning=path in {'/plan','/plan/joint'})
                request.response=(status,'application/json',foundation.APIError(status,code,str(exc)))
            finally:request.event.set()

        def physics_step(self):
            identity_error=None
            try:self.integrity.check()
            except IdentityError as exc:identity_error=str(exc);self.planning_interrupt.set()
            if self.active and (identity_error or not self.planner_alive()):
                action=self.actions[self.active]
                barrier=foundation.Request('POST','/stop',{},self.epoch);super().execute(barrier)
                action.update(status='failed',error={'code':'runtime_identity_failed'if identity_error else'v2_unavailable',
                    'message':identity_error or'Authenticated V2 child exited during physical execution'})
                self.v2.invalidate('v2_unavailable');self.planning_interrupt.set()
                self.log('v2_unavailable_stop',action=action.copy())
            if self.active:
                action=self.actions[self.active]
                if action.get('planner_generation')==2:
                    try:self.world_monitor.check()
                    except PlanningRejected as exc:
                        barrier=foundation.Request('POST','/stop',{},self.epoch)
                        super().execute(barrier)
                        action.update(status='failed',error={'code':getattr(exc,'code','stale_collision_world'),'message':str(exc)})
                        self.v2.invalidate('world_changed_during_execution');self.planning_interrupt.set()
                        self.log('stale_world_stop',action=action.copy())
                    else:
                        samples=action['trajectory_samples'];index=min(action['physical_steps']+1,len(samples)-1)
                        self.rig.goal[self.v2.indices]=samples[index]
            super().physics_step()

        def run(self):
            try:super().run()
            finally:
                if self.v2:self.v2.close()
                self.integrity.close()
    return Runtime,foundation


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--lock',required=True,type=Path);parser.add_argument('--robot-config',required=True,type=Path)
    parser.add_argument('--task-source',required=True,type=Path);parser.add_argument('--evidence',required=True,type=Path)
    parser.add_argument('--socket-fd',required=True,type=int);parser.add_argument('--task',required=True,choices=['counter_to_sink','counter_to_cab'])
    parser.add_argument('--arm',required=True,choices=['left','right']);parser.add_argument('--seed',required=True,type=int)
    parser.add_argument('--deadline',required=True,type=float)
    parser.add_argument('--parameters',required=True,type=Path)
    args=parser.parse_args()
    from .native_auth import require_native_owner,inherited_listener,authenticated_handler
    token,nonce=require_native_owner();lock,lock_sha=load_lock(args.lock);verify_runtime(lock);install_import_guard()
    from .native_assets import verify_assets,verify_data_environment
    assets_proof=verify_assets(lock)
    actual_assets=verify_data_environment(lock)
    robot_config=str(args.robot_config.resolve())
    if lock['robot_configs'].get(robot_config)!=digest(args.robot_config):raise IdentityError('Native robot configuration is not locked')
    if (not args.task_source.is_absolute()or not (args.task_source/'robocasa_tasks/__init__.py').is_file()
            or lock.get('task_sources',{}).get(str(args.task_source.resolve()))!=digest(args.task_source/'robocasa_tasks/__init__.py')):
        raise IdentityError('Explicit existing kitchen task source is required')
    # The owner imports native task definitions; the GPU worker never inherits this path.
    sys.path.insert(0,str(args.task_source));Runtime,foundation=runtime_class()
    args.evidence.mkdir(parents=True,exist_ok=False)
    (args.evidence/'native-assets-verification.json').write_text(json.dumps(assets_proof,indent=2)+'\n')
    parameters=str(args.parameters.resolve())
    if lock['files'].get(parameters)!=digest(args.parameters):raise IdentityError('Native parameters differ from locked content')
    runtime=Runtime(args.evidence,parameters_path=args.parameters,task_id=args.task,seed=args.seed,execution_arm=args.arm,
                    lock_path=args.lock,robot_config=args.robot_config,deadline=args.deadline)
    listener=inherited_listener(args.socket_fd)
    selection=dict(robot_config=robot_config,task_source=str(args.task_source.resolve()),
                   task=args.task,seed=args.seed,arm=args.arm,native_assets=actual_assets,
                   parameters={'path':parameters,'sha256':digest(args.parameters)})
    handler=authenticated_handler(foundation,lock=lock,lock_sha=lock_sha,token=token,nonce=nonce,selection=selection)
    server=foundation.Server(listener.getsockname(),handler,bind_and_activate=False)
    server.socket.close();server.socket=listener;server.server_address=listener.getsockname()
    server.server_name='localhost';server.server_port=listener.getsockname()[1]
    server.runtime=runtime;server.timeout=.1
    signal.signal(signal.SIGTERM,lambda *_:runtime.closing.set());signal.signal(signal.SIGINT,lambda *_:runtime.closing.set())
    runtime.thread.start()
    try:
        while not runtime.closing.is_set():server.handle_request()
    finally:
        runtime.closing.set();server.server_close();runtime.thread.join(10)
        if runtime.thread.is_alive():raise RuntimeError('Original simulation owner did not terminate')
    return int(runtime.error is not None)


if __name__=='__main__':sys.exit(main())
