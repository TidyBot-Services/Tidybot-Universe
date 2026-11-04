"""Dual-arm task contract: saved red before any implementation."""
from benchmarks.attention_harness.workspace_paths import get_str
import importlib
from pathlib import Path
import pytest


def test_bimanual_host_policy_defaults_strict(tmp_path, monkeypatch):
    import json
    from openarm_bimanual.v2_adapter import identity
    path = tmp_path / 'runtime-lock.json'
    value = dict(schema='attentionbench.curobo-v2.runtime.v1', generation=2,
                 prefix='/prefix', python='/prefix/bin/python', library_root='/library',
                 service_root='/service', project_root='/project',
                 library_revision=identity.OFFICIAL_COMMIT, library_base=identity.OFFICIAL_COMMIT,
                 distribution_version='1', module_version='1', files={'/source': 'a'},
                 dependencies={'numpy': {}}, distributions={}, robot_configs={}, official_only=True,
                 host_policy='record')
    path.write_text(json.dumps(value))
    monkeypatch.setattr(identity, 'approved_deployment', lambda *args: True)
    assert not isinstance(identity.load_lock(path)[0], identity._RecordHostLock)
    assert not isinstance(identity.load_lock(path, tier='formal', boundary='formal')[0], identity._RecordHostLock)
    assert isinstance(identity.load_lock(path, tier='dev', boundary='dev_loop')[0], identity._RecordHostLock)


def test_both_identity_has_no_inactive_arm():
    from benchmarks.attention_harness.robocasa_native.robot_config import RobotSelection
    selected = RobotSelection('OpenArm', 'both')
    assert selected.inactive_arm is None
    assert selected.artifact() == {'model':'OpenArm', 'execution_arm':'both'}


def test_repeated_close_preserves_certified_grasp_without_ratchet():
    from openarm_bimanual.contact_policy import grasp_command
    assert grasp_command('right', ['right'], {'right':.02421}, .006) == (.02421, True)
    assert grasp_command('right', [], {'right':.02421}, .006) == (.006, False)
    assert grasp_command('left', ['right'], {'right':.02421}, .006) == (.006, False)
    with pytest.raises(ValueError):grasp_command('right', ['right'], {'right':float('nan')}, .006)


def test_phase_ledger_requires_real_grasp_and_handoff_receipts():
    from openarm_bimanual.phases import PhaseLedger
    ledger=PhaseLedger()
    def event(source,op,result=None,status='completed'):
        return {'source':'robot_sdk.'+source,'operation':op,'status':status,'timestamp':5.,'duration_ms':100.,'arguments':{},'result':result or {}}
    before,after=ledger.consume(event('left_gripper','open'))
    assert before[0]['arguments']['skill_id']=='left_pickup'
    ledger.consume(event('bimanual','begin_handoff',{'phase':'receiving','id':'0:h:1'}))
    _,after=ledger.consume(event('right_gripper','close',{'status':'completed','contact_hold_committed':False}))
    assert not after
    _,after=ledger.consume(event('right_gripper','close',{'status':'completed','contact_hold_committed':True}))
    assert after[0]['arguments']['skill_id']=='right_acquire'
    before,after=ledger.consume(event('bimanual','handoff',{'status':'failed','handoff':True},status='failed'))
    assert before[0]['event_type']=='skill.started'
    assert after[0]['event_type']=='skill.blocked'
    assert ledger.active=='physical_handoff' and not ledger.transferred


def test_handoff_waits_for_receiver_proof_without_dispatching_early(monkeypatch):
    from benchmarks.attention_harness.tests.test_openarm_grasp_v2 import handoff_backend
    backend,_,_,_,_,_=handoff_backend();attempts=[];original=backend._call
    def request(method,path,body):
        attempts.append(path)
        if len(attempts)==1:raise RuntimeError('OpenArm HTTP 409: receiver_not_confirmed')
        return original(method,path,body)
    backend._call=request
    monkeypatch.setattr('openarm_bimanual.backend.time.sleep',lambda _:None)
    assert backend.handoff('left','right')['id']=='release'
    assert attempts==['/handoff','/handoff']


def test_completed_handoff_disallows_donor_finger_target_contact():
    from openarm_bimanual.contact_policy import permitted_target_sides
    h={'phase':'completed','donor':'left','receiver':'right'}
    assert permitted_target_sides(['left','right'],h,None)==['right']
    assert permitted_target_sides(['left','right'],{**h,'phase':'receiving'},None)==['left','right']
    assert permitted_target_sides(['left','right'],h,{'grasp_arm':'left'})==['left','right']
    assert permitted_target_sides(['left'],None,None)==['left']


def test_sdk_preserves_only_valid_public_object_orientation():
    from types import SimpleNamespace as S
    from openarm_bimanual.perception import BimanualPerceptionBackend
    row={'name':'obj','position':[1,2,3],'confidence':1.,'source':'sim_gt','frame':'world',
         'quaternion_wxyz':[1.,0.,0.,0.],'native_success':True}
    calls=[]
    def find(**kwargs):
        calls.append(kwargs)
        return [row]
    sdk=BimanualPerceptionBackend(S(control_frame='world'),mode='sim_gt',target='robocasa_sim',provider=S(find_objects=find))
    result=sdk.find_objects()[0]
    assert result['quaternion_wxyz']==row['quaternion_wxyz'] and 'native_success' not in result
    assert len(calls)==1
    from tidybot_sdk.perception import ModeBoundPerceptionBackend
    legacy=ModeBoundPerceptionBackend(S(control_frame='world'),mode='sim_gt',target='robocasa_sim',provider=S(find_objects=find))
    assert 'quaternion_wxyz' not in legacy.find_objects()[0]
    row['quaternion_wxyz']=[2.,0.,0.,0.]
    with pytest.raises(ValueError):sdk.find_objects()


def test_both_stop_receipt_requires_measured_rest_and_process_reap():
    from benchmarks.attention_harness.service_recovery import service_stop_confirmed
    state={s:{'qvel':[0.]*7} for s in ['left','right']}
    state.update({s+'_gripper':{'driver_velocity_m_s':0.,'follower_velocity_m_s':0.} for s in ['left','right']});state['base']={'qvel':[0.]*3}
    stop={'mechanism':'dedicated_openarm_kitchen_process_group','robot':{'model':'OpenArm','execution_arm':'both'},
          'services':{'openarm':{'leader_reaped':True,'process_group_gone':True}},
          'stop_barrier':{'physical_rest_confirmed':True,'stopped_components':{'left':True,'right':True,'base':True},'stopped_state':state}}
    assert service_stop_confirmed(stop,'robocasa')
    state['right']['qvel'][0]=.051
    assert not service_stop_confirmed(stop,'robocasa')


def test_run_registers_bimanual_mobile_base_before_first_action():
    from benchmarks.attention_harness.robocasa_native.robot_config import robot_run_configuration
    config={'suite':'robocasa','schema_version':'attentionbench.robocasa-openarm-bimanual-config.v1','robot':{'model':'OpenArm','execution_arm':'both'}}
    result=robot_run_configuration(config,'a'*64)
    assert result['base']['capability']=='mobile_base_joint_drive' and 'base_status' not in result


@pytest.mark.parametrize('component', ['backend','sdk','service','contact_policy'])
def test_explicit_bimanual_components(component):
    if component == 'service':
        import subprocess,sys,os
        result=subprocess.run([get_str('sim.legacy_python'),'-B','-c','import openarm_bimanual.service'],capture_output=True,text=True)
        assert result.returncode == 0, result.stderr
    else:
        importlib.import_module('openarm_bimanual.' + component)


def test_sandbox_exposes_independent_arms_and_grippers():
    from benchmarks.attention_harness.robosuite_memory.formal_sandbox import _OPERATIONS
    for side in ['left','right']:
        assert {'move_to_position','plan_to_position'} <= _OPERATIONS[side + '_arm']
        assert {'open','close'} <= _OPERATIONS[side + '_gripper']
    assert {'handoff','state','stop','cancel','reset'} <= _OPERATIONS['bimanual']


def test_bimanual_policy_validates_in_existing_gate():
    from benchmarks.attention_harness.robocasa_native.policy_sandbox import validate_generated_policy
    validate_generated_policy('from robot_sdk import left_arm, right_gripper, bimanual\nleft_arm.plan_to_position(1,2,3)\nright_gripper.close()\nbimanual.handoff("left","right")')


def test_release_requires_current_receiver_contact_proof():
    p = importlib.import_module('openarm_bimanual.contact_policy')
    assert not p.release_allowed({'receiver':'right','receiver_contacts':None,'contact_steps':12}, 'left')
    assert not p.release_allowed({'receiver':'right','receiver_contacts':{'left':[], 'right':[]},'contact_steps':0}, 'left')


def test_ui_has_execution_mode_and_scoped_hint():
    text = (Path(__file__).resolve().parents[1]/'ui/live.js').read_text()
    assert 'Both arms' in text
    assert 'Execution mode' in text
    assert 'data-hint-arm' in text


def test_two_wrist_camera_bindings():
    specs = importlib.import_module('openarm_bimanual.display').camera_specs('both')
    assert specs['left_wrist']['parent'] == 'openarm_left_link7'
    assert specs['right_wrist']['parent'] == 'openarm_right_link7'
    assert specs['left_wrist']['name'] == 'Left wrist'
    assert specs['right_wrist']['name'] == 'Right wrist'


def test_contact_hold_preserves_accepted_closure_without_relaxing_tolerance():
    from openarm_bimanual.contact_policy import contact_hold_goal
    goal=contact_hold_goal(.02465,.02506,inset=.0004,free_limit=.006)
    assert goal == pytest.approx(.02425)
    assert max(abs(.02465-goal),abs(.02506-goal)) < .001


@pytest.mark.parametrize('scope', [None, {}, {'arm':'other','phase':'handoff','attempt_id':'a'},
    {'arm':'right','phase':'handoff','attempt_id':'old'}, {'arm':'right','phase':'unknown','attempt_id':'a'}])
def test_hints_cannot_target_unknown_arm_phase_or_attempt(scope):
    from openarm_bimanual.hint import validate_scope
    with pytest.raises(ValueError):validate_scope(scope,attempt_id='a')


def test_receiving_contact_does_not_authorize_wrong_donor():
    from openarm_bimanual.contact_policy import release_allowed
    h={'receiver_grasp_status':'completed','receiver':'right','receiver_contacts':{'openarm_right_left_finger':[1],'openarm_right_right_finger':[1]},'contact_steps':12}
    assert release_allowed(h,'left')
    assert not release_allowed(h,'right')
    h['contact_steps']=11
    assert not release_allowed(h,'left')


def fake_backend(mode='both'):
    from openarm_bimanual.backend import BimanualActionBackend
    calls=[]
    def transport(method,path,body,timeout):
        calls.append((method,path,body))
        if path=='/health':return {'ready':True,'robot':{'model':'OpenArm','execution_arm':mode},'capabilities':['mobile_base_joint_drive','independent_arms','independent_grippers','physical_handoff']}
        if path=='/state':return {'robot':{'model':'OpenArm','execution_arm':mode},'epoch':7}
        if path=='/plan':return {'robot':{'model':'OpenArm','execution_arm':mode},'arm':body['arm'],'epoch':7,'reachable':True,'collision_check':{'collision_free':True}}
        raise AssertionError(path)
    return BimanualActionBackend('http://127.0.0.1:45678',execution_arm=mode,transport=transport),calls


def test_unmanaged_bimanual_planning_remains_retired():
    from curobo_v2.identity import IdentityError
    backend,calls=fake_backend()
    with pytest.raises(IdentityError,match='V2-only'):
        backend.plan_limb('right',1.,2.,3.)
    assert not any(method=='POST' and path=='/plan' for method,path,body in calls)


def test_single_only_rejects_other_hand_before_transport():
    backend,calls=fake_backend('left');before=len(calls)
    with pytest.raises(ValueError):backend.plan_limb('right',1.,2.,3.)
    assert len(calls)==before


def test_both_mode_rejects_ambiguous_legacy_command():
    from tidybot_sdk import CapabilityNotAvailableError
    backend,calls=fake_backend()
    with pytest.raises(CapabilityNotAvailableError):backend.move_arm_to_position(1.,2.,3.,tolerance=.004,max_steps=100)


@pytest.mark.parametrize('tolerance',[.005,float('nan'),True,0])
def test_dual_motion_preserves_four_millimeter_limit(tolerance):
    backend,calls=fake_backend();before=len(calls)
    with pytest.raises(ValueError):backend.move_limb('left',1.,2.,3.,tolerance=tolerance)
    assert len(calls)==before


def rest_state():
    result={'base':{'qvel':[0.,0.,0.]}}
    for side in ['left','right']:
        result[side]={'qvel':[0.]*7}
        result[side+'_gripper']={'driver_velocity_m_s':0.,'follower_velocity_m_s':0.}
    return result


def test_stop_requires_both_arms_independent_fingers_and_mobile_base():
    from openarm_bimanual.lifecycle import stopped_components
    state=rest_state();assert all(stopped_components(state).values())
    state['right']['qvel'][3]=.051
    assert stopped_components(state)=={'left':True,'right':False,'base':True}
    state=rest_state();state['left_gripper']['follower_velocity_m_s']=.006
    assert not stopped_components(state)['left']
    state=rest_state();state['base']['qvel'][2]=.006
    assert not stopped_components(state)['base']


@pytest.mark.parametrize('invalid',[None,[],[0.]*6,[float('nan')]*7,[True]*7])
def test_stop_cannot_confirm_missing_or_nonfinite_arm_velocities(invalid):
    from openarm_bimanual.lifecycle import stopped_components
    state=rest_state();state['left']['qvel']=invalid
    assert not stopped_components(state)['left']


def test_idle_target_preserves_actual_pose_inside_official_limits():
    from openarm_bimanual.hold import bounded_idle_pose
    import numpy as np
    current=np.array([.2,.3]);bounds=np.array([[-1,1],[0,.044]])
    current[1]=.03
    result=bounded_idle_pose(current,bounds,slack=np.array([.001,.0001]))
    assert np.array_equal(result,current)
    assert result is not current


def test_idle_target_clips_permitted_physics_slack_without_mutating_measured_state():
    from openarm_bimanual.hold import bounded_idle_pose
    import numpy as np
    current=np.array([1.570796132,-.00000001]);bounds=np.array([[-1.570796013,1.570796013],[0,.044]])
    result=bounded_idle_pose(current,bounds,slack=np.array([.001,.0001]))
    assert np.array_equal(result,[bounds[0,1],0.])
    assert current[0]>bounds[0,1] and current[1]<0.


@pytest.mark.parametrize('current',[[1.002,.02],[0.,.0442],[float('nan'),.02]])
def test_idle_pose_outside_frozen_physics_slack_fails_closed(current):
    from openarm_bimanual.hold import bounded_idle_pose
    import numpy as np
    with pytest.raises(ValueError):bounded_idle_pose(np.array(current),np.array([[-1,1],[0,.044]]),slack=np.array([.001,.0001]))


def hold_fixture():
    import numpy as np
    names=[f'openarm_{s}_joint{i}' for s in ['left','right'] for i in range(1,8)]
    names += [f'openarm_{s}_finger_joint{i}' for s in ['left','right'] for i in [1,2]]
    return np.zeros(len(names)),names


def test_idle_guard_detects_uncommanded_arm_motion_but_allows_participating_arm():
    from openarm_bimanual.hold import idle_hold_metrics
    q,names=hold_fixture();reference=q.copy();q[0]=.2;q[7]=.011
    metrics=idle_hold_metrics(q,reference,names,{'left':[0]*7})
    assert metrics['violation']=='idle_arm_drift'
    assert metrics['arm']['right']==pytest.approx(.011)
    assert 'left' not in metrics['arm']


def test_arm_move_still_preserves_both_grippers():
    from openarm_bimanual.hold import idle_hold_metrics
    q,names=hold_fixture();reference=q.copy();q[-1]=.00051
    metrics=idle_hold_metrics(q,reference,names,{'left':[0]*7})
    assert metrics['violation']=='idle_gripper_drift'


def test_independent_gripper_command_preserves_its_arm_and_the_other_gripper():
    from openarm_bimanual.hold import idle_hold_metrics
    q,names=hold_fixture();reference=q.copy();q[0]=.011;q[14]=.044
    metrics=idle_hold_metrics(q,reference,names,{'left_gripper':.044})
    assert metrics['violation']=='idle_arm_drift'
    assert set(metrics['arm'])=={'left','right'}
    assert set(metrics['gripper'])=={'right'}


@pytest.mark.parametrize('status',['failed','running','stopped',None])
def test_handoff_cannot_release_after_receiver_action_failed_or_is_unfinished(status):
    from openarm_bimanual.contact_policy import release_allowed
    proof={f'openarm_right_{f}_finger':[1] for f in ['left','right']}
    assert not release_allowed({'receiver':'right','receiver_contacts':proof,'contact_steps':12,'receiver_grasp_status':status},'left')


def test_pair_mean_contact_hold_preserves_physical_jaw_closure_and_one_millimeter_limit():
    from openarm_bimanual.contact_policy import contact_hold_goal
    goal=contact_hold_goal(.0248,.0258,inset=.0004,free_limit=.006,reference='pair_mean')
    assert goal==pytest.approx(.0249)
    assert 2*goal==pytest.approx(.0248+.0258-2*.0004)
    assert max(abs(.0248-goal),abs(.0258-goal))<.001


def test_unknown_contact_hold_reference_is_rejected():
    from openarm_bimanual.contact_policy import contact_hold_goal
    with pytest.raises(ValueError):contact_hold_goal(.0248,.0258,inset=.0004,free_limit=.006,reference='unknown')


def test_idle_arm_lock_does_not_recapture_drift_at_each_other_hand_action():
    from openarm_bimanual.hold import idle_arm_goal
    q,names=hold_fixture();q[0]=.2
    goal,holds=idle_arm_goal(q.copy(),q,names,{'right':[0]*7},{})
    q[0]+=.003
    next_goal,_=idle_arm_goal(goal,q,names,{'right':[0]*7},holds)
    assert next_goal[0]==pytest.approx(.2)
    assert q[0]==pytest.approx(.203)


def test_reactivated_arm_clears_lock_before_next_actual_idle_pose_capture():
    from openarm_bimanual.hold import idle_arm_goal
    q,names=hold_fixture();q[0]=.2
    goal,holds=idle_arm_goal(q.copy(),q,names,{'right':[0]*7},{})
    goal,holds=idle_arm_goal(goal,q,names,{'left':[0]*7},holds)
    assert 'left' not in holds
    q[0]=-.3
    goal,holds=idle_arm_goal(goal,q,names,{'right':[0]*7},holds)
    assert goal[0]==pytest.approx(-.3)


def test_gripper_action_preserves_completed_joint_target_and_existing_idle_lock():
    from openarm_bimanual.hold import idle_arm_goal
    q,names=hold_fixture();q[0]=.199;goal=q.copy();goal[0]=.2
    result,holds=idle_arm_goal(goal,q,names,{'left_gripper':.04},{})
    assert result[0]==pytest.approx(.2)
    q[0]=.202
    result,_=idle_arm_goal(result,q,names,{'left_gripper':.02},holds)
    assert result[0]==pytest.approx(.2)


def test_target_contact_report_distinguishes_unilateral_contact_and_wrong_object():
    from openarm_bimanual.contact_policy import target_finger_contacts
    from types import SimpleNamespace as S
    import numpy as np
    finger=S(name='openarm_right_left_finger',per_scene_id=2)
    target=S(name='obj',per_scene_id=3)
    point=S(position=np.zeros(3),normal=np.array([1.,0,0]),separation=-.0001,impulse=np.array([.001,0,0]))
    scene=S(get_contacts=lambda:[S(bodies=[S(entity=finger),S(entity=target)],points=[point])])
    report=target_finger_contacts(scene,side='right',target_id=3)
    assert set(report)=={'openarm_right_left_finger'}
    assert report['openarm_right_left_finger'][0]['impulse_Ns']==[.001,0,0]
    assert target_finger_contacts(scene,side='left',target_id=3)=={}
    assert target_finger_contacts(scene,side='right',target_id=4)=={}


def test_historical_control_samples_are_bound_to_exact_trace_identity():
    from openarm_bimanual.replay import validate_control_recording
    row={'schema':'attentionbench.bimanual-control-recording.v1','run_id':'r','attempt_id':'a','samples':[{'video_seconds':0.,'trace_seconds':1.,'control':{'step':10,'epoch':0,'arms':{s:{'active':False,'grasped':False,'action_id':None} for s in ['left','right']},'grippers':{s:{'driver_position_m':.02,'follower_position_m':.02} for s in ['left','right']},'handoff':None}}]}
    assert validate_control_recording(row,run_id='r',attempt_id='a')==row
    with pytest.raises(ValueError):validate_control_recording(row,run_id='r',attempt_id='other')
    row['samples'][0]['video_seconds']=float('nan')
    with pytest.raises(ValueError):validate_control_recording(row,run_id='r',attempt_id='a')


def test_planner_includes_right_carried_object_for_base_and_left_idle_motion():
    from openarm_bimanual.planning_scope import planning_sides
    assert planning_sides({'delta':[0,.1,0]},['right'])==['right']
    assert planning_sides({'left':[0]*7},['right'])==['left','right']
    assert planning_sides({'right':[0]*7},['left','right'])==['left','right']
    assert planning_sides({'left_gripper':.044},[])==['left']


def test_control_replay_http_enforces_advisor_hash_and_trace_identity(tmp_path):
    import json,hashlib,threading
    from dataclasses import replace
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from benchmarks.attention_harness.tests.test_attention_store import records
    from benchmarks.attention_harness.core.store import AttentionStore
    from benchmarks.attention_harness.ui_server import create_server
    episode=tmp_path/'episode';episode.mkdir()
    value={'schema':'attentionbench.bimanual-control-recording.v1','run_id':'run-1','attempt_id':'attempt-1','samples':[{'video_seconds':0.,'trace_seconds':1.,'control':{'step':10,'epoch':0,'arms':{s:{'active':False,'grasped':False,'action_id':None} for s in ['left','right']},'grippers':{s:{'driver_position_m':.02,'follower_position_m':.02} for s in ['left','right']},'handoff':None}}]}
    file=episode/'bimanual_control.json';file.write_text(json.dumps(value))
    evidence={'evidence_id':'controls','kind':'bimanual_control','uri':'artifact://episode/bimanual_control.json','sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'mime_type':'application/json','visibility':['advisor']}
    run,attempt,trace,_=records();store=AttentionStore(tmp_path/'store.sqlite3');store.create_run(run);store.create_attempt(attempt);store.put_trace(replace(trace,evidence=(evidence,)))
    server=create_server(store=store,port=0,artifact_root=tmp_path);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();url=f'http://127.0.0.1:{server.server_port}/api/traces/trace-1/evidence/controls'
    try:
        with urlopen(url) as response:assert json.load(response)==value
        file.write_text('tampered')
        with pytest.raises(HTTPError) as error:urlopen(url)
        assert error.value.code==409
    finally:server.shutdown();server.server_close();thread.join(2)


def test_release_tracks_real_grasp_transform_and_fails_on_contact_or_object_loss():
    import numpy as np
    from openarm_bimanual.contact_policy import release_hold_metrics
    origin=np.eye(4);current=origin.copy();current[2,3]=.0002
    assert not release_hold_metrics(origin,current,contact_age=2,unilateral=True,fingers=[.0245,.0250],goal=.0242)['lost']
    current[2,3]=.0041
    assert release_hold_metrics(origin,current,contact_age=0,unilateral=True,fingers=[.0245,.0250],goal=.0242)['lost']
    assert release_hold_metrics(origin,origin,contact_age=12,unilateral=True,fingers=[.0245,.0250],goal=.0242)['lost']
    assert release_hold_metrics(origin,origin,contact_age=0,unilateral=False,fingers=[.0245,.0250],goal=.0242)['lost']
    assert release_hold_metrics(origin,origin,contact_age=0,unilateral=True,fingers=[.0245,.0253],goal=.0242)['lost']


def test_begin_handoff_retries_read_only_contact_rejection_without_changing_epoch():
    from benchmarks.attention_harness.tests.test_openarm_grasp_v2 import handoff_backend,SCHEMA
    b,state,_,_,_,_=handoff_backend()
    state['handoff']=state['grasp_control']['handoff']=None
    b._state=lambda:state
    requests=[]
    def call(method,path,body):
        requests.append(body)
        if len(requests)==1:raise RuntimeError('OpenArm HTTP 409: donor_not_grasped')
        h={'schema':SCHEMA,'phase':'receiving','id':'7:h','donor':'left','receiver':'right'}
        state['handoff']=state['grasp_control']['handoff']=h
        return h
    b._call=call
    assert b.handoff('left','right',begin=True)['phase']=='receiving'
    assert requests==[{'donor':'left','receiver':'right','epoch':7}]*2


def test_bimanual_hint_cannot_change_the_other_hand_or_phase():
    from openarm_bimanual.hint import compile_hint
    left={'arm':'left','phase':'pickup','attempt_id':'a'}
    right={'arm':'right','phase':'handoff','attempt_id':'a'}
    assert compile_hint('AB_CONTROL_V1 grasp_offset_m=0.005;',left)['parameters']=={'grasp_offset_m':.005}
    assert compile_hint('AB_CONTROL_V1 grasp_offset_m=0.005;',right)['status']=='unsupported'
    assert compile_hint('AB_CONTROL_V1 receiving_offset_m=0.030;',right)['parameters']=={'receiving_offset_m':.030}
    assert compile_hint('AB_CONTROL_V1 receiving_offset_m=0.030;',left)['status']=='unsupported'
    assert compile_hint('AB_CONTROL_V1 receiving_offset_m=0.1;',right)['status']=='unrecognized'
    assert compile_hint('AB_CONTROL_V1 grasp_offset_m=0.005;',None)['status']=='unsupported'


def test_planning_uses_only_epoch_bound_physical_hold_certificate_when_impulses_flicker():
    from openarm_bimanual.planning_scope import physical_holders
    contacts={'left':{'proof':None,'hold_valid':False},'right':{'proof':None,'hold_valid':True}}
    receipt={'right':{'schema':'openarm.grasp/2','epoch':7,'status':'completed','contact_hold_committed':True,'holding':True}}
    assert physical_holders(contacts,{'right':.024},receipt,7)==['right']
    assert physical_holders(contacts,{'right':.024},receipt,8)==[]
    assert physical_holders(contacts,{},receipt,7)==[]
    contacts['right']['hold_valid']=False
    assert physical_holders(contacts,{'right':.024},receipt,7)==[]


def test_started_phase_is_not_reported_as_completed():
    from openarm_bimanual.phases import PhaseLedger
    event={'timestamp':5.,'duration_ms':100.,'source':'robot_sdk.bimanual','operation':'handoff',
           'status':'failed','result':{},'error':{'message':'Contact lost'}}
    before,after=PhaseLedger().consume(event)
    assert before[0]['event_type']=='skill.started' and before[0]['status']=='started'
    assert after[0]['event_type']=='skill.blocked' and after[0]['status']=='failed'


def test_daily_entry_selects_only_its_hashed_catalog_and_keeps_legacy_read_only(tmp_path):
    import json,hashlib
    from openarm_bimanual.entry import resolve_daily_catalog
    legacy=tmp_path/'benchmarks/attention_harness/protocol/v2/skill_graphs/live_run_catalog.json'
    legacy.parent.mkdir(parents=True);legacy.write_text('{"legacy":true}')
    assert resolve_daily_catalog(tmp_path)==legacy.resolve()
    directory=tmp_path/'openarm_bimanual';catalog=directory/'launch_configs/daily-v1/catalog.json'
    catalog.parent.mkdir(parents=True);catalog.write_text('{"profiles":["both"]}')
    pointer=directory/'current.json';pointer.write_text(json.dumps({'catalog':'launch_configs/daily-v1/catalog.json','sha256':hashlib.sha256(catalog.read_bytes()).hexdigest()}))
    assert resolve_daily_catalog(tmp_path)==catalog.resolve()
    assert legacy.read_text()=='{"legacy":true}'
    catalog.write_text('{"profiles":["tampered"]}')
    with pytest.raises(ValueError,match='identity'):resolve_daily_catalog(tmp_path)
    assert resolve_daily_catalog(tmp_path,override=str(legacy))==legacy.resolve()


def test_current_bilateral_contact_refreshes_age_without_resetting_grasp_geometry():
    import numpy as np
    from openarm_bimanual.contact_policy import certified_hold_metrics
    reference=np.eye(4);current=reference.copy()
    current[0,3]=.0002
    certificate={'relative':reference,'step':1}
    params={'unilateral':True,'fingers':[.0244,.0250],'goal':.0242}
    assert certified_hold_metrics(certificate,current,step=20000,bilateral=False,**params)['lost']
    assert not certified_hold_metrics(certificate,current,step=20000,bilateral=True,**params)['lost']
    current[0,3]=.0041
    assert certified_hold_metrics(certificate,current,step=20000,bilateral=True,**params)['lost']
    assert certificate['step']==1 and np.array_equal(certificate['relative'],reference)


def test_four_recorded_views_keep_the_original_storage_limit_independently():
    from openarm_bimanual.recording_budget import enforce_storage_budget, VIEW_LIMIT_BYTES
    sizes={name:550_000_000 for name in ['chest','external','left_wrist','right_wrist']}
    enforce_storage_budget(sizes)
    sizes['left_wrist']=VIEW_LIMIT_BYTES+1
    with pytest.raises(RuntimeError,match='storage limit'):enforce_storage_budget(sizes)


def test_failed_display_recording_explicitly_reports_truncation(tmp_path):
    from openarm_bimanual.recording import MultiViewRecording
    episode=tmp_path/'episode';episode.mkdir()
    recorder=MultiViewRecording(episode,tmp_path/'missing-service',run_id='run:test',attempt_id='attempt:test')
    recorder.finalize()
    import json
    receipt=json.loads((episode/'camera_recording.json').read_text())
    assert receipt['error'] and receipt['truncated'] is True


def test_final_public_control_requires_measured_rest_and_keeps_task_phase(tmp_path):
    import json
    from openarm_bimanual.control_recording import publish_stopped_control
    state={'epoch':7,'total_steps':812,'active_action':None,'task_phase':'placement','handoff':{'phase':'completed'},
        'arms':{s:{'active':False,'grasped':False,'holding':False,'action_id':None} for s in ['left','right']},'base':{'qvel':[0.,0.,0.]}}
    for s in ['left','right']:
        state[s]={'qvel':[0.]*7}
        state[s+'_gripper']={'driver_position_m':.044,'follower_position_m':.044,'driver_velocity_m_s':0.,'follower_velocity_m_s':0.}
    receipt={'stop_barrier':{'physical_rest_confirmed':True,'stopped_state':state}}
    assert publish_stopped_control(tmp_path,'run:test','attempt:test:0',receipt)
    file=tmp_path/'public_control-0.json';saved=file.read_bytes();row=json.loads(saved)
    assert row['control']['task_phase']=='placement' and row['control']['step']==812 and row['control']['epoch']==7
    assert row['source']=='measured_stop_receipt' and not any(a['active'] for a in row['control']['arms'].values())
    state['right']['qvel'][0]=.051
    assert not publish_stopped_control(tmp_path,'run:test','attempt:test:0',receipt)
    assert file.read_bytes()==saved
