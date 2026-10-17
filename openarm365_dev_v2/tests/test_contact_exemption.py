"""Offline collision-world, config and contact-event regression."""
import hashlib,json
from types import SimpleNamespace
import numpy as np
import pytest
from curobo_v2.sapien_world import WorldError
from openarm365_dev import world as wm
from openarm365_dev import planner as pm
from openarm365_dev.base_motion import check_chassis_path
from openarm365_dev.executor import contact_transitions,bounded_contact_events,CONTACT_EVENT_LIMIT

class Model:
    ngeom=5;nbody=7
    geom_contype=np.ones(5,dtype=int);geom_conaffinity=np.ones(5,dtype=int)
    geom_bodyid=np.array([1,2,3,4,6]);geom_type=np.full(5,6)
    geom_size=np.array([[.1,.1,.1],[.1,.1,.1],[.05,.05,.1],[.05,.05,.1],[.05,.05,.1]])
    body_parentid=np.array([0,0,0,2,0,0,4])
    body_jntadr=np.array([0,0,0,0,0,0,1]);body_jntnum=np.array([0,0,1,0,0,1,0])
    jnt_type=np.array([0,0])
    names=['world','robot0_openarm_body_link0','obj_main','obj_child','fixture','distr_main','fixture_child']
    geoms=['robot','obj','obj_child','counter','fixture_child']
    def body_name2id(self,name):return self.names.index(name)
    def body_id2name(self,bid):return self.names[bid]
    def geom_id2name(self,gid):return self.geoms[gid]

def environment():
    model=Model();model.body_jntnum[4]=0
    data=SimpleNamespace(geom_xpos=np.array([[0,0,.5],[0,.36,.5],[0,.38,.5],[1,0,.5],[1,0,.5]],float),
                         geom_xmat=np.tile(np.eye(3).reshape(1,9),(5,1)))
    return SimpleNamespace(sim=SimpleNamespace(model=model,data=data),obj_body_id={'obj':2,'distr':5,'bad_fixture':4})

def test_snapshot_default_and_exempt(monkeypatch):
    monkeypatch.setattr(wm,'mount_pose',lambda env:np.eye(4))
    env=environment();full=wm.snapshot(env)
    assert set(full)=={'cuboids','planner_from_world','inventory','excluded_robot_geoms','complete_shape_count','world_sha256'}
    identity={'cuboids':full['cuboids'],'planner_from_world':np.eye(4).tolist()}
    assert full['world_sha256']==hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert len(full['cuboids'])==4
    assert wm.movable_object_roots(env)=={'distr':5,'obj':2}
    slim=wm.snapshot(env,exempt_body_ids={2,5})
    assert [x['name'] for x in slim['cuboids']]==['mujoco:3:counter','mujoco:4:fixture_child']
    assert [x['geom_id'] for x in slim['exempted_geoms']]==[1,2]
    assert slim['exempted_geom_count']==2
    assert slim['world_sha256_unexempted']==full['world_sha256']
    assert slim['world_sha256']!=full['world_sha256']
    assert len(slim['inventory'])==slim['complete_shape_count']==2
    with pytest.raises(WorldError,match='nonmovable'):wm.snapshot(env,exempt_body_ids={4})
    attached=wm.snapshot(env,exempt_body_ids={2,5},attached_body_id=2)
    assert {x['name'] for x in attached['inventory'] if x['body_id'] in (2,3)}=={'entity:2:shape:1','entity:2:shape:2'}
    assert attached['world_sha256_unexempted']==full['world_sha256']

def test_base_precheck_full_world(monkeypatch):
    monkeypatch.setattr(wm,'mount_pose',lambda env:np.eye(4))
    env=environment();binding={'center_in_planner_frame':[0,0,.5],'half_size':[.2,.2,.2]}
    full=wm.snapshot(env);slim=wm.snapshot(env,exempt_body_ids={2,5})
    from curobo_v2.client import PlanningRejected
    with pytest.raises(PlanningRejected,match='obj'):check_chassis_path(full,binding,(0,.1,0))
    assert check_chassis_path(slim,binding,(0,.1,0))['status']=='clear'
    import inspect
    from openarm365_dev.executor import NativeExecutor
    assert 'snapshot(self.env,chassis_binding=self.planner.chassis_binding)' in inspect.getsource(NativeExecutor.base_move)

def test_modes(monkeypatch):
    monkeypatch.delenv('ATTN_DIAG_CONTACT_EXEMPT',raising=False)
    assert pm.contact_exemption_mode('none')=='none'
    monkeypatch.setenv('ATTN_DIAG_CONTACT_EXEMPT','movable_objects')
    assert pm.contact_exemption_mode('none')=='movable_objects'
    monkeypatch.setenv('ATTN_DIAG_CONTACT_EXEMPT','none')
    assert pm.contact_exemption_mode('movable_objects')=='none'
    monkeypatch.setenv('ATTN_DIAG_CONTACT_EXEMPT','invalid')
    with pytest.raises(ValueError,match='ATTN_DIAG_CONTACT_EXEMPT'):pm.contact_exemption_mode('none')

def test_trajectory_and_world_receipt_audit(tmp_path,monkeypatch):
    monkeypatch.setattr(wm,'mount_pose',lambda env:np.eye(4))
    env=environment()
    p=pm.MujocoV2Planner.__new__(pm.MujocoV2Planner)
    p.env=env;p.evidence=tmp_path;p.sequence=0;p.chassis_binding=None
    p.contact_exemption_configured='none';p.contact_exemption_mode='movable_objects'
    p.attachment_provider=lambda:None;p.prepared={('joint',)};p.failed=False;p.tools={}
    p.owner=SimpleNamespace(close=lambda *args:None)
    p.client=SimpleNamespace(identity={'test':True},call=lambda path,body: (
        {'cuboid_count':len(body['cuboids']),'world_sha256':'server-hash'} if path=='/world/cuboids' else
        {'status':'success','trajectory':[[0],[0]],'joint_names':['joint']} if path=='/plan/joint' else {}))
    issued=[]
    p.ledger=SimpleNamespace(issue=lambda response,**kwargs:(issued.append(kwargs) or 'plan-id'),invalidate=lambda *args:None)
    p.body=lambda active:{'current_q':[0.],'joint_names':['joint'],'env_id':'robocasa365'}
    response=p.plan('/plan/joint',{'target_qpos':[0.]},['joint'],'epoch')
    audit=response['contact_exemption']
    assert response['world_receipt']['contact_exemption']==audit
    assert {x['name'] for x in audit['objects']}=={'obj','distr'}
    assert audit['source']=='config' or audit['source']=='ATTN_DIAG_CONTACT_EXEMPT'
    assert audit['world_sha256']!=audit['world_sha256_unexempted']
    assert len(issued[0]['world'])==2
    assert json.loads((tmp_path/'trajectory-0001.json').read_text())['contact_exemption']==audit
    p.contact_exemption_mode='none';p.contact_exemption_configured='movable_objects'
    _,disabled=p.upload()
    assert disabled['contact_exemption']['mode']=='none'
    assert disabled['contact_exemption']['objects']==[]
    assert disabled['contact_exemption']['world_sha256']==disabled['contact_exemption']['world_sha256_unexempted']

def test_contact_edges_and_cap():
    entry=dict(robot_geom_name='finger',object_body_name='obj_main',contact_force_n=2.5,distance_m=-.001)
    begin=contact_transitions({}, {(1,2):entry},17)
    assert begin==[dict(event='movable_contact_begin',physical_step=17,**entry)]
    assert contact_transitions({(1,2):entry},{(1,2):entry},18)==[]
    assert contact_transitions({(1,2):entry},{},19)==[dict(event='movable_contact_end',physical_step=19,**entry)]
    accepted,dropped=bounded_contact_events(list(range(10)),CONTACT_EVENT_LIMIT-3)
    assert accepted==[0,1,2] and dropped==7
    assert bounded_contact_events([1],CONTACT_EVENT_LIMIT)==([],1)
