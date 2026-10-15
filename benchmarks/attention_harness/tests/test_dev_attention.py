"""Dev Attention state machine and real worker loop with isolated providers/runners."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.dev_attention import DevAttentionControl, ALLOWED
from benchmarks.attention_harness import dev_loop as dl
from benchmarks.attention_harness.parcc_client import ParccResponse
from benchmarks.attention_harness.robocasa365.config import EXPECTED

OWNER={'pid':123,'start_ticks':'fixture','boot_id':'fixture'}
FAIL={'execution_status':'failed','public_error':'gripper failed','action_trace':[{'operation':'close','source':'gripper','status':'failed','error':{'type':'RuntimeError','message':'gripper blocked'}}]}


def session(tmp_path):
    store=AttentionStore(tmp_path/'attention.sqlite3')
    cfg={**EXPECTED,'task_id':dl.TASK,'task_prompt':'pick object','asset_manifest_sha256':'a'*64}
    s=store.create_dev_session(locked={'config':cfg,'config_text':json.dumps(cfg),'resources':{k:{'path':str(tmp_path)} for k in ('service_source_root','sim_python','sim_source_root','task_source_root','asset_manifest')},'resource_sha256':'b'*64},budget={**dl.DEFAULT_BUDGET,'executions':3,'model_calls':3},artifact_dir=tmp_path/'runs',model='fixture',initial_source='initial',request_key='attention-fixture')
    return store,s['session_id'],DevAttentionControl(store)


@pytest.mark.parametrize('kind',['model','execute'])
def test_switch_waits_for_actual_job_settlement_and_requires_executor_owner(tmp_path,kind):
    store,sid,c=session(tmp_path);store.acquire_dev_owner(sid,OWNER)
    j=store.reserve_dev_job(sid,1,kind,{'model_calls':1,'tokens':10} if kind=='model' else {'executions':1,'sdk_calls':0,'execution_seconds':0})
    store.claim_dev_job(sid,j['job_id'],OWNER)
    r=c.request(sid,'reactive_help','switch',0)
    assert r['state']=='Pending' and c.snapshot(sid)['active_policy']=='autonomous'
    assert c.request(sid,'reactive_help','switch',0)==r
    with pytest.raises(DevConflict):c.boundary(sid,{'pid':999})
    assert c.boundary(sid,OWNER)['request']['state']=='Pending'
    with pytest.raises(DevConflict):c.decide(sid,OWNER,2,FAIL,1)
    receipt={'model_output':{}} if kind=='model' else {'bundle':{},'trace':{}}
    # Real settlement requires actual model_calls/usage and execution dimensions.
    if kind=='model':
        store.settle_dev_job(sid,j['job_id'],{'model_calls':1,'tokens':1},receipt)
    else:store.settle_dev_job(sid,j['job_id'],{'executions':1,'sdk_calls':0,'execution_seconds':0},receipt)
    active=c.boundary(sid,OWNER)
    assert active['active_policy']=='reactive_help' and active['generation']==1
    assert active['request']['state']=='Active' and active['segments'][-1]['boundary_kind']=='dev_iteration'
    assert c.boundary(sid,OWNER)['generation']==1
    with pytest.raises(DevConflict):c.request(sid,'retry_k_then_ask','stale',0)


def activate(c,sid,policy):
    c.request(sid,policy,'switch-'+policy,c.snapshot(sid)['generation']);c.boundary(sid,OWNER)


@pytest.mark.parametrize('policy,failures,asks',[('autonomous',2,False),('reactive_help',1,True),('retry_k_then_ask',1,False),('retry_k_then_ask',2,True),('trace_aware_hint_only',1,False),('trace_aware_hint_only',2,True)])
def test_selected_policy_really_changes_help_decision(tmp_path,policy,failures,asks):
    store,sid,c=session(tmp_path);store.acquire_dev_owner(sid,OWNER);activate(c,sid,policy)
    result=c.decide(sid,OWNER,2,FAIL,failures,[FAIL] if failures>1 else [])
    assert (result['action']=='request') is asks
    assert bool(result['hint']) is asks
    assert c.decide(sid,OWNER,2,FAIL,failures)==result
    assert c.snapshot(sid)['hint_count']==int(asks)


def test_guidance_is_version_bound_and_policy_switch_cancels_wait_without_carryover(tmp_path):
    store,sid,c=session(tmp_path);store.acquire_dev_owner(sid,OWNER);activate(c,sid,'reactive_help')
    h=c.decide(sid,OWNER,2,FAIL,1)['hint']
    activate(c,sid,'autonomous')
    with pytest.raises(DevConflict):c.guidance(sid,h['request_id'],content='Lower the grip')
    assert c.snapshot(sid)['hint']['state']=='cancelled'
    assert c.decide(sid,OWNER,2,FAIL,1)['hint'] is None
    with pytest.raises(ValueError):c.request(sid,'full_trace_aware_attention_planner','unsupported',2)


def test_pending_cancel_and_stop_never_activate_switch_or_accept_guidance(tmp_path):
    store,sid,c=session(tmp_path);store.acquire_dev_owner(sid,OWNER)
    c.request(sid,'reactive_help','cancel',0);assert c.cancel(sid,'cancel')['state']=='Cancelled'
    assert c.boundary(sid,OWNER)['active_policy']=='autonomous'
    activate(c,sid,'reactive_help');h=c.decide(sid,OWNER,2,FAIL,1)['hint']
    c.request(sid,'autonomous','end',1);store.stop_dev_session(sid)
    with pytest.raises(DevConflict):c.guidance(sid,h['request_id'],content='do not dispatch')
    final=c.boundary(sid,OWNER,ending=True)
    assert final['request']['state']=='Rejected' and final['hint']['state']=='cancelled'
    assert final['active_policy']=='reactive_help' and final['read_only']


def test_guidance_reopens_identically_and_private_scoring_is_rejected(tmp_path):
    store,sid,c=session(tmp_path);store.acquire_dev_owner(sid,OWNER);activate(c,sid,'reactive_help')
    h=c.decide(sid,OWNER,2,FAIL,1)['hint']
    with pytest.raises(ValueError):c.guidance(sid,h['request_id'],content='reward=1')
    reply=c.guidance(sid,h['request_id'],content='Lower the grip slightly')
    assert c.guidance(sid,h['request_id'],content='Lower the grip slightly')==reply
    reopened=DevAttentionControl(AttentionStore(store.path));assert reopened.snapshot(sid)['hint']==reply
    assert c.decide(sid,OWNER,2,FAIL,1)['hint']['content']=='Lower the grip slightly'


@pytest.mark.parametrize('operation',['answer','skip','switch','stop'])
def test_worker_pauses_before_model_and_consumes_only_answered_public_guidance(tmp_path,monkeypatch,operation):
    store,sid,c=session(tmp_path)
    monkeypatch.setattr(dl,'verify_resources',lambda _:None)
    c.request(sid,'reactive_help','initial',0)
    from benchmarks.attention_harness import formal_runner_boundary as boundary
    inputs=[];errors=[]
    class Client:
        def chat(self,**kwargs):
            inputs.append(kwargs['messages'])
            if len(inputs)==2:store.stop_dev_session(sid)
            return ParccResponse('fixture',json.dumps({'source':'from robot_sdk import sensors\nprint(1)\n','reason':'fixture'}),None,.01,1,{'total_tokens':5})
    def execute(req,**kwargs):
        d=req.artifact_root/'episode';d.mkdir(parents=True)
        trace=dl.immutable(d/'trace.json',{'status':'failed','error':'public gripper failure','sdk_events':[{'operation':'close','source':'gripper','status':'failed','error':'gripper blocked'}]})
        receipt=dl.immutable(d/'sandbox_receipt.json',{'sdk_call_budget':{'dispatched':1},'elapsed_seconds':.1})
        return {'status':'failed','artifact_dir':str(d),'native_evaluator':{'native_success':False,'private_state':'SECRET'},'artifacts':{'trace':trace,'sandbox_receipt':receipt}}
    monkeypatch.setattr(boundary,'run_with_formal_boundary',execute)
    def run():
        try:dl.run_session(store,sid,client=Client(),runner_factory=lambda **_:NS(cancel_event=threading.Event()))
        except Exception as e:errors.append(e)
    thread=threading.Thread(target=run);thread.start()
    end=time.monotonic()+4
    while time.monotonic()<end:
        h=c.snapshot(sid).get('hint')
        if h and h['state']=='pending':break
        time.sleep(.01)
    else:
        store.stop_dev_session(sid);thread.join(3);pytest.fail(f'no help request; errors={errors}')
    assert len(inputs)==1 and h['version']==2
    if operation=='answer':c.guidance(sid,h['request_id'],content='Lower the grip slightly')
    elif operation=='skip':c.guidance(sid,h['request_id'],skip=True)
    elif operation=='switch':c.request(sid,'autonomous','continue',c.snapshot(sid)['generation'])
    else:store.stop_dev_session(sid)
    end=time.monotonic()+4
    while len(inputs)<2 and thread.is_alive() and time.monotonic()<end:time.sleep(.01)
    store.stop_dev_session(sid);thread.join(3)
    assert not thread.is_alive() and not errors
    assert len(inputs)==(1 if operation=='stop' else 2)
    if len(inputs)==2:
        packet=json.loads(inputs[1][1]['content']);attention=packet['attention_control']
        assert attention.get('human_guidance','')==('Lower the grip slightly' if operation=='answer' else '')
        assert attention['policy_id']==('autonomous' if operation=='switch' else 'reactive_help')
    assert 'SECRET' not in json.dumps(inputs) and 'native_success' not in json.dumps(inputs)
    assert not store.get_dev_session(sid)['owner']

def test_http_attention_auth_switch_confirmation_and_guidance_binding(tmp_path,monkeypatch):
    from benchmarks.attention_harness.tests.test_ui_history import api
    store,sid,c=session(tmp_path)
    monkeypatch.setattr(dl,'spawn_worker',lambda *args:None)
    monkeypatch.setattr(dl,'recover_sessions',lambda *args:[])
    with api(store,tmp_path) as request:
        path='/api/dev/sessions/'+sid
        selection={'policy_id':'reactive_help','request_id':'http-switch','expected_generation':0}
        assert request(path+'/attention-policy',selection,False)[0]==403
        status,result=request(path+'/attention-policy',selection)
        assert status==200 and result['state']=='Pending'
        assert request(path)[1]['attention_control']['active_policy']=='autonomous'
        store.acquire_dev_owner(sid,OWNER);c.boundary(sid,OWNER)
        assert request(path)[1]['attention_control']['active_policy']=='reactive_help'
        hint=c.decide(sid,OWNER,2,FAIL,1)['hint']
        reply={'request_id':hint['request_id'],'content':'Lower the grip slightly','skip':False}
        assert request(path+'/attention-guidance',reply)[0]==200
        assert request(path)[1]['attention_control']['hint']['content']==reply['content']
        assert request(path+'/attention-policy',{**selection,'request_id':'bad','policy_id':'full_trace_aware_attention_planner','expected_generation':1})[0]==400
        assert request(path+'/attention-guidance',{**reply,'request_id':'stale'})[0]==409
        store.stop_dev_session(sid)
        assert request(path+'/attention-policy',{**selection,'request_id':'ended','expected_generation':1})[0]==409


@pytest.mark.parametrize('phase',['model','execute'])
@pytest.mark.parametrize('outcome',['failed','completed'])
def test_running_worker_switch_controls_next_help_decision_without_forcing_pause(tmp_path,monkeypatch,phase,outcome):
    """An operator changes policy during a real worker job, not in a projection."""
    store,sid,c=session(tmp_path)
    monkeypatch.setattr(dl,'verify_resources',lambda _:None)
    from benchmarks.attention_harness import formal_runner_boundary as boundary
    entered=threading.Event();release=threading.Event();inputs=[];errors=[]
    def hold():
        entered.set()
        assert release.wait(5), 'test did not release in-flight job'
    class Client:
        def chat(self,**kwargs):
            inputs.append(kwargs['messages'])
            if len(inputs)==1 and phase=='model':hold()
            if len(inputs)==2:store.stop_dev_session(sid)
            return ParccResponse('fixture',json.dumps({'source':'from robot_sdk import sensors\nprint(1)\n','reason':'fixture'}),None,.01,1,{'total_tokens':5})
    def execute(req,**kwargs):
        if phase=='execute':hold()
        d=req.artifact_root/'episode';d.mkdir(parents=True)
        trace=dl.immutable(d/'trace.json',{'status':outcome,'error':'gripper blocked' if outcome=='failed' else None,'sdk_events':[]})
        receipt=dl.immutable(d/'sandbox_receipt.json',{'sdk_call_budget':{'dispatched':0},'elapsed_seconds':.1})
        return {'status':outcome,'artifact_dir':str(d),'artifacts':{'trace':trace,'sandbox_receipt':receipt}}
    monkeypatch.setattr(boundary,'run_with_formal_boundary',execute)
    def run():
        try:dl.run_session(store,sid,client=Client(),runner_factory=lambda **_:NS(cancel_event=threading.Event()))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=run);thread.start()
    try:
        assert entered.wait(4), errors
        c.request(sid,'reactive_help','operator-during-'+phase,0)
        current=store.get_dev_session(sid)
        assert any(j['kind']==phase and j['state']=='claimed' for j in current['jobs'])
        assert current['attention_control']['request']['state']=='Pending'
        assert current['attention_control']['active_policy']=='autonomous'
        assert not current['attention_control']['hint']
        release.set()
        if outcome=='failed':
            end=time.monotonic()+4
            while time.monotonic()<end:
                hint=c.snapshot(sid).get('hint')
                if hint and hint['state']=='pending':break
                time.sleep(.01)
            else:pytest.fail(f'new policy did not request help; errors={errors}')
            assert c.snapshot(sid)['active_policy']=='reactive_help'
            assert len(inputs)==1 and hint['version']==2
            c.guidance(sid,hint['request_id'],content='Adjust the grasp alignment')
        thread.join(4)
        assert not thread.is_alive() and not errors
        assert len(inputs)==2
        packet=json.loads(inputs[1][1]['content'])['attention_control']
        assert packet['policy_id']=='reactive_help' and packet['generation']==1
        assert packet.get('human_guidance','')==('Adjust the grasp alignment' if outcome=='failed' else '')
        assert c.snapshot(sid)['hint_count']==int(outcome=='failed')
        assert len([j for j in store.get_dev_session(sid)['jobs'] if j['kind']=='execute'])==1
    finally:
        release.set();store.stop_dev_session(sid);thread.join(5)
