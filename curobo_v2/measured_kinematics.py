"""Prepare measured fixed-joint inputs using official FK, preserving all geometry.

The auxiliary full-joint model computes transforms only. It cannot plan, check
collisions, or authorize execution. The actual planner retains its full original
sphere geometry, self-collision rules and independent native geometry checks.
"""
from copy import deepcopy
import xml.etree.ElementTree as ET
from .identity import IdentityError


class MeasuredKinematicsInputs:
    def __init__(self,robot,device_cfg):
        from curobo.kinematics import Kinematics,KinematicsCfg
        self.device_cfg=device_cfg
        self.joints={j.get('name'):j for j in ET.parse(robot['kinematics']['urdf_path']).getroot().findall('joint')}
        frames=sorted({j.find(k).get('link') for j in self.joints.values() for k in ['parent','child']})
        reader=deepcopy(robot);kin=reader['kinematics']
        kin.update(lock_joints={},tool_frames=frames,load_collision_spheres=False)
        self.reader=Kinematics(KinematicsCfg.from_data_dict(reader,device_cfg=device_cfg))

    def prepare(self,current,robot,*,payload_link=None):
        import torch
        from curobo.types import JointState
        locks=robot['kinematics']['lock_joints']
        if set(locks)!=set(current.lock_jointstate.joint_names):
            raise IdentityError('Measured lock topology changed')
        if not set(locks)<=set(self.reader.joint_names):
            raise IdentityError('Measured lock is absent from full-joint FK input')
        q=torch.as_tensor([[locks.get(n,0.)for n in self.reader.joint_names]],
            device=current.fixed_transforms.device,dtype=current.fixed_transforms.dtype)
        poses=self.reader.compute_kinematics(JointState.from_position(q,joint_names=self.reader.joint_names)).tool_poses
        new=current.clone()
        affected=set(locks)
        for name,node in self.joints.items():
            mimic=node.find('mimic')
            if mimic is not None and mimic.get('joint')in locks:affected.add(name)
        for name in sorted(affected):
            node=self.joints[name];parent=node.find('parent').get('link');child=node.find('child').get('link')
            if child not in new.link_name_to_idx_map:
                raise IdentityError('Locked joint child is absent from full collision model')
            index=new.link_name_to_idx_map[child]
            if int(new.joint_map_type[index])!=-1 or int(new.joint_map[index])!=-1:
                raise IdentityError('Measured update would modify an active joint')
            relative=poses.get_link_pose(parent).inverse().multiply(poses.get_link_pose(child))
            new.fixed_transforms[index].copy_(relative.get_affine_matrix().reshape_as(new.fixed_transforms[index]))
        new.lock_jointstate.position.copy_(torch.as_tensor(
            [locks[n]for n in new.lock_jointstate.joint_names],device=q.device,dtype=q.dtype).reshape_as(new.lock_jointstate.position))
        reference=dict(zip(robot['kinematics']['cspace']['joint_names'],robot['kinematics']['cspace']['default_joint_position']))
        new.cspace.default_joint_position.copy_(torch.as_tensor(
            [reference[n]for n in new.joint_names],device=q.device,dtype=q.dtype).reshape_as(new.cspace.default_joint_position))
        static=torch.ones(current.total_spheres,device=q.device,dtype=torch.bool)
        if payload_link is not None:
            kin=robot['kinematics'];extra=kin.get('extra_links',{}).get(payload_link)
            if (not extra or extra.get('fixed_transform')!=[0.,0.,0.,1.,0.,0.,0.]
                    or kin.get('collision_sphere_buffer')!=0.):
                raise IdentityError('Unsupported carried geometry input update')
            spheres=kin['collision_spheres'][payload_link]
            indices=new.get_sphere_index_from_link_name(payload_link)
            if len(spheres)!=len(indices):raise IdentityError('Carried sphere topology changed')
            values=torch.as_tensor([s['center']+[s['radius']]for s in spheres],device=q.device,dtype=q.dtype)
            if not torch.isfinite(values).all()or not torch.all(values[:,3]>0):
                raise IdentityError('Carried shape input is incomplete or disabled')
            new.update_link_spheres(payload_link,values)
            new.reference_link_spheres[:,indices,:]=values
            static[indices]=False
        if (new.total_spheres!=current.total_spheres
                or not torch.equal(new.link_spheres[:,static,:],current.link_spheres[:,static,:])
                or not torch.equal(new.link_sphere_idx_map,current.link_sphere_idx_map)
                or not torch.equal(new.joint_limits.position,current.joint_limits.position)):
            raise IdentityError('Measured input update changed collision geometry or hard limits')
        return new
