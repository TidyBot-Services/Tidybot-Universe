import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import pytest
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.core.dev_store import DevConflict,DevBudgetExhausted
from benchmarks.attention_harness.dev_loop import public_feedback,messages_for,DEFAULT_BUDGET,process_identity,owner_alive

@pytest.fixture
def store(tmp_path):return AttentionStore(tmp_path/'attention.sqlite3')

def session(store,budget=None):
    s=store.create_dev_session(locked={},budget=budget or DEFAULT_BUDGET,artifact_dir='/tmp/dev-tests',model='test',initial_source='old',request_key='unique-key')
    store.acquire_dev_owner(s['session_id'],{'pid':123});return s['session_id']

@pytest.mark.parametrize('poison',[
 {'reward':42},{'native_success':True},{'hidden_evaluator':{'secret':42}},
 {'private_state':[42]},{'derived_diagnosis':'reward=42'},
 {'error':{'message':'private state SECRET'}},{'result':{'objects':[{'reward':9}]}},
])
def test_hidden_feedback_rejected_before_model(poison):
    trace={'status':'failed','error':None,'sdk_events':[{'operation':'move_delta','status':'failed',**poison}]}
    with pytest.raises(ValueError):public_feedback(trace)


def test_public_trace_projection_drops_nonpublic_fields():
    f=public_feedback({'status':'failed','error':'SDK call budget exceeded','sdk_events':[
        {'operation':'get_observation','status':'completed','result':{'robot0_eef_pos':[1,2,3],'unapproved_secret':123}},
        {'operation':'move_delta','status':'failed','arguments':{'dx':.01},'error':{'type':'ValueError','message':'call budget exceeded'}}]})
    assert f['action_trace'][0]['observation']=={'robot0_eef_pos':[1,2,3]}
    assert 'unapproved_secret' not in json.dumps(f)


def test_idempotent_create_does_not_duplicate(store):
    sid=session(store)
    assert store.create_dev_session(locked={},budget=DEFAULT_BUDGET,artifact_dir='/tmp/dev-tests',model='test',initial_source='old',request_key='unique-key')['session_id']==sid
    assert len(store.list_dev_sessions())==1


def test_concurrent_claim_only_one_dispatch(store):
    sid=session(store);j=store.reserve_dev_job(sid,1,'model',{'model_calls':1,'tokens':100})
    def claim(_):
        try:store.claim_dev_job(sid,j['job_id'],{'pid':123});return True
        except DevConflict:return False
    with ThreadPoolExecutor(max_workers=8) as pool:assert sum(pool.map(claim,range(8)))==1


def test_budget_cumulative_across_versions_and_atomic_reservation(store):
    b={**DEFAULT_BUDGET,'tokens':100};sid=session(store,b)
    j=store.reserve_dev_job(sid,1,'model',{'model_calls':1,'tokens':80});store.claim_dev_job(sid,j['job_id'],{'pid':123})
    with pytest.raises(DevBudgetExhausted):store.reserve_dev_job(sid,2,'model',{'tokens':21})
    store.settle_dev_job(sid,j['job_id'],{'model_calls':1,'tokens':70},{'response':'ok'})
    store.settle_dev_job(sid,j['job_id'],{'model_calls':1,'tokens':70},{'response':'ok'})
    assert store.get_dev_session(sid)['remaining']['tokens']==30
    with pytest.raises(DevBudgetExhausted):store.reserve_dev_job(sid,2,'model',{'tokens':31})
    with pytest.raises(DevConflict):store.settle_dev_job(sid,j['job_id'],{'tokens':2},{})


def test_unknown_restart_never_refunds_or_redispatches(store):
    sid=session(store);j=store.reserve_dev_job(sid,1,'execute',{'executions':1,'sdk_calls':40})
    store.claim_dev_job(sid,j['job_id'],{'pid':123})
    reopened=AttentionStore(store.path);assert reopened.recover_dev_sessions(lambda _:False)==[sid]
    s=reopened.get_dev_session(sid)
    assert s['state']=='needs_review' and s['reserved']['executions']==1
    with pytest.raises(DevConflict):reopened.resume_dev_session(sid)
    with pytest.raises(DevConflict):reopened.claim_dev_job(sid,j['job_id'],{'pid':123})


def test_safe_restart_preserves_reserved_job_and_live_owner(store):
    sid=session(store);j=store.reserve_dev_job(sid,1,'model',{'tokens':10})
    assert store.recover_dev_sessions(lambda _:True)==[]
    store.recover_dev_sessions(lambda _:False);assert store.get_dev_session(sid)['state']=='ready'
    store.acquire_dev_owner(sid,{'pid':456});assert store.reserve_dev_job(sid,1,'model',{'tokens':10})==j
    store.claim_dev_job(sid,j['job_id'],{'pid':456})


def test_stop_all_dispatch_and_resume(store):
    sid=session(store);j=store.reserve_dev_job(sid,1,'execute',{'executions':1})
    store.stop_dev_session(sid)
    with pytest.raises(DevConflict):store.claim_dev_job(sid,j['job_id'],{'pid':123})
    store.release_dev_owner(sid,{'pid':123});store.dev_state(sid,'stopped')
    store.resume_dev_session(sid);assert store.get_dev_session(sid)['state']=='ready'


def test_versions_immutable_separate_from_formal_tables(store):
    sid=session(store);v={'version':1,'formal_eligible':False,'source':'code'}
    store.add_dev_version(sid,v);store.add_dev_version(sid,v)
    with pytest.raises(DevConflict):store.add_dev_version(sid,{**v,'source':'changed'})
    with pytest.raises(ValueError):store.add_dev_version(sid,{**v,'version':2,'formal_eligible':True})
    with store._connect() as db:assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0]==0


def test_provider_input_has_full_source_and_remaining_and_no_native():
    from benchmarks.attention_harness.robocasa365.config import EXPECTED
    cfg={**EXPECTED,'task_id':'pick_place_counter_to_sink','task_prompt':'Pick object','asset_manifest_sha256':'a'*64}
    s={'locked':{'config':cfg,'resource_sha256':'b'*64},'remaining':DEFAULT_BUDGET}
    m=messages_for(s,'FULL PRIOR SOURCE',{'execution_status':'failed','public_error':'budget exceeded'})
    p=json.loads(m[1]['content']);assert p['previous_complete_source']=='FULL PRIOR SOURCE'
    assert p['remaining_budget']==DEFAULT_BUDGET and p['execution_arm']=='right'
    with pytest.raises(ValueError):messages_for(s,'source',{'reward':1})


def test_process_identity_checks_start_and_boot():
    owner=process_identity();assert owner_alive(owner)
    assert not owner_alive({**owner,'start_ticks':'wrong'})

@pytest.mark.parametrize('resource',['executions','model_calls','tokens','sdk_calls','execution_seconds'])
def test_each_resource_exhaustion_blocks_before_claim(store,resource):
    sid=session(store,{**DEFAULT_BUDGET,resource:0})
    with pytest.raises(DevBudgetExhausted):store.reserve_dev_job(sid,1,'model' if resource in ('model_calls','tokens') else 'execute',{resource:1})
    assert store.get_dev_session(sid)['jobs']==[]


def test_deadline_and_stop_block_new_versions_of_work(store):
    sid=session(store)
    with store._transaction() as db:
        s=store._dev_get(db,sid);s['deadline_at']=0;store._dev_save(db,s,'test_expired')
    with pytest.raises(DevBudgetExhausted):store.reserve_dev_job(sid,1,'execute',{'executions':1})


def test_precise_authored_code_diagnostic():
    from benchmarks.attention_harness.dev_loop import validation_diagnostic
    from benchmarks.attention_harness.robocasa365.policy_validation import validate_generated_policy
    code="from robot_sdk import sensors\nname='cup'.lower()\n"
    try:validate_generated_policy(code)
    except Exception as exc:assert validation_diagnostic(code,exc).endswith('forbidden methods: lower')


@pytest.mark.parametrize('native', [False,True,'fault'])
def test_native_result_and_fault_do_not_change_public_execution(tmp_path,monkeypatch,native):
    from types import SimpleNamespace as NS
    from benchmarks.attention_harness.robocasa365 import formal_runner as fr
    from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
    from benchmarks.attention_harness.robocasa365.config import EXPECTED
    cfg={**EXPECTED,'task_id':'pick_place_counter_to_sink','task_prompt':'pick object','asset_manifest_sha256':'a'*64}
    monkeypatch.setattr(fr,'_config',lambda req:cfg)
    class Service:
        def __init__(self,**kwargs):self.owner=self.public=self;self.url='test'
        def start(self):return {'recording_supported':True}
        def call(self,path,*args):
            if path=='/reset':return {'episode_id':'test','identity':{},'capabilities':{}}
            if native=='fault':raise RuntimeError('hidden evaluator reward SECRET')
            return {'native_success':native,'evaluated':True,'private_state':'SECRET'}
        def stop(self,*args):return {'leader_reaped':True,'process_group_gone':True}
    class Adapter:
        def __init__(self,*args):self.failed=False;self.ledger=[]
        def public_observation(self):return {'robot':{},'images':{}}
    monkeypatch.setattr(fr,'DedicatedService',Service);monkeypatch.setattr(fr,'Adapter',Adapter)
    monkeypatch.setattr(fr,'Safety',lambda *args:NS(violations=[],events=[]))
    monkeypatch.setattr(fr,'build_sdk',lambda *args:None);monkeypatch.setattr(fr,'probe_sandbox',lambda:{} )
    monkeypatch.setattr(fr,'execute_formal_policy',lambda **kwargs:NS(status='failed',error='public SDK failure',call_count=1))
    p=tmp_path/'policy.py';p.write_text('from robot_sdk import sensors')
    runner=fr.RoboCasa365FormalSuiteRunner(service_source_root=tmp_path,sim_python=tmp_path,sim_source_root=tmp_path,task_source_root=tmp_path,asset_manifest=tmp_path,independent_evaluation=True)
    request=FormalRunRequest(suite='robocasa365',task_id='pick_place_counter_to_sink',seed=101,policy_code_path=p,policy_sha256='a'*64,config_sha256='b'*64,artifact_root=tmp_path/'executions',overall_deadline_seconds=120,attempt_deadline_seconds=120)
    result=runner.execute(request)
    feedback=public_feedback(json.loads(Path(result['artifacts']['trace']['uri']).read_bytes()))
    assert feedback=={'schema_version':'attentionbench.dev-public-feedback.v1','execution_status':'failed','public_error':'public SDK failure','action_trace':[]}
    assert 'SECRET' not in json.dumps(feedback)
    assert result['native_evaluator']['evaluated'] is (native!='fault')


def test_orchestration_revises_full_source_using_only_public_failure(tmp_path,monkeypatch):
    from types import SimpleNamespace as NS
    from benchmarks.attention_harness import dev_loop as dl, formal_runner_boundary as boundary
    from benchmarks.attention_harness.parcc_client import ParccResponse
    from benchmarks.attention_harness.robocasa365.config import EXPECTED
    store=AttentionStore(tmp_path/'loop.sqlite3')
    cfg={**EXPECTED,'task_id':dl.TASK,'task_prompt':'pick object','asset_manifest_sha256':'a'*64}
    s=store.create_dev_session(locked={'config':cfg,'config_text':json.dumps(cfg),'resources':{k:{'path':str(tmp_path)} for k in ('service_source_root','sim_python','sim_source_root','task_source_root','asset_manifest')},'resource_sha256':'b'*64},budget={**DEFAULT_BUDGET,'executions':2,'model_calls':2},artifact_dir=tmp_path/'runs',model='test',initial_source='FULL INITIAL SOURCE',request_key='orchestration')
    monkeypatch.setattr(dl,'verify_resources',lambda _:None)
    class Client:
        def __init__(self):self.inputs=[]
        def chat(self,**kwargs):
            self.inputs.append(kwargs['messages']);n=len(self.inputs)
            return ParccResponse('reported-test-model',json.dumps({'source':f'from robot_sdk import sensors\nprint({n})\n','reason':f'revision {n}'}),None,.01,1,{'total_tokens':5})
    def execute(req,**kwargs):
        assert kwargs['runner'].independent_evaluation is True
        d=req.artifact_root/'episode';d.mkdir(parents=True)
        trace=dl.immutable(d/'trace.json',{'status':'failed','error':'public SDK failure','sdk_events':[]})
        receipt=dl.immutable(d/'sandbox_receipt.json',{'sdk_call_budget':{'dispatched':7},'elapsed_seconds':.5})
        return {'status':'failed','artifact_dir':str(d),'native_evaluator':{'reward':999,'private_state':'SECRET','native_success':True},'artifacts':{'trace':trace,'sandbox_receipt':receipt}}
    monkeypatch.setattr(boundary,'run_with_formal_boundary',execute)
    client=Client();dl.run_session(store,s['session_id'],client=client,runner_factory=lambda **kwargs:NS(cancel_event=__import__('threading').Event(),independent_evaluation=kwargs['independent_evaluation']))
    final=store.get_dev_session(s['session_id']);assert final['state']=='budget_exhausted'
    assert final['used']['model_calls']==2 and final['used']['tokens']==10 and final['used']['sdk_calls']==14
    packet=json.loads(client.inputs[1][1]['content'])
    assert packet['previous_complete_source']==Path(final['versions'][0]['source']['uri']).read_text()
    assert packet['public_feedback']['public_error']=='public SDK failure'
    assert 'SECRET' not in json.dumps(client.inputs) and 'reward' not in json.dumps(client.inputs)
    assert len(final['versions'])==2 and all(v['formal_eligible'] is False for v in final['versions'])

@pytest.mark.parametrize('http_status',[200,500])
def test_empty_provider_response_known_usage_vs_unknown(tmp_path,monkeypatch,http_status):
    from benchmarks.attention_harness import dev_loop as dl
    from benchmarks.attention_harness.parcc_client import ParccClient,ParccError
    from benchmarks.attention_harness.robocasa365.config import EXPECTED
    store=AttentionStore(tmp_path/'empty.sqlite3');original='from robot_sdk import sensors\nobs=sensors.get_observation()\n'
    cfg={**EXPECTED,'task_id':dl.TASK,'task_prompt':'pick object','asset_manifest_sha256':'a'*64}
    s=store.create_dev_session(locked={'config':cfg,'resource_sha256':'b'*64},budget={**DEFAULT_BUDGET,'model_calls':1},artifact_dir=tmp_path/'runs',model='test',initial_source=original,request_key='known-empty-response')
    monkeypatch.setattr(dl,'verify_resources',lambda _:None)
    body={'model':'reported-model','usage':{'total_tokens':5},'choices':[{'finish_reason':'length','message':{'content':None,'reasoning_content':'reasoning is not executable source'}}]}
    client=ParccClient(api_key='test-fixture',max_attempts=1,transport=lambda *args:(http_status,{},json.dumps(body).encode()))
    if http_status==500:
        with pytest.raises(ParccError):dl.run_session(store,s['session_id'],client=client)
        final=store.get_dev_session(s['session_id'])
        assert final['state']=='needs_review' and final['jobs'][0]['state']=='needs_review'
        assert final['used']['tokens']==0 and final['reserved']['tokens']>0
    else:
        dl.run_session(store,s['session_id'],client=client)
        final=store.get_dev_session(s['session_id']);v=final['versions'][0]
        assert final['state']=='budget_exhausted' and final['used']['tokens']==5
        assert v['valid'] is False and Path(v['source']['uri']).read_text()==original
        assert v['actual_model']=='reported-model' and all(j['kind']=='model' for j in final['jobs'])


def test_missing_actual_usage_cannot_release_reservation(store):
    sid=session(store);job=store.reserve_dev_job(sid,1,'model',{'model_calls':1,'tokens':10})
    store.claim_dev_job(sid,job['job_id'],{'pid':123})
    with pytest.raises(DevConflict):store.settle_dev_job(sid,job['job_id'],{'tokens':5},{})
    assert store.get_dev_session(sid)['reserved']['model_calls']==1


def test_stop_during_model_settles_response_but_never_executes(tmp_path,monkeypatch):
    from benchmarks.attention_harness import dev_loop as dl
    from benchmarks.attention_harness.parcc_client import ParccResponse
    from benchmarks.attention_harness.robocasa365.config import EXPECTED
    store=AttentionStore(tmp_path/'stop.sqlite3');cfg={**EXPECTED,'task_id':dl.TASK,'task_prompt':'pick object','asset_manifest_sha256':'a'*64}
    s=store.create_dev_session(locked={'config':cfg,'resource_sha256':'b'*64},budget=DEFAULT_BUDGET,artifact_dir=tmp_path/'runs',model='test',initial_source='old source',request_key='stop-during-model')
    monkeypatch.setattr(dl,'verify_resources',lambda _:None)
    class Client:
        def chat(self,**kwargs):
            store.stop_dev_session(s['session_id'])
            return ParccResponse('test',json.dumps({'source':'from robot_sdk import sensors\n','reason':'fixture'}),None,.01,1,{'total_tokens':5})
    dl.run_session(store,s['session_id'],client=Client(),runner_factory=lambda **kwargs:pytest.fail('execution dispatched after session stop'))
    final=store.get_dev_session(s['session_id'])
    assert final['state']=='stopped' and final['used']['tokens']==5
    assert len(final['versions'])==1 and not final['owner'] and all(j['kind']=='model' for j in final['jobs'])


def test_lost_worker_recovers_without_leaking_ephemeral_credentials(tmp_path):
    from benchmarks.attention_harness import dev_loop as dl
    store=AttentionStore(tmp_path/'lost.sqlite3')
    s=store.create_dev_session(locked={},budget=DEFAULT_BUDGET,artifact_dir=tmp_path/'runs',model='fixture',initial_source='code',request_key='lost-credentials')
    sid=s['session_id'];owner={'pid':999999999,'start_ticks':'1','boot_id':'fixture'};store.acquire_dev_owner(sid,owner)
    j=store.reserve_dev_job(sid,1,'execute',{'executions':1});store.claim_dev_job(sid,j['job_id'],owner)
    d=Path(s['artifact_dir']);d.mkdir(parents=True);(d/'owned-service-999999999.json').write_text(json.dumps(owner))
    token=d/'v1/episode/.owner-tokens.json';token.parent.mkdir(parents=True);token.write_text('test credential')
    reopened=AttentionStore(store.path);assert dl.recover_sessions(reopened)==[sid]
    assert not token.exists() and reopened.get_dev_session(sid)['state']=='needs_review'
    assert reopened.get_dev_session(sid)['reserved']['executions']==1
    assert dl.recover_sessions(reopened)==[]


def test_formal_store_reopen_preserves_frozen_sqlite_bytes(tmp_path):
    import hashlib
    path=tmp_path/'formal.sqlite3';AttentionStore(path)
    before=path.read_bytes();reopened=AttentionStore(path)
    assert path.read_bytes()==before
    with reopened._connect() as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'dev_%'").fetchall()
    reopened.list_dev_sessions()
    with reopened._connect() as db:assert db.execute("SELECT COUNT(*) FROM dev_sessions").fetchone()[0]==0
