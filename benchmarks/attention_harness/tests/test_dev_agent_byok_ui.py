"""Provider HTTP contract and full offline run flow through the real UI handler."""
import io
import json
import re
import secrets
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
import pytest
from benchmarks.attention_harness import dev_ui_server as ui,dev_agent_provider as providers,dev_agent_key_vault as vault_module
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.dev_agent_key_vault import ProviderKeyVault,launch_run_with_keys
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.tests.fakes.byok_http import MemoryServer,exchange,loopback
from benchmarks.attention_harness.tests.fakes.fake_provider import FakeProvider
from benchmarks.attention_harness.tests.c21_fixtures import base,r4_deployment,offline_dev_identity


def control(server):
    status,body,headers=exchange(server,'GET','/')
    assert status==200
    return re.search(rb'name="control-token" content="([^"]+)"',body)[1].decode()


def server(tmp_path,monkeypatch,**kwargs):
    monkeypatch.setattr(ui,'ThreadingHTTPServer',MemoryServer)
    return ui.serve(DevV2Store(tmp_path/'db'),tmp_path/'runs',0,**kwargs)


def selection():
    return dict(dev_mode='agent',agent_policy_version='agent-policy-v1',request_key='byok-ui-test',tasks=['open_cabinet'],seeds=[102],
                policies=['autonomous'],mode='interactive',agent_provider=dict(roles=dict(dev=dict(credential='mine',provider='openai',model='fake'))))


def test_credentials_contract(tmp_path,monkeypatch):
    key=Secret('sk-'+secrets.token_urlsafe(32));vault=ProviderKeyVault();service=server(tmp_path,monkeypatch,vault=vault)
    token=control(service);responses=[]
    def call(method,path,body=None,**kwargs):
        status,raw,head=exchange(service,method,path,body,{'X-Attention-Token':token},**kwargs)
        responses.extend([raw,head]);assert b'Cache-Control: no-store' in head
        return status,json.loads(raw)
    assert exchange(service,'GET','/api/provider/presets')[0]==403
    assert call('GET','/api/provider/presets')[0]==200
    status,value=call('PUT','/api/provider/credentials/mine',dict(provider='openai',key=key.reveal()))
    assert status==200 and value['key_set'] and set(value)=={'ok','credential_id','provider','key_set','expires_at'}
    assert call('GET','/api/provider/credentials')[1][0]['credential_id']=='mine'
    assert set(call('GET','/api/provider/credentials/mine')[1][0])=={'credential_id','provider','base_url_host','expires_at'}
    status,value=call('PUT','/api/provider/credentials/mine',raw=b'bad json')
    assert status==400 and set(value['error'])=={'code','message','redacted'}
    assert call('PUT','/api/provider/credentials/mine',raw=b' '*8193)[0]==400
    assert call('PUT','/api/provider/credentials/mine',dict(provider='openai-compatible',key=key.reveal(),base_url='http://127.0.0.1/v1'))[1]['error']['code']=='custom_base_url_disabled'
    assert call('DELETE','/api/provider/credentials/mine')[0]==200
    assert call('GET','/api/provider/credentials')[1]==[]
    call('PUT','/api/provider/credentials/mine',dict(provider='openai',key=key.reveal()))
    assert call('DELETE','/api/provider/credentials')[1]['cleared']==1
    assert not scan([key],roots=[tmp_path],sqlite_paths=[tmp_path/'db'],extra_blobs=responses)


@pytest.mark.parametrize('masked',[False,True])
def test_ui_test_error_redaction(tmp_path,monkeypatch,masked):
    key=Secret('sk-'+secrets.token_urlsafe(32));service=server(tmp_path,monkeypatch);token=control(service)
    headers={'X-Attention-Token':token}
    assert exchange(service,'PUT','/api/provider/credentials/mine',dict(provider='openai',key=key.reveal()),headers)[0]==200
    def fail(req,**kw):
        message='Incorrect API key provided: sk-xxxx****abcd' if masked else key.reveal()
        raise urllib.error.HTTPError(req.full_url,401,'bad',{},io.BytesIO(message.encode()))
    monkeypatch.setattr(providers,'open_request',fail)
    status,body,head=exchange(service,'POST','/api/provider/credentials/mine/test',dict(model='m'),headers)
    value=json.loads(body)
    assert status==200 and not value['ok'] and value['errors'][0]['redacted']
    assert b'[REDACTED:key]' in body
    assert not scan([key],roots=[tmp_path],extra_blobs=[body,head])


def test_start_missing_preflight_and_pipe_branch(tmp_path,monkeypatch,offline_dev_identity):
    key=Secret('sk-'+secrets.token_urlsafe(32));service=server(tmp_path,monkeypatch);token=control(service);headers={'X-Attention-Token':token}
    replies=[]
    def call(method,path,body):
        status,raw,head=exchange(service,method,path,body,headers);replies.extend([raw,head]);return status,json.loads(raw)
    status,exp=call('POST','/api/dev/experiments',selection());assert status==201,exp
    path='/api/dev/experiments/'+exp['experiment_id']+'/run'
    status,value=call('POST',path,dict(max_cells=1))
    assert status==409 and value['error']['code']=='provider_key_missing' and 'dev' in value['error']['message']
    call('PUT','/api/provider/credentials/mine',dict(provider='openai',key=key.reveal()))
    def fail(req,**kw):raise urllib.error.HTTPError(req.full_url,401,'bad',{},io.BytesIO(key.reveal().encode()))
    monkeypatch.setattr(providers,'open_request',fail)
    status,value=call('POST',path,dict(max_cells=1))
    assert status==409 and value['error']['code']=='provider_preflight_failed' and value['error']['redacted']
    def good(req,**kw):
        if req.data is None:return io.BytesIO(b'{"data":[]}')
        return io.BytesIO(b'data: {"choices":[{"delta":{"tool_calls":[{"function":{"name":"ping"}}]}}]}\n\ndata: [DONE]\n')
    monkeypatch.setattr(providers,'open_request',good)
    launched=[]
    def launch(argv,keys,**kwargs):
        assert keys._keys['mine']==key and not set(kwargs['env'])&set(vault_module.KEY_ENV)
        code="from benchmarks.attention_harness.dev_agent_key_vault import install_run_keys_from_fd,run_key;import sys;install_run_keys_from_fd(int(sys.argv[-1]));assert run_key('mine')"
        process=launch_run_with_keys([sys.executable,'-B','-c',code],keys,**kwargs);launched.append(process);return process
    monkeypatch.setattr(vault_module,'launch_run_with_keys',launch)
    # serve captured its launcher on construction, so rebind a fresh real handler.
    service=server(tmp_path,monkeypatch,vault=ProviderKeyVault());token=control(service);headers['X-Attention-Token']=token
    call('PUT','/api/provider/credentials/mine',dict(provider='openai',key=key.reveal()))
    status,value=call('POST',path,dict(max_cells=1))
    assert status==202 and value['worker_started']
    assert launched[0].wait(timeout=15)==0
    assert not scan([key],roots=[tmp_path],sqlite_paths=[tmp_path/'db'],extra_blobs=replies)


def test_end_to_end_loopback_canary(tmp_path,monkeypatch,loopback,offline_dev_identity):
    key=Secret('sk-'+secrets.token_urlsafe(32));responses=[];processes=[]
    def launch(argv,keys,**kwargs):
        script=Path(__file__).parent/'fakes/byok_run.py'
        process=launch_run_with_keys([sys.executable,'-B',str(script),'--output',str(tmp_path/'run-output'),'--allow-private-provider-hosts',argv[argv.index('--allow-private-provider-hosts')+1]],keys,**kwargs)
        processes.append(process);return process
    monkeypatch.setattr(vault_module,'launch_run_with_keys',launch)
    with FakeProvider(key.reveal()) as fake:
        service=ui.serve(DevV2Store(tmp_path/'db'),tmp_path/'runs',0,allow_custom=True,allow_private_hosts=('127.0.0.1',))
        thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
        base_url='http://127.0.0.1:%s'%service.server_port
        def request(method,path,body=None,token=None):
            req=urllib.request.Request(base_url+path,data=None if body is None else json.dumps(body).encode(),method=method,
                headers={'Content-Type':'application/json',**({'X-Attention-Token':token} if token else {})})
            try:response=urllib.request.urlopen(req,timeout=15)
            except urllib.error.HTTPError as exc:response=exc
            with response:raw=response.read();responses.extend([str(response.headers),raw]);return response.status,raw
        try:
            _,html=request('GET','/');token=re.search(rb'name="control-token" content="([^"]+)"',html)[1].decode()
            assert request('PUT','/api/provider/credentials/mine',dict(provider='openai-compatible',key=key.reveal(),base_url=fake.base_url),token)[0]==200
            status,raw=request('POST','/api/provider/credentials/mine/test',dict(model='fake-model'),token)
            assert status==200 and json.loads(raw)['ok']
            chosen=selection();chosen['agent_provider']['roles']['dev'].update(provider='openai-compatible',model='fake-model')
            status,raw=request('POST','/api/dev/experiments',chosen,token);assert status==201
            eid=json.loads(raw)['experiment_id']
            status,_=request('POST','/api/dev/experiments/'+eid+'/run',dict(max_cells=1),token);assert status==202
            assert processes[0].wait(timeout=30)==0
            assert len(fake.authorizations)>=7 and all(h=='Bearer '+key.reveal() for h in fake.authorizations)
            assert not scan([key],roots=[tmp_path],sqlite_paths=[tmp_path/'db',tmp_path/'run-output/run.sqlite'],extra_blobs=responses)
        finally:
            for process in processes:
                if process.poll() is None:process.kill();process.wait()
            service.shutdown();service.server_close();thread.join()


def test_two_owner_concurrent_runs(loopback,tmp_path):
    keys=[Secret('sk-'+secrets.token_urlsafe(32)) for _ in range(2)]
    vault=ProviderKeyVault();processes=[]
    with FakeProvider(keys[0].reveal()) as a,FakeProvider(keys[1].reveal()) as b:
        for i,fake in enumerate((a,b)):
            owner=str(i);vault.put(owner,'mine','openai-compatible',keys[i],base_url=fake.base_url)
            bundle=vault.take_for_run(owner,{'dev':dict(credential='mine',provider='openai-compatible',model='fake-model')})
            script=Path(__file__).parent/'fakes/byok_run.py'
            process=launch_run_with_keys([sys.executable,'-B',str(script),'--output',str(tmp_path/owner),'--allow-private-provider-hosts','127.0.0.1'],bundle,
                                         env=vault_module.keyless_env(__import__('os').environ),stdout=__import__('subprocess').PIPE,stderr=__import__('subprocess').PIPE)
            processes.append(process)
        try:
            for process in processes:
                out,err=process.communicate(timeout=30)
                assert process.returncode==0 and not out and not err
            assert all(h=='Bearer '+keys[0].reveal() for h in a.authorizations)
            assert all(h=='Bearer '+keys[1].reveal() for h in b.authorizations)
            for i in range(2):assert not scan(keys,roots=[tmp_path/str(i)],sqlite_paths=[tmp_path/str(i)/'run.sqlite'])
        finally:
            for process in processes:
                if process.poll() is None:process.kill();process.wait()


def test_ui_rejects_unsupported_lab_eval_role(tmp_path,monkeypatch):
    service=server(tmp_path,monkeypatch);headers={'X-Attention-Token':control(service)}
    value=selection();value['agent_provider']['roles']['eval']=dict(credential='lab',provider='deepseek',model='gpt-6.1-sol')
    status,body,_=exchange(service,'POST','/api/dev/experiments',value,headers)
    assert status==400 and json.loads(body)['error']=='lab_provider_not_supported'
