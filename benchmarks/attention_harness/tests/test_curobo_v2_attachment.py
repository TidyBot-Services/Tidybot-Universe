import itertools
import numpy as np
import pytest
from curobo_v2.attachment import SingleArmAttachment,cover_cuboid

def measured():
    box=dict(name='entity:147:shape:0:target',center=[.2,0,.3],half_size=[.01,.012,.04],quat_wxyz=[1,0,0,0])
    support=dict(name='entity:4:shape:0:counter',center=[0,0,0],half_size=[1,1,.1],quat_wxyz=[1,0,0,0])
    pose=np.eye(4);pose[:3,3]=box['center']
    context=dict(holder='left',target_id=147,tool_frame='openarm_left_hand_tcp',object_pose=pose.tolist(),tcp_object_transform=np.eye(4).tolist(),shape_names=[box['name']],support_names=[support['name']])
    return context,[box,support]

def test_conservative_cover_contains_all_box_corners_and_interior_cells():
    context,world=measured();box=world[0];spheres=cover_cuboid(box,np.eye(4))
    for point in itertools.product(*[np.linspace(-h,h,11)for h in box['half_size']]):
        distance=np.linalg.norm(spheres[:,:3]-(np.array(point)+box['center']),axis=1)
        assert np.any(distance<=spheres[:,3])
    assert SingleArmAttachment(context,world,['openarm_left_hand_tcp']).sphere_count==len(spheres)

@pytest.mark.parametrize('field,value',[('shape_names',[]),('shape_names',['entity:99:shape:0:other']),('support_names',['missing']),('tool_frame','openarm_right_hand_tcp'),('holder','both')])
def test_incomplete_or_wrong_measured_geometry_is_refused(field,value):
    context,world=measured();context[field]=value
    with pytest.raises(ValueError):SingleArmAttachment(context,world,['openarm_left_hand_tcp'])

def test_additional_actual_shape_cannot_be_omitted():
    context,world=measured();world.append(dict(world[0],name='entity:147:shape:1:target'))
    with pytest.raises(ValueError):SingleArmAttachment(context,world,['openarm_left_hand_tcp'])
