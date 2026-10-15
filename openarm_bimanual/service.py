"""Mobile service owns physics; display child only consumes real snapshots."""
import argparse
import json
import math
import multiprocessing
from pathlib import Path
import queue
import signal
import sys
import time

import numpy as np
from .runtime import Runtime as FixedRuntime, foundation
from .kitchen import OpenArmKitchen, PARAMETERS
from .planning import OpenArmPlanner
from .model import build_model
from .display import display_worker


class Runtime(FixedRuntime):
    def __init__(self,*args,display=True,**kwargs):
        super().__init__(*args,**kwargs)
        self.display_enabled=display;self.renderer=None;self.last_publish=0
        self.base_log=[];self.snapshot_dropped=0;self.display_out=self.evidence/'display'

    def init_rig(self):
        first = self.kitchen is None
        super().init_rig()
        if not first: return
        self._start_display()

    def _start_display(self):
        if self.display_enabled:
            from .render_scene import export_scene, entities
            blueprint=export_scene(self.kitchen)
            self.display_entities=entities(self.rig.scene,self.rig.robot)
            (self.evidence/'display_scene.json').write_text(json.dumps(blueprint))
            context=multiprocessing.get_context('spawn')
            self.display_queue=context.Queue(maxsize=1);self.display_done=context.Event()
            self.renderer=context.Process(target=display_worker,args=(self.display_queue,self.display_done,
                str(self.display_out),self.execution_arm,blueprint),name='openarm-display')
            self.renderer.start()

    def health(self):
        result=super().health();result.update(api_version='openarm-bimanual/2.0',model_sha256=build_model()['sha256'],
            capabilities=['gripper_contact_hold','independent_arms','independent_grippers','physical_handoff','mobile_base_joint_drive','display_four_native_1080p'],
            display_pid=self.renderer.pid if self.renderer else None,
            independent_controls=19 if self.ready else None)
        return result

    def model(self):
        result=super().model()
        result.update(api_version='openarm-bimanual/2.0',model_version='openarm-tidyverse-bimanual-v1',
            urdf_sha256=build_model()['sha256'],base_model=build_model())
        return result

    def state(self):
        result=super().state();idx=self.rig.base
        result['base']={'model':'Tidyverse planar chassis','control':'PhysX joint drives',
            'qpos':np.array(self.rig.robot.qpos)[idx],'qvel':np.array(self.rig.robot.qvel)[idx],
            'goal':self.rig.goal[idx].copy(),'drive_target':self.rig.command[idx].copy(),
            'frame':'initial mount X/Y; yaw about Z',
            'world_pose':self.rig.robot.find_link_by_name('base_link').entity_pose.to_transformation_matrix()}
        result['display']={'enabled':self.display_enabled,'snapshot_dropped':self.snapshot_dropped}
        return result

    def execute(self,request):
        if request.path!='/base/actions':return super().execute(request)
        try:
            body=request.body
            if self.active:raise foundation.APIError(409,'action_busy','Await or cancel current action')
            if self.guard_fault:raise foundation.APIError(409,'physical_guard_latched','Reset required after physical safety fault')
            if request.canceled:raise foundation.APIError(503,'request_timeout','Canceled base command')
            if request.method!='POST' or not isinstance(body,dict) or set(body)!={'delta','epoch'}:
                raise foundation.APIError(400,'invalid_base','Expected delta[3] and locked epoch')
            if type(body['epoch']) is not int or body['epoch']!=self.epoch or request.epoch!=self.epoch:
                raise foundation.APIError(409,'stale_epoch','Base command belongs to an old epoch')
            delta=body['delta']
            if not isinstance(delta,list) or len(delta)!=3 or not all(foundation.finite_number(x) for x in delta):
                raise foundation.APIError(400,'invalid_base','Finite dx/dy/dtheta required')
            if math.hypot(*delta[:2])>.3 or abs(delta[2])>.5:
                raise foundation.APIError(400,'base_safety_limit','Base delta exceeds unchanged safety bounds')
            q=np.array(self.rig.robot.qpos);goal=self.rig.goal.copy()
            # SDK deltas use world coordinates. The URDF planar axes are mount-local.
            rotation=self.rig.robot.root_pose.to_transformation_matrix()[:2,:2]
            local=rotation.T@np.array(delta[:2]);goal[self.rig.base]+=np.array([*local,delta[2]])
            from .hold import idle_arm_goal, bounded_idle_pose
            slack=np.full(self.rig.robot.dof,foundation.CFG['tolerances']['arm_limit_slack_rad'])
            slack[self.rig.finger]=foundation.CFG['tolerances']['finger_limit_slack_m']
            idle=bounded_idle_pose(q,self.rig.qlimits,slack=slack)
            goal,self.idle_arm_holds=idle_arm_goal(goal,idle,self.rig.names,body,self.idle_arm_holds)
            if np.any(goal<self.rig.qlimits[:,0]) or np.any(goal>self.rig.qlimits[:,1]):
                raise foundation.APIError(400,'base_joint_limit','Base target exceeds model limits')
            check=self.check_action_path(goal,body)
            if not check['collision_free']:
                self.log('base_rejected_collision',collision_check=check)
                raise foundation.APIError(422,'collision_rejected','Base sweep intersects robot or kitchen')
            self.cancel('superseded');self.rig.goal=goal.copy()
            aid=f'{self.epoch}:base:{len(self.actions)+1}'
            action={'id':aid,'epoch':self.epoch,'status':'running','start_step':self.total_steps,
                'physical_steps':0,'step_budget':foundation.CFG['execution']['action_steps'],
                'requested':body,'goal':goal.tolist(),'error':None,'base_action':True,
                'start_base':q[self.rig.base].tolist(),'collision_check':check,'hold_reference':q.tolist(),
                'participating_arms':[], 'coordination_group':None,'started_monotonic':time.monotonic(),'started_unix':time.time()}
            self.actions[aid]=action;self.active=aid;self.log('base_started',action=action)
            request.response=(202,'application/json',action.copy())
        except foundation.APIError as exc:request.response=(exc.status,'application/json',exc)
        finally:request.event.set()

    def physics_step(self):
        current=self.actions.get(self.active)
        super().physics_step()
        q=np.array(self.rig.robot.qpos);v=np.array(self.rig.robot.qvel)
        self.base_log.append({'step':self.total_steps,'epoch':self.epoch,'monotonic':time.monotonic(),
            'qpos':q[self.rig.base].tolist(),'qvel':v[self.rig.base].tolist(),
            'world_pose':self.rig.robot.find_link_by_name('base_link').entity_pose.to_transformation_matrix().tolist(),
            'root_pose':self.rig.robot.root_pose.to_transformation_matrix().tolist()})
        if current and current.get('base_action') and current['physical_steps']>=current['step_budget']:
            error=abs(q[self.rig.base]-self.rig.goal[self.rig.base]);speed=abs(v[self.rig.base])
            ok=bool(np.max(error[:2])<=.002 and error[2]<=.003 and np.max(speed)<=.005)
            current.update(base_final_error=error.tolist(),base_final_velocity=speed.tolist())
            if not ok:
                current.update(status='failed',error={'code':'base_convergence_timeout','message':'Base ended outside frozen position/velocity bounds'})
            self.log('base_finished',action=current.copy())
        # The shared physical guard checks chassis, both arms and all kitchen contacts.
        if self.renderer and not self.renderer.is_alive() and not getattr(self,'display_failure_reported',False):
            from .display import atomic
            self.display_failure_reported=True
            atomic(self.display_out/'latest.json',json.dumps({'state':'disconnected','error':'Display process exited unexpectedly'}).encode())
            self.log('display_disconnected',exit_code=self.renderer.exitcode)
        if self.renderer and self.renderer.is_alive() and time.monotonic()-self.last_publish>=.05:
            self.last_publish=time.monotonic()
            # Small actual state capture; no RGB, no wait and no extra physics.
            control=self.state()
            from .control_recording import arm_control_sample
            snapshot={'control':arm_control_sample(control),'step':self.total_steps,'epoch':self.epoch,'qpos':q.tolist(),
                'actors':[actor.pose.to_transformation_matrix().tolist() for actor in self.display_entities],
                'monotonic':self.last_publish,'unix':time.time()}
            try:self.display_queue.put_nowait(snapshot)
            except queue.Full:self.snapshot_dropped+=1

    def run(self):
        try:super().run()
        finally:
            (self.evidence/'base_trajectory.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.base_log))
            if self.renderer:
                self.display_done.set();self.renderer.join(5)
                if self.renderer.is_alive():self.renderer.terminate();self.renderer.join(3)
                if self.renderer.is_alive():self.renderer.kill();self.renderer.join(3)
                self.log('display_reaped',pid=self.renderer.pid,exit_code=self.renderer.exitcode,alive=self.renderer.is_alive())


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8873)
    parser.add_argument('--evidence',required=True,type=Path)
    parser.add_argument('--task',default='counter_to_sink')
    parser.add_argument('--seed',type=int,default=101)
    parser.add_argument('--arm',choices=['left','right','both'],default='both')
    parser.add_argument('--parameters',type=Path,default=PARAMETERS)
    parser.add_argument('--fault-after-steps',type=int)
    parser.add_argument('--no-display',action='store_true')
    args=parser.parse_args();args.evidence.mkdir(parents=True,exist_ok=False)
    runtime=Runtime(args.evidence,task_id=args.task,seed=args.seed,execution_arm=args.arm,
        parameters_path=args.parameters,fault_after_steps=args.fault_after_steps,display=not args.no_display)
    server=foundation.Server(('127.0.0.1',args.port),foundation.Handler);server.runtime=runtime;server.timeout=.1
    signal.signal(signal.SIGTERM,lambda *_:runtime.closing.set());signal.signal(signal.SIGINT,lambda *_:runtime.closing.set())
    runtime.thread.start()
    try:
        while not runtime.closing.is_set():server.handle_request()
    finally:
        runtime.closing.set();server.server_close();runtime.thread.join(12)
        if runtime.thread.is_alive():raise RuntimeError('Mobile simulation owner did not terminate')
    return int(runtime.error is not None)


if __name__=='__main__':sys.exit(main())
