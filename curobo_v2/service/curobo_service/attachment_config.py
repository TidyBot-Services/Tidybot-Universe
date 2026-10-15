"""Describe carried geometry using the unchanged official robot configuration API."""
from copy import deepcopy

def carried_model_topology(attachment):
    """Cache only structure; measured transforms/world must be reapplied per call."""
    if attachment is None:return None
    return (type(attachment).__name__,attachment.link,attachment.tool,
            tuple(sorted(attachment.names)),attachment.sphere_count,
            tuple(sorted(attachment.allowed_fingers)))

def configure_carried_body(robot,attachment):
    result=deepcopy(robot)
    if attachment is None:return result
    kin=result['kinematics'];name=attachment.link
    if name in kin.get('collision_link_names',[])or name in (kin.get('extra_links')or{}):
        raise ValueError('Attachment link name conflicts with robot configuration')
    kin['extra_links']=kin.get('extra_links')or{}
    kin['extra_links'][name]=dict(link_name=name,joint_name=name+'_fixed_joint',
        joint_type='FIXED',parent_link_name=attachment.tool,fixed_transform=[0.,0.,0.,1.,0.,0.,0.])
    kin['collision_link_names']=[*kin['collision_link_names'],name]
    kin['collision_spheres'][name]=[dict(center=s[:3].tolist(),radius=float(s[3])) for s in attachment.spheres[:attachment.sphere_count]]
    # Only the explicitly declared grasp fingers are contact partners. Every
    # other robot body, inactive arm, and world obstacle retains its constraint.
    kin.setdefault('self_collision_ignore',{})[name]=sorted(attachment.allowed_fingers)
    kin.setdefault('self_collision_buffer',{})[name]=0.
    return result
