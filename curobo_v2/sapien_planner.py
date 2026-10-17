"""V2 adapter for an existing SAPIEN kitchen and its unchanged physical rig."""
from __future__ import annotations
import json
import os
from pathlib import Path
import time
import numpy as np
import yaml
from scipy.spatial.transform import Rotation
from .identity import IdentityError,digest
from .client import PlanningRejected
from .session import V2Service
from .sapien_world import snapshot
from .trajectory import TrajectoryLedger


class SapienV2Planner:
    def __init__(self, kitchen, *, lock_path, robot_config, evidence, deadline,
                 cancel_check, attachment_provider):
        self.kitchen,self.rig=kitchen,kitchen.rig
        self.evidence=Path(evidence);self.evidence.mkdir(parents=True,exist_ok=True)
        config=yaml.safe_load(Path(robot_config).read_text())
        self.names=config['service']['joint_names'];self.tools=config['robot_cfg']['kinematics']['tool_frames']
        self.locks=list(config['robot_cfg']['kinematics'].get('lock_joints',{}))
        self.motion_limits={k:config['robot_cfg']['kinematics']['cspace'][k]for k in ['max_acceleration','max_jerk']}
        if (digest(Path(config['robot_cfg']['kinematics']['urdf_path']))!=digest(Path(self.rig.urdf))
                or not set(self.names+self.locks)<=set(self.rig.names)):
            raise IdentityError('V2 configuration differs from actual physical robot URDF/joints')
        self.indices=[self.rig.names.index(n) for n in self.names]
        self.attachment_provider=attachment_provider
        self.ledger=TrajectoryLedger();self.sequence=0;self.prepared=set();self.failed=False
        self.owner=V2Service(lock_path=lock_path,robot_config=robot_config,evidence=self.evidence,
                             deadline=deadline,cancel_check=cancel_check)
        self.client=self.owner.__enter__()

    def state(self):
        return np.asarray(self.rig.robot.qpos,float)[self.indices]

    def lock_state(self):
        q=np.asarray(self.rig.robot.qpos,float)
        return {n:float(q[self.rig.names.index(n)]) for n in self.locks}

    def world(self):
        return snapshot(self.kitchen.scene,self.rig.robot,
            planner_from_world=np.linalg.inv(self.rig.robot.root_pose.to_transformation_matrix()))

    def body(self, active_joints):
        return dict(env_id='kitchen',current_q=self.state().tolist(),joint_names=self.names,
                    locked_joints=self.lock_state(),active_joints=list(active_joints))

    def upload(self):
        world=self.world();self.sequence+=1
        (self.evidence/f'world-{self.sequence:04d}.json').write_text(json.dumps(world,indent=2)+'\n')
        receipt=self.client.call('/world/cuboids',dict(env_id='kitchen',cuboids=world['cuboids']))
        return world,receipt

    def _guard(self, operation):
        if self.failed:raise PlanningRejected('V2 kitchen planner is latched after a failure; reset required')
        try:return operation()
        except Exception:
            self.failed=True;self.ledger.invalidate('planning_failure');self.owner.close('planning_failure')
            raise

    def prepare(self, active_joints, *, target_tools):
        def operation():
            self.upload();body=self.body(active_joints)
            self.client.call('/warmup',{k:v for k,v in body.items() if k!='env_id'})
            fk=self.client.call('/fk',body)
            mount=self.rig.robot.root_pose.to_transformation_matrix();errors={}
            for tool in self.tools:
                expected=np.linalg.inv(mount)@self.rig.robot.find_link_by_name(tool).entity_pose.to_transformation_matrix()
                got=fk['poses'][tool];rotation=Rotation.from_quat(np.array(got['quaternion_wxyz'])[[1,2,3,0]]).as_matrix()
                pe=float(np.linalg.norm(expected[:3,3]-got['position']))
                re=float(np.linalg.norm(Rotation.from_matrix(rotation@expected[:3,:3].T).as_rotvec()))
                errors[tool]=dict(position_error_m=pe,rotation_error_rad=re)
                if pe>1e-5 or re>1e-5:raise IdentityError('Actual robot/TCP/frame differs from authenticated V2 model')
            (self.evidence/'alignment.json').write_text(json.dumps(errors,indent=2)+'\n')
            if target_tools!=self.tools:
                self.client.call('/warmup',dict({k:v for k,v in body.items()if k!='env_id'},tool_frames=target_tools))
            self.prepared.add(tuple(active_joints))
            return errors
        return self._guard(operation)

    def _plan(self, path, extra, *, active_joints, epoch):
        def operation():
            if tuple(active_joints)not in self.prepared:
                raise PlanningRejected('Explicit bounded V2 model preparation is required before planning')
            attachment=self.attachment_provider()
            world,receipt=self.upload();body=self.body(active_joints);body.update(extra)
            body['attachment']=self.attachment_context(attachment,world)
            # A gripper action changes measured immutable locks. Compile that
            # exact model explicitly before the bounded planning call; no lock
            # rounding, solver retry or substitute target is permitted.
            preparation={k:v for k,v in body.items() if k not in {'targets','target_qpos'}}
            if 'targets' in body:preparation['tool_frames']=list(body['targets'])
            self.client.call('/warmup',preparation)
            result=self.client.call(path,body)
            pid=self.ledger.issue(result,epoch=epoch,state=body['current_q'],world=world['cuboids'],
                                  identity=self.client.identity,attachment=attachment)
            result.update(plan_id=pid,epoch=epoch,planner_generation=2,planner_nonce=self.client.nonce,
                          world_receipt=receipt)
            (self.evidence/f'trajectory-{self.sequence:04d}.json').write_text(json.dumps(result,indent=2)+'\n')
            return result
        return self._guard(operation)

    def attachment_context(self,attachment,world):
        if attachment is None:return None
        target=self.kitchen.target_entity
        side=attachment['holder'];identifier=target.per_scene_id
        if attachment!={'holder':side,'target_id':identifier}:raise PlanningRejected('Measured grasp holder/target changed')
        tools=f'openarm_{side}_hand_tcp'
        mount=self.rig.robot.root_pose.to_transformation_matrix()
        tcp=self.rig.robot.find_link_by_name(tools).entity_pose.to_transformation_matrix()
        object_pose=target.pose.to_transformation_matrix();support_ids=set()
        robot_ids={link.entity.per_scene_id for link in self.rig.robot.links}
        for contact in self.kitchen.scene.get_contacts():
            a,b=[body.entity.per_scene_id for body in contact.bodies]
            for first,other in [(a,b),(b,a)]:
                if first==identifier and other not in robot_ids and any(p.separation<=0 for p in contact.points):support_ids.add(other)
        context=dict(attachment,object_pose=(np.linalg.inv(mount)@object_pose).tolist(),
            tcp_object_transform=(np.linalg.inv(tcp)@object_pose).tolist(),tool_frame=tools,
            shape_names=[c['name']for c in world['inventory']if c['entity_id']==identifier],
            support_names=[c['name']for c in world['inventory']if c['entity_id']in support_ids])
        from .attachment import SingleArmAttachment
        SingleArmAttachment(context,world['cuboids'],self.tools)
        return context

    def plan_joints(self, target, *, active_joints, epoch):
        from .native_requests import finite_vector
        target=finite_vector(target,len(self.names),'target_qpos')
        return self._plan('/plan/joint',dict(target_qpos=target.tolist()),
                          active_joints=active_joints,epoch=epoch)

    def plan_pose(self, position, orientation, *, side, epoch):
        from .native_requests import finite_vector,rotation_matrix
        target=finite_vector(position,3,'position');orientation=rotation_matrix(orientation)
        mount=self.rig.robot.root_pose.to_transformation_matrix();local=np.linalg.inv(mount)
        position=(local@np.r_[target,1])[:3];rotation=local[:3,:3]@orientation
        quat=Rotation.from_matrix(rotation).as_quat()[[3,0,1,2]].tolist()
        active=[f'openarm_{side}_joint{i}'for i in range(1,8)]
        mode=os.environ.get('ATTN_PLANNER_MODE','plan')
        if mode not in {'plan','ik_path'}:
            raise ValueError('ATTN_PLANNER_MODE must be plan or ik_path')
        extra=dict(targets={f'openarm_{side}_hand_tcp':dict(position=position.tolist(),quaternion_wxyz=quat)})
        if mode=='ik_path':
            raw=os.environ.get('ATTN_IK_CANDIDATES','1')
            if not raw.isdecimal() or int(raw)<1:
                raise ValueError('ATTN_IK_CANDIDATES must be a positive integer')
            extra['ik_candidates']=int(raw)
        return self._plan('/plan' if mode=='plan' else '/plan/ik_path',extra,
                          active_joints=active,epoch=epoch)

    def claim(self, plan_id, *, epoch):
        def operation():
            self.client.attest()
            return self.ledger.claim(plan_id,epoch=epoch,state=self.state(),world=self.world()['cuboids'],
                                     identity=self.client.identity,attachment=self.attachment_provider())
        return self._guard(operation)

    def invalidate(self, reason):
        self.ledger.invalidate(reason)

    def close(self):
        self.invalidate('close');self.owner.close()
