"""Explicit r3 execution tests and baseline replay; no native service or simulation."""
import json
from types import SimpleNamespace as NS
from pathlib import Path
import numpy as np
import pytest
from curobo_v2 import execution_settings as es,attachment
from curobo_v2.client import PlanningRejected
from openarm365_dev import base_motion as base,executor as ex,planner as plan
from openarm365_dev_v2.tests.test_r3_config import baseline
from openarm365_dev_v2.tests.test_base_motion import fake_settling_executor
from openarm365_dev_v2.tests.test_held_reference import FakeExecutor,pose
from openarm365_dev_v2.tests.test_mujoco_ik_mode import planner
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment


@pytest.mark.parametrize('settings',[False,True])
def test_base_clearance_and_same_hold_limit_at_entry(monkeypatch,r3_deployment,settings):
    value=r3_deployment if settings else None
    assert base.effective_base_min_distance(value)==(.002 if settings else .003)
    assert base.required_pair_distance(.04,execution_settings=value)==(.010 if settings else .020)
    assert base.required_pair_distance(.003,execution_settings=value)==(.002 if settings else .003)
    executor,events=fake_settling_executor(monkeypatch,[[.0025,0,.0025]]*6)
    executor.execution_settings=value
    executor.env.sim.data.qpos[:]=[.0025,0,.0025]
    monkeypatch.setattr(ex,'check_exact_base_path',lambda *a,**k:dict(status='clear'))
    if settings:
        base.guard_base_and_arms([.0025,0,.0025],[0,0,0],None,execution_settings=value)
        result=executor.base_move(0,0,0,request_id='move')
        assert result['base_drift']['max_translation_m']==.0025
        assert result['base_drift']['max_yaw_rad']==.0025
        assert events[-1]['event']=='base_drift'
        assert result['execution_profile']==es.PROFILE
    else:
        with pytest.raises(RuntimeError,match='base drift'):executor.base_move(0,0,0,request_id='move')
        with pytest.raises(RuntimeError,match='base drift'):base.guard_base_and_arms([.0025,0,0],[0,0,0],None)
    with pytest.raises(RuntimeError,match='base drift'):
        base.guard_base_and_arms([.0031,0,0],[0,0,0],None,execution_settings=value)
    with pytest.raises(RuntimeError,match='Arm joint drift'):
        base.guard_base_and_arms([0,0,0],[0,0,0],[.0041],[0],execution_settings=value)


def test_r3_drift_recorded_on_rejection(monkeypatch,r3_deployment):
    executor,events=fake_settling_executor(monkeypatch,[[0,0,0]])
    executor.execution_settings=r3_deployment;executor.env.sim.data.qpos[0]=.0031
    with pytest.raises(RuntimeError,match='base drift'):executor.base_move(0,0,0,request_id='bad')
    assert events[-1]['base_drift']['max_translation_m']==.0031
    assert events[-1]['status']=='failed'


def test_grasp_confirmation_and_held_envelope(monkeypatch,r3_deployment):
    import openarm365_dev.attachment as native_attachment
    for confirmed in [False,True]:
        obj=FakeExecutor([0],'fixed');obj.execution_settings=r3_deployment
        obj.base_hold=np.zeros(3);obj.env=NS(sim=NS(data=NS(qpos=np.zeros(3))),robots=[NS(_ref_base_joint_pos_indexes=[0,1,2])])
        obj.record_drift_sample=ex.NativeExecutor.record_drift_sample.__get__(obj)
        obj.drift_receipt=ex.NativeExecutor.drift_receipt.__get__(obj)
        if not confirmed:obj.grasp_counts['left']={}
        monkeypatch.setattr(ex,'tcp_pose',lambda *a:np.eye(4))
        monkeypatch.setattr(native_attachment,'object_pose',lambda *a:np.eye(4))
        if not confirmed:
            with pytest.raises(PlanningRejected,match='confirm bilateral grasp'):ex.NativeExecutor.gripper(obj,'left',1.,request_id='close')
            assert obj.held is None and obj.failed
        else:
            result=ex.NativeExecutor.gripper(obj,'left',1.,request_id='close')
            assert obj.sequence==20 and result['attached_object']=={'holder':'left','target_id':7}
            assert result['held_envelope']=={'translation_m':.01,'rotation_rad':.1}
    assert attachment.effective_held_envelope(r3_deployment)==(.01,.1)


@pytest.mark.parametrize('mode',['plan','ik_path'])
def test_legacy_planner_requests_receipts_evidence_bytes(monkeypatch,planner,mode):
    prior=baseline('openarm365_dev/planner.py');prior.mount_pose=plan.mount_pose
    planner.planner_mode=mode;planner.ik_candidates=8;planner.ik_max_joint_jump=1.
    results=[]
    for cls in (prior.MujocoV2Planner,plan.MujocoV2Planner):
        planner.client.calls=[];planner.ledger.issued=[]
        planner.plan=cls.plan.__get__(planner)
        response=cls.plan_pose(planner,[.1,.2,.3],np.eye(3),side='left',epoch=7)
        results.append((json.dumps(planner.client.calls).encode(),json.dumps(response).encode(),
                        (planner.evidence/'trajectory-0001.json').read_bytes()))
    assert results[0]==results[1]


def test_legacy_executor_receipts_and_journal_bytes(monkeypatch,tmp_path):
    prior=baseline('openarm365_dev/executor.py');results=[]
    prior.time=NS(monotonic=lambda:10.)
    monkeypatch.setattr(ex,'time',prior.time)
    for i,cls in enumerate((prior.NativeExecutor,ex.NativeExecutor)):
        obj,_=fake_settling_executor(monkeypatch,[[0,0,0]]*6)
        prior.check_exact_base_path=ex.check_exact_base_path
        obj.journal=tmp_path/f'journal-{i}.jsonl';obj.log=cls.log.__get__(obj)
        result=cls.base_move(obj,0,0,0,request_id='same')
        results.append((json.dumps(result).encode(),obj.journal.read_bytes()))
    assert results[0]==results[1]


def test_r3_margin_rechecks_all_seven_before_and_after_service(monkeypatch,planner,r3_deployment,tmp_path):
    planner.execution_settings=r3_deployment
    planner.names=[f'openarm_{side}_joint{i}' for side in ['left','right'] for i in range(1,8)]
    urdf=tmp_path/'robot.urdf';urdf.write_text('<robot>'+''.join(f'<joint name="{n}"><limit lower="-1" upper="1"/></joint>' for n in planner.names)+'</robot>')
    planner.spec={'robot_cfg':{'kinematics':{'urdf_path':str(urdf)}}}
    planner.state=lambda:np.zeros(14);planner.lock_addresses={}
    planner.body=plan.MujocoV2Planner.body.__get__(planner)
    planner.ledger.invalidate=lambda *a:None
    target=np.zeros(14);target[6]=.99
    with pytest.raises(PlanningRejected,match='Goal joint margin'):planner.plan_joints(target,side='left',epoch=1)
    assert not planner.client.calls
    planner.failed=False;planner.ledger.invalidate=lambda *a:None
    # An authenticated service returning a bad seventh-joint endpoint is refused before issue.
    def call(path,body):
        planner.client.calls.append((path,body))
        return dict(trajectory=[np.zeros(14).tolist(),target.tolist()]) if path.startswith('/plan') else {}
    planner.client.call=call
    with pytest.raises(PlanningRejected,match='Goal joint margin'):planner.plan_pose([0,0,0],np.eye(3),side='left',epoch=1)
    assert not planner.ledger.issued
    assert all(body['execution_settings']==r3_deployment.to_dict() for _,body in planner.client.calls)
    target[6]=.97;planner.failed=False
    result=planner.plan_pose([0,0,0],np.eye(3),side='left',epoch=1)
    assert result['goal_margin']['minimum_joint_margin_rad']==pytest.approx(.03)
    assert result['planner_lock_sha256']==r3_deployment.planner_deployment['sha256']


def test_no_diagnostic_environment_settings(monkeypatch,r3_deployment):
    for name in ['ATTN_DIAG_BASE_MIN_DISTANCE_M','ATTN_DIAG_BASE_CLEARANCE_M','ATTN_DIAG_BASE_HOLD_DRIFT_M','ATTN_DIAG_JOINT_MARGIN_RAD','ATTN_DIAG_HELD_ENVELOPE_M','ATTN_DIAG_PLANNER_LOCK']:
        monkeypatch.setenv(name,'invalid')
    assert base.effective_base_clearance(r3_deployment)==.01
    assert base.effective_base_clearance()==.02
    assert attachment.effective_held_envelope(r3_deployment)==(.01,.1)
    assert attachment.effective_held_envelope()==(.004,.03)


@pytest.mark.parametrize('r3',[False,True])
@pytest.mark.parametrize('wrong_sha',[False,True])
def test_planner_pins_deployment_and_service_boundary(monkeypatch,tmp_path,r3_deployment,r3,wrong_sha):
    import yaml
    settings=r3_deployment if r3 else None
    expected=es.D05_SHA if settings is None else settings.planner_deployment['sha256']
    calls=[]
    def load(path,**kw):
        calls.append((str(path),kw))
        return {'project_root':str(tmp_path)},'f'*64 if wrong_sha else expected
    monkeypatch.setattr(plan,'load_lock',load)
    monkeypatch.setattr(plan,'verify_files',lambda _:None);monkeypatch.setattr(plan,'verify_client_imports',lambda _:None)
    monkeypatch.setattr(plan,'joint_address',lambda *a:0)
    (tmp_path/'config').mkdir()
    (tmp_path/'config/openarm_fixed.yml').write_text(yaml.safe_dump(dict(service=dict(joint_names=[],native_chassis={}),robot_cfg=dict(kinematics=dict(lock_joints={},tool_frames={})))))
    owners=[]
    class Owner:
        def __init__(self,**kw):owners.append(kw)
        def __enter__(self):return object()
    monkeypatch.setattr(plan,'V2Service',Owner)
    if wrong_sha:
        with pytest.raises(plan.IdentityError,match='lock changed'):
            plan.MujocoV2Planner(NS(sim=None),evidence=tmp_path/'evidence',deadline=100,cancel_check=lambda:False,execution_settings=settings)
        assert not owners
    else:
        planner=plan.MujocoV2Planner(NS(sim=None),evidence=tmp_path/'evidence',deadline=100,cancel_check=lambda:False,execution_settings=settings)
        assert planner.planner_lock_sha256==expected
        assert owners[0].get('execution_settings')==settings
    assert calls[0][1]==(dict(tier='dev',boundary='dev_loop') if r3 else {})
    assert calls[0][0]==(settings.planner_deployment['lock_path'] if r3 else str(__import__('openarm365_dev.config',fromlist=['LOCK']).LOCK))


@pytest.mark.parametrize('r3',[False,True])
def test_service_spawn_evidence_and_legacy_bytes(tmp_path,r3_deployment,r3):
    import ast,inspect
    from curobo_v2 import session
    settings=r3_deployment if r3 else None
    objects=[]
    modules=[session] if r3 else [baseline('curobo_v2/session.py'),session]
    for module in modules:
        source=inspect.getsource(session.V2Service.__enter__) if module is session else __import__('subprocess').check_output(['git','show','3344c6f:curobo_v2/session.py'],text=True)
        tree=ast.parse(__import__('textwrap').dedent(source))
        statement=next(n for n in ast.walk(tree) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and n.value.func.attr=='write_text' and 'service_spawn.json' in ast.unparse(n))
        self=NS(evidence=tmp_path,process=NS(pid=123),lock_sha=es.D05_SHA if settings is None else settings.planner_deployment['sha256'],robot_config='robot.yml',tier='dev' if r3 else None,boundary='dev_loop' if r3 else None,execution_settings=settings)
        exec(compile(ast.Module(body=[statement],type_ignores=[]),'spawn-evidence','exec'),dict(self=self,json=json,trust_fields={},receipt_fields=es.receipt_fields))
        objects.append((tmp_path/'service_spawn.json').read_bytes())
    if r3:
        assert json.loads(objects[0])['execution_profile']==es.PROFILE
        assert json.loads(objects[0])['execution_settings_source']=='config'
        assert json.loads(objects[0])['planner_lock_sha256']==json.loads(objects[0])['lock_sha256']==settings.planner_deployment['sha256']
    else:assert objects[0]==objects[1]


@pytest.mark.parametrize('r3',[False,True])
@pytest.mark.parametrize('shift,angle',[(.006,0),(0,.06),(.011,0),(0,.11)])
def test_executor_and_client_held_monitors_share_envelope(monkeypatch,r3_deployment,r3,shift,angle):
    from scipy.spatial.transform import Rotation
    from openarm365_dev_v2.tests.test_contact_exemption import contact_executor
    import openarm365_dev.attachment as native
    from curobo_v2.sapien_monitor import KitchenWorldMonitor,AttachmentShifted
    settings=r3_deployment if r3 else None
    obj,_=contact_executor(monkeypatch,0,2,'movable_objects')
    obj.execution_settings=settings;obj.held={'holder':'left','target_id':4};obj.held_transform=np.eye(4)
    relative=np.eye(4);relative[0,3]=shift;relative[:3,:3]=Rotation.from_rotvec([angle,0,0]).as_matrix()
    monkeypatch.setattr(native,'bilateral_contacts',lambda env:{'left':{4},'right':set()})
    monkeypatch.setattr(native,'object_pose',lambda *a:relative)
    client=KitchenWorldMonitor.__new__(KitchenWorldMonitor)
    client.execution_settings=settings;client.attachment=obj.held;client.attachment_provider=lambda:obj.held
    client.attachment_transform=np.eye(4);client._attachment_transform=lambda:relative
    translation,rotation=attachment.effective_held_envelope(settings)
    for check,error in [(obj.monitor,RuntimeError),(client._check_attachment,AttachmentShifted)]:
        if shift>translation or angle>rotation:
            with pytest.raises(error):check()
        else:check()


def test_legacy_gripper_and_physics_evidence_bytes(monkeypatch):
    from openarm365_dev_v2.tests.test_held_reference import drive
    from openarm365_dev_v2.tests.test_contact_exemption import contact_executor
    prior=baseline('openarm365_dev/executor.py')
    with monkeypatch.context() as patch:
        old=drive(patch,[0],method=prior.NativeExecutor.gripper)
        current=drive(patch,[0])
    assert json.dumps(old.events).encode()==json.dumps(current.events).encode()
    values=[]
    for cls in (prior.NativeExecutor,ex.NativeExecutor):
        obj,rows=contact_executor(monkeypatch,7,6,'none')
        cls.monitor(obj);values.append(json.dumps(rows).encode())
    assert values[0]==values[1]


@pytest.mark.parametrize('gap,delta,legacy_ok,r3_ok',[(.0025,0,False,True),(.04,.025,False,True),(.0019,0,False,False)])
def test_exact_clearance_settings_on_copied_data(r3_deployment,gap,delta,legacy_ok,r3_ok):
    import mujoco
    from openarm365_dev_v2.tests.test_exact_base_motion import ModelAdapter
    native=mujoco.MjModel.from_xml_string(f'''<mujoco><worldbody>
      <body name="robot0_openarm_body_link0">
        <joint type="slide" axis="1 0 0"/><joint type="slide" axis="0 1 0"/>
        <joint type="hinge" axis="0 0 1"/>
        <geom name="chassis" type="sphere" size=".05"/>
      </body><body pos="{.1+gap} 0 0"><geom name="fixture" type="sphere" size=".05"/></body>
    </worldbody></mujoco>''')
    data=mujoco.MjData(native);mujoco.mj_forward(native,data)
    env=NS(sim=NS(model=ModelAdapter(native),data=NS(_data=data,qpos=data.qpos)),robots=[NS(_ref_base_joint_pos_indexes=np.array([0,1,2]))])
    world=dict(excluded_robot_geoms=[dict(geom_id=0,name='chassis')],inventory=[dict(geom_id=1,name='fixture')],world_sha256='fixed')
    for settings,ok in [(None,legacy_ok),(r3_deployment,r3_ok)]:
        def check():return base.check_exact_base_path(env,world,{'native_geom':'chassis'},[delta,0,0],np.zeros(3),execution_settings=settings)
        if ok:
            audit=check()
            assert audit['absolute_min_distance_m']==(.002 if settings else .003)
            assert audit['required_clearance_m']==(.010 if settings else .020)
        else:
            with pytest.raises(base.BasePathRejected):check()
        assert data.qpos.tolist()==[0,0,0]


def test_legacy_apply_receipt_and_intent_bytes(monkeypatch):
    from openarm365_dev_v2.tests.test_motion_tracking_diagnostics import fake_apply
    prior=baseline('openarm365_dev/executor.py');results=[]
    for cls in (prior.NativeExecutor,ex.NativeExecutor):
        obj,_,events,_=fake_apply(monkeypatch,position=[.011,.02])
        prior.sample_for_original_drive=ex.sample_for_original_drive
        receipt=cls.apply(obj,{'plan_id':'same'},request_id='same')
        results.append((json.dumps(receipt).encode(),json.dumps(events).encode()))
    assert results[0]==results[1]


def test_legacy_planner_common_body_bytes():
    prior=baseline('openarm365_dev/planner.py')
    obj=NS(state=lambda:np.zeros(2),names=['a','b'],lock_addresses={'locked':0},env=NS(sim=NS(data=NS(qpos=[1.]))))
    assert json.dumps(prior.MujocoV2Planner.body(obj,['a'])).encode()==json.dumps(plan.MujocoV2Planner.body(obj,['a'])).encode()
