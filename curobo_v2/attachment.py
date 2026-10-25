"""Measured single-hand attachment, preserving every declared collision shape."""
from copy import deepcopy
import itertools
import numpy as np
from scipy.spatial.transform import Rotation

ATTACHED_SPHERES=256
ATTACHED_CELL_M=.02
ATTACH_TRANSLATION_M=.004
ATTACH_ROTATION_RAD=.03


def effective_held_envelope(execution_settings=None):
    if execution_settings is None:
        return ATTACH_TRANSLATION_M, ATTACH_ROTATION_RAD
    return execution_settings.held_envelope_m, execution_settings.held_envelope_rad


def held_envelope_receipt(execution_settings=None):
    if execution_settings is None:
        return {}
    translation, rotation = effective_held_envelope(execution_settings)
    return {'held_envelope': {'translation_m': translation, 'rotation_rad': rotation}}


def attachment_inflation(spheres, envelope=None):
    translation, rotation = effective_held_envelope() if envelope is None else envelope
    return translation+2*(np.linalg.norm(spheres[:,:3],axis=1)+spheres[:,3])*np.sin(rotation/2)


def rigid_pose(value):
    raw=np.asarray(value,dtype=object)
    if raw.shape!=(4,4)or any(type(v)not in (int,float,np.float64,np.float32) for v in raw.flat):
        raise ValueError('Attachment transform requires sixteen numeric values; no coercion')
    p=np.asarray(value,float)
    if (p.shape!=(4,4)or not np.isfinite(p).all()or not np.allclose(p[3],[0,0,0,1],atol=1e-8)
            or not np.allclose(p[:3,:3].T@p[:3,:3],np.eye(3),atol=1e-6)
            or abs(np.linalg.det(p[:3,:3])-1)>1e-6):raise ValueError('Invalid measured attachment transform')
    return p


def cuboid_pose(c):
    p=np.eye(4);p[:3,3]=c['center'];p[:3,:3]=Rotation.from_quat(np.asarray(c['quat_wxyz'])[[1,2,3,0]]).as_matrix()
    return rigid_pose(p)


def cover_cuboid(c,link_from_planner,*,cell=ATTACHED_CELL_M):
    half=np.asarray(c['half_size'],float)
    if half.shape!=(3,)or np.any(half<=0)or not np.isfinite(half).all():raise ValueError('Invalid attached shape bounds')
    counts=np.maximum(1,np.ceil(2*half/cell)).astype(int);small=half/counts
    if np.prod(counts)>ATTACHED_SPHERES:raise ValueError('Attached shape exceeds fixed collision capacity')
    axes=[np.linspace(-h+s,h-s,n)for h,s,n in zip(half,small,counts)]
    points=np.array(list(itertools.product(*axes)));pose=rigid_pose(link_from_planner)@cuboid_pose(c)
    centers=points@pose[:3,:3].T+pose[:3,3]
    # Each sphere encloses an entire cell, including all corners/interior.
    return np.c_[centers,np.full(len(points),np.linalg.norm(small)+1e-7)]


class SingleArmAttachment:
    def __init__(self,context,world,tool_frames,*,execution_settings=None):
        fields={'holder','target_id','object_pose','tcp_object_transform','shape_names','support_names','tool_frame'}
        if (not isinstance(context,dict)or set(context)!=fields or not isinstance(context['holder'],str)
                or context['holder']not in {'left','right'} or type(context['target_id'])is not int):
            raise ValueError('Complete explicit measured attachment context required')
        self.holder=context['holder'];self.target_id=context['target_id'];self.tool=context['tool_frame']
        if self.tool!=f'openarm_{self.holder}_hand_tcp' or self.tool not in tool_frames:raise ValueError('Attachment TCP is not the declared holder')
        self.initial_pose=rigid_pose(context['object_pose']);self.transform=rigid_pose(context['tcp_object_transform'])
        for key in ('shape_names','support_names'):
            if not isinstance(context[key],list)or any(not isinstance(n,str)or not n for n in context[key]):
                raise ValueError('Attachment shape/support names must be explicit lists of nonempty strings')
        self.names=set(context['shape_names']);world_names={c['name']for c in world}
        actual={n for n in world_names if n.startswith(f'entity:{self.target_id}:shape:')}
        if not actual or self.names!=actual or len(self.names)!=len(context['shape_names']):raise ValueError('Attachment omitted or substituted actual target shapes')
        self.supports=set(context['support_names'])
        if not self.supports<=world_names-self.names or len(self.supports)!=len(context['support_names']):raise ValueError('Departure supports must exist in the complete measured world')
        self.cuboids=[deepcopy(c)for c in world if c['name']in self.names]
        self.local_shapes={c['name']:np.linalg.inv(self.initial_pose)@cuboid_pose(c)for c in self.cuboids}
        self.allowed_fingers={f'openarm_{self.holder}_{s}_finger'for s in ['left','right']}
        self.link=f'curobo_attached_{self.holder}'
        link_from_planner=self.transform@np.linalg.inv(self.initial_pose)
        spheres=np.concatenate([cover_cuboid(c,link_from_planner)for c in self.cuboids])
        if len(spheres)>ATTACHED_SPHERES:raise ValueError('All attached shapes exceed fixed sphere capacity; no shape may be dropped')
        self.spheres=np.zeros((ATTACHED_SPHERES,4));self.spheres[:,3]=-100.;self.spheres[:len(spheres)]=spheres
        self.sphere_count=len(spheres)
        # Bound every point under the admitted physical grasp compliance envelope.
        self.held_envelope=effective_held_envelope(execution_settings)
        margin=attachment_inflation(spheres,self.held_envelope)
        self.spheres[:len(spheres),3]+=margin

    def pose(self,robot):
        return robot.find_link_by_name(self.tool).entity_pose.to_transformation_matrix()@self.transform

    def artifact(self):
        return dict(holder=self.holder,target_id=self.target_id,shape_names=sorted(self.names),shape_count=len(self.names),
                    sphere_count=self.sphere_count,sphere_capacity=ATTACHED_SPHERES,cell_edge_m=ATTACHED_CELL_M,
                    geometry='conservative complete cuboid cell cover from all actual PhysX shapes',
                    allowed_fingers=sorted(self.allowed_fingers),departure_support_names=sorted(self.supports),
                    grasp_translation_envelope_m=self.held_envelope[0],grasp_rotation_envelope_rad=self.held_envelope[1])
