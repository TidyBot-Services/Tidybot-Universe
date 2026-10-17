"""Fixed support proximity for a measured carried object."""
import json
from types import SimpleNamespace as NS

import mujoco
import numpy as np
import pytest

from openarm365_dev.attachment import (
    SUPPORT_PROXIMITY_M, planner_context, proximity_support_distances,
)


def test_proximity_support_selection_is_fixed_bounded_and_body_sorted():
    # body 1 is the object, 3 the robot, and 6 has a joint.
    parent=[0,0,0,0,0,0,0,1,3]
    joints=[0,1,0,0,0,0,1,0,0]
    geom_body=[1,4,2,5,6,8,7,4]
    inventory=[{'geom_id':gid,'body_id':geom_body[gid],'name':str(gid)}
               for gid in [1,4,6,3,0,5,2,7]]
    distances={(0,1):0.009,(6,1):0.008,(0,2):SUPPORT_PROXIMITY_M,
               (6,2):0.012,(0,3):0.0100001,(6,3):0.011,
               (0,7):0.0095,(6,7):0.009}
    queried=[]
    def distance(obj,other):
        queried.append((obj,other))
        return distances[obj,other]
    result=proximity_support_distances(inventory,1,3,geom_body,parent,joints,distance)
    assert list(result)==[2,4]
    assert result=={2:0.010,4:0.008}
    assert set(queried)==set(distances)


class NativeModelWrapper:
    def __init__(self,model):self._model=model
    def __getattr__(self,name):return getattr(self._model,name)
    def body_name2id(self,name):return mujoco.mj_name2id(self._model,mujoco.mjtObj.mjOBJ_BODY,name)
    def body_id2name(self,bid):return mujoco.mj_id2name(self._model,mujoco.mjtObj.mjOBJ_BODY,bid)
    def geom_id2name(self,gid):return mujoco.mj_id2name(self._model,mujoco.mjtObj.mjOBJ_GEOM,gid)


def native_case(gap):
    xml="""<mujoco><worldbody>
      <body name="robot0_openarm_body_link0"><site name="tcp"/></body>
      <body name="counter"><geom name="counter_geom" type="box" size=".1 .1 .01" pos="0 0 .01"/></body>
      <body name="distant"><geom name="distant_geom" type="box" size=".1 .1 .01" pos="0 0 -.01"/></body>
      <body name="obj_main" pos="0 0 .03"><freejoint/>
        <geom name="obj_geom" type="sphere" size=".01" mass=".01"/></body>
    </worldbody></mujoco>"""
    model=mujoco.MjModel.from_xml_string(xml)
    data=mujoco.MjData(model)
    data.qpos[2]=0.03+gap
    mujoco.mj_forward(model,data)
    wrapped=NativeModelWrapper(model)
    sim=NS(model=wrapped,data=NS(_data=data,body_xpos=data.xpos,body_xmat=data.xmat,
        site_xpos=data.site_xpos,site_xmat=data.site_xmat,contact=data.contact))
    object_id=wrapped.body_name2id('obj_main')
    cuboids=[];inventory=[]
    for geom_name in ('counter_geom','distant_geom','obj_geom'):
        gid=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_GEOM,geom_name)
        bid=int(model.geom_bodyid[gid])
        name=f'entity:{object_id}:shape:{gid}' if bid==object_id else f'mujoco:{gid}:{geom_name}'
        inventory.append(dict(name=name,geom_id=gid,body_id=bid))
        cuboids.append(dict(name=name,center=data.geom_xpos[gid].tolist(),
            quat_wxyz=[1.,0.,0.,0.],half_size=[.01,.01,.01]))
    env=NS(sim=sim,robots=[NS(eef_site_id={'right':0})])
    return env,dict(inventory=inventory,cuboids=cuboids),object_id,model,data


@pytest.mark.parametrize('gap,expected',[
    (0.003,True),(-0.001,True),(0.0,True),(0.020,False),
])
def test_planner_context_uses_native_mujoco_distance(gap,expected,monkeypatch,tmp_path):
    monkeypatch.delenv('ATTN_DIAG_ATTACHMENT_DUMP',raising=False)
    env,world,object_id,model,data=native_case(gap)
    counter=next(row for row in world['inventory'] if 'counter_geom' in row['name'])
    obj=next(row for row in world['inventory'] if row['body_id']==object_id)
    native_distance=float(mujoco.mj_geomDistance(model,data,obj['geom_id'],counter['geom_id'],.05,None))
    context=planner_context(env,dict(holder='left',target_id=object_id),world,
                            ['openarm_left_hand_tcp'],tmp_path)
    assert (counter['name'] in context['support_names']) is expected
    assert all('distant_geom' not in name for name in context['support_names'])
    assert not list(tmp_path.iterdir())
    print(f'native gap={gap:.3f} m distance={native_distance:.6f} m '
          f'contacts={data.ncon} supports={context["support_names"]}')


def test_attachment_dump_explains_proximity(monkeypatch,tmp_path):
    monkeypatch.setenv('ATTN_DIAG_ATTACHMENT_DUMP','1')
    env,world,object_id,_,_=native_case(0.003)
    planner_context(env,dict(holder='left',target_id=object_id),world,
                    ['openarm_left_hand_tcp'],tmp_path)
    path,=tmp_path.glob('attachment-dump-*.json')
    decisions=json.loads(path.read_text())['support_decisions']
    counter=next(row for row in decisions if 'counter_geom' in row['name'])
    assert counter['included'] and counter['reason']=='proximity_within_10mm'
    assert counter['proximity_distance_m']==pytest.approx(0.003)
