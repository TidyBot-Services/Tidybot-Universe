"""Full URDF geometry veto for compact cuRobo search proxies."""
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np


class ExactGeometryValidator:
    def __init__(self, settings: dict, joint_names: list[str]):
        import sapien
        self.sapien = sapien
        from curobo_v2.identity import require_service_context, digest, IdentityError
        lock,_ = require_service_context()
        urdf = Path(settings['urdf_path'])
        xml = ET.parse(urdf).getroot()
        for mesh in xml.findall('.//collision//mesh'):
            name = mesh.get('filename')
            path = (urdf.parent / name).resolve()
            if not path.is_file() or lock['files'].get(str(path)) != digest(path):
                raise IdentityError('Complete URDF mesh is missing or not locked: '+str(path))
        self.names = joint_names
        self.clearance = float(settings['clearance_m'])
        self.scene = sapien.Scene([sapien.physx.PhysxCpuSystem()])
        self.scene.set_timestep(1/240)
        self.scene.physx_system.config.gravity = [0,0,0]
        loader = self.scene.create_urdf_loader(); loader.fix_root_link = True
        builder = loader.load_file_as_articulation_builder(settings['urdf_path'])
        for link in builder.link_builders:
            link.visual_records = []
        self.robot = builder.build(fix_root_link=True,build_mimic_joints=True)
        expected_shapes = len(xml.findall('.//collision'))
        actual_shapes = sum(len(link.collision_shapes) for link in self.robot.links)
        if actual_shapes != expected_shapes:
            raise IdentityError(f'URDF collision geometry was dropped: expected {expected_shapes}, loaded {actual_shapes}')
        self.geometry_counts = {'expected_collision_shapes':expected_shapes,'loaded_collision_shapes':actual_shapes}
        self.joint_names = [j.name for j in self.robot.get_active_joints()]
        self.indices = {n:i for i,n in enumerate(self.joint_names)}
        self.xml = ET.parse(settings['urdf_path']).getroot()
        from .collision_policy import fixed_body_components
        self.fixed_groups, self.fixed_components = fixed_body_components(self.xml)
        self.joints = {j.get('name'):j for j in self.xml.findall('joint')}
        self.obstacles = []
        self.attachment = None
        self.carried_actor = None
        for link in self.robot.links:
            link.disable_gravity = True
            for shape in link.collision_shapes:
                shape.contact_offset = self.clearance
                shape.rest_offset = 0.
        self.ignored_pairs = [list(p) for p in loader.ignore_pairs]

    def update_world(self, cuboids: list[dict]) -> None:
        for entity in self.obstacles:
            self.scene.remove_entity(entity)
        self.obstacles = []
        self.carried_actor = None
        from curobo_v2.attachment import SingleArmAttachment
        if isinstance(self.attachment,SingleArmAttachment):
            builder=self.scene.create_actor_builder()
            for c in self.attachment.cuboids:
                builder.add_box_collision(half_size=c['half_size'],pose=self.sapien.Pose(self.attachment.local_shapes[c['name']]))
            self.carried_actor=builder.build(f'carried:{self.attachment.target_id}')
            self.carried_actor.pose=self.sapien.Pose(self.attachment.initial_pose)
            body=self.carried_actor.find_component_by_type(self.sapien.physx.PhysxRigidDynamicComponent);body.disable_gravity=True
            for shape in body.collision_shapes:shape.contact_offset=self.clearance;shape.rest_offset=0.
            self.obstacles.append(self.carried_actor)
        for c in cuboids:
            if isinstance(self.attachment,SingleArmAttachment)and c['name']in self.attachment.names:continue
            builder = self.scene.create_actor_builder()
            builder.add_box_collision(half_size=c['half_size'])
            carried=self.attachment is not None and not isinstance(self.attachment,SingleArmAttachment)and c['name']==self.attachment.name
            actor = builder.build('world:'+c['name']) if carried else builder.build_static('world:'+c['name'])
            actor.pose = self.sapien.Pose(c['center'],c.get('quat_wxyz',[1,0,0,0]))
            body=actor.find_component_by_type(self.sapien.physx.PhysxRigidBaseComponent)
            if carried:body.disable_gravity=True
            for shape in body.collision_shapes:
                shape.contact_offset = self.clearance
                shape.rest_offset = 0.
            self.obstacles.append(actor)
            if self.attachment is not None and not isinstance(self.attachment,SingleArmAttachment)and c["name"]==self.attachment.name:
                self.carried_actor=actor

    def validate(self, samples: np.ndarray, locks: dict, *, measured_start=True) -> dict:
        from curobo_v2.attachment import SingleArmAttachment
        if isinstance(self.attachment,SingleArmAttachment):return self.validate_single_attachment(samples,locks,measured_start=measured_start)
        q = np.zeros(len(self.joint_names))
        maximum={"translation_error_m":0.,"rotation_error_rad":0.}
        allowed_contact_samples=0
        for name,value in locks.items():
            if name in self.indices:q[self.indices[name]]=value
        for index,sample in enumerate(samples):
            for name,value in zip(self.names,sample):q[self.indices[name]]=value
            for name,node in self.joints.items():
                mimic=node.find('mimic')
                if mimic is not None and name in self.indices:
                    q[self.indices[name]]=float(mimic.get('multiplier','1'))*q[self.indices[mimic.get('joint')]]+float(mimic.get('offset','0'))
            self.robot.set_qpos(q);self.robot.set_qvel(np.zeros_like(q));self.robot.set_qf(np.zeros_like(q))
            if self.attachment is not None:
                poses=self.attachment.poses(self.robot)
                errors=self.attachment.error(poses['left'],poses['right'])
                for key in maximum:maximum[key]=max(maximum[key],errors[key])
                if errors['translation_error_m']>.004 or errors['rotation_error_rad']>.03:
                    return {'collision_free':False,'sample':index,'reason':'closed_chain_constraint',**errors,'samples':len(samples)}
                if index==0 and measured_start:
                    for pose in poses.values():
                        error=self.attachment.error(pose,self.attachment.initial_pose)
                        if error['translation_error_m']>.004 or error['rotation_error_rad']>.03:
                            return {'collision_free':False,'sample':index,'reason':'measured_attachment_inconsistent',**error}
                self.carried_actor.pose=self.sapien.Pose(poses['left'])
                body=self.carried_actor.find_component_by_type(self.sapien.physx.PhysxRigidDynamicComponent)
                body.linear_velocity=np.zeros(3);body.angular_velocity=np.zeros(3)
            self.scene.step()
            contacts=[]
            for contact in self.scene.get_contacts():
                points=[{'separation_m':float(p.separation),'position':p.position.tolist()}
                        for p in contact.points if p.separation < self.clearance-1e-7]
                bodies=[b.entity.name for b in contact.bodies]
                from .collision_policy import same_fixed_body
                if same_fixed_body(bodies, self.fixed_groups):
                    continue
                if self.attachment is not None and 'world:'+self.attachment.name in bodies:
                    other=next(b for b in bodies if b!='world:'+self.attachment.name)
                    departing=(other in {'world:'+n for n in self.attachment.supports}
                        and poses['left'][2,3]>=self.attachment.initial_pose[2,3]-.0001
                        and all(p['separation_m']>=-.0001 for p in points))
                    if other in self.attachment.allowed_fingers or departing:
                        allowed_contact_samples+=bool(points)
                        continue
                if points:contacts.append({'bodies':[b.entity.name for b in contact.bodies],'points':points})
            if contacts:return {'collision_free':False,'sample':index,'contacts':contacts,'samples':len(samples),
                                'geometry':'complete URDF PhysX collision meshes','clearance_m':self.clearance}
        return {'collision_free':True,'samples':len(samples),'geometry':'complete URDF PhysX collision meshes',
                'clearance_m':self.clearance,'mimic':'native fixed tendon and explicit URDF relation',
                'fixed_body_components':self.fixed_components,
                'closed_chain':None if self.attachment is None else {**maximum,'position_limit_m':.004,'rotation_limit_rad':.03,'carried_object_included':True,'allowed_contact_samples':allowed_contact_samples}}

    def validate_single_attachment(self,samples,locks,*,measured_start=True):
        context=self.attachment;q=np.zeros(len(self.joint_names));allowed=0
        for name,value in locks.items():
            if name in self.indices:q[self.indices[name]]=value
        for index,sample in enumerate(samples):
            for name,value in zip(self.names,sample):q[self.indices[name]]=value
            for name,node in self.joints.items():
                mimic=node.find('mimic')
                if mimic is not None and name in self.indices:
                    q[self.indices[name]]=float(mimic.get('multiplier','1'))*q[self.indices[mimic.get('joint')]]+float(mimic.get('offset','0'))
            self.robot.set_qpos(q);self.robot.set_qvel(np.zeros_like(q));self.robot.set_qf(np.zeros_like(q))
            pose=context.pose(self.robot)
            from scipy.spatial.transform import Rotation
            measured_rotation_error=float(Rotation.from_matrix(pose[:3,:3]@context.initial_pose[:3,:3].T).magnitude())
            if index==0 and measured_start and (np.linalg.norm(pose[:3,3]-context.initial_pose[:3,3])>.004 or
                    measured_rotation_error>.03):
                return dict(collision_free=False,reason='measured_attachment_inconsistent',sample=index,
                    translation_error_m=float(np.linalg.norm(pose[:3,3]-context.initial_pose[:3,3])),rotation_error_rad=measured_rotation_error)
            self.carried_actor.pose=self.sapien.Pose(pose)
            body=self.carried_actor.find_component_by_type(self.sapien.physx.PhysxRigidDynamicComponent)
            body.linear_velocity=np.zeros(3);body.angular_velocity=np.zeros(3)
            self.scene.step();contacts=[]
            for contact in self.scene.get_contacts():
                points=[dict(separation_m=float(p.separation),position=p.position.tolist())for p in contact.points if p.separation<self.clearance-1e-7]
                if not points:continue
                bodies=[b.entity.name for b in contact.bodies]
                from .collision_policy import same_fixed_body
                if same_fixed_body(bodies, self.fixed_groups):
                    continue
                if self.carried_actor.name in bodies:
                    other=next(n for n in bodies if n!=self.carried_actor.name)
                    departing=(other in {'world:'+n for n in context.supports}and pose[2,3]>=context.initial_pose[2,3]-.0001
                        and all(p['separation_m']>=-.0001 for p in points))
                    if other in context.allowed_fingers or departing:allowed+=1;continue
                contacts.append(dict(bodies=bodies,points=points))
            if contacts:return dict(collision_free=False,sample=index,contacts=contacts,samples=len(samples),carried_object_included=True)
        return dict(collision_free=True,samples=len(samples),clearance_m=self.clearance,
                    geometry='complete URDF and all conservative kitchen/attached shape bounds',
                    attachment=context.artifact(),allowed_contact_samples=allowed)
