"""Invalidate an executing trajectory before driving against a changed kitchen."""
from __future__ import annotations
import numpy as np
from .client import PlanningRejected


def pose_changes(before, after, *, translation=1e-4, angle=1e-4):
    a,b=np.asarray(before,float),np.asarray(after,float)
    if a.shape!=b.shape or a.ndim!=2 or a.shape[1]!=7 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise PlanningRejected('Kitchen pose inventory changed or became nonfinite')
    displacement=np.linalg.norm(a[:,:3]-b[:,:3],axis=1)
    qa,qb=a[:,3:],b[:,3:]
    if np.any(abs(np.linalg.norm(qb,axis=1)-1)>1e-5)or np.any(abs(np.linalg.norm(qa,axis=1)-1)>1e-5):raise PlanningRejected('Invalid physical kitchen quaternion')
    # PhysX returns float32 quaternions. Normalize the dot product rather than
    # interpreting harmless norm rounding as a physical rotation.
    cosine=np.sum(qa*qb,axis=1)/(np.linalg.norm(qa,axis=1)*np.linalg.norm(qb,axis=1))
    rotation=2*np.arccos(np.clip(abs(cosine),0.,1.))
    return np.flatnonzero((displacement>translation)|(rotation>angle)).tolist()


class GraspLost(PlanningRejected):
    code='grasp_lost'


class AttachmentShifted(PlanningRejected):
    code='attachment_transform_changed'


class KitchenWorldMonitor:
    def __init__(self, scene, robot, *, attachment=None, attachment_provider=None, execution_settings=None):
        self.execution_settings=execution_settings
        import sapien
        self.attachment,self.attachment_provider=attachment,attachment_provider
        if attachment is not None and attachment_provider is None:raise PlanningRejected("Carried motion requires continuous bilateral contact checks")
        self.px=sapien.physx;self.scene,self.robot=scene,robot
        self.entities=self._entities();self.ids=[e.per_scene_id for e in self.entities]
        self.shapes=self._shapes();self.reference=self._poses();self.local=self._local()
        self.attachment_transform=None if attachment is None else self._attachment_transform()

    def _entities(self):
        robot_id=self.robot.root.entity.per_scene_id
        entities={e.per_scene_id:e for e in self.scene.get_all_actors()}
        for art in self.scene.get_all_articulations():
            if art.root.entity.per_scene_id!=robot_id:
                entities.update({link.entity.per_scene_id:link.entity for link in art.links})
        return [e for _,e in sorted(entities.items())if (b:=e.find_component_by_type(self.px.PhysxRigidBaseComponent))is not None and b.collision_shapes]

    def _poses(self):
        poses=[e.pose for e in self.entities]
        return np.asarray([np.concatenate((p.p,p.q))for p in poses],float)

    def _shapes(self):
        return [tuple(e.find_component_by_type(self.px.PhysxRigidBaseComponent).collision_shapes)for e in self.entities]

    def _local(self):
        poses=[s.local_pose for group in self._shapes()for s in group]
        return np.asarray([np.concatenate((p.p,p.q))for p in poses],float)

    def _attachment_transform(self):
        target=next((e for e in self.entities if e.per_scene_id==self.attachment['target_id']),None)
        if target is None:raise GraspLost('Carried entity absent from actual world')
        tool=self.robot.find_link_by_name(f"openarm_{self.attachment['holder']}_hand_tcp")
        return np.linalg.inv(tool.entity_pose.to_transformation_matrix())@target.pose.to_transformation_matrix()

    def _check_attachment(self):
        from .attachment import effective_held_envelope
        translation,rotation=effective_held_envelope(getattr(self,"execution_settings",None))
        from scipy.spatial.transform import Rotation
        if self.attachment_provider()!=self.attachment:raise GraspLost('Bilateral grasp lost during physical carrying')
        pose=self._attachment_transform();reference=self.attachment_transform
        if (np.linalg.norm(pose[:3,3]-reference[:3,3])>translation or
                np.linalg.norm(Rotation.from_matrix(pose[:3,:3]@reference[:3,:3].T).as_rotvec())>rotation):
            raise AttachmentShifted('Carried geometry moved outside its collision envelope')

    def check(self):
        if [e.per_scene_id for e in self._entities()]!=self.ids or self._shapes()!=self.shapes:
            raise PlanningRejected('Kitchen collision shape inventory changed during execution')
        if pose_changes(self.local,self._local()):raise PlanningRejected('Kitchen collision shape local pose changed during execution')
        changed=pose_changes(self.reference,self._poses())
        if self.attachment is not None:
            self._check_attachment()
            changed=[i for i in changed if self.ids[i]!=self.attachment['target_id']]
        if changed:
            raise PlanningRejected('Kitchen changed during execution: '+','.join(str(self.ids[i])+':'+self.entities[i].name for i in changed))
