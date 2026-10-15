"""Native MuJoCo tracking of one-use authenticated V2 command curves."""
import hashlib,json,math,os,time,uuid
from pathlib import Path
import xml.etree.ElementTree as E
import numpy as np
from curobo_v2.command_curve import sample_for_original_drive
from curobo_v2.client import PlanningRejected
from .environment import PHYSICAL_TO_NATIVE,tcp_pose
from .world import is_descendant

class NativeExecutor:
    def __init__(self,env,planner,*,evidence,deadline,cancel_check,frame_callback=None):
        self.env,self.planner=env,planner;self.evidence=Path(evidence)
        self.deadline,self.cancel_check=deadline,cancel_check;self.frame_callback=frame_callback
        self.epoch=uuid.uuid4().hex;self.sequence=0;self.physics_steps=0;self.failed=False
        self.goals=planner.state().copy();self.grips={'left':0.,'right':0.}
        self.parts=env.robots[0].composite_controller._action_split_indexes
        if any(self.parts[n][1]-self.parts[n][0]!=7 for n in ('left','right')):
            raise ValueError('Native dual-arm joint controller layout mismatch')
        self.base_initial=env.sim.data.qpos[env.robots[0]._ref_base_joint_pos_indexes].copy()
        self.robot_root=env.sim.model.body_name2id('robot0_openarm_body_link0')
        xml=E.parse(planner.spec['robot_cfg']['kinematics']['urdf_path']).getroot()
        joints={j.get('name'):j for j in xml.findall('joint')}
        self.limits=np.array([[float(joints[n].find('limit').get(k)) for k in ('lower','upper')] for n in planner.names])
        self.max_speed={n:float(joints[n].find('limit').get('velocity')) for n in planner.names}
        self.gripper_limits={side:np.array([[float(joints[f'openarm_{side}_finger_joint{i}'].find('limit').get(k))
            for k in ('lower','upper','velocity')] for i in (1,2)]) for side in PHYSICAL_TO_NATIVE}
        self.moving=False;self.gripping=False;self.active_request=None
        self.held=None;self.held_transform=None;self.grasp_counts={'left':{},'right':{}}
        self.planner.attachment_provider=lambda:self.held.copy() if self.held else None
        self.journal=self.evidence/'native-actions.jsonl';self.physics=self.evidence/'native-physics.jsonl'
        self._step2=env.sim.step2
        def step2():
            self._step2()
            if self.moving:self.monitor()
        env.sim.step2=step2

    def log(self,path,value):
        with path.open('a') as f:
            f.write(json.dumps(value,sort_keys=True,allow_nan=False)+'\n');f.flush();os.fsync(f.fileno())

    def check_live(self):
        if self.failed:raise RuntimeError('Native executor latched after failure; reset required')
        if self.cancel_check():raise InterruptedError('operator cancelled')
        if time.monotonic()>=self.deadline:raise TimeoutError('native execution deadline')

    def action(self):
        a=np.zeros(len(self.env.action_spec[0]))
        for side,native in PHYSICAL_TO_NATIVE.items():
            indices=[self.planner.names.index(f'openarm_{side}_joint{i}') for i in range(1,8)]
            start,end=self.parts[native];a[start:end]=self.goals[indices]
            start,end=self.parts[native+'_gripper'];a[start:end]=self.grips[side]
        return a

    def monitor(self):
        self.physics_steps+=1
        sim=self.env.sim;r=self.env.robots[0];q=self.planner.state()
        v=np.asarray(sim.data.qvel[r._ref_arm_joint_vel_indexes],float)
        if np.any(q<self.limits[:,0]) or np.any(q>self.limits[:,1]):raise RuntimeError('Measured native joint limit exceeded')
        if np.any(abs(v)>np.array([self.max_speed[n] for n in self.planner.names])):
            raise RuntimeError('Measured native joint velocity exceeded URDF limit')
        base=sim.data.qpos[r._ref_base_joint_pos_indexes]
        if np.max(abs(base-self.base_initial))>.001:raise RuntimeError('Stationary base drift exceeded 1mm/1mrad')
        gripper_q={side:np.asarray(sim.data.qpos[r._ref_gripper_joint_pos_indexes[native]],float) for side,native in PHYSICAL_TO_NATIVE.items()}
        gripper_v={side:np.array([sim.data.qvel[sim.model.get_joint_qvel_addr(name)] for name in r.gripper[native].joints])
            for side,native in PHYSICAL_TO_NATIVE.items()}
        for side,values in gripper_q.items():
            lim=self.gripper_limits[side]
            if np.any(values<lim[:,0]) or np.any(values>lim[:,1]) or np.any(abs(gripper_v[side])>lim[:,2]):
                raise RuntimeError('Measured native gripper position/velocity exceeds original URDF limit')
            if abs(values[0]-values[1])>.001:raise RuntimeError('Measured gripper mimic error exceeds original 1mm bound')
        contacts=[]
        from .attachment import bilateral_contacts,object_pose
        bilateral=bilateral_contacts(self.env)
        for side in PHYSICAL_TO_NATIVE:
            for bid in set(self.grasp_counts[side])|bilateral[side]:
                self.grasp_counts[side][bid]=self.grasp_counts[side].get(bid,0)+1 if bid in bilateral[side] else 0
        if self.held and not self.gripping:
            side=self.held['holder'];bid=self.held['target_id']
            if bid not in bilateral[side]:raise RuntimeError('Measured bilateral grasp contact lost during transport')
            relative=np.linalg.inv(tcp_pose(self.env,side))@object_pose(self.env,bid)
            from scipy.spatial.transform import Rotation
            if (np.linalg.norm(relative[:3,3]-self.held_transform[:3,3])>.004 or
                    Rotation.from_matrix(relative[:3,:3]@self.held_transform[:3,:3].T).magnitude()>.03):
                raise RuntimeError('Measured object/hand transform exceeds original 4mm/0.03rad bound')
        objects=set(getattr(self.env,'obj_body_id',{}).values())
        for con in sim.data.contact:
            g1,g2=int(con.geom1),int(con.geom2);b1,b2=int(sim.model.geom_bodyid[g1]),int(sim.model.geom_bodyid[g2])
            a=is_descendant(sim.model,b1,self.robot_root);b=is_descendant(sim.model,b2,self.robot_root)
            if con.dist>=0 or not (a or b):continue
            if a and b:
                if sim.model.body_weldid[b1]==sim.model.body_weldid[b2]:continue
                n1=sim.model.geom_id2name(g1) or '';n2=sim.model.geom_id2name(g2) or ''
                opposing_fingers=('finger' in n1 and 'finger' in n2 and
                    any(f'openarm_{s}_' in n1 and f'openarm_{s}_' in n2 for s in ('left','right')))
                if opposing_fingers:continue
                contacts.append([n1,n2,float(con.dist)]);continue
            robot_geom=g1 if a else g2;other_body=b2 if a else b1
            robot_name=sim.model.geom_id2name(robot_geom) or ''
            allowed=('finger' in robot_name and
                ((self.gripping and any(is_descendant(sim.model,other_body,obj) for obj in objects)) or
                 (self.held and is_descendant(sim.model,other_body,self.held['target_id']))))
            if not allowed:contacts.append([sim.model.geom_id2name(g1),sim.model.geom_id2name(g2),float(con.dist)])
        row=dict(request_id=self.active_request,physics_step=self.physics_steps,qpos=q.tolist(),qvel=v.tolist(),
            base_qpos=base.tolist(),gripper_qpos={side:v.tolist() for side,v in gripper_q.items()},
            gripper_qvel={side:v.tolist() for side,v in gripper_v.items()},
            tcp={side:tcp_pose(self.env,side).tolist() for side in PHYSICAL_TO_NATIVE},unexpected_contacts=contacts)
        row['bilateral_contact_objects']={side:sorted(values) for side,values in bilateral.items()}
        self.log(self.physics,row)
        if contacts:raise RuntimeError('Unexpected physical robot/world contact')

    def control_step(self):
        if self.sequence>=self.env.horizon:raise RuntimeError('Native control-step budget exhausted')
        self.check_live();self.env.step(self.action());self.sequence+=1
        if self.frame_callback:self.frame_callback(self.sequence)

    def apply(self,response,*,request_id):
        self.check_live()
        plan_id=response['plan_id']
        response=self.planner.claim(plan_id,self.epoch)
        dt=float(self.env.control_timestep)
        count=math.ceil(response['time_from_start_seconds'][-1]/dt)+20
        if count>100:raise PlanningRejected('Official timing exceeds 100-control-step action ceiling')
        kin=self.planner.spec['robot_cfg']['kinematics']['cspace']
        samples,certificate=sample_for_original_drive(response,joint_names=self.planner.names,
            maximum_speed=self.max_speed,position_limits=self.limits,max_acceleration=kin['max_acceleration'],
            max_jerk=kin['max_jerk'],dt=dt,step_budget=count,settle_steps=20)
        self.active_request=request_id;start=self.sequence;before=self.planner.state().copy()
        self.log(self.journal,dict(event='intent',request_id=request_id,epoch=self.epoch,
            plan_id=plan_id,start_sequence=start,command_curve=certificate))
        self.moving=True
        try:
            for target in samples[1:]:self.goals=target.copy();self.control_step()
            for _ in range(20):self.control_step()
            error=float(np.max(abs(self.planner.state()-samples[-1])))
            if error>.004:raise RuntimeError('Native joint tracking exceeds 4mrad endpoint bound')
            receipt=dict(status='completed',request_id=request_id,epoch=self.epoch,start_sequence=start,
                sequence=self.sequence,physics_steps=self.physics_steps,before_qpos=before.tolist(),
                after_qpos=self.planner.state().tolist(),maximum_endpoint_joint_error_rad=error)
            self.log(self.journal,dict(event='receipt',**receipt));return receipt
        except BaseException:
            self.failed=True;self.epoch=uuid.uuid4().hex;self.planner.ledger.invalidate('native_failure');raise
        finally:self.moving=False;self.active_request=None

    def move_joints(self,target,*,side,request_id):
        self.check_live()
        # Held-object transport needs its own measured attachment model. Until
        # that adapter is available this entry fails closed, never ignores it.
        if self.any_object_contact() and not self.held:raise PlanningRejected('Measured object contact requires a confirmed bilateral grasp before arm motion')
        response=self.planner.plan_joints(target,side=side,epoch=self.epoch)
        return self.apply(response,request_id=request_id)

    def any_object_contact(self):
        sim=self.env.sim;objects=set(getattr(self.env,'obj_body_id',{}).values())
        for con in sim.data.contact:
            if con.dist>0:continue
            bodies=[int(sim.model.geom_bodyid[g]) for g in (con.geom1,con.geom2)]
            if any(is_descendant(sim.model,b,self.robot_root) for b in bodies) and any(any(is_descendant(sim.model,b,obj) for obj in objects) for b in bodies):return True
        return False

    def move_delta(self,side,dx,dy,dz,rotation_delta=None,*,request_id):
        self.check_live()
        from scipy.spatial.transform import Rotation
        delta=np.array([dx,dy,dz],float);rotation=np.array(rotation_delta if rotation_delta is not None else [0,0,0],float)
        if (delta.shape!=(3,) or rotation.shape!=(3,) or not np.isfinite(np.r_[delta,rotation]).all()
                or np.any(abs(delta)>.05) or np.any(abs(rotation)>.5)):
            raise ValueError('World-frame deltas limited to .05m/.5rad per coordinate')
        if self.any_object_contact() and not self.held:raise PlanningRejected('Object contact requires confirmed bilateral grasp before arm motion')
        current=tcp_pose(self.env,side)
        response=self.planner.plan_pose(current[:3,3]+delta,Rotation.from_rotvec(rotation).as_matrix()@current[:3,:3],side=side,epoch=self.epoch)
        return self.apply(response,request_id=request_id)

    def gripper(self,side,command,*,request_id):
        self.check_live()
        if side not in PHYSICAL_TO_NATIVE or command not in (-1.,1.):raise ValueError('Explicit physical gripper/command required')
        self.active_request=request_id;self.gripping=True;self.moving=True;start=self.sequence
        self.log(self.journal,dict(event='gripper_intent',request_id=request_id,side=side,command=command,sequence=start))
        try:
            if command<0 and self.held and self.held['holder']==side:
                self.held=None;self.held_transform=None;self.planner.ledger.invalidate('release')
            # Native position actuators have soft limits; keep commanded endpoints
            # 1mm inside the original [0,.044] range. Never clamp measured state.
            self.grips[side]=command*(1-2*.001/.044)
            for _ in range(20):self.control_step()
            if command>0:
                candidates=[bid for bid,count in self.grasp_counts[side].items() if count>=12]
                if len(candidates)>1:raise RuntimeError('Ambiguous measured grasp target')
                if candidates:
                    if self.held and self.held['holder']!=side:raise RuntimeError('Dual-held transport is outside this dev profile')
                    from .attachment import object_pose
                    self.held=dict(holder=side,target_id=candidates[0])
                    self.held_transform=np.linalg.inv(tcp_pose(self.env,side))@object_pose(self.env,candidates[0])
                    self.planner.ledger.invalidate('grasp_acquired')
            result=dict(status='completed',request_id=request_id,physical_side=side,start_sequence=start,sequence=self.sequence)
            self.log(self.journal,dict(event='receipt',**result));return result
        except BaseException:
            self.failed=True;self.epoch=uuid.uuid4().hex;self.planner.ledger.invalidate('gripper_failure');raise
        finally:self.active_request=None;self.gripping=False;self.moving=False

    def close(self):
        self.epoch=uuid.uuid4().hex;self.planner.ledger.invalidate('stop')
        self.env.sim.step2=self._step2
        return self.planner.close()
