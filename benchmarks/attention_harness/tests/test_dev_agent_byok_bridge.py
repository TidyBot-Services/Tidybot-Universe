"""Authenticated bridge error boundaries and Codex command/environment isolation."""
import io
import json
import os
import secrets
import urllib.error
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness import dev_agent_parcc_bridge as bridge_module
from benchmarks.attention_harness.dev_agent_parcc_bridge import ChatBridge,provider_args,to_chat
from benchmarks.attention_harness.dev_agent_provider import presets
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession
from benchmarks.attention_harness.dev_agent_key_vault import KEY_ENV
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.tests.fakes.byok_http import MemoryServer,exchange


@pytest.fixture
def memory_bridge(monkeypatch):
    monkeypatch.setattr(bridge_module.http.server,'ThreadingHTTPServer',MemoryServer)


def upstream(chat,key,**kwargs):
    yield dict(_request_id='test-request')
    yield dict(model=chat['model'],choices=[dict(delta=dict(content='ok'),finish_reason='stop')])


def test_bridge_authorization_and_token_isolation(tmp_path,memory_bridge):
    a=ChatBridge(tmp_path/'a',presets()['openai'],Secret(secrets.token_urlsafe()),token=secrets.token_urlsafe(),upstream=upstream)
    b=ChatBridge(tmp_path/'b',presets()['deepseek'],Secret(secrets.token_urlsafe()),token=secrets.token_urlsafe(),upstream=upstream)
    try:
        for bridge in (a,b):
            for auth in ('','wrong',b.token if bridge is a else a.token):
                status,body,_=exchange(bridge.server,'POST','/v1/responses',dict(model='fake',input=[]),{'Authorization':'Bearer '+auth})
                assert status==401 and bridge.token.encode() not in body
            assert exchange(bridge.server,'POST','/v1/other',{})[0]==404
            status,body,_=exchange(bridge.server,'POST','/v1/responses',dict(model='fake',input=[]),{'Authorization':'Bearer '+bridge.token})
            assert status==200 and b'response.completed' in body
    finally:a.close();b.close()


@pytest.mark.parametrize('provider,param,reasoning',[('parcc','max_tokens',True),('openai','max_completion_tokens',True),('deepseek','max_tokens',False)])
def test_token_mapping(provider,param,reasoning):
    result=to_chat(dict(model='fake',input=[],reasoning=dict(effort='high')),presets()[provider])
    assert result[param]==32768 and ('reasoning_effort' in result)==reasoning
    assert (set(result)&{'max_tokens','max_completion_tokens'})=={param}


@pytest.mark.parametrize('mode',['echo_key_401','mask_key_401','exception','stream_failed'])
def test_bridge_scrubs_all_error_sinks(tmp_path,memory_bridge,mode):
    key=Secret('sk-'+secrets.token_urlsafe(32));token=secrets.token_urlsafe(32)
    def failing(chat,secret,**kwargs):
        if mode=='stream_failed':yield dict(_request_id='request')
        if mode in ('exception','stream_failed'):raise RuntimeError(secret.reveal())
        raw=secret.reveal() if mode=='echo_key_401' else 'Incorrect API key provided: sk-xxxx****abcd'
        raise urllib.error.HTTPError('http://127.0.0.1',401,'bad',{},io.BytesIO(raw.encode()))
    bridge=ChatBridge(tmp_path,presets()['openai'],key,token=token,upstream=failing)
    try:status,body,headers=exchange(bridge.server,'POST','/v1/responses',dict(model='m',input=[]),{'Authorization':'Bearer '+token})
    finally:bridge.close()
    row=json.loads((tmp_path/'calls.jsonl').read_bytes())
    assert row['redacted'] and '[REDACTED:key]' in row['error']
    assert b'[REDACTED:key]' in body and b'"redacted": true' in body
    assert status==(200 if mode=='stream_failed' else 502 if mode=='exception' else 400)
    assert not scan([key],roots=[tmp_path],extra_blobs=[body,headers])


def test_provider_args_legacy_bytes():
    # Literal copy of the GL (db4501c) provider_args output for base_url=http://127.0.0.1:1/v1.
    assert provider_args('http://127.0.0.1:1/v1')==[
        '-c','model_providers.parcc={ name="PARCC (bridge)", base_url="http://127.0.0.1:1/v1", wire_api="responses", '
             'stream_idle_timeout_ms = 900000, request_max_retries = 4, stream_max_retries = 2 }',
        '-c','model_provider="parcc"','-c','model_context_window=262144']
    assert 'env_key="BRIDGE_TOKEN"' in provider_args('http://127.0.0.1:1/v1',token_env='BRIDGE_TOKEN')[1]


def test_session_command_and_child_environment(tmp_path,memory_bridge,monkeypatch):
    key=Secret('sk-'+secrets.token_urlsafe(32));token=secrets.token_urlsafe(32)
    provider=SimpleNamespace(spec=presets()['openai'],secret=key,token=token,token_env='ATTENTION_BYOK_BRIDGE_TOKEN')
    factory=lambda ws,prompt:['codex','exec','-C',str(ws),'--json','-m','old','-c','model_reasoning_effort="low"',prompt]
    (tmp_path/'private').mkdir()
    session=CodexAgentSession(tmp_path,tmp_path/'private',model='m',provider=provider,command_factory=factory)
    observed=[]
    class Process:
        pid=99999999
        def __init__(self,argv,**kw):
            observed.append((argv,kw));self.stdin=io.BytesIO()
            self.stdout=io.BytesIO(b'{"type":"thread.started","thread_id":"fake"}\n{"type":"turn.completed"}\n')
        def wait(self):return 0
    monkeypatch.setattr('benchmarks.attention_harness.dev_agent_session.subprocess.Popen',Process)
    for name in KEY_ENV:monkeypatch.setenv(name,secrets.token_urlsafe())
    list(session.start('Hello'))
    argv,kw=observed[0]
    assert kw['env'][provider.token_env]==token and not (set(kw['env'])&set(KEY_ENV))
    assert token not in ' '.join(argv) and session.bridge is None
    assert not scan([key],roots=[tmp_path],extra_blobs=[json.dumps(argv),json.dumps(kw['env'])])


def test_disconnected_client_does_not_print_chained_secret(tmp_path,memory_bridge,capsys):
    key=Secret('sk-'+secrets.token_urlsafe(32))
    def failure(*a,**kw):raise RuntimeError(key.reveal())
    bridge=ChatBridge(tmp_path,presets()['openai'],key,token=secrets.token_urlsafe(),upstream=failure)
    raw=('POST /v1/responses HTTP/1.0\r\nAuthorization: Bearer '+bridge.token+'\r\nContent-Length: 24\r\n\r\n{"model":"m","input":[]}').encode()
    class Connection:
        def makefile(self,*a):return io.BytesIO(raw)
        def sendall(self,data):raise BrokenPipeError()
    try:bridge.server.RequestHandlerClass(Connection(),('127.0.0.1',1),bridge.server)
    finally:bridge.close()
    out,err=capsys.readouterr()
    assert not out and not err
    assert not scan([key],roots=[tmp_path],extra_blobs=[out,err])
