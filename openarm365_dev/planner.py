"""Authenticated official V2 planning from real MuJoCo state, with no fallback."""
import json
import os
from pathlib import Path
import numpy as np
import yaml
from scipy.spatial.transform import Rotation
from curobo_v2.identity import IdentityError,load_lock,verify_files,verify_client_imports
from curobo_v2.client import PlanningRejected
from curobo_v2.session import V2Service
from curobo_v2.trajectory import TrajectoryLedger
from curobo_v2.ik_path_jump import DEFAULT_MAX_JOINT_JUMP,diagnostic_max_joint_jump
from .environment import joint_address, mount_pose, tcp_pose
from .world import snapshot

def contact_exemption_mode(configured):
    if configured not in ('none','movable_objects'):
        raise ValueError('contact_exempt_objects must be none or movable_objects')
    override=os.environ.get('ATTN_DIAG_CONTACT_EXEMPT')
    if override is not None and override not in ('none','movable_objects'):
        raise ValueError('ATTN_DIAG_CONTACT_EXEMPT must be none or movable_objects')
    return override if override is not None else configured

class MujocoV2Planner:
    def __init__(self, env, *, evidence, deadline, cancel_check, contact_exempt_objects='none'):
        self.env=env;self.evidence=Path(evidence);self.evidence.mkdir(parents=True,exist_ok=True)
        self.contact_exemption_configured=contact_exempt_objects
        self.contact_exemption_mode=contact_exemption_mode(contact_exempt_objects)
        lock_path=Path('/home/truares/Documents/Codex/2026-10-11/new-chat-2/outputs/openarm-dev-integration/deployment-04/runtime-lock.json')
        lock,sha=load_lock(lock_path)
        if sha!='6e289e0db4025f47e629fb807b963ca384af3f3543a406696a64912b600d000e':
            raise IdentityError('OpenArm365 dual-arm deployment lock changed')
        verify_files(lock);verify_client_imports(lock)
        config=Path(lock['project_root'])/'config/openarm_fixed.yml'
        self.spec=yaml.safe_load(config.read_text());self.names=self.spec['service']['joint_names']
        self.locks=list(self.spec['robot_cfg']['kinematics']['lock_joints'])
        self.tools=self.spec['robot_cfg']['kinematics']['tool_frames']
        self.chassis_binding=self.spec['service']['native_chassis']
        self.addresses=[joint_address(env.sim,n) for n in self.names]
        self.lock_addresses={n:joint_address(env.sim,n) for n in self.locks}
        self.ledger=TrajectoryLedger();self.sequence=0;self.failed=False;self.prepared=set()
        self.attachment_provider=lambda:None
        self.owner=V2Service(lock_path=lock_path,robot_config=config,evidence=self.evidence,
            deadline=deadline,cancel_check=cancel_check)
        self.client=self.owner.__enter__()

    def state(self):return np.asarray(self.env.sim.data.qpos[self.addresses],float)
    def body(self, active):
        return dict(env_id='robocasa365',current_q=self.state().tolist(),joint_names=self.names,
            locked_joints={n:float(self.env.sim.data.qpos[a]) for n,a in self.lock_addresses.items()},active_joints=list(active))
    def guard(self, operation):
        if self.failed:raise PlanningRejected('Authenticated 365 planner latched; reset required')
        try:return operation()
        except BaseException:
            self.failed=True;self.ledger.invalidate('planner_failure');self.owner.close('planner_failure');raise
    def world_snapshot(self):
        if self.contact_exemption_mode=='none':
            return snapshot(self.env,chassis_binding=self.chassis_binding)
        from .world import movable_object_roots
        roots=movable_object_roots(self.env)
        held=self.attachment_provider()
        attached=held['target_id'] if held else None
        return snapshot(self.env,chassis_binding=self.chassis_binding,
            exempt_body_ids=set(roots.values()),attached_body_id=attached)
    def upload(self):
        world=self.world_snapshot();self.sequence+=1
        (self.evidence/f'world-{self.sequence:04d}.json').write_text(json.dumps(world,indent=2)+'\n')
        receipt=self.client.call('/world/cuboids',dict(env_id='robocasa365',cuboids=world['cuboids']))
        if (self.contact_exemption_mode!='none' or self.contact_exemption_configured!='none'
                or os.environ.get('ATTN_DIAG_CONTACT_EXEMPT') is not None):
            from .world import movable_object_roots
            roots=movable_object_roots(self.env)
            held=self.attachment_provider()
            audit=dict(mode=self.contact_exemption_mode,configured=self.contact_exemption_configured,
                source='ATTN_DIAG_CONTACT_EXEMPT' if os.environ.get('ATTN_DIAG_CONTACT_EXEMPT') is not None else 'config',
                objects=[{'name':name,'body_id':bid} for name,bid in roots.items()
                    if self.contact_exemption_mode!='none' and (not held or bid!=held['target_id'])],
                attached_object=next((name for name,bid in roots.items() if held and bid==held['target_id']),None),
                exempted_geom_count=world.get('exempted_geom_count',0),world_sha256=world['world_sha256'],
                world_sha256_unexempted=world.get('world_sha256_unexempted',world['world_sha256']))
            receipt={**receipt,'contact_exemption':audit}
        return world,receipt
    def prepare(self, active):
        def op():
            self.upload();body=self.body(active)
            self.client.call('/warmup',{k:v for k,v in body.items() if k!='env_id'})
            fk=self.client.call('/fk',body);errors={}
            for side in ('left','right'):
                tool=f'openarm_{side}_hand_tcp';got=fk['poses'][tool]
                actual=np.linalg.inv(mount_pose(self.env))@tcp_pose(self.env,side)
                rotation=Rotation.from_quat(np.array(got['quaternion_wxyz'])[[1,2,3,0]]).as_matrix()
                pe=float(np.linalg.norm(actual[:3,3]-got['position']))
                re=float(Rotation.from_matrix(rotation@actual[:3,:3].T).magnitude())
                errors[tool]=dict(position_error_m=pe,rotation_error_rad=re)
            (self.evidence/'alignment.json').write_text(json.dumps(errors,indent=2)+'\n')
            if any(e['position_error_m']>1e-5 or e['rotation_error_rad']>1e-5 for e in errors.values()):
                raise IdentityError('Actual 365 robot/TCP differs from authenticated V2 URDF')
            self.prepared.add(tuple(active));return errors
        return self.guard(op)
    def plan(self,path,extra,active,epoch):
        def op():
            if tuple(active) not in self.prepared:self.prepare(active)
            world,receipt=self.upload();body=self.body(active);body.update(extra)
            from .attachment import planner_context
            attachment=self.attachment_provider()
            body['attachment']=planner_context(self.env,attachment,world,self.tools,self.evidence)
            warm={k:v for k,v in body.items() if k not in {'targets','target_qpos','ik_candidates','max_joint_jump'}}
            if 'targets' in body:warm['tool_frames']=list(body['targets'])
            self.client.call('/warmup',warm)
            response=self.client.call(path,body)
            if path=='/plan/ik_path':
                response['max_joint_jump']=body.get('max_joint_jump',DEFAULT_MAX_JOINT_JUMP)
                response['max_joint_jump_source']='override' if 'max_joint_jump' in body else 'default'
            pid=self.ledger.issue(response,epoch=epoch,state=body['current_q'],world=world['cuboids'],
                identity=self.client.identity,attachment=attachment)
            response.update(plan_id=pid,epoch=epoch,world_receipt=receipt)
            if 'contact_exemption' in receipt:response['contact_exemption']=receipt['contact_exemption']
            (self.evidence/f'trajectory-{self.sequence:04d}.json').write_text(json.dumps(response,indent=2)+'\n')
            return response
        return self.guard(op)
    def plan_joints(self,target,*,side,epoch):
        active=self.names if side=='both' else [n for n in self.names if n.startswith(f'openarm_{side}_joint')]
        return self.plan('/plan/joint',dict(target_qpos=np.asarray(target,float).tolist()),active,epoch)
    def plan_pose(self,position,orientation,*,side,epoch):
        if side not in {'left','right'}:raise ValueError('Physical side required')
        local=np.linalg.inv(mount_pose(self.env));pos=(local@np.r_[position,1])[:3]
        rot=local[:3,:3]@orientation;quat=Rotation.from_matrix(rot).as_quat()[[3,0,1,2]].tolist()
        active=[f'openarm_{side}_joint{i}' for i in range(1,8)]
        mode=os.environ.get('ATTN_PLANNER_MODE','plan')
        if mode not in {'plan','ik_path'}:
            raise ValueError('ATTN_PLANNER_MODE must be plan or ik_path')
        max_joint_jump=diagnostic_max_joint_jump(mode)
        extra=dict(targets={f'openarm_{side}_hand_tcp':dict(position=pos.tolist(),quaternion_wxyz=quat)})
        if mode=='ik_path':
            raw=os.environ.get('ATTN_IK_CANDIDATES','1')
            if not raw.isdecimal() or int(raw)<1:
                raise ValueError('ATTN_IK_CANDIDATES must be a positive integer')
            extra['ik_candidates']=int(raw)
            if max_joint_jump is not None:extra['max_joint_jump']=max_joint_jump
        return self.plan('/plan' if mode=='plan' else '/plan/ik_path',extra,active,epoch)
    def claim(self,pid,epoch):
        def op():
            self.client.attest()
            return self.ledger.claim(pid,epoch=epoch,state=self.state(),world=self.world_snapshot()['cuboids'],
                identity=self.client.identity,attachment=self.attachment_provider())
        return self.guard(op)
    def close(self):
        self.ledger.invalidate('close');return self.owner.close()
