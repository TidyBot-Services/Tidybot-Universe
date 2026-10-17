"""Offline collision-world, config and contact-event regression."""
import hashlib,json,sys
from types import SimpleNamespace
import numpy as np
import pytest
from curobo_v2.sapien_world import WorldError
from openarm365_dev import world as wm
from openarm365_dev import planner as pm
from openarm365_dev.base_motion import check_chassis_path
from openarm365_dev.executor import (NativeExecutor,contact_transitions,bounded_contact_events,
    is_expected_movable_contact,CONTACT_EVENT_LIMIT)

@pytest.fixture(autouse=True)
def fake_mujoco_if_unavailable(monkeypatch):
    try:import mujoco
    except ModuleNotFoundError:
        geoms=SimpleNamespace(mjGEOM_BOX=6,mjGEOM_SPHERE=2,mjGEOM_ELLIPSOID=4,
            mjGEOM_CAPSULE=3,mjGEOM_CYLINDER=5,mjGEOM_PLANE=0,mjGEOM_MESH=7)
        monkeypatch.setitem(sys.modules,'mujoco',SimpleNamespace(mjtGeom=geoms,
            mj_contactForce=lambda model,data,index,force:force.__setitem__(0,2.5)))

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
    with pytest.raises(PlanningRejected,match='obj'):check_chassis_path(slim,binding,(0,.1,0))
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

class ContactModel:
    nbody=10
    body_parentid=np.array([0,0,1,1,0,4,0,0,0,0])
    body_weldid=np.arange(10)
    body_jntadr=np.array([0,0,0,0,0,0,1,2,0,0])
    body_jntnum=np.array([0,0,0,0,1,0,1,1,0,0])
    jnt_type=np.array([0,0,0])
    geom_bodyid=np.array([2,3,4,5,6,7,8,9])
    names=['world','robot0_openarm_body_link0','finger','palm','obj_g6',
           'obj_child','distr_counter','distr_sink','counter','chassis']
    geoms=['openarm_left_finger_collision0','openarm_left_palm_collision0',
           'banana','banana_child','counter_distractor','sink_distractor','counter_top','chassis_geom']
    _model=object()
    def body_name2id(self,name):return self.names.index(name)
    def body_id2name(self,bid):return self.names[bid]
    def geom_id2name(self,gid):return self.geoms[gid]
    def get_joint_qvel_addr(self,name):return 1

def contact_executor(monkeypatch,geom1,geom2,mode):
    from openarm365_dev import executor as em
    from openarm365_dev import attachment
    import mujoco
    model=ContactModel()
    data=SimpleNamespace(qpos=np.zeros(10),qvel=np.zeros(10),_data=object(),
        contact=[SimpleNamespace(geom1=geom1,geom2=geom2,dist=-.0001)])
    env=SimpleNamespace(sim=SimpleNamespace(model=model,data=data),
        obj_body_id={'obj':4,'distr_counter':6,'distr_sink':7,'counter':8})
    robot=SimpleNamespace(_ref_arm_joint_vel_indexes=[0],_ref_base_joint_pos_indexes=[0,1,2],
        _ref_gripper_joint_pos_indexes={'right':[3,4],'left':[5,6]},
        gripper={'right':SimpleNamespace(joints=['g1','g2']),
                 'left':SimpleNamespace(joints=['g3','g4'])})
    env.robots=[robot]
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':set(),'right':set()})
    monkeypatch.setattr(em,'tcp_pose',lambda env,side:np.eye(4))
    monkeypatch.setattr(mujoco,'mj_contactForce',lambda model,data,index,force:force.__setitem__(0,2.5))
    planner=SimpleNamespace(state=lambda:np.zeros(1),names=['joint'],
        chassis_binding={'native_geom':'chassis_geom'},contact_exemption_mode=mode)
    executor=NativeExecutor.__new__(NativeExecutor)
    executor.env=env;executor.planner=planner;executor.physics_steps=0;executor.robot_root=1
    executor.limits=np.array([[-1.,1.]]);executor.max_speed={'joint':1.}
    executor.base_hold=np.zeros(3);executor.base_moving=False;executor.base_arm_start=None
    executor.gripper_limits={side:np.array([[0.,.044,1.],[0.,.044,1.]]) for side in ('left','right')}
    executor.grasp_counts={'left':{},'right':{}};executor.held=None;executor.gripping=False
    executor.contact_active={};executor.contact_event_count=0;executor.contact_event_overflow=0
    executor.active_request='req';executor.physics='physics';executor.journal='journal'
    rows=[];executor.log=lambda path,value:rows.append((path,json.loads(json.dumps(value,sort_keys=True))))
    return executor,rows

@pytest.mark.parametrize('geom1,geom2,allowed',[
    (0,2,True),       # left finger / banana
    (1,3,True),       # palm / descendant of banana free-joint body
    (0,4,True),       # counter distractor
    (5,1,True),       # sink distractor, reversed contact ordering
    (7,2,True),       # chassis / banana
    (0,6,False),      # finger / fixed counter
    (1,6,False),      # palm / fixed counter
    (7,6,False),      # chassis / fixed counter
])
def test_monitor_movable_boundary(monkeypatch,geom1,geom2,allowed):
    executor,rows=contact_executor(monkeypatch,geom1,geom2,'movable_objects')
    if allowed:executor.monitor()
    else:
        with pytest.raises(RuntimeError,match='Unexpected physical robot/world contact'):
            executor.monitor()
    physics=[row for path,row in rows if path=='physics'][0]
    assert bool(physics['unexpected_contacts']) is not allowed
    assert ('expected_movable_contacts' in physics) is allowed
    journal=[row for path,row in rows if path=='journal']
    assert [row['event'] for row in journal]==(['movable_contact_begin'] if allowed else [])
    if allowed:
        robot_geom=geom1 if geom1 in (0,1,7) else geom2
        object_geom=geom2 if robot_geom==geom1 else geom1
        assert physics['expected_movable_contacts']==[
            [executor.env.sim.model.geom_id2name(robot_geom),
             executor.env.sim.model.geom_id2name(object_geom),-.0001]]
        executor.env.sim.data.contact=[]
        executor.monitor()
        assert [row['event'] for path,row in rows if path=='journal']==[
            'movable_contact_begin','movable_contact_end']

def test_monitor_default_contact_rule_and_row_shape(monkeypatch):
    executor,rows=contact_executor(monkeypatch,0,2,'none')
    with pytest.raises(RuntimeError,match='Unexpected physical robot/world contact'):
        executor.monitor()
    physics=[row for path,row in rows if path=='physics'][0]
    assert physics['unexpected_contacts']==[['openarm_left_finger_collision0','banana',-.0001]]
    assert set(physics)=={'request_id','physics_step','qpos','qvel','base_qpos','gripper_qpos',
        'gripper_qvel','tcp','unexpected_contacts','bilateral_contact_objects'}
    assert 'expected_movable_contacts' not in physics
    executor,rows=contact_executor(monkeypatch,0,2,'none');executor.gripping=True
    executor.monitor()
    assert [row for path,row in rows if path=='physics'][0]['unexpected_contacts']==[]
    executor,rows=contact_executor(monkeypatch,1,2,'none');executor.gripping=True
    with pytest.raises(RuntimeError,match='Unexpected physical robot/world contact'):
        executor.monitor()
    executor,rows=contact_executor(monkeypatch,7,6,'none')
    executor.monitor()  # Historical default does not inspect chassis-only contacts.
    assert [row for path,row in rows if path=='physics'][0]['unexpected_contacts']==[]

def test_enabled_fixed_contact_is_unexpected_even_when_gripping(monkeypatch):
    executor,rows=contact_executor(monkeypatch,0,6,'movable_objects')
    executor.gripping=True  # The fixed counter body is deliberately in obj_body_id.
    with pytest.raises(RuntimeError,match='Unexpected physical robot/world contact'):
        executor.monitor()
    physics=[row for path,row in rows if path=='physics'][0]
    assert physics['unexpected_contacts']==[['openarm_left_finger_collision0','counter_top',-.0001]]
    assert 'expected_movable_contacts' not in physics

@pytest.mark.parametrize('mode',['none','movable_objects'])
def test_existing_robot_self_contact_still_aborts(monkeypatch,mode):
    executor,rows=contact_executor(monkeypatch,0,1,mode)
    with pytest.raises(RuntimeError,match='Unexpected physical robot/world contact'):
        executor.monitor()
    assert [row for path,row in rows if path=='physics'][0]['unexpected_contacts']==[
        ['openarm_left_finger_collision0','openarm_left_palm_collision0',-.0001]]

def test_pure_contact_classifier_uses_free_joint_roots():
    model=ContactModel()
    roots=tuple(wm.movable_object_roots(SimpleNamespace(sim=SimpleNamespace(model=model),
        obj_body_id={'obj':4,'distr_counter':6,'distr_sink':7,'counter':8})).values())
    assert roots==(6,7,4)
    assert is_expected_movable_contact(model,2,5,'openarm_left_finger_collision0',1,'chassis_geom',roots)
    assert is_expected_movable_contact(model,9,6,'chassis_geom',1,'chassis_geom',roots)
    assert not is_expected_movable_contact(model,2,8,'openarm_left_finger_collision0',1,'chassis_geom',roots)
    assert not is_expected_movable_contact(model,8,4,'counter_top',1,'chassis_geom',roots)
