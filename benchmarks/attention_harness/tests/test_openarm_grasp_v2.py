"""Grasp-v2 contract/negative controls; synthetic evidence is never real handoff."""
import copy
import math
import numpy as np
import pytest
from openarm_bimanual.grasp import GraspCoordinator, GraspError, Limits, Feedback, SCHEMA
from openarm_bimanual.grasp_feedback import normal_force


def coordinator():
    stops=[]; rows=[]
    c=GraspCoordinator({s:Limits(0,.044,333,-.00552238) for s in ('left','right')},
                       epoch=7,target_id=42,stop_callback=stops.append,emit=rows.append)
    return c,stops,rows


def request(side='left', mode='acquire', rid='one', **kwargs):
    b=dict(schema=SCHEMA,epoch=7,arm=side,mode=mode,request_id=rid,
           finger_displacement_m=.006 if mode=='acquire' else .044,drive_force_limit_n=20.)
    if mode=='acquire':b.update(target_id=42,desired_contact_force_n=2.)
    b.update(kwargs);return b


def sample(c,step, *, side='left',contacts=True,q=.02,speed=0.,epoch=7,pose=None,identity=True,force=None):
    active={s:c.fingers[s].mode in {'acquire','hold'} for s in ('left','right')}
    result={}
    for s in ('left','right'):
        contact=active[s] and (contacts if s==side else True)
        pos=q if s==side else .02
        if c.fingers[s].mode=='position':pos=c.fingers[s].goal_m
        forces=force if s==side and force is not None else [2.,2.] if contact else [0.,0.]
        result[s]=Feedback(step,epoch,[pos]*2,[speed if s==side else 0.]*2,[pos]*2,42,contact,forces,
                           (np.eye(4) if pose is None or s!=side else pose).tolist(),identity_valid=identity if s==side else True)
    return result


def advance(c,start,end,**kwargs):
    for step in range(start,end+1):c.observe(sample(c,step,**kwargs))


def test_units_are_geometry_derived_and_force_cap_is_separate():
    c,_,_=coordinator();l=c.limits['left']
    b=request();b.pop('finger_displacement_m');b['jaw_gap_m']=l.gap([.02,.02])
    assert c.parse(b)==pytest.approx((.02,20,2))
    b['finger_displacement_m']=.02
    with pytest.raises(GraspError,match='OR'):c.parse(b)
    for cap in (334,0,-1,math.nan,True):
        with pytest.raises(GraspError):c.parse(request(drive_force_limit_n=cap))
    with pytest.raises(GraspError):c.parse(request(desired_contact_force_n=11))


@pytest.mark.parametrize('body',[{},request(schema='old'),request(epoch=True),request(epoch=6),request(arm='other'),request(target_id=77),request(finger_displacement_m=.05),request(extra=1)])
def test_bad_request_never_changes_targets(body):
    c,stops,_=coordinator();before=c.snapshot()
    with pytest.raises(GraspError):c.command(body,step=0)
    assert c.snapshot()==before and not stops and not c.targets()


def test_returned_action_is_not_hold_lifetime_and_duplicate_does_not_ratchet():
    c,stops,_=coordinator();a=c.command(request(),step=0)
    assert a['status']=='running' and not a['grasped']
    advance(c,1,720)
    assert c.actions[a['id']]['status']=='completed' and c.fingers['left'].holding
    goal=c.targets()['left']
    assert c.command(request(),step=720)['id']==a['id']
    assert c.targets()['left']==goal
    c.command(request(rid='again',finger_displacement_m=.001),step=720)
    advance(c,721,1440)
    assert c.fingers['left'].holding and c.targets()['left']==goal and not stops
    advance(c,1441,1453)
    assert c.fingers['left'].holding


@pytest.mark.parametrize('case',['empty','stop','single','force_without_contact','fast','wrong_object','invalid','stale_sample','identity'])
def test_negative_feedback_never_certifies_or_permits_handoff(case):
    c,stops,_=coordinator();a=c.command(request(),step=0)
    for step in range(1,721):
        x=sample(c,step,contacts=case not in {'empty','stop','force_without_contact'},
                 q=0 if case=='stop' else .02,speed=.006 if case=='fast' else 0,
                 identity=case!='identity',force=[2,0] if case=='single' else [20,20] if case=='force_without_contact' else None)
        if case=='wrong_object':x['left'].target_id=99
        if case=='invalid':x['left'].v_m_s=[float('nan'),0]
        if case=='stale_sample':x['left'].epoch=6
        c.observe(x)
        if c.suspended:break
    assert not c.fingers['left'].holding and c.ownership is None and stops
    assert c.actions[a['id']]['status']=='failed'
    with pytest.raises(GraspError):c.begin('left','right',epoch=c.epoch,step=721)


@pytest.mark.parametrize('donor,receiver',[('left','right'),('right','left')])
def test_all_handoff_stages_require_common_and_independent_evidence(donor,receiver):
    c,stops,rows=coordinator();c.command(request(side=donor),step=0);advance(c,1,720)
    c.begin(donor,receiver,epoch=7,step=720)
    with pytest.raises(GraspError):c.release(donor,receiver,epoch=7,step=720,request_id='early')
    with pytest.raises(GraspError):c.command(request(donor,mode='position',rid='open'),step=720)
    c.command(request(receiver,rid='receive'),step=720);advance(c,721,1440)
    assert c.handoff['phase']=='shared' and c.ownership==donor
    a=c.release(donor,receiver,epoch=7,step=1440,request_id='release')
    assert c.handoff['phase']=='releasing' and c.ownership==donor
    advance(c,1441,2160)
    assert c.actions[a['id']]['status']=='completed'
    assert c.handoff['phase']=='receiver_independent' and c.ownership==donor
    advance(c,2161,2171)
    assert c.handoff['phase']=='completed' and c.ownership==receiver
    assert not c.fingers[donor].holding and c.fingers[receiver].holding and not stops
    events=[r['event'] for r in rows]
    for name in ('handoff_begin','handoff_shared','handoff_release','handoff_receiver_independent','handoff_completed'):assert name in events


@pytest.mark.parametrize('problem',['contact_lost','slip','feedback','clock','identity'])
def test_persistent_hold_fault_stops_even_after_action_return(problem):
    c,stops,_=coordinator();c.command(request(),step=0);advance(c,1,720)
    pose=np.eye(4);pose[0,3]=.0041 if problem=='slip' else 0
    x=sample(c,721,contacts=problem!='contact_lost',pose=pose,identity=problem!='identity')
    if problem=='feedback':x['left'].contact_force_n=[float('nan')]*2
    if problem=='clock':x['right'].step=722
    c.observe(x)
    assert stops and c.suspended and c.epoch==8 and not c.targets()
    assert c.ownership is None and not c.fingers['left'].holding


@pytest.mark.parametrize('reason,status',[('cancel','cancelled'),('stop','stopped'),('runtime_identity_failed','failed')])
def test_barrier_cancels_action_invalidates_epoch_and_leaves_no_controller(reason,status):
    c,stops,_=coordinator();a=c.command(request(),step=0);advance(c,1,20)
    c.abort(reason,step=20,status=status)
    assert c.actions[a['id']]['status']==status and c.epoch==8 and stops==[reason]
    assert c.targets()=={}
    c.abort(reason,step=21);assert stops==[reason]
    with pytest.raises(GraspError):c.command(request(),step=21)


def test_duplicate_conflict_does_not_change_live_action_or_other_gripper():
    c,_,_=coordinator();a=c.command(request(),step=0);before=c.snapshot()
    with pytest.raises(GraspError):c.command(request(finger_displacement_m=.007),step=1)
    assert c.snapshot()==before and c.fingers['right'].mode=='idle'


def test_force_projection_excludes_tangent_and_rejects_bad_normal():
    p=dict(normal=[0,0,1],impulse_Ns=[12,0,.01])
    assert normal_force([p],.005)==pytest.approx(2)
    with pytest.raises(ValueError):normal_force([dict(p,normal=[0,0,0])],.005)


def test_bare_contacts_never_become_v2_planning_holder():
    from openarm_bimanual.planning_scope import physical_holders
    contacts={s:dict(proof={'fake':[1]},hold_valid=False) for s in ('left','right')}
    assert physical_holders(contacts,{}, {},7)==[]


def test_old_gripper_action_path_rejected_before_native_dispatch():
    from openarm_bimanual.runtime import Runtime, foundation
    rt=Runtime.__new__(Runtime);rt.execution_arm='both'
    with pytest.raises(foundation.APIError,match='openarm.grasp/2'):
        rt.validate_action({'left_gripper':.02,'epoch':0},0)


def runtime_shell():
    from types import SimpleNamespace
    from openarm_bimanual.runtime import Runtime
    rt=Runtime.__new__(Runtime);rt.execution_arm='both';rt.epoch=7;rt.total_steps=0
    rt.active=None;rt.actions={};rt.guard_fault=None;rt.kitchen=SimpleNamespace(carrying_sides=[])
    rt.log=lambda *args,**kwargs:None;rt.commit_hold=lambda:None
    c,_,_=coordinator();c.stop_callback=rt.grasp_stop;rt.grasp_control=c
    rt.state=lambda:dict(epoch=rt.epoch,grasp_control=c.snapshot())
    return rt,c


def test_runtime_stop_commits_exactly_one_epoch(monkeypatch):
    from openarm_bimanual.runtime import Runtime,foundation
    rt,c=runtime_shell();a=c.command(request(),step=0);rt.actions=c.actions;rt.active=a['id']
    def native_barrier(self,req):
        self.active=None;self.epoch+=1;req.response=(200,'application/json',self.state());req.event.set()
    monkeypatch.setattr(foundation.Runtime,'execute',native_barrier)
    req=foundation.Request('POST','/stop',{},7);Runtime.execute(rt,req)
    assert rt.epoch==c.epoch==8 and c.suspended
    assert c.actions[a['id']]['status']=='stopped'
    assert req.response[2]['epoch']==8


def test_runtime_cancel_keeps_cancelled_status_and_epoch():
    from openarm_bimanual.runtime import Runtime,foundation
    rt,c=runtime_shell();a=c.command(request(),step=0);rt.actions=c.actions;rt.active=a['id']
    req=foundation.Request('POST','/cancel',dict(epoch=7,action_id=a['id']),7)
    Runtime.execute(rt,req)
    assert req.response[0]==200 and rt.epoch==c.epoch==8
    assert c.actions[a['id']]['status']=='cancelled' and rt.active is None


def test_missing_authenticated_identity_stops_before_grasp_dispatch():
    from openarm_bimanual.runtime import Runtime,foundation
    rt,c=runtime_shell();req=foundation.Request('POST','/grasp',request(),7)
    Runtime.execute(rt,req)
    assert req.response[0]==503 and req.response[2].code=='runtime_identity_failed'
    assert not c.actions and rt.epoch==c.epoch==8


def test_sdk_emits_v2_units_and_waits_for_contact_certificate():
    b,_,_,_,calls=grasp_backend()
    assert b.grasp('right',jaw_gap_m=.03447762,request_id='sdk')['holding'] is True
    assert calls[0][2]['jaw_gap_m']==.03447762 and 'finger_displacement_m' not in calls[0][2]
    assert calls[0][2]['desired_contact_force_n']==2 and calls[0][2]['drive_force_limit_n']==20


def test_v2_phase_ledger_requires_independent_ownership_receipt():
    from openarm_bimanual.phases import PhaseLedger
    ledger=PhaseLedger()
    ledger.consume(dict(source='robot_sdk.bimanual',operation='begin_handoff',status='completed',result=dict(phase='receiving',donor='right',receiver='left',schema=SCHEMA)))
    event=dict(source='robot_sdk.bimanual',operation='handoff',status='completed',result=dict(schema=SCHEMA,status='completed',handoff=True,ownership='right'))
    ledger.consume(event);assert not ledger.transferred
    event['result'].update(handoff_completed=True,ownership='left')
    ledger.consume(event);assert ledger.transferred


def test_full_group_convergence_is_part_of_action_gate_before_success_event():
    c,stops,events=coordinator();a=c.command(request(mode='position'),step=0)
    for step in range(1,721):
        x=sample(c,step)
        x['left'].full_group_completion=False
        c.observe(x)
    result=c.actions[a['id']]
    assert result['status']=='failed' and result['legacy_position_completion'] is True
    assert result['legacy_full_group_completion'] is False
    assert stops==['convergence_timeout']
    assert not any(e.get('action',{}).get('status')=='completed' for e in events)


def test_failed_feedback_frame_still_counts_executed_step():
    c,stops,_=coordinator();a=c.command(request(mode='position'),step=0);advance(c,1,29)
    x=sample(c,30);x['left'].valid=False;c.observe(x)
    assert c.actions[a['id']]['physical_steps']==30


def test_hold_certificate_does_not_fabricate_completed_action_receipt():
    rt,c=runtime_shell();a=c.command(request(),step=0);advance(c,1,20)
    assert c.fingers['left'].holding and c.actions[a['id']]['status']=='running'
    rt.sync_grasp_state()
    assert rt.grasp_receipts['left']['holding'] is True
    assert rt.grasp_receipts['left']['status']=='running'


def test_release_needs_current_stability_not_only_earlier_certificate():
    c,_,_=coordinator();c.command(request(),step=0);advance(c,1,720)
    c.begin('left','right',epoch=7,step=720);c.command(request('right',rid='receive'),step=720);advance(c,721,1440)
    assert c.handoff['phase']=='shared'
    c.fingers['right'].stable_confirmed=False
    with pytest.raises(GraspError):c.release('left','right',epoch=7,step=1440,request_id='release')
    assert c.fingers['left'].holding and c.handoff['phase']=='shared'


def shared_grip(c, *, start=0):
    c.command(request(rid='donor'),step=start);advance(c,start+1,start+720)
    c.begin('left','right',epoch=7,step=start+720)
    c.command(request('right',rid='receiver'),step=start+720);advance(c,start+721,start+1440)


def test_historical_open_request_id_cannot_become_handoff_release():
    c,_,_=coordinator();c.command(request(mode='position',rid='old-open'),step=0);advance(c,1,720)
    shared_grip(c,start=720);before=c.snapshot();actions=copy.deepcopy(c.actions)
    with pytest.raises(GraspError) as exc:
        c.release('left','right',epoch=7,step=2160,request_id='old-open')
    assert exc.value.code=='duplicate_conflict'
    assert c.snapshot()==before and c.actions==actions and c.fingers['left'].holding


@pytest.mark.parametrize('last_step',[1460,2160,2171])
def test_release_retry_is_read_only_for_current_handoff(last_step):
    c,_,_=coordinator();shared_grip(c)
    first=c.release('left','right',epoch=7,step=1440,request_id='release')
    advance(c,1441,last_step);before=c.snapshot();actions=copy.deepcopy(c.actions)
    replay=c.release('left','right',epoch=7,step=last_step,request_id='release')
    assert replay==c.actions[first['id']] and c.actions==actions and c.snapshot()==before


@pytest.mark.parametrize('last_step',[1460,2171])
def test_runtime_release_retry_does_not_rebind_or_restart_action(last_step):
    from openarm_bimanual.runtime import Runtime,foundation
    c,_,_=coordinator();shared_grip(c)
    first=c.release('left','right',epoch=7,step=1440,request_id='release')
    advance(c,1441,last_step)
    rt=Runtime.__new__(Runtime);rt.execution_arm='both';rt.epoch=7;rt.total_steps=last_step
    rt.active=first['id'] if last_step<2160 else None;rt.grasp_control=c;rt.actions=c.actions
    rt.grasp_identity_valid=lambda:True;rt.log=lambda *args,**kwargs:None
    before=copy.deepcopy(c.actions);active=rt.active
    req=foundation.Request('POST','/handoff',dict(epoch=7,donor='left',receiver='right',request_id='release'),7)
    Runtime.execute(rt,req)
    assert req.response[0]==(202 if last_step<2160 else 200)
    assert req.response[2]==c.actions[first['id']] and rt.active==active and c.actions==before


def test_handoff_begin_needs_current_donor_stability():
    c,_,_=coordinator();c.command(request(),step=0);advance(c,1,720)
    advance(c,721,721,q=.0203)
    assert c.fingers['left'].holding and not c.fingers['left'].stable_confirmed
    with pytest.raises(GraspError) as exc:c.begin('left','right',epoch=7,step=721)
    assert exc.value.code=='donor_not_grasped' and c.handoff is None


def test_receiver_cannot_bypass_coordination_with_direct_open():
    c,_,_=coordinator();shared_grip(c);before=c.snapshot()
    with pytest.raises(GraspError) as exc:c.command(request('right',mode='position',rid='receiver-open'),step=1440)
    assert exc.value.code=='handoff_release_guard' and c.snapshot()==before


@pytest.mark.parametrize('reason,status',[('cancel','cancelled'),('stop','stopped')])
def test_terminal_event_and_receipt_agree_after_barrier(reason,status):
    c,_,events=coordinator();a=c.command(request(),step=0);advance(c,1,20)
    c.abort(reason,step=20,status=status)
    terminal=[e['action'] for e in events if e['event']=='grasp_action_finished']
    assert len(terminal)==1 and terminal[0]==c.actions[a['id']]
    assert terminal[0]['status']==status


def test_release_dispatch_receipt_and_event_keep_donor_ownership():
    c,_,events=coordinator();shared_grip(c)
    a=c.release('left','right',epoch=7,step=1440,request_id='release')
    event=[e for e in events if e['event']=='grasp_request'][-1]
    for receipt in (a,c.actions[a['id']],event['response']):
        assert receipt['ownership']=='left' and not receipt['holding'] and not receipt['grasped']
    assert c.ownership=='left' and not c.fingers['left'].holding


def handoff_backend(donor='left',receiver='right'):
    """SDK protocol fixture only; not an authenticated native service."""
    from openarm_bimanual.backend import BimanualActionBackend
    from benchmarks.attention_harness.robocasa_native.robot_config import RobotSelection
    b=BimanualActionBackend.__new__(BimanualActionBackend);b.selection=RobotSelection('OpenArm','both')
    b._attested_health=dict(grasp_schema=SCHEMA,native_target_id=42,
        grasp_limits={s:vars(Limits(0,.044,333,-.00552238)) for s in ('left','right')})
    h=dict(id='7:handoff:720',schema=SCHEMA,donor=donor,receiver=receiver,phase='shared',common_steps=12,independent_steps=0)
    control=dict(schema=SCHEMA,epoch=7,fault=None,controller_suspended=False,ownership=donor,
        grippers={s:dict(grasped=True,holding=True,stable_confirmed=True,stable_steps=12,target_id=42,action_id='donor' if s==donor else 'receiver') for s in ('left','right')})
    initial=dict(epoch=7,handoff=copy.deepcopy(h),grasp_control=copy.deepcopy(control),ownership=donor)
    initial['grasp_control']['handoff']=initial['handoff']
    terminal=copy.deepcopy(initial);terminal.update(ownership=receiver)
    terminal['handoff'].update(phase='completed',release_action='release',ownership=receiver,independent_steps=12)
    terminal['grasp_control'].update(ownership=receiver)
    terminal['grasp_control']['grippers'][donor].update(grasped=False,holding=False,stable_confirmed=False,action_id='release')
    result=dict(id='release',schema=SCHEMA,epoch=7,status='completed',arm=donor,mode='position',handoff=True,coordination_group=h['id'])
    stops=[];calls=[];posted=[]
    b._state=lambda:copy.deepcopy(terminal if posted else initial)
    def call(method,path,body):
        calls.append((method,path,copy.deepcopy(body)));posted.append(body)
        a=copy.deepcopy(result)
        a['grasp_request']=dict(schema=SCHEMA,epoch=7,arm=donor,mode='position',request_id=body['request_id'],finger_displacement_m=.044,drive_force_limit_n=20.)
        return a
    b._call=call;b._wait_action=lambda a:a;b.stop=lambda:stops.append('stop') or {}
    return b,initial,terminal,result,stops,calls


@pytest.mark.parametrize('donor,receiver',[('left','right'),('right','left')])
def test_sdk_accepts_only_bound_independent_handoff(donor,receiver):
    b,_,_,_,stops,calls=handoff_backend(donor,receiver)
    a=b.handoff(donor,receiver)
    assert a['handoff_completed'] is True and a['ownership']==receiver and not stops
    assert len(calls)==1


@pytest.mark.parametrize('problem',['ownership','control_owner','handoff_owner','handoff_id','release_action','roles','schema','epoch','receiver_not_holding','receiver_unstable','wrong_target','donor_still_holding','short_independent_window','suspended'])
def test_sdk_rejects_inconsistent_completed_handoff_and_stops(problem):
    b,_,terminal,_,stops,_=handoff_backend()
    if problem=='ownership':terminal['ownership']='left'
    elif problem=='control_owner':terminal['grasp_control']['ownership']='left'
    elif problem=='handoff_owner':terminal['handoff']['ownership']='left'
    elif problem=='handoff_id':terminal['handoff']['id']='other'
    elif problem=='release_action':terminal['handoff']['release_action']='other'
    elif problem=='roles':terminal['handoff'].update(donor='right',receiver='left')
    elif problem=='schema':terminal['grasp_control']['schema']='old'
    elif problem=='epoch':terminal['grasp_control']['epoch']=8
    elif problem=='receiver_not_holding':terminal['grasp_control']['grippers']['right']['holding']=False
    elif problem=='receiver_unstable':terminal['grasp_control']['grippers']['right']['stable_confirmed']=False
    elif problem=='wrong_target':terminal['grasp_control']['grippers']['right']['target_id']=99
    elif problem=='donor_still_holding':terminal['grasp_control']['grippers']['left']['holding']=True
    elif problem=='short_independent_window':terminal['handoff']['independent_steps']=11
    else:terminal['grasp_control']['controller_suspended']=True
    with pytest.raises(RuntimeError):b.handoff('left','right')
    assert stops==['stop']


@pytest.mark.parametrize('problem',['id','schema','epoch','arm','group','status','request_id'])
def test_sdk_rejects_misbound_release_action_and_stops(problem):
    b,_,_,result,stops,_=handoff_backend()
    original=b._wait_action
    def wait(a):
        a=original(a)
        if problem=='id':a['id']='other'
        elif problem=='schema':a['schema']='old'
        elif problem=='epoch':a['epoch']=8
        elif problem=='arm':a['arm']='right'
        elif problem=='group':a['coordination_group']='other'
        elif problem=='status':a['status']='running'
        else:a['grasp_request']['request_id']='other'
        return a
    b._wait_action=wait
    with pytest.raises(RuntimeError):b.handoff('left','right')
    assert stops==['stop']


def test_unmanaged_handoff_transport_stays_retired():
    from openarm_bimanual.backend import BimanualActionBackend
    from curobo_v2.identity import IdentityError
    b=BimanualActionBackend.__new__(BimanualActionBackend);b.base_url='http://127.0.0.1:45678'
    calls=[];b._transport=lambda *args:calls.append(args)
    b.interrupt_check=None;b.timeout_seconds=10;b.deadline=None
    with pytest.raises(IdentityError,match='V2-only'):b._call('POST','/handoff',{})
    assert not calls


@pytest.mark.parametrize('problem',['grippers','receiver','donor'])
def test_sdk_malformed_holding_feedback_stops(problem):
    b,_,terminal,_,stops,_=handoff_backend();grippers=terminal['grasp_control']['grippers']
    if problem=='grippers':terminal['grasp_control']['grippers']=[]
    else:grippers['right' if problem=='receiver' else 'left']=[1]
    with pytest.raises(RuntimeError):b.handoff('left','right')
    assert stops==['stop']


def test_sdk_begin_receipt_id_must_match_current_handoff():
    b,initial,terminal,_,stops,_=handoff_backend();initial['handoff']=initial['grasp_control']['handoff']=None
    b._state=lambda:terminal
    terminal['handoff']['phase']='receiving'
    b._call=lambda *args:{k:v for k,v in terminal['handoff'].items() if k!='id'}
    with pytest.raises(RuntimeError):b.handoff('left','right',begin=True)
    assert stops==['stop']


@pytest.mark.parametrize('problem',['cancel','timeout','identity'])
def test_sdk_preserves_cancellation_and_timeout_types_and_stops(problem):
    from benchmarks.attention_harness.core.control import EmergencyInterrupt
    b,_,_,_,stops,_=handoff_backend()
    def wait(a):
        if problem=='cancel':b.stop();raise EmergencyInterrupt('operator cancelled')
        if problem=='timeout':raise TimeoutError('transport timeout')
        raise RuntimeError('runtime_identity_failed')
    b._wait_action=wait
    expected=EmergencyInterrupt if problem=='cancel' else TimeoutError if problem=='timeout' else RuntimeError
    with pytest.raises(expected):b.handoff('left','right')
    assert stops==['stop']


def test_sdk_handoff_request_identity_error_stops():
    b,_,_,_,stops,_=handoff_backend()
    def call(*args):raise RuntimeError('runtime_identity_failed')
    b._call=call
    with pytest.raises(RuntimeError):b.handoff('left','right')
    assert stops==['stop']


def grasp_backend():
    from openarm_bimanual.backend import BimanualActionBackend
    from benchmarks.attention_harness.robocasa_native.robot_config import RobotSelection
    b=BimanualActionBackend.__new__(BimanualActionBackend);b.selection=RobotSelection('OpenArm','both')
    b._attested_health=dict(grasp_schema=SCHEMA,native_target_id=42,gripper_free_close_m=.006,
        grasp_limits={s:vars(Limits(0,.044,333,-.00552238)) for s in ('left','right')})
    result={};calls=[];stops=[]
    state=dict(epoch=7,ownership=None,handoff=None,grasp_control=dict(schema=SCHEMA,epoch=7,ownership=None,handoff=None,
        controller_suspended=False,fault=None,grippers={s:dict(mode='idle',grasped=False,holding=False,stable_confirmed=False,stable_steps=0,target_id=None,action_id=None,goal_m=.044,drive_force_limit_n=0.,desired_contact_force_n=0.) for s in ('left','right')}))
    b._state=lambda:copy.deepcopy(state)
    def call(method,path,body):
        calls.append((method,path,copy.deepcopy(body)));side=body['arm'];acquire=body['mode']=='acquire'
        result.update(id='grasp',schema=SCHEMA,epoch=7,arm=side,status='completed',mode=body['mode'],requested=copy.deepcopy(body),grasped=acquire,holding=acquire,contact_hold_committed=acquire)
        f=state['grasp_control']['grippers'][side]
        f.update(mode='hold' if acquire else 'position',grasped=acquire,holding=acquire,stable_confirmed=acquire,stable_steps=12 if acquire else 0,
            target_id=42 if acquire else None,action_id='grasp',drive_force_limit_n=body['drive_force_limit_n'],desired_contact_force_n=body.get('desired_contact_force_n',0),
            goal_m=body.get('finger_displacement_m',Limits(0,.044,333,-.00552238).displacement(body.get('jaw_gap_m',0))))
        state['ownership']=state['grasp_control']['ownership']=side if acquire else None
        return copy.deepcopy(result)
    b._call=call;b._wait_action=lambda a:copy.deepcopy(result);b.stop=lambda:stops.append('stop') or {}
    return b,state,result,stops,calls


@pytest.mark.parametrize('side,mode',[('left','acquire'),('right','acquire'),('left','position'),('right','position')])
def test_sdk_grasp_requires_matching_receipt_and_current_control(side,mode):
    b,_,_,stops,_=grasp_backend()
    a=b.grasp(side,mode=mode,request_id='grasp-test')
    assert a['status']=='completed' and a['holding'] is (mode=='acquire') and not stops


@pytest.mark.parametrize('problem',['schema','side','epoch','id','request','uncertified','mode','lost_hold','unstable','target','cap','force','position_holding'])
def test_sdk_grasp_rejects_misbound_or_stale_certificates(problem):
    b,state,result,stops,_=grasp_backend();original=b._wait_action
    def wait(a):
        x=original(a);f=state['grasp_control']['grippers']['right']
        if problem=='schema':x['schema']='old'
        elif problem=='side':x['arm']='left'
        elif problem=='epoch':x['epoch']=8
        elif problem=='id':x['id']='other'
        elif problem=='request':x['requested']['request_id']='other'
        elif problem=='uncertified':x['holding']=False
        elif problem=='mode':x['mode']='position'
        elif problem=='lost_hold':f['holding']=False
        elif problem=='unstable':f['stable_confirmed']=False
        elif problem=='target':f['target_id']=99
        elif problem=='cap':f['drive_force_limit_n']=333.
        elif problem=='force':f['desired_contact_force_n']=333.
        else:f['holding']=True
        return x
    b._wait_action=wait
    with pytest.raises(RuntimeError):b.grasp('right',mode='position' if problem=='position_holding' else 'acquire',request_id='grasp-test')
    assert stops==['stop']


@pytest.mark.parametrize('operation',['grasp','handoff'])
def test_sdk_preserves_operator_cancel_during_final_state_read(operation):
    from benchmarks.attention_harness.core.control import EmergencyInterrupt
    if operation=='grasp':
        b,_,_,stops,_=grasp_backend();run=lambda:b.grasp('right',request_id='cancel-read')
    else:
        b,_,_,_,stops,_=handoff_backend();run=lambda:b.handoff('left','right')
    original=b._state;reads=[]
    def state():
        reads.append(1)
        if len(reads)>1:b.stop();raise EmergencyInterrupt('operator cancelled query')
        return original()
    b._state=state
    with pytest.raises(EmergencyInterrupt):run()
    assert stops==['stop']


@pytest.mark.parametrize('side',['left','right'])
def test_new_grasp_cannot_interrupt_independent_transfer_window(side):
    c,stops,_=coordinator();shared_grip(c)
    c.release('left','right',epoch=7,step=1440,request_id='release');advance(c,1441,2160)
    assert c.handoff['phase']=='receiver_independent'
    before=c.snapshot();actions=copy.deepcopy(c.actions)
    with pytest.raises(GraspError) as exc:c.command(request(side,rid='new-close'),step=2160)
    assert exc.value.code=='handoff_release_guard'
    assert c.snapshot()==before and c.actions==actions and not stops
    advance(c,2161,2171)
    assert c.handoff['phase']=='completed' and c.ownership=='right'


def test_component_begin_matches_runtime_action_busy_barrier():
    c,_,_=coordinator();c.command(request(),step=0);advance(c,1,720)
    c.command(request('right',rid='receiver'),step=720);before=c.snapshot()
    with pytest.raises(GraspError) as exc:c.begin('left','right',epoch=7,step=720)
    assert exc.value.code=='action_busy' and c.snapshot()==before


@pytest.mark.parametrize('field,value',[('valid','true'),('identity_valid',1),('bilateral',1),('epoch',True),('v_m_s',[False,False]),('contact_force_n',[True,True])])
def test_malformed_feedback_types_stop_before_coercion(field,value):
    c,stops,_=coordinator();c.epoch=1;c.command(request(epoch=1),step=0)
    x=sample(c,1,epoch=1);setattr(x['left'],field,value);c.observe(x)
    assert c.suspended and stops==['feedback_invalid'] and c.ownership is None and not c.targets()
