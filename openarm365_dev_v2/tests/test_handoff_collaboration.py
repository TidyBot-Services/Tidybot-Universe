import hashlib,json
from pathlib import Path
from types import SimpleNamespace as NS
import numpy as np
import pytest
from curobo_v2.client import PlanningRejected
from openarm365_dev import executor as ex
from openarm365_dev_v2 import dev_config
from openarm365_dev_v2.tests.test_c1_collaboration import fake_executor
from benchmarks.attention_harness.dev_tasks import dev_sdk_prompt


def handoff_executor(monkeypatch):
    obj=fake_executor(monkeypatch)
    obj.bimanual_collaboration='handoff';obj.handoff=None;obj.moving=False;obj.gripping=False;obj.failed=False
    obj.grasp_counts={'left':{4:12},'right':{4:12}}
    from curobo_v2.execution_settings import R3_20261025
    obj.execution_settings=R3_20261025
    monkeypatch.setattr(ex,'base_drift_limits',lambda settings:None)
    from openarm365_dev import attachment
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{4},'right':{4}})
    monkeypatch.setattr(ex.NativeExecutor,'touching_other_held',lambda self,side:side=='right')
    monkeypatch.setattr(ex.NativeExecutor,'handoff_donor_released',lambda self,side,bid:True)
    return obj


def shared_executor(monkeypatch):
    obj=handoff_executor(monkeypatch)
    ex.NativeExecutor.gripper(obj,'left',1.,request_id='left-close')
    receipt=ex.NativeExecutor.gripper(obj,'right',1.,request_id='right-close')
    assert receipt['handoff']['phase']=='shared'
    assert obj.held['left']['target_id']==obj.held['right']['target_id']==4
    return obj


def test_handoff_begin_release_and_receiver_attachment(monkeypatch):
    obj=shared_executor(monkeypatch)
    assert obj.handoff['begin_request_id']=='right-close'
    assert obj.handoff['donor']=='left' and obj.handoff['receiver']=='right'
    original=obj.control_step
    def step():
        original()
        if obj.handoff and obj.handoff['phase']=='releasing':obj.handoff['independent_steps']+=1
    obj.control_step=step
    receipt=ex.NativeExecutor.gripper(obj,'left',-1.,request_id='left-open')
    assert receipt['handoff_completed'] is True and receipt['ownership']=='right'
    assert receipt['handoff']['phase']=='completed' and receipt['handoff']['independent_steps']==12
    assert receipt['handoff']['release_request_id']=='left-open'
    assert obj.held['left'] is None and obj.held['right']['target_id']==4
    obj.planner._attachment_side='right'
    assert ex.NativeExecutor.planner_attachment(obj)['target_id']==4


def test_handoff_unconfirmed_receiver_public_rejection(monkeypatch):
    obj=handoff_executor(monkeypatch)
    ex.NativeExecutor.gripper(obj,'left',1.,request_id='left-close')
    obj.grasp_counts['right'].clear()
    with pytest.raises(PlanningRejected,match='Handoff receiver did not confirm a bilateral grasp'):
        ex.NativeExecutor.gripper(obj,'right',1.,request_id='right-close')
    assert obj.held['left']['target_id']==4 and obj.held['right'] is None
    assert obj.handoff is None and obj.failed is False


def test_handoff_shared_motion_rejected_before_planning(monkeypatch):
    obj=shared_executor(monkeypatch)
    obj.planner.plan_joints=lambda *args,**kwargs:pytest.fail('planner called')
    obj.planner.plan_pose=lambda *args,**kwargs:pytest.fail('planner called')
    for call in (lambda:ex.NativeExecutor.move_joints(obj,np.zeros(14),side='left',request_id='m'),
                 lambda:ex.NativeExecutor.move_joints(obj,np.zeros(14),side='both',request_id='m'),
                 lambda:ex.NativeExecutor.move_delta(obj,'right',0,0,0,request_id='m'),
                 lambda:ex.NativeExecutor.base_move(obj,0,0,0,request_id='m')):
        with pytest.raises(PlanningRejected,match='Arm and base motion are not allowed'):call()
    assert obj.sequence==40


def test_handoff_receiver_loss_during_release_is_fatal(monkeypatch):
    obj=shared_executor(monkeypatch)
    original=obj.control_step
    def step():
        if obj.gripping and obj.gripping_side=='left':raise RuntimeError('Measured bilateral grasp contact lost during transport')
        original()
    obj.control_step=step
    with pytest.raises(RuntimeError,match='Receiver independent holding not confirmed'):
        ex.NativeExecutor.gripper(obj,'left',-1.,request_id='left-open')
    assert obj.failed is True


def test_handoff_donor_release_must_be_measured(monkeypatch):
    obj=shared_executor(monkeypatch)
    monkeypatch.setattr(ex.NativeExecutor,'handoff_donor_released',lambda self,side,bid:False)
    with pytest.raises(RuntimeError,match='Donor release not confirmed'):
        ex.NativeExecutor.gripper(obj,'left',-1.,request_id='left-open')
    assert obj.failed is True and obj.held['left']['target_id']==4


def test_handoff_receiver_open_cancels(monkeypatch):
    obj=shared_executor(monkeypatch)
    receipt=ex.NativeExecutor.gripper(obj,'right',-1.,request_id='right-open')
    assert receipt['handoff']['phase']=='cancelled'
    assert obj.held['left']['target_id']==4 and obj.held['right'] is None


def test_handoff_unstable_donor_rejected_before_gripper_command(monkeypatch):
    obj=handoff_executor(monkeypatch)
    ex.NativeExecutor.gripper(obj,'left',1.,request_id='left-close')
    obj.held_transform['left']=None
    before=obj.sequence
    with pytest.raises(PlanningRejected,match='Handoff donor is not stably holding'):
        ex.NativeExecutor.gripper(obj,'right',1.,request_id='right-close')
    assert obj.sequence==before and obj.held['right'] is None
    obj.held_transform['left']=np.eye(4);obj.moving=True
    with pytest.raises(PlanningRejected,match='Handoff donor is not stably holding'):
        ex.NativeExecutor.gripper(obj,'right',1.,request_id='mid-grasp')
    assert obj.sequence==before


def test_handoff_inconsistent_actions_reject_before_motion(monkeypatch):
    obj=shared_executor(monkeypatch)
    before=obj.sequence
    with pytest.raises(PlanningRejected,match='Handoff state is inconsistent'):
        ex.NativeExecutor.gripper(obj,'right',1.,request_id='third-close')
    assert obj.sequence==before
    obj.handoff['target_id']=99
    with pytest.raises(PlanningRejected,match='Handoff state is inconsistent'):
        ex.NativeExecutor.move_delta(obj,'left',0,0,0,request_id='stale')
    assert obj.sequence==before
    empty=handoff_executor(monkeypatch)
    # Opening an empty gripper stays an ordinary open, as in c1 (policies open both grippers up front).
    receipt=ex.NativeExecutor.gripper(empty,'left',-1.,request_id='empty-open')
    assert receipt['status']=='completed' and empty.held=={'left':None,'right':None} and empty.handoff is None


def test_handoff_shared_monitor_both_contacts_and_envelopes(monkeypatch):
    import sys
    from openarm365_dev_v2.tests.test_contact_exemption import contact_executor
    from openarm365_dev import attachment
    if 'mujoco' not in sys.modules:
        monkeypatch.setitem(sys.modules,'mujoco',NS(mj_contactForce=lambda model,data,index,force:None))
    obj,rows=contact_executor(monkeypatch,0,2,'movable_objects')
    obj.bimanual_collaboration='handoff';obj.held={'left':{'holder':'left','target_id':4},'right':{'holder':'right','target_id':4}}
    obj.held_transform={'left':np.eye(4),'right':np.eye(4)}
    obj.handoff=dict(id='h',donor='left',receiver='right',target_id=4,phase='shared',begin_request_id='b')
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{4},'right':{4}})
    monkeypatch.setattr(attachment,'object_pose',lambda env,bid:np.eye(4))
    obj.monitor()
    for missing in ('left','right'):
        monkeypatch.setattr(attachment,'bilateral_contacts',lambda env,s=missing:{'left':set() if s=='left' else {4},'right':set() if s=='right' else {4}})
        with pytest.raises(RuntimeError,match='bilateral grasp contact lost'):obj.monitor()
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':{4},'right':{4}})
    obj.held_transform['left'][0,3]=.01
    with pytest.raises(RuntimeError,match='4mm/0.03rad bound'):obj.monitor()
    obj.held_transform['left']=np.eye(4);obj.held_transform['right'][0,3]=.01
    with pytest.raises(RuntimeError,match='4mm/0.03rad bound'):obj.monitor()


def test_handoff_independent_monitor_counts_physics_steps(monkeypatch):
    import sys
    from openarm365_dev_v2.tests.test_contact_exemption import contact_executor
    from openarm365_dev import attachment
    if 'mujoco' not in sys.modules:
        monkeypatch.setitem(sys.modules,'mujoco',NS(mj_contactForce=lambda model,data,index,force:None))
    obj,rows=contact_executor(monkeypatch,0,2,'movable_objects')
    obj.bimanual_collaboration='handoff';obj.held={'left':None,'right':{'holder':'right','target_id':4}}
    obj.held_transform={'left':None,'right':np.eye(4)}
    obj.handoff=dict(id='h',donor='left',receiver='right',target_id=4,phase='releasing',
        begin_request_id='begin',release_request_id='release',independent_steps=0)
    obj.env.sim.data.contact=[]
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':set(),'right':{4}})
    monkeypatch.setattr(attachment,'object_pose',lambda env,bid:np.eye(4))
    for _ in range(ex.HANDOFF_INDEPENDENT_STEPS):obj.monitor()
    assert obj.handoff['independent_steps']==12
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':set(),'right':set()})
    with pytest.raises(RuntimeError,match='bilateral grasp contact lost'):obj.monitor()


def test_handoff_config_prompt_and_golden(monkeypatch,tmp_path):
    from benchmarks.attention_harness.dev_experiment import validate_selection_fields
    validate_selection_fields({'bimanual_collaboration':'handoff'})
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs:None)
    args=('pick_place_counter_to_sink',102,{'path':'fixture','sha256':'fixture'})
    old=dev_config._make_dev_config(*args,budget_tier='legacy')
    none=dev_config._make_dev_config(*args,budget_tier='legacy',bimanual_collaboration='none')
    assert old==none and dev_sdk_prompt(old)==dev_sdk_prompt(none)
    c1=dev_config._make_dev_config(*args,budget_tier='legacy',bimanual_collaboration='c1')
    handoff=dev_config._make_dev_config(*args,budget_tier='legacy',bimanual_collaboration='handoff')
    assert dev_config.validate_dev_config(handoff)==handoff
    assert 'While both hands hold the same object, no arm or base motion is allowed.' in dev_sdk_prompt(handoff)
    assert 'Do not grasp an object the other arm is holding' in dev_sdk_prompt(c1)
    for mode in ('none','c1','handoff'):
        obj=ex.NativeExecutor.__new__(ex.NativeExecutor)
        obj.bimanual_collaboration=mode;obj.execution_settings=None;obj._execution_receipt={}
        obj.held={'left':None,'right':None} if mode!='none' else None
        obj.handoff=None;obj.journal=tmp_path/mode
        ex.NativeExecutor.log(obj,obj.journal,dict(event='receipt',request_id='r'))
        row=json.loads(obj.journal.read_bytes())
        assert ('handoff' in row)==(mode=='handoff')
    assert hashlib.sha256(json.dumps(old,sort_keys=True).encode()).digest()==hashlib.sha256(json.dumps(none,sort_keys=True).encode()).digest()
    assert hashlib.sha256(json.dumps(none,sort_keys=True).encode()).hexdigest()=='a2a6a773562d850a564cbe84a079e3b0776b187feff7df6534f074adfc02d733'
    assert hashlib.sha256(json.dumps(c1,sort_keys=True).encode()).hexdigest()=='2e37ce05923b000c87cd127b4c05af9c56a82c1ac34e70f26290e8ccdc4e2a75'
    assert hashlib.sha256(dev_sdk_prompt(none).encode()).hexdigest()=='8cb6a858ac954608432785b184e5aaf54e8db4d31e41f2cc40ea768b897db7ad'
    assert hashlib.sha256(dev_sdk_prompt(c1).encode()).hexdigest()=='1e886977f975ab433953a9a426c2ba59b1ca02f573a9710ab16d01b2520df44c'
    assert (tmp_path/'none').read_text()=='{"event": "receipt", "request_id": "r"}\n'
    assert (tmp_path/'c1').read_text()=='{"bimanual_collaboration": "c1", "event": "receipt", "held_by_arm": {"left": null, "right": null}, "request_id": "r"}\n'


def test_handoff_journal_tracks_current_record(tmp_path):
    obj=ex.NativeExecutor.__new__(ex.NativeExecutor)
    obj.bimanual_collaboration='handoff';obj.execution_settings=None;obj._execution_receipt={}
    obj.held={'left':{'holder':'left','target_id':4},'right':{'holder':'right','target_id':4}}
    obj.handoff=dict(id='h',donor='left',receiver='right',target_id=4,phase='shared',begin_request_id='begin')
    obj.journal=tmp_path/'journal'
    ex.NativeExecutor.log(obj,obj.journal,dict(event='gripper_intent'))
    obj.held['left']=None;obj.handoff.update(phase='completed',release_request_id='release',independent_steps=12)
    ex.NativeExecutor.log(obj,obj.journal,dict(event='receipt'))
    rows=[json.loads(line) for line in obj.journal.read_text().splitlines()]
    assert [row['handoff']['phase'] for row in rows]==['shared','completed']
    assert rows[0]['held_by_arm']['left']['target_id']==4 and rows[1]['held_by_arm']['left'] is None


def test_handoff_agent_run_config_creation(monkeypatch):
    from dataclasses import replace
    from curobo_v2 import identity,dev_local,execution_settings as es
    from benchmarks.attention_harness.dev_experiment import validate_selection_fields
    validate_selection_fields({'bimanual_collaboration':'handoff','dev_mode':'agent'})
    pin={'lock_path':'fixture-planner','sha256':'a'*64}
    monkeypatch.setattr(es,'_deployment_pin',lambda:pin)
    settings=replace(es.R3_20261025,planner_deployment=pin)
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs:None)
    monkeypatch.setattr(dev_config,'_planner_source',lambda value,*args:value)
    monkeypatch.setattr(identity,'load_lock',lambda *args,**kwargs:({},pin['sha256']))
    monkeypatch.setattr(dev_local,'is_dev_local',lambda value:True)
    c=dev_config.make_dev_config('pick_place_counter_to_sink',102,
        {'path':'fixture','sha256':'fixture'},execution_settings=settings,bimanual_collaboration='handoff')
    c['dev_mode']='agent'
    assert dev_config.validate_dev_config(c)==c
    assert c['dev_sdk_prompt_sha256']==hashlib.sha256(dev_sdk_prompt(c).encode()).hexdigest()
    with pytest.raises(ValueError,match='bimanual_collaboration'):
        dev_config._make_dev_config('pick_place_counter_to_sink',102,{'path':'fixture'},
            bimanual_collaboration='invalid')


def test_donor_release_confirms_at_the_commanded_open_endpoint():
    from openarm365_dev.executor import GRIPPER_COMMAND_SCALE,gripper_open_confirmed
    limits=np.array([[0.,.044,1.],[0.,.044,1.]])
    endpoint=.044*(1+GRIPPER_COMMAND_SCALE)/2  # command in [-1,1] maps to q in [0,.044]: limit-1mm
    assert gripper_open_confirmed([endpoint,endpoint],limits)
    assert gripper_open_confirmed([.0429993,.0429993],limits)  # pilot_a110r 11-01: rested 0.7um short of the endpoint
    assert gripper_open_confirmed([.0425,.0425],limits)
    assert not gripper_open_confirmed([.0424,.044],limits)
    assert not gripper_open_confirmed([.02,.02],limits)


def release_executor(monkeypatch,q,qvel=0.,contacts=frozenset(),force=0.):
    from openarm365_dev import attachment
    native=ex.PHYSICAL_TO_NATIVE['left']
    sim=NS(data=NS(qpos=np.asarray(q,float),qvel=np.full(2,qvel)),
           model=NS(get_joint_qvel_addr=lambda name:{'g0':0,'g1':1}[name]))
    robot=NS(_ref_gripper_joint_pos_indexes={native:[0,1]},gripper={native:NS(joints=['g0','g1'])})
    obj=NS(env=NS(sim=sim,robots=[robot]),
           gripper_limits={'left':np.array([[0.,.044,1.],[0.,.044,1.]])})
    monkeypatch.setattr(attachment,'bilateral_contacts',lambda env:{'left':set(contacts),'right':{4}})
    monkeypatch.setattr(ex,'finger_object_contact_measurements',lambda env,side,bid:({'g0':force,'g1':0.},{}))
    return obj


@pytest.mark.parametrize('q',[.043,.0429993,.0425])
def test_donor_release_confirms_a_resting_open_gripper(monkeypatch,q):
    # The open command stops at limit-1mm (.043); the old bound q>=limit-1mm sat exactly on that endpoint, so a gripper
    # resting a few micrometres short (pilot_a110r 11-01: .0429993) never confirmed. The bound is now endpoint-0.5mm.
    old=bool(np.all(np.array([q,q])>=.044-.001))
    assert old is (q>=.043)
    assert ex.NativeExecutor.handoff_donor_released(release_executor(monkeypatch,[q,q]),'left',4)


@pytest.mark.parametrize('kwargs',[dict(contacts={4}),dict(force=.1),dict(qvel=.006),dict(q=[.0424,.043])])
def test_donor_release_still_requires_every_other_condition(monkeypatch,kwargs):
    q=kwargs.pop('q',[.0429993,.0429993])
    assert not ex.NativeExecutor.handoff_donor_released(release_executor(monkeypatch,q,**kwargs),'left',4)
