"""Frozen policy handover contracts; no simulator or human submissions."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import pytest
from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.core.models import RunStatus
from benchmarks.attention_harness.tests.test_attention_store import records
from benchmarks.attention_harness.attention_control import AttentionControl, run_identity

@pytest.fixture
def setup(tmp_path):
    store=AttentionStore(tmp_path/'store.sqlite3');run,*_=records()
    run=replace(run,policy_id='autonomous',robot_configuration={'robot':{'model':'OpenArm','execution_arm':'left'},'base':{'capability':'mobile_base_joint_drive'}})
    store.create_run(run);store.transition_run(run.run_id,RunStatus.RUNNING,event_key='started')
    c=AttentionControl(store)
    c.bind(run.run_id,{'schema_version':'attentionbench.attention-control.v1','binding_id':'engineering-v1','allowed_policies':['autonomous','reactive_help'],'source_sha256':'a'*64})
    return store,run,c

def test_stable_identity_and_collision_suffix(setup):
    s,r,c=setup
    a=run_identity(s,r.run_id);other=replace(r,run_id=r.run_id+'-other');s.create_run(other)
    assert a['display_name']==run_identity(s,r.run_id)['display_name']
    assert a['display_name']!=run_identity(s,other.run_id)['display_name']
    assert 'OpenArm' in a['display_name'] and 'Left' in a['display_name'] and 'Mobile' in a['display_name']
    assert a['started_at']==r.created_at

def test_rename_keeps_id_and_initial_policy(setup):
    s,r,c=setup;c.rename(r.run_id,'Operator named run',now=20)
    assert run_identity(s,r.run_id)['display_name']=='Operator named run'
    assert s.get_run(r.run_id)['policy_id']=='autonomous'
    with pytest.raises(ValueError):c.rename(r.run_id,' ',now=21)

def test_requested_pending_only_executor_boundary_activates(setup):
    s,r,c=setup
    x=c.request(r.run_id,'reactive_help',request_id='switch1',expected_generation=0,now=10)
    assert x['state']=='Pending' and c.snapshot(r.run_id)['active_policy']=='autonomous'
    assert c.boundary(r.run_id,boundary_id='sdk:1',kind='sdk_action_completed',now=12)['active_policy']=='reactive_help'
    snap=c.snapshot(r.run_id)
    assert [e['state'] for e in snap['events']]==['Requested','Pending','Active']
    assert snap['segments'][1]['effective_at']==12 and snap['segments'][1]['boundary_id']=='sdk:1'
    assert s.get_run(r.run_id)['policy_id']=='autonomous'

def test_idempotent_request_and_reconnect(setup):
    s,r,c=setup
    a=c.request(r.run_id,'reactive_help',request_id='x',expected_generation=0,now=10)
    assert c.request(r.run_id,'reactive_help',request_id='x',expected_generation=0,now=11)==a
    assert AttentionControl(AttentionStore(s.path)).snapshot(r.run_id)==c.snapshot(r.run_id)
    with pytest.raises(StateConflictError):c.request(r.run_id,'autonomous',request_id='x',expected_generation=0,now=11)

def test_concurrent_requests_serialize(setup):
    s,r,c=setup
    def request(i):return c.request(r.run_id,'reactive_help',request_id=str(i),expected_generation=0,now=10)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(request,range(2)))
    assert sorted(x['state'] for x in results)==['Pending','Rejected']

def test_stale_generation_rejected(setup):
    s,r,c=setup;c.request(r.run_id,'reactive_help',request_id='x',expected_generation=0,now=10)
    c.boundary(r.run_id,boundary_id='sdk:1',kind='sdk_action_completed',now=11)
    x=c.request(r.run_id,'autonomous',request_id='stale',expected_generation=0,now=12)
    assert x['state']=='Rejected' and 'generation' in x['reason']

def test_cancel_and_timeout_do_not_activate(setup):
    s,r,c=setup;c.request(r.run_id,'reactive_help',request_id='x',expected_generation=0,now=10)
    assert c.cancel(r.run_id,'x',now=11)['state']=='Cancelled'
    c.request(r.run_id,'reactive_help',request_id='y',expected_generation=0,now=12,timeout_seconds=1)
    assert c.boundary(r.run_id,boundary_id='sdk:1',kind='sdk_action_completed',now=14)['active_policy']=='autonomous'
    assert c.snapshot(r.run_id)['events'][-1]['state']=='Rejected'

def test_frozen_and_terminal_are_rejected(setup):
    s,r,c=setup;other=replace(r,run_id='frozen');s.create_run(other)
    x=c.request(other.run_id,'reactive_help',request_id='f',expected_generation=0,now=1)
    assert x['state']=='Rejected' and 'frozen' in x['reason']
    s.transition_run(r.run_id,RunStatus.COMPLETED,event_key='done')
    assert c.request(r.run_id,'reactive_help',request_id='t',expected_generation=0,now=1)['state']=='Rejected'

def test_pending_hint_blocks_handover(setup):
    s,r,c=setup
    c.request(r.run_id,'reactive_help',request_id='x',expected_generation=0,now=10)
    assert c.boundary(r.run_id,boundary_id='safe',kind='attention_decision',now=11,unresolved_hint=True)['active_policy']=='autonomous'
    assert c.snapshot(r.run_id)['request']['state']=='Pending'

def test_complete_path_generation_and_budget_preserved(setup):
    s,r,c=setup;before=s.get_run(r.run_id)
    for n,p in enumerate(['reactive_help','autonomous','reactive_help']):
        c.request(r.run_id,p,request_id=str(n),expected_generation=n,now=10+n)
        c.boundary(r.run_id,boundary_id=str(n),kind='sdk_action_completed',now=11+n)
    a=c.snapshot(r.run_id)
    assert [x['policy_id'] for x in a['segments']]==['autonomous','reactive_help','autonomous','reactive_help']
    assert a['generation']==3 and a['statistical_class']=='Mixed policy'
    assert c.policy_at(r.run_id,11.5)=='reactive_help'
    assert s.get_run(r.run_id)==before

def test_repeated_boundary_is_idempotent(setup):
    s,r,c=setup;c.request(r.run_id,'reactive_help',request_id='x',expected_generation=0,now=10)
    c.boundary(r.run_id,boundary_id='same',kind='sdk_action_completed',now=11)
    before=c.snapshot(r.run_id);c.boundary(r.run_id,boundary_id='same',kind='sdk_action_completed',now=12)
    assert c.snapshot(r.run_id)==before

def test_invalid_boundary_cannot_acknowledge(setup):
    s,r,c=setup;c.request(r.run_id,'reactive_help',request_id='x',expected_generation=0,now=10)
    with pytest.raises(ValueError):c.boundary(r.run_id,boundary_id='unsafe',kind='action_in_flight',now=11)
    assert c.snapshot(r.run_id)['active_policy']=='autonomous'

def test_binding_and_provenance_are_immutable(setup):
    s,r,c=setup
    with pytest.raises(StateConflictError):c.bind(r.run_id,{'binding_id':'other'})
    other=replace(r,run_id='copied');s.create_run(other);c.link(other.run_id,r.run_id,'retry',now=20)
    assert run_identity(s,other.run_id)['source_run_id']==r.run_id
    assert run_identity(s,other.run_id)['source_relation']=='retry'

def test_unknown_policy_is_logged_rejection(setup):
    s,r,c=setup;x=c.request(r.run_id,'missing',request_id='x',expected_generation=0,now=10)
    assert x['state']=='Rejected' and c.snapshot(r.run_id)['active_policy']=='autonomous'

def test_http_requests_cannot_acknowledge_and_snapshot_survives_reconnect(setup):
    import json,re,threading
    from urllib.request import Request,urlopen
    from urllib.error import HTTPError
    from benchmarks.attention_harness.ui_server import create_server
    s,r,c=setup;server=create_server(store=s,port=0)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(base+'/ui/') as response:token=re.search(r'data-control-token="([^"]+)"',response.read().decode()).group(1)
        def post(path,body,auth=token):
            with urlopen(Request(base+'/api/runs/'+r.run_id+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Attention-Token':auth})) as response:return json.load(response)
        with pytest.raises(HTTPError) as e:post('/attention-policy',{'policy_id':'reactive_help','request_id':'http','expected_generation':0},auth='wrong')
        assert e.value.code==403
        a=post('/attention-policy',{'policy_id':'reactive_help','request_id':'http','expected_generation':0})
        assert a['state']=='Pending' and c.snapshot(r.run_id)['active_policy']=='autonomous'
        assert post('/attention-policy',{'policy_id':'reactive_help','request_id':'http','expected_generation':0})==a
        with pytest.raises(HTTPError) as e:post('/attention-policy/ack',{'state':'Active'})
        assert e.value.code==405
        assert post('/attention-policy/cancel',{'request_id':'http'})['state']=='Cancelled'
        assert post('/display-name',{'display_name':'Stable HTTP identity'})['display_name']=='Stable HTTP identity'
        with urlopen(base+'/api/runs') as response:listed=json.load(response)
        assert listed['runs'][0]['display_name']=='Stable HTTP identity'
        assert listed['runs'][0]['attention_control']['initial_policy']=='autonomous'
    finally:server.shutdown();server.server_close();thread.join(timeout=2)

def test_completed_action_receipt_precedes_boundary_ack(setup):
    from benchmarks.attention_harness.attention_control import boundary_event_sink
    from types import SimpleNamespace
    s,r,c=setup;events=[]
    c.request(r.run_id,'reactive_help',request_id='safe',expected_generation=0,now=10)
    def ack(event):
        assert events[-1] is event
        c.boundary(r.run_id,boundary_id=event['event_id'],kind='sdk_action_completed',now=11)
    sink=boundary_event_sink(SimpleNamespace(attention_boundary_hook=ack),events)
    sink({'event_type':'sdk.arm_command','status':'running','event_id':'in-flight'})
    assert c.snapshot(r.run_id)['active_policy']=='autonomous'
    sink({'event_type':'sdk.arm_command','status':'failed','event_id':'error'})
    assert c.snapshot(r.run_id)['active_policy']=='autonomous'
    sink({'event_type':'sdk.arm_command','status':'completed','event_id':'safe'})
    assert c.snapshot(r.run_id)['active_policy']=='reactive_help'

def test_unassociated_answer_and_pending_hint_are_fenced(setup):
    from benchmarks.attention_harness.attention_modes import AssistanceMode,RequestState
    from benchmarks.attention_harness.core.models import AttentionResponseRecord
    s,r,c=setup;_,a,t,request=records();s.create_attempt(a);s.put_trace(t)
    s.create_request(replace(request,mode=AssistanceMode.LIVE_HUMAN_FIRST))
    assert c.unresolved_hint(r.run_id)
    s.transition_request(request.request_id,RequestState.CANCELLED,event_key='cancelled-hint')
    assert not c.unresolved_hint(r.run_id)

def test_same_policy_ack_does_not_create_false_switch(setup):
    s,r,c=setup;c.request(r.run_id,'autonomous',request_id='same',expected_generation=0,now=10)
    x=c.boundary(r.run_id,boundary_id='done',kind='sdk_action_completed',now=11)
    assert x['request']['state']=='Active' and x['switch_count']==0 and x['generation']==0

@pytest.mark.parametrize('module,class_name,schema',[
    ('openarm_mobile.runner','MobileOpenArmFormalSuiteRunner','attentionbench.robocasa-openarm-mobile-config.v1'),
    ('benchmarks.attention_harness.robocasa_native.openarm_runner','OpenArmFormalSuiteRunner','attentionbench.robocasa-openarm-formal-config.v1')])
def test_routed_openarm_runner_forwards_safe_boundary_hook(monkeypatch,module,class_name,schema):
    import importlib
    from pathlib import Path
    from types import SimpleNamespace
    from benchmarks.attention_harness.robocasa_native import formal_runner
    monkeypatch.setattr(formal_runner,'_config',lambda request:{'schema_version':schema})
    hook=lambda e:None
    def execute(self,request):
        assert getattr(self,'attention_boundary_hook',None) is hook
        return {'confirmed_callback':True}
    monkeypatch.setattr(getattr(importlib.import_module(module),class_name),'execute',execute)
    runner=formal_runner.RobocasaFormalSuiteRunner(sim_source_root=Path('.'),agent_source_root=Path('.'),task_source_root=Path('.'),sim_python=Path('python'),agent_python=Path('python'))
    runner.attention_boundary_hook=hook
    assert runner.execute(SimpleNamespace(validate=lambda:None,suite='robocasa',seed=101))['confirmed_callback']

def test_timeout_rejects_without_waiting_for_inflight_action(setup):
    s,r,c=setup;c.request(r.run_id,'reactive_help',request_id='timeout',expected_generation=0,now=10,timeout_seconds=1)
    c.expire(r.run_id,now=12)
    x=c.snapshot(r.run_id)
    assert x['request']['state']=='Rejected' and x['active_policy']=='autonomous'

def test_sandbox_cancelled_exit_keeps_operator_stop_reason():
    import threading
    from openarm_mobile.session import OwnedOpenArmService
    service=object.__new__(OwnedOpenArmService);service.cancel_event=threading.Event();service.cancel_event.set()
    reasons=[];service.close=lambda reason:reasons.append(reason)
    service.__exit__(None,None,None)
    assert reasons==['operator_cancel']

def test_active_policy_changes_the_real_scheduler_decision_without_new_run(tmp_path):
    import time
    from benchmarks.attention_harness.sim_gt_attention_run import run_robosuite_attention
    from test_robosuite_memory import FakeClient,_adapter
    from test_sim_gt_attention_run import advisor_reply
    store=AttentionStore(tmp_path/'attention.sqlite3');calls=[]
    def action_policy(sdk,context):
        calls.append(context)
        if len(calls)==1:
            run_id=next(e['entity_id'] for e in store.events() if e['event_type']=='run.created')
            AttentionControl(store).request(run_id,'reactive_help',request_id='during-attempt',expected_generation=0,now=time.time())
        if context['attention_input'].get('advisor_guidance'):sdk.gripper.close()
    result=run_robosuite_attention(task_id='cube_lift',seed=101,artifact_root=tmp_path/'runs',
        policy_id='autonomous',robot_policy=action_policy,adapter_factory=lambda task,camera:_adapter(task,camera,client=FakeClient()),
        store_path=store.path,max_attempts=3,assistance_credits=2,advisor_transport=advisor_reply,sleeper=lambda _:None,
        hypothesis='gripper never closed',attention_control_binding={'schema_version':'attentionbench.attention-control.v1','binding_id':'test-runtime','allowed_policies':['autonomous','reactive_help']})
    assert result['native_success'] is True
    assert result['decisions'][0]['effective_attention_policy']=='reactive_help'
    assert result['decisions'][0]['action']=='request' and len(result['requests'])==1
    assert len({a['attention_trace']['run_id'] for a in result['attempts']})==1
    run_id=result['attempts'][0]['attention_trace']['run_id']
    assert store.get_run(run_id)['policy_id']=='autonomous'
    assert AttentionControl(store).snapshot(run_id)['switch_count']==1

def test_stop_request_fences_pending_and_new_policy_switch(setup):
    s,r,c=setup
    c.request(r.run_id,'reactive_help',request_id='before-stop',expected_generation=0,now=10)
    s.request_interrupt(r.run_id,event_key='operator-stop',requested_at=11)
    result=c.boundary(r.run_id,boundary_id='sdk:stopped',kind='sdk_action_completed',now=12)
    assert result['active_policy']=='autonomous'
    assert result['request']['state']=='Rejected'
    assert 'stop' in result['request']['reason']
    assert c.request(r.run_id,'reactive_help',request_id='after-stop',expected_generation=0,now=13)['state']=='Rejected'

def test_engineering_binding_matches_config_action_policy_and_dag():
    from benchmarks.attention_harness.attention_control import validate_launch_binding
    binding={'config_sha256':'a','action_policy_sha256':'b','graph_sha256':'c'}
    profile={'approved_config_sha256':'a','approved_policy_sha256':'b','skill_graph_sha256':'c'}
    validate_launch_binding(binding,profile)
    for key in binding:
        with pytest.raises(ValueError):validate_launch_binding({**binding,key:'different'},profile)

def test_answered_hint_remains_owned_until_execution_association(setup):
    from benchmarks.attention_harness.attention_modes import AssistanceMode,RequestState
    from benchmarks.attention_harness.core.models import AttentionResponseRecord
    s,r,c=setup;_,a,t,request=records();s.create_attempt(a);s.put_trace(t)
    s.create_request(replace(request,mode=AssistanceMode.LIVE_HUMAN_FIRST))
    response=AttentionResponseRecord('response',request.request_id,'human','draft submitted by test',5)
    s.transition_request(request.request_id,RequestState.ANSWERED,response=response,event_key='answer')
    assert c.unresolved_hint(r.run_id)
    s.record_response_use(request.request_id,response_id='response',use='guidance')
    assert c.unresolved_hint(r.run_id)
    s.link_response_execution(request.request_id,execution_id='execution:owned-by-original-generation')
    assert not c.unresolved_hint(r.run_id)
