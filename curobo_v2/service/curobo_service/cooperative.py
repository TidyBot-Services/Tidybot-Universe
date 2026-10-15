"""Explicit carried cuboid and closed-chain validation context."""
from copy import deepcopy
import numpy as np
from scipy.spatial.transform import Rotation


def rigid_pose(value):
    from curobo_v2.attachment import rigid_pose as measured_rigid_pose
    return measured_rigid_pose(value)


class CarriedCuboid:
    def __init__(self,context,world,tool_frames):
        if set(context) != {'object_name','object_pose','tcp_object_transforms','tool_frames','support_names'}:
            raise ValueError('Complete measured two-hand attachment context required')
        self.name = context['object_name']
        if not isinstance(self.name,str)or not self.name:raise ValueError('Explicit carried cuboid name required')
        self.cuboid = deepcopy(next((c for c in world if c['name']==self.name),None))
        if self.cuboid is None:
            raise ValueError('Carried object must remain declared in the complete world')
        if set(context['tool_frames']) != {'left','right'} or set(context['tcp_object_transforms']) != {'left','right'}:
            raise ValueError('Both measured grasp transforms required')
        if not isinstance(context['support_names'],list)or any(not isinstance(n,str)or not n for n in context['support_names']):
            raise ValueError('Explicit support name list required')
        self.supports=set(context['support_names'])
        if len(self.supports)!=len(context['support_names']):raise ValueError('Duplicate support names')
        if not self.supports <= {c['name'] for c in world if c['name']!=self.name}:
            raise ValueError('Declared departure support absent from full world')
        self.tools = context['tool_frames']
        if len(set(self.tools.values())) != 2 or not set(self.tools.values()) <= set(tool_frames):
            raise ValueError('Distinct configured TCP frames required')
        self.initial_pose = rigid_pose(context['object_pose'])
        w,x,y,z=self.cuboid.get('quat_wxyz',[1,0,0,0]);world_pose=np.eye(4)
        world_pose[:3,:3]=Rotation.from_quat([x,y,z,w]).as_matrix();world_pose[:3,3]=self.cuboid['center']
        if not np.allclose(world_pose,self.initial_pose,atol=1e-6):
            raise ValueError('Attachment pose differs from uploaded measured world')
        self.transforms={s:rigid_pose(t) for s,t in context['tcp_object_transforms'].items()}
        self.allowed_fingers={f'openarm_{s}_{finger}_finger' for s in ['left','right'] for finger in ['left','right']}
        if self.tools!={s:f'openarm_{s}_hand_tcp' for s in ['left','right']}:
            raise ValueError('Cooperative side names must match the configured left/right TCPs')
        from curobo_v2.attachment import cover_cuboid, ATTACHED_SPHERES, ATTACH_TRANSLATION_M, ATTACH_ROTATION_RAD
        self.names={self.name};self.tool=self.tools['left'];self.link='curobo_attached_cooperative'
        spheres=cover_cuboid(self.cuboid,self.transforms['left']@np.linalg.inv(self.initial_pose))
        margin=ATTACH_TRANSLATION_M+2*(np.linalg.norm(spheres[:,:3],axis=1)+spheres[:,3])*np.sin(ATTACH_ROTATION_RAD/2)
        self.spheres=np.zeros((ATTACHED_SPHERES,4));self.spheres[:,3]=-100.
        self.spheres[:len(spheres)]=spheres;self.spheres[:len(spheres),3]+=margin;self.sphere_count=len(spheres)

    def artifact(self):
        return dict(mode='cooperative',shape_names=sorted(self.names),shape_count=1,sphere_count=self.sphere_count,
            sphere_capacity=len(self.spheres),parent_tcp=self.tool,tool_frames=self.tools,
            allowed_fingers=sorted(self.allowed_fingers),departure_support_names=sorted(self.supports),
            closure_position_limit_m=.004,closure_rotation_limit_rad=.03,
            geometry='conservative complete cuboid cell cover; full two-TCP closure veto at every dense sample')

    def targets(self,object_pose):
        goal=rigid_pose(object_pose);result={}
        for side in ['left','right']:
            target=goal@np.linalg.inv(self.transforms[side]);x,y,z,w=Rotation.from_matrix(target[:3,:3]).as_quat()
            result[self.tools[side]]={'position':target[:3,3].tolist(),'quaternion_wxyz':[w,x,y,z]}
        return result

    def poses(self,robot):
        return {s:robot.find_link_by_name(self.tools[s]).entity_pose.to_transformation_matrix()@self.transforms[s] for s in ['left','right']}

    def error(self,a,b):
        return {'translation_error_m':float(np.linalg.norm(a[:3,3]-b[:3,3])),
                'rotation_error_rad':float(np.linalg.norm(Rotation.from_matrix(a[:3,:3]@b[:3,:3].T).as_rotvec()))}
