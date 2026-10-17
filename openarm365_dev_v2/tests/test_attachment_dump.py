"""Opt-in attachment diagnostics leave the planning context untouched."""
import json
import sys
from types import SimpleNamespace

import numpy as np
import pytest

try:
    import mujoco
except ModuleNotFoundError:
    # These tests exercise pure filtering/context code in the official planner env,
    # which does not install the MuJoCo runtime.
    sys.modules['mujoco']=SimpleNamespace()

from openarm365_dev import attachment as am


def test_distance_order_and_support_reasons():
    rows=[dict(object_geom_id=2,other_geom_id=7,distance_m=.02),
          dict(object_geom_id=1,other_geom_id=9,distance_m=-.001),
          dict(object_geom_id=1,other_geom_id=8,distance_m=.02)]
    assert [(r['object_geom_id'],r['other_geom_id']) for r in am.nearest_distances(rows,2)]==[(1,9),(1,8)]
    inventory=[dict(name='counter',body_id=4),dict(name='sink',body_id=5),dict(name='floor',body_id=6)]
    contacts=[dict(other_body_id=4,distance_m=.001,contact_index=0),
              dict(other_body_id=5,distance_m=0,contact_index=1)]
    decisions=am.support_decisions(inventory,contacts,['sink'])
    assert [(d['name'],d['included'],d['reason'],d['contact_indices']) for d in decisions]==[
        ('counter',False,'positive_distance_contacts_filtered',[0]),
        ('sink',True,'nonpositive_contact_with_object',[1]),
        ('floor',False,'no_contact_with_object',[])]


def test_opt_in_validation_and_default_context(tmp_path,monkeypatch):
    monkeypatch.delenv('ATTN_DIAG_ATTACHMENT_DUMP',raising=False)
    assert am.attachment_dump_enabled() is False
    assert am.planner_context(None,None,None,None,tmp_path) is None
    monkeypatch.setenv('ATTN_DIAG_ATTACHMENT_DUMP','')
    with pytest.raises(ValueError,match='ATTN_DIAG_ATTACHMENT_DUMP'):
        am.planner_context(None,None,None,None,tmp_path)
    monkeypatch.setenv('ATTN_DIAG_ATTACHMENT_DUMP','true')
    with pytest.raises(ValueError,match='ATTN_DIAG_ATTACHMENT_DUMP'):
        am.attachment_dump_enabled()
    monkeypatch.setenv('ATTN_DIAG_ATTACHMENT_DUMP','1')
    assert am.attachment_dump_enabled() is True
    monkeypatch.delenv('ATTN_DIAG_ATTACHMENT_DUMP')

    class Model:
        geom_bodyid=[2,4]
        body_parentid=[0,0,0,0,0]
    data=SimpleNamespace(body_xmat=np.tile(np.eye(3).reshape(1,9),(5,1)),
                         body_xpos=np.zeros((5,3)),contact=[SimpleNamespace(dist=.002,geom1=0,geom2=1)])
    env=SimpleNamespace(sim=SimpleNamespace(model=Model(),data=data))
    world=dict(inventory=[dict(name='entity:2:shape:0',body_id=2),dict(name='counter',body_id=4)],
               cuboids=[])
    monkeypatch.setattr(am,'tcp_pose',lambda env,side:np.eye(4))
    from openarm365_dev import environment
    monkeypatch.setattr(environment,'mount_pose',lambda env:np.eye(4))
    from curobo_v2 import attachment as service_attachment
    monkeypatch.setattr(service_attachment,'SingleArmAttachment',lambda context,cuboids,tools:None)
    context=am.planner_context(env,dict(holder='left',target_id=2),world,{},tmp_path)
    assert list(context)==['holder','target_id','object_pose','tcp_object_transform',
                           'tool_frame','shape_names','support_names']
    assert context['shape_names']==['entity:2:shape:0']
    assert context['support_names']==[]
    assert context['object_pose']==np.eye(4).tolist()
    expected=dict(holder='left',target_id=2,object_pose=np.eye(4).tolist(),
        tcp_object_transform=np.eye(4).tolist(),tool_frame='openarm_left_hand_tcp',
        shape_names=['entity:2:shape:0'],support_names=[])
    assert json.dumps(context,separators=(',',':')).encode()==json.dumps(expected,separators=(',',':')).encode()
    assert list(tmp_path.iterdir())==[]
