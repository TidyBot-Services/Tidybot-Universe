import json
import sys

import pytest
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment

from benchmarks.attention_harness import dev_provider_worker
from benchmarks.attention_harness.codex_client import CodexClient
from benchmarks.attention_harness.dev_capabilities import provider_environment
from benchmarks.attention_harness.dev_configuration import normalize_configuration
from benchmarks.attention_harness.dev_experiment import MODEL
from benchmarks.attention_harness.dev_advisor_proxy import default_config as advisor_default, normalize as normalize_advisor
from benchmarks.attention_harness.eval_agent import default_config as eval_default, normalize as normalize_eval
from benchmarks.attention_harness.parcc_client import ParccResponse
from benchmarks.attention_harness import eval_agent
from benchmarks.attention_harness import dev_advisor_proxy
from benchmarks.attention_harness.dev_request_view import build_request_view
from benchmarks.attention_harness.free_models import FREE_MODELS, limited_resources
from benchmarks.attention_harness.dev_experiment import create, verify_manifest
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.daily_dev_v2 import bind_daily_codex


def test_default_configuration_stays_parcc_and_byte_identical():
    original=normalize_configuration({})
    explicit=normalize_configuration({'model_source':'parcc'})
    assert json.dumps(original,sort_keys=True)==json.dumps(explicit,sort_keys=True)
    assert 'model_source' not in original
    assert original['eval_agent']['model']=='parcc/Qwen'
    assert original['advisor_proxy']['model']=='parcc/Qwen'
    assert MODEL['model']=='parcc/GLM'


def test_codex_models_are_pinned_and_do_not_enter_free_models():
    result=normalize_configuration({'budget_tier':'dev','model_source':'codex'})
    assert result['model_source']=='codex'
    assert result['eval_agent']['model']==result['advisor_proxy']['model']=='gpt-6-astra'
    assert result['eval_agent']['reasoning_effort']==result['advisor_proxy']['reasoning_effort']=='high'
    assert 'gpt-6-astra' not in FREE_MODELS
    assert 'provider_tokens' not in limited_resources(eval_config=result['eval_agent'],model_names=('gpt-6-astra',),model_source='codex')
    with pytest.raises(ValueError):normalize_eval({**eval_default(True,'codex'),'model':'parcc/Qwen'},'codex')
    with pytest.raises(ValueError):normalize_advisor({**advisor_default(True,'codex'),'model':'parcc/Qwen'},'codex')
    with pytest.raises(ValueError):normalize_eval({**eval_default(True,'codex'),'model':'gpt-6-sol'},'codex')
    with pytest.raises(ValueError):normalize_advisor({**advisor_default(True,'codex'),'model':'gpt-6-sol'},'codex')
    with pytest.raises(ValueError):normalize_eval({**eval_default(True,'codex'),'reasoning_effort':'low'},'codex')
    with pytest.raises(ValueError):normalize_advisor({**advisor_default(True,'codex'),'reasoning_effort':'low'},'codex')
    with pytest.raises(ValueError,match='reasoning effort'):
        normalize_configuration({'budget_tier':'dev','model_source':'codex','reasoning_effort':'low'})
    with pytest.raises(ValueError,match='Codex model'):
        normalize_configuration({'budget_tier':'dev','model_source':'codex','model':'gpt-6-sol'})


@pytest.mark.parametrize('selection',[{'budget_tier':'legacy','model_source':'codex'},
    {'budget_tier':'formal','model_source':'codex'}, {'model_source':'automatic'}])
def test_non_dev_or_unknown_source_rejected(selection):
    with pytest.raises(ValueError):normalize_configuration(selection)


def test_daily_codex_selection_converts_legacy_dialog_envelope(monkeypatch):
    from openarm365_dev_v2 import scene_contract
    monkeypatch.setattr(scene_contract,'registered_contract_ref',lambda:{'path':'scene'})
    source={'model_source':'codex','budget_tier':'dev','configuration':{'goal_id':'daily-codex','experiment_budget':{'dev_calls':3},
            'cell_budget':{'dev_calls':3},'max_iterations':3,'wall_seconds':4000}}
    converted=bind_daily_codex(source)
    assert converted['budget_tier']=='dev' and converted['goal_key']=='daily-codex'
    assert converted['max_versions']==3 and converted['scene_contract']=={'path':'scene'}
    assert 'configuration' not in converted
    with pytest.raises(ValueError,match='dev tier'):
        bind_daily_codex({**source,'budget_tier':'legacy'})


def test_provider_environment_keeps_home_and_drops_secrets(tmp_path,monkeypatch):
    for name in ('LITELLM_KEY','PARCC_API_KEY','ATTENTION_MEMORY_API_KEY','MEMORY_SERVICE_KEY'):
        monkeypatch.setenv(name,'secret')
    env=provider_environment(tmp_path,'codex')
    assert env['HOME']
    assert all(name not in env for name in ('LITELLM_KEY','PARCC_API_KEY','ATTENTION_MEMORY_API_KEY','MEMORY_SERVICE_KEY'))


def test_eval_cache_key_separates_sources(tmp_path):
    evidence={'frames':[{'view':'overview','filename':'frame.jpg','sha256':'a'*64,'data_url':'data:image/jpeg;base64,/9g='}]}
    class Fake:
        timeout_seconds=90
        def chat(self,**kw):
            assert kw.get('reasoning_effort')==('high' if kw['model']=='gpt-6-astra' else None)
            return ParccResponse(kw['model'],'{"passed": false, "reason": "frame #1 shows no motion."}',None,0.1,1,
                                 {'prompt_tokens':3,'completion_tokens':4,'total_tokens':7})
    parcc=eval_agent.ask(evidence,eval_agent.default_config(True),Fake(),cache_dir=tmp_path)
    codex=eval_agent.ask(evidence,eval_agent.default_config(True,'codex'),Fake(),cache_dir=tmp_path)
    assert parcc['cache_key']!=codex['cache_key']
    assert codex['model_source']=='codex'


def test_eval_cache_key_separates_reasoning_effort(tmp_path):
    evidence={'frames':[{'view':'overview','filename':'frame.jpg','sha256':'a'*64,'data_url':'data:image/jpeg;base64,/9g='}]}
    class Fake:
        timeout_seconds=90
        def chat(self,**kw):
            return ParccResponse(kw['model'],'{"passed": false, "reason": "frame #1 shows no motion."}',None,0.1,1,
                                 {'prompt_tokens':3,'completion_tokens':4,'total_tokens':7})
    config=eval_default(True,'codex')
    high=eval_agent.ask(evidence,config,Fake(),cache_dir=tmp_path)
    low=eval_agent.ask(evidence,{**config,'reasoning_effort':'low'},Fake(),cache_dir=tmp_path)
    assert high['cache_key']!=low['cache_key']


def test_advisor_cache_key_separates_sources(tmp_path):
    frames=[{'view':'external','filename':'frame.jpg','sha256':'a'*64,'data_url':'data:image/jpeg;base64,/9g='}]
    request=build_request_view(task={'task_id':'fake','task_prompt':'Move object'},question='What happened?',frames=frames,shown_video_seconds=1)
    class Fake:
        timeout_seconds=90
        def chat(self,**kw):
            assert kw.get('reasoning_effort')==('high' if kw['model']=='gpt-6-astra' else None)
            return ParccResponse(kw['model'],'Retry the motion.',None,0.1,1,
                                 {'prompt_tokens':3,'completion_tokens':4,'total_tokens':7})
    parcc=dev_advisor_proxy.ask(request,advisor_default(True),Fake(),cache_dir=tmp_path)
    codex=dev_advisor_proxy.ask(request,advisor_default(True,'codex'),Fake(),cache_dir=tmp_path)
    assert parcc['cache_key']!=codex['cache_key']
    assert codex['model_source']=='codex'


def test_advisor_cache_key_separates_reasoning_effort(tmp_path):
    frames=[{'view':'external','filename':'frame.jpg','sha256':'a'*64,'data_url':'data:image/jpeg;base64,/9g='}]
    request=build_request_view(task={'task_id':'fake','task_prompt':'Move object'},question='What happened?',frames=frames,shown_video_seconds=1)
    class Fake:
        timeout_seconds=90
        def chat(self,**kw):return ParccResponse(kw['model'],'Retry the motion.',None,0.1,1,
                                                  {'prompt_tokens':3,'completion_tokens':4,'total_tokens':7})
    config=advisor_default(True,'codex')
    high=dev_advisor_proxy.ask(request,config,Fake(),cache_dir=tmp_path)
    low=dev_advisor_proxy.ask(request,{**config,'reasoning_effort':'low'},Fake(),cache_dir=tmp_path)
    assert high['cache_key']!=low['cache_key']


def test_codex_manifest_and_task_configs_are_pinned(tmp_path,monkeypatch):
    from openarm365_dev_v2 import dev_config
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    selection={'request_key':'codex-manifest-test','tasks':['open_cabinet'],'seeds':[101],
               'policies':['autonomous'],'mode':'interactive','budget_tier':'dev',
               'model_source':'codex','max_versions':3}
    result=create(store,tmp_path/'artifacts',selection)
    manifest=verify_manifest(result)
    assert manifest['model_source']==manifest['selection']['model_source']=='codex'
    assert manifest['model']=={**MODEL,'model':'gpt-6-astra','reasoning_effort':'high'}
    assert manifest['configs']['open_cabinet:101']['model_source']=='codex'
    assert manifest['configs']['open_cabinet:101']['eval_agent']['model']=='gpt-6-astra'
    assert manifest['configs']['open_cabinet:101']['eval_agent']['reasoning_effort']=='high'
    with pytest.raises(ValueError,match='Codex model source requires dev tier'):
        dev_config.validate_dev_config({**manifest['configs']['open_cabinet:101'],'budget_tier':'legacy'})
    with pytest.raises(ValueError,match='Codex model source requires dev tier'):
        dev_config.validate_dev_config(manifest['configs']['open_cabinet:101'],budget_boundary='formal')


def test_explicit_parcc_reuses_byte_identical_default_manifest(tmp_path,monkeypatch):
    from openarm365_dev_v2 import dev_config
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    selection={'request_key':'parcc-default-manifest','tasks':['open_cabinet'],'seeds':[101],
               'policies':['autonomous'],'mode':'interactive','budget_tier':'dev','max_versions':3}
    first=create(store,tmp_path/'artifacts',selection)
    from pathlib import Path
    original=Path(first['manifest']['uri']).read_bytes()
    second=create(store,tmp_path/'artifacts',{**selection,'model_source':'parcc'})
    assert Path(second['manifest']['uri']).read_bytes()==original
    manifest=verify_manifest(second)
    assert manifest.get('model_source','parcc')=='parcc'
    assert 'model_source' not in manifest
    assert manifest['model']['provider_retries']==0
    assert manifest['connectivity_retry_max_attempts']==5


def test_codex_dev_call_uses_contained_worker_even_without_planner(tmp_path,monkeypatch):
    from openarm365_dev_v2 import dev_config
    from benchmarks.attention_harness import dev_v2_loop as loop
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    result=create(store,tmp_path/'artifacts',{'request_key':'codex-contained-call','tasks':['open_cabinet'],
        'seeds':[101],'policies':['autonomous'],'mode':'interactive','budget_tier':'dev',
        'model_source':'codex','max_versions':3})
    key=result['cells'][0]['key'];owner=loop.process_identity()
    store.update_cell(key,state='running',owner=owner)
    directory=tmp_path/'call';directory.mkdir()
    seen=[]
    def contained(store,key,kind,request,directory,deadline):
        body=json.loads(__import__('pathlib').Path(request['uri']).read_text())
        seen.append((kind,body['model_source'],body['model'],body['reasoning_effort']))
        response=ParccResponse('gpt-6-astra','hint',None,.1,1,{'prompt_tokens':15000,'completion_tokens':3,'total_tokens':15003})
        loop.immutable(directory/(kind+'-provider-response.json'),{'http_status':200,'request_id':None,
            'body':{'choices':[{'message':{'content':'hint'}}],'usage':response.usage}})
        loop.immutable(directory/(kind+'-output.json'),{**response.artifact(),'model_source':'codex'})
        return response
    monkeypatch.setattr(loop,'contained_provider',contained)
    answer,_,_,usage=loop.provider_call(store,key,1,'advisor',[{'role':'user','content':'{}'}],owner,directory)
    assert seen==[('advisor','codex','gpt-6-astra','high')]
    assert answer=='hint' and usage['total_tokens']==15003


@pytest.mark.parametrize('injected',[False,True])
def test_nonplanner_parcc_dev_call_streams_unless_client_injected(tmp_path,monkeypatch,injected):
    from openarm365_dev_v2 import dev_config
    from benchmarks.attention_harness import dev_v2_loop as loop
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    result=create(store,tmp_path/'artifacts',{'request_key':'parcc-stream-call','tasks':['open_cabinet'],
        'seeds':[101],'policies':['autonomous'],'mode':'interactive','max_versions':3})
    key=result['cells'][0]['key'];owner=loop.process_identity()
    store.update_cell(key,state='running',owner=owner)
    directory=tmp_path/'call';directory.mkdir()
    constructed=[]
    class Fake:
        def __init__(self,**kwargs):
            constructed.append(kwargs)
            self.response_observer=None
        def chat(self,**kwargs):
            usage={'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}
            self.response_observer(200,{},json.dumps({'usage':usage}).encode())
            return ParccResponse(kwargs['model'],'hint',None,.1,1,usage)
    monkeypatch.setattr(loop,'ParccClient',Fake)
    monkeypatch.setattr(loop,'contained_provider',lambda *args:pytest.fail('non-planner call used worker'))
    supplied=Fake() if injected else None
    answer,_,_,usage=loop.provider_call(store,key,1,'advisor',[{'role':'user','content':'{}'}],owner,directory,supplied)
    assert answer=='hint' and usage['total_tokens']==3
    assert len(constructed)==1
    if injected:assert constructed[0]=={}
    else:
        assert constructed[0]['stream'] is True and constructed[0]['max_attempts']==5
        assert constructed[0]['timeout_seconds']>0


def test_planner_parcc_dev_call_keeps_contained_worker(tmp_path,monkeypatch):
    from openarm365_dev_v2 import dev_config
    from benchmarks.attention_harness import dev_v2_loop as loop
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    result=create(store,tmp_path/'artifacts',{'request_key':'planner-contained-call','tasks':['open_cabinet'],
        'seeds':[101],'policies':['autonomous'],'mode':'interactive','max_versions':3})
    key=result['cells'][0]['key'];owner=loop.process_identity()
    store.update_cell(key,state='running',owner=owner,planner_features={'background_seconds':1})
    directory=tmp_path/'call';directory.mkdir()
    class UnusedClient:
        def __init__(self,**kwargs):pass
        def chat(self,**kwargs):pytest.fail('planner used in-process PARCC chat')
    monkeypatch.setattr(loop,'ParccClient',UnusedClient)
    seen=[]
    def contained(_store,_key,kind,request,_directory,deadline):
        seen.append((kind,deadline))
        response=ParccResponse('parcc/GLM','source',None,.1,1,{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3})
        loop.immutable(directory/(kind+'-output.json'),response.artifact())
        return response
    monkeypatch.setattr(loop,'contained_provider',contained)
    answer,_,_,_=loop.provider_call(store,key,1,'model',[{'role':'user','content':'{}'}],owner,directory)
    assert answer=='source' and len(seen)==1 and seen[0][0]=='model'


def test_parcc_eval_advisor_proxy_streams_and_honors_injected_client(monkeypatch):
    from benchmarks.attention_harness import dev_v2_loop as loop
    seen=[]
    class Fake:
        def __init__(self,**kwargs):seen.append(kwargs)
    monkeypatch.setattr(loop,'ParccClient',Fake)
    assert loop.proxy_client(None,{'model_source':'parcc','budget_tier':'dev'},'parcc/Qwen') is not None
    assert seen[0]['stream'] is True and seen[0]['max_attempts']==5
    assert loop.proxy_client(None,{'model_source':'parcc','budget_tier':'dev'},'paid/model') is not None
    assert seen[1]['max_attempts']==1
    assert loop.proxy_client(None,{'model_source':'parcc'},'parcc/Qwen') is not None
    assert seen[2]['max_attempts']==1
    injected=object()
    assert loop.proxy_client(injected,{'model_source':'parcc'}) is injected


def test_inprocess_retry_writes_ledger_event_and_output_attempts(tmp_path,monkeypatch):
    from openarm365_dev_v2 import dev_config
    from benchmarks.attention_harness import dev_v2_loop as loop
    from benchmarks.attention_harness.parcc_client import ParccClient
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    result=create(store,tmp_path/'artifacts',{'request_key':'parcc-retry-event','tasks':['open_cabinet'],
        'seeds':[101],'policies':['autonomous'],'mode':'interactive','max_versions':3})
    key=result['cells'][0]['key'];owner=loop.process_identity()
    store.update_cell(key,state='running',owner=owner)
    directory=tmp_path/'call';directory.mkdir()
    calls=[]
    def transport(*_args):
        calls.append(1)
        if len(calls)==1:return 503,{},b'{"error":{"type":"no_db_connection"}}'
        return 200,{},b'{"choices":[{"message":{"content":"OK"}}],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}'
    provider=ParccClient(api_key='fixture',max_attempts=5,retry_delay_seconds=0,transport=transport)
    answer,_,output,_=loop.provider_call(store,key,1,'advisor',[{'role':'user','content':'{}'}],owner,directory,provider)
    assert answer=='OK' and len(calls)==2
    assert json.loads((directory/'advisor-output.json').read_text())['failed_attempts'][0]['reason']=='http_503_no_db'
    with store.tx() as db:
        rows=[json.loads(row[0]) for row in db.execute("SELECT payload FROM v2_events WHERE kind='provider_retry'")]
    assert len(rows)==1 and rows[0]['version']==1 and rows[0]['kind']=='advisor' and rows[0]['attempt']==2


def test_partial_200_with_usage_remains_needs_review(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from openarm365_dev_v2 import dev_config
    from benchmarks.attention_harness import dev_v2_loop as loop
    from benchmarks.attention_harness.parcc_client import ParccClient,ParccError
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    result=create(store,tmp_path/'artifacts',{'request_key':'parcc-partial-review','tasks':['open_cabinet'],
        'seeds':[101],'policies':['autonomous'],'mode':'interactive','max_versions':3})
    key=result['cells'][0]['key'];owner=loop.process_identity()
    store.update_cell(key,state='running',owner=owner)
    directory=tmp_path/'call';directory.mkdir()
    calls=[]
    @contextmanager
    def transport(*_args):
        calls.append(1)
        yield 200,{},iter([b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
                           b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":1,"total_tokens":3}}\n']),lambda _seconds:None
    provider=ParccClient(api_key='fixture',max_attempts=5,stream=True,stream_transport=transport)
    with pytest.raises(ParccError,match='before \\[DONE\\]'):
        loop.provider_call(store,key,1,'advisor',[{'role':'user','content':'{}'}],owner,directory,provider)
    assert len(calls)==1
    assert loop.job_for(store.cell(key),1,'advisor')['state']=='needs_review'
    assert not (directory/'advisor-output.json').exists()


def test_missing_stream_usage_is_held_without_retry(tmp_path,monkeypatch):
    from contextlib import contextmanager
    from openarm365_dev_v2 import dev_config
    from benchmarks.attention_harness import dev_v2_loop as loop
    from benchmarks.attention_harness.parcc_client import ParccClient
    from benchmarks.attention_harness.core.dev_store import DevConflict
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    result=create(store,tmp_path/'artifacts',{'request_key':'parcc-missing-usage','tasks':['open_cabinet'],
        'seeds':[101],'policies':['autonomous'],'mode':'interactive','max_versions':3})
    key=result['cells'][0]['key'];owner=loop.process_identity()
    store.update_cell(key,state='running',owner=owner)
    directory=tmp_path/'call';directory.mkdir()
    calls=[]
    @contextmanager
    def transport(*_args):
        calls.append(1)
        yield 200,{},iter([b'data: {"choices":[{"delta":{"content":"OK"}}]}\n',
                           b'data: [DONE]\n']),lambda _seconds:None
    provider=ParccClient(api_key='fixture',max_attempts=5,stream=True,stream_transport=transport)
    with pytest.raises(DevConflict,match='usage missing'):
        loop.provider_call(store,key,1,'advisor',[{'role':'user','content':'{}'}],owner,directory,provider)
    assert len(calls)==1
    assert loop.job_for(store.cell(key),1,'advisor')['state']=='needs_review'


def test_five_unreachable_attempts_hold_claim_for_review(tmp_path,monkeypatch):
    from urllib.error import URLError
    from openarm365_dev_v2 import dev_config
    from benchmarks.attention_harness import dev_v2_loop as loop,parcc_client
    from benchmarks.attention_harness.parcc_client import ParccClient,ParccError
    monkeypatch.setattr(dev_config,'model_sources',lambda **kwargs:{})
    monkeypatch.setattr(parcc_client.time,'sleep',lambda _delay:None)
    store=DevV2Store(tmp_path/'ledger.sqlite3')
    result=create(store,tmp_path/'artifacts',{'request_key':'parcc-five-review','tasks':['open_cabinet'],
        'seeds':[101],'policies':['autonomous'],'mode':'interactive','max_versions':3})
    key=result['cells'][0]['key'];owner=loop.process_identity()
    store.update_cell(key,state='running',owner=owner)
    directory=tmp_path/'call';directory.mkdir()
    calls=[]
    def transport(*_args):
        calls.append(1)
        raise URLError('offline')
    provider=ParccClient(api_key='fixture',max_attempts=5,retry_delay_seconds=0,transport=transport)
    with pytest.raises(ParccError,match='unreachable after 5 attempts'):
        loop.provider_call(store,key,1,'advisor',[{'role':'user','content':'{}'}],owner,directory,provider)
    assert len(calls)==5
    assert loop.job_for(store.cell(key),1,'advisor')['state']=='needs_review'
    assert len(json.loads((directory/'advisor-attempts.json').read_text()))==5


@pytest.mark.parametrize('source,expected', [('codex','CodexClient'),('parcc','ParccClient')])
def test_worker_selects_client_without_real_provider(tmp_path,monkeypatch,source,expected):
    request=tmp_path/'request.json';request.write_text(json.dumps({'model_source':source,'model':'gpt-6-astra' if source=='codex' else 'parcc/GLM',
        'messages':[{'role':'user','content':'hello'}],'max_tokens':8,'temperature':0,'reasoning_effort':'high' if source=='codex' else 'low'}))
    seen=[]
    class Fake:
        def __init__(self,**kw):
            seen.append((expected,kw))
            self.response_observer=kw['response_observer']
            self.quota_before={'used_percent':1,'timestamp':'before'}
            self.quota_after={'used_percent':2,'timestamp':'after'}
        def chat(self,**kw):
            assert kw['reasoning_effort']==('high' if source=='codex' else 'low')
            body={'choices':[{'message':{'content':'done'}}],'usage':{'prompt_tokens':2,'completion_tokens':1,'total_tokens':3}}
            self.response_observer(200,{},json.dumps(body).encode())
            return ParccResponse(kw['model'],'done',None,0.1,1,body['usage'])
    monkeypatch.setattr(dev_provider_worker,expected,Fake)
    monkeypatch.setattr(sys,'argv',['worker','--request',str(request),'--directory',str(tmp_path),'--kind','model','--deadline',str(__import__('time').time()+10)])
    dev_provider_worker.main()
    assert seen and seen[0][0]==expected
    assert seen[0][1]['response_observer']
    if source=='parcc':assert seen[0][1]['stream'] is True
    output=json.loads((tmp_path/'model-output.json').read_text())
    assert output['model']==('gpt-6-astra' if source=='codex' else 'parcc/GLM')
    assert ('quota_before' in output)==(source=='codex')


@pytest.fixture(autouse=True)
def offline_r4_snapshot(r4_deployment):
    """New dev defaults bind an isolated authenticated snapshot."""
