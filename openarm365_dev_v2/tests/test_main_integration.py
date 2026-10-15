"""Controlled contract tests only: no provider, native scene or human claim."""
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_planner_memory as memory, dev_v2_loop as loop
from benchmarks.attention_harness.dev_capabilities import execution_environment, provider_environment
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.dev_loop import sha, process_identity
from benchmarks.attention_harness.dev_v2_loop import immutable
from benchmarks.attention_harness.dev_main_bridge import DevMainBridge
from benchmarks.attention_harness.dev_planner import POLICY
from benchmarks.attention_harness.core.dev_store import DevConflict, DevBudgetExhausted
from openarm365_dev_v2.tests.test_planner_features import setup, TestOnlyGateway, SENSOR, SOURCE


def test_memory_clock_failure_and_restart_not_renewed(tmp_path,monkeypatch):
    s,e,c,cfg,owner,root=setup(tmp_path)
    class Unavailable:
        def store_id(self):time.sleep(.03);raise ConnectionError('controlled authority failure')
    monkeypatch.setattr(memory,'client_for',lambda _:Unavailable())
    settings=dict(base_url='http://127.0.0.1:1',store_id='controlled',namespace='controlled')
    assert s.goal_for_cell(c['key'])['real_started_at'] is None
    for _ in range(2):
        with pytest.raises(ConnectionError):memory.load_memory(s,c['key'],1,SENSOR,cfg,settings,root)
        start=s.goal_for_cell(c['key'])['real_started_at']
        if 'first' not in locals():first=start
        assert start==first
    reopened=DevV2Store(s.path);g=reopened.goal_for_cell(c['key'])
    assert g['real_started_at']==first and g['wall_used']>=.06
    assert not any(g['reserved'].values()) and not any(g['used'].values())
    with s.tx() as db:
        events=[json.loads(p) for p, in db.execute("SELECT payload FROM v2_events WHERE kind='planner_memory_query_finished'")]
    assert len(events)==2 and all(v['status']=='failed' and v['actual_seconds']>=.03 for v in events)


def test_offline_create_and_refresh_never_start_clock(tmp_path):
    s,e,c,cfg,owner,root=setup(tmp_path)
    from benchmarks.attention_harness.dev_experiment import snapshot
    snapshot(s,e['experiment_id']);s.export_ledger()
    assert s.goal_for_cell(c['key'])['real_started_at'] is None


def test_memory_private_ranking_cannot_change_online_input_or_decision(tmp_path):
    s,e,c,cfg,owner,root=setup(tmp_path)
    gw=TestOnlyGateway(cfg);base=gw.record
    a=copy.deepcopy(base);a.memory_id='a';a.confidence=.1
    z=copy.deepcopy(base);z.memory_id='z';z.confidence=.99
    def authorize(**kw):
        m={'a':a,'z':z}[kw['memory_id']]
        assert kw['memory_version']==m.version
        grant=dict(grant_id='opaque-'+m.memory_id,memory_id=m.memory_id,memory_version=m.version,
            development_id=kw['development_id'],source_policy_sha256=kw['source_policy_sha256'],context=kw['context'],
            guidance=m.guidance,source_trace_id='opaque-source',evidence_refs=['opaque-evidence'])
        return grant
    gw.authorize_dev_use=authorize
    settings=dict(base_url='http://127.0.0.1:1',store_id=gw.store_id(),namespace='controlled-test-only')
    service=memory.DevMemory(gw,settings,cfg)
    from benchmarks.attention_harness.dev_help import decide,policy_parameters
    inputs=[];decisions=[]
    for records in ([z,a],[a,z]):
        a.confidence,z.confidence=z.confidence,a.confidence
        gw.retrieve=lambda context,now:records
        packet=service.packet(development_id=c['key']+':v1',source=SENSOR)
        decision=decide(POLICY,policy_parameters(POLICY),2,{},1,[],help_remaining=1,demo_available=True,memory=packet,trace_visible=True)
        decisions.append(decision)
        inputs.append(loop.model_messages(cfg,SENSOR,{},dict(action='continue',memory=packet),c['budget'],True))
    assert inputs[0]==inputs[1] and decisions[0]==decisions[1]
    assert [e['memory_id'] for e in packet['entries']]==['a','z']
    assert 'confidence' not in json.dumps(inputs)


def test_predispatch_provider_construction_failure_leaves_no_reservation(tmp_path,monkeypatch):
    s,e,c,cfg,owner,root=setup(tmp_path)
    def fail(**kw):raise PermissionError('controlled missing credential')
    monkeypatch.setattr(loop,'ParccClient',fail)
    with pytest.raises(PermissionError):loop.provider_call(s,c['key'],1,'model',[{'role':'user','content':'{}'}],owner,root)
    assert not s.cell(c['key'])['jobs'] and not any(s.goal_for_cell(c['key'])['reserved'].values())


def test_child_capabilities_are_separated(monkeypatch):
    values={'ATTENTION_MEMORY_API_KEY':'online','ATTENTION_MEMORY_IMPORTER_KEY':'importer','ATTENTION_MEMORY_OPERATOR_KEY':'operator',
        'ATTENTION_HUMAN_APPROVAL_TOKEN':'human','CUSTOM_IMPORTER_FILE':'sensitive-file','SOME_OPERATOR_CAPABILITY':'sensitive',
        'PARCC_API_KEY':'provider'}
    for k,v in values.items():monkeypatch.setenv(k,v)
    scheduler=execution_environment('/controlled');provider=provider_environment('/controlled')
    assert scheduler['ATTENTION_MEMORY_API_KEY']=='online' and provider['PARCC_API_KEY']=='provider'
    assert not any(k in scheduler for k in values if k not in {'ATTENTION_MEMORY_API_KEY','PARCC_API_KEY'})
    assert not any(k.startswith('ATTENTION_MEMORY_') for k in provider)


def make_bridge(tmp_path):
    import hashlib
    from benchmarks.attention_harness.dev_configuration import normalize_configuration,derive_cell_budget
    key='controlled-shared-goal';gid=hashlib.sha256(('goal|'+key).encode()).hexdigest()[:32]
    s=DevV2Store(tmp_path/'ledger.sqlite');budget=derive_cell_budget(normalize_configuration(dict(max_versions=1,target_depth=1,target_valid_executions=1,help_budget=0,planner_features={'human_wait_seconds':1},policies=[POLICY])))
    limits={k:2*v for k,v in budget.items()};s.ensure_goal(gid,limits,7200)
    memory_settings=dict(base_url='http://127.0.0.1:1',store_id='controlled-authority',namespace='controlled-namespace')
    b=DevMainBridge(s.path,tmp_path/'artifacts',key,memory_settings)
    body=dict(start=False,selection=dict(request_key='controlled-main-bridge',tasks=['pick_place_counter_to_sink','open_cabinet'],seeds=[125],policies=[POLICY],mode='experiment',help_budget=0,human_wait_seconds=1,
        configuration=dict(goal_id=key,experiment_budget=limits,cell_budget=budget,max_iterations=1,wall_seconds=7200)))
    return b,body


def test_main_bridge_maps_actual_frozen_configuration_and_no_clock_on_refresh(tmp_path):
    b,body=make_bridge(tmp_path);e=b.create(body)
    assert len(e['cells'])==2
    m=json.loads(Path(e['manifest']['uri']).read_bytes())
    assert m['target_depth']==m['target_valid_executions']==m['max_versions']==1
    assert m['planner_features']['memory']==b.memory and m['planner_features']['human_wait_seconds']==1
    assert {v['seed'] for v in m['configs'].values()}=={125}
    assert {v['sdk_call_limit'] for v in m['configs'].values()}=={80}
    b.list();b.get('/api/dev-v2/options');assert b.store.goal(b.goal_id)['real_started_at'] is None
    assert b.create(body)['experiment_id']==e['experiment_id']
    assert json.loads((Path(e['artifact_dir'])/'ui-selection.json').read_bytes())==body['selection']
    wrong=copy.deepcopy(body);wrong['selection']['configuration']['wall_seconds']=3600
    with pytest.raises(DevConflict):b.create(wrong)
    with pytest.raises(DevConflict):b.start(e['experiment_id'],wall_seconds=3600)
    wrong=copy.deepcopy(body);wrong['selection']['configuration']['goal_id']='another-goal'
    with pytest.raises(ValueError):b.create(wrong)
    wrong=copy.deepcopy(body);wrong['selection']['seeds']=[126]
    with pytest.raises(ValueError):b.create(wrong)


def test_bridge_ordinary_control_cannot_approve(tmp_path):
    b,body=make_bridge(tmp_path);e=b.create(body)
    with pytest.raises(PermissionError):b.post('/api/dev-v2/experiments/'+e['experiment_id']+'/approvals/planner-'+'a'*64+'/decision',{},human_authorized=False)


def test_offline_queue_is_idempotent_and_not_authority_candidate(tmp_path):
    from benchmarks.attention_harness.dev_v2_memory_import import queue
    s,e,c,cfg,owner,root=setup(tmp_path)
    refs={'guidance_source':immutable(root/'help.json',{'content':'controlled public advice'})}
    a=queue(s,c['key'],1,root,'source',refs);assert queue(s,c['key'],1,root,'source',refs)==a
    v=json.loads(Path(a['uri']).read_bytes());assert v['status']=='pending_offline_import' and v['authority_candidate'] is False
    with s.tx() as db:assert db.execute("SELECT COUNT(*) FROM v2_events WHERE kind='planner_memory_offline_queued'").fetchone()[0]==1


def test_offline_http_submit_uses_new_contract_with_importer_only(tmp_path,monkeypatch):
    from benchmarks.attention_harness.dev_v2_memory_import import submit
    from benchmarks.attention_harness import memory_service_client
    seen=[]
    class Client:
        def __init__(self,url,**kw):seen.append((url,kw))
        def create_dev_v2_candidate(self,**kw):seen.append(kw);return {'memory_id':kw['memory_id'],'status':'candidate'}
    monkeypatch.setattr(memory_service_client,'MemoryServiceClient',Client)
    monkeypatch.setenv('ATTENTION_MEMORY_API_KEY','controlled-online');monkeypatch.setenv('ATTENTION_MEMORY_IMPORTER_KEY','controlled-offline')
    payload=dict(memory_id='candidate',source_import_id='verified-source',created_at=1)
    submit('http://127.0.0.1:1','candidate',payload,tmp_path/'receipt.json')
    assert seen[0][1]==dict(api_key='controlled-online',importer_key='controlled-offline') and seen[1]==payload


def test_validation_consumption_binds_original_input_and_code(tmp_path):
    from benchmarks.attention_harness.dev_memory_validation import policy_input
    import hashlib
    binding=dict(cell='cell',version=1,seed=101,policy_sha256='a'*64,config_sha256='b'*64,
        memory_id='candidate',memory_version=2,exposure='candidate_validation',context={'public':'scope'})
    grant=immutable(tmp_path/'grant.json',dict(grant_id='original-grant',binding=binding))
    inp=immutable(tmp_path/'input.json',dict(memory_ids=['candidate'],exposure='candidate_validation',context=binding['context'],
        policy_sha256='a'*64,config_sha256='b'*64,validation_grant_id='original-grant'))
    r=SimpleNamespace(policy_sha256='a'*64,config_sha256='b'*64,seed=101,run_id='run:cell',attempt_id='attempt:cell:0')
    public,receipt=policy_input(dict(input=inp,grant=grant,guidance='public candidate guidance'),r)
    assert public['guidance']=='public candidate guidance' and receipt['guidance_sha256']==hashlib.sha256(public['guidance'].encode()).hexdigest()
    r.policy_sha256='c'*64
    with pytest.raises(DevConflict):policy_input(dict(input=inp,grant=grant,guidance=public['guidance']),r)


def test_real_main_http_handler_without_transport(tmp_path,monkeypatch):
    """Handler routing only; the separate test_main_bridge_http uses real TCP."""
    import io
    import re
    from benchmarks.attention_harness import ui_server
    from benchmarks.attention_harness.core.store import AttentionStore
    bridge,body=make_bridge(tmp_path)
    monkeypatch.setattr(ui_server,'ThreadingHTTPServer',lambda address,handler:SimpleNamespace(handler=handler))
    server=ui_server.create_server(store=AttentionStore(tmp_path/'ui.sqlite'),artifact_root=tmp_path,port=0,dev_v2_bridge=bridge)
    def call(path,body=None,token=None):
        h=server.handler.__new__(server.handler);h.path=path;h.headers={};h.wfile=io.BytesIO()
        h.send_response=lambda status:setattr(h,'result_status',status)
        h.send_header=lambda *args:None;h.end_headers=lambda:None
        if token:h.headers['X-Attention-Token']=token
        if body is not None:
            raw=json.dumps(body).encode();h.rfile=io.BytesIO(raw);h.headers.update({'Content-Type':'application/json','Content-Length':str(len(raw))});h.do_POST()
        else:h.do_GET()
        return h.result_status,h.wfile.getvalue()
    status,page=call('/ui/');assert status==200
    token=re.search(rb'data-control-token="([^"]+)"',page)[1].decode()
    options=json.loads(call('/api/run-options')[1]);assert options['dev_loop']['enabled'] and len([p for p in options['dev_loop']['profiles'] if p.get('channel')=='dev_v2'])==2
    status,response=call('/api/dev-v2/experiments',body,token);assert status==200,response
    result=json.loads(response)
    assert len(json.loads(call('/api/runs')[1])['dev_sessions'])==2
    assert call('/api/dev-v2/experiments/'+result['experiment_id']+'/cancel',{},token)[0]==200


def test_native_child_drops_role_files_and_capabilities(tmp_path,monkeypatch):
    from openarm365_dev_v2.owner import NativeOwnerV2
    import openarm365_dev_v2.owner as owner_module
    for name in ('ATTENTION_MEMORY_IMPORTER_FILE','PRIVATE_OPERATOR_CAPABILITY','ATTENTION_HUMAN_APPROVAL_TOKEN','ATTENTION_MEMORY_API_KEY'):
        monkeypatch.setenv(name,'controlled-never-export')
    owner=NativeOwnerV2(python='/usr/bin/python3',config=tmp_path/'config.json',evidence=tmp_path,deadline=time.monotonic()+1,cancel_check=lambda:False)
    captured={}
    def spawn(*args,**kw):captured.update(kw);raise RuntimeError('controlled stop before spawning')
    monkeypatch.setattr(owner_module.subprocess,'Popen',spawn)
    with pytest.raises(RuntimeError):owner.start()
    assert not any('IMPORTER' in k or 'OPERATOR' in k or 'HUMAN' in k or k.startswith('ATTENTION_MEMORY_') for k in captured['env'])
    owner.log.close()


@pytest.mark.parametrize('startup_ok',[True,False])
def test_native_runner_records_only_actual_validation_input_supply(tmp_path,monkeypatch,startup_ok):
    """Controlled native adapter, no simulator or successful validation claim."""
    import openarm365_dev_v2.runner as runner_module
    from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
    s,e,c,cfg,owner,root=setup(tmp_path)
    policy=immutable(root/'policy.py',SENSOR);config_ref={'uri':str(root/'config.json'),'sha256':sha(root/'config.json')}
    req=FormalRunRequest(suite='robocasa365',task_id=cfg['task_id'],seed=cfg['seed'],policy_code_path=Path(policy['uri']),policy_sha256=policy['sha256'],
        config_path=Path(config_ref['uri']),config_sha256=config_ref['sha256'],artifact_root=root/'executions',overall_deadline_seconds=90,attempt_deadline_seconds=60,sdk_call_limit=80,run_id='run:'+c['key'],attempt_id=f"attempt:{c['key']}:0",entry_lock=None)
    grant=immutable(root/'grant.json',dict(grant_id='controlled-validation',binding=dict(cell=c['key'],version=1,seed=cfg['seed'],policy_sha256=policy['sha256'],config_sha256=config_ref['sha256'],memory_id='candidate',memory_version=1,exposure='candidate_validation',context={'public':'scope'})))
    inp=immutable(root/'input.json',dict(memory_ids=['candidate'],exposure='candidate_validation',context={'public':'scope'},policy_sha256=policy['sha256'],config_sha256=config_ref['sha256'],validation_grant_id='controlled-validation'))
    observed=[]
    class Owner:
        def __init__(self,**kw):self.ledger=[];self.sent={};self.unresolved=[]
        def start(self):
            if not startup_ok:raise RuntimeError('controlled native startup failure')
            return {'initial_observation':{'robot':{},'perception':{'values':{}}},'capabilities':{}}
        def rpc(self,group,method):return {'robot':{},'perception':{'values':{}}} if method=='observe' else {'evaluated':True,'native_success':False}
        def stop(self,reason):return dict(leader_reaped=True,process_group_gone=True,planner_process_groups_gone=True)
    monkeypatch.setattr(runner_module,'NativeOwner',Owner);monkeypatch.setattr(runner_module,'probe_sandbox',lambda:{'controlled':True})
    monkeypatch.setattr(runner_module,'build_sdk',lambda *args:None)
    monkeypatch.setattr(runner_module,'finalize_recording',lambda *args:{})
    def policy_run(**kw):observed.append(kw['context']);return SimpleNamespace(status='completed',error=None,call_count=0)
    monkeypatch.setattr(runner_module,'execute_formal_policy',policy_run)
    runner=runner_module.OpenArm365DevRunnerV2(service_source_root='',sim_python='',sim_source_root='',task_source_root='',asset_manifest='',memory_validation=dict(input=inp,grant=grant,guidance='controlled public guidance'))
    result=runner.execute(req);trace=json.loads(Path(result['artifacts']['trace']['uri']).read_bytes())
    assert bool(observed)==startup_ok
    assert ('memory_validation' in trace)==startup_ok
    if startup_ok:
        assert observed[0]['memory_validation']['guidance']=='controlled public guidance'
        assert trace['memory_validation']['input_sha256']==inp['sha256']
