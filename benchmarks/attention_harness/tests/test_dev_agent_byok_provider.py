"""Provider URL admission and preflight using memory and loopback transports."""
import io
import json
import secrets
import socket
import urllib.error
import pytest
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.dev_agent_provider import resolve_provider,probe,ProviderError,presets,_NoRedirect
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.tests.fakes.fake_provider import FakeProvider
from benchmarks.attention_harness.tests.fakes.byok_http import loopback


@pytest.mark.parametrize('url,code',[
    ('http://public.test/v1','base_url_not_https'),('https://user:pass@public.test/v1','base_url_invalid'),
    ('https://public.test/v1?a=1','base_url_invalid'),('https://public.test/v1#x','base_url_invalid'),
    ('https://public.test:bad/v1','base_url_invalid'),('https://public.test\\@other/v1','base_url_invalid'),
    ('https://127.0.0.1/v1','base_url_private_host'),('https://10.0.0.1/v1','base_url_private_host'),
    ('https://169.254.169.254/v1','base_url_private_host'),('https://0.0.0.0/v1','base_url_private_host'),
    ('https://[::1]/v1','base_url_private_host'),('https://[fc00::1]/v1','base_url_private_host'),
    ('https://[fe80::1]/v1','base_url_private_host'),('https://[::]/v1','base_url_private_host'),
    ('https://mixed.test/v1','base_url_private_host')])
def test_url_matrix(monkeypatch,url,code):
    def dns(host,*args,**kw):
        values=['93.184.216.34','127.0.0.1'] if host=='mixed.test' else ['93.184.216.34' if host=='public.test' else host]
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(v,443)) for v in values]
    monkeypatch.setattr(socket,'getaddrinfo',dns)
    with pytest.raises(ProviderError) as exc:resolve_provider('openai-compatible',url,allow_custom=True)
    assert exc.value.code==code


def test_provider_defaults_and_explicit_loopback(monkeypatch):
    monkeypatch.setattr(socket,'getaddrinfo',lambda host,*a,**k:[(2,1,6,'',('127.0.0.1' if host=='127.0.0.1' else '93.184.216.34',443))])
    with pytest.raises(ProviderError,match='disabled'):resolve_provider('openai-compatible','https://public.test/v1',allow_custom=False)
    with pytest.raises(ProviderError):resolve_provider('openai','https://public.test/v1',allow_custom=True)
    with pytest.raises(ProviderError):resolve_provider('missing',allow_custom=True)
    spec=resolve_provider('openai-compatible','http://127.0.0.1:12345/v1/',allow_custom=True,allow_private_hosts=('127.0.0.1',))
    assert spec.chat_url=='http://127.0.0.1:12345/v1/chat/completions'
    assert resolve_provider('openai',allow_custom=False).token_param=='max_completion_tokens'
    assert all(v=='unknown' for p in presets().values() for v in p.capabilities.values())
    assert _NoRedirect().redirect_request(None,None,302,'',{},'https://other.test') is None


@pytest.mark.parametrize('role',['dev','eval','advisor'])
def test_probe_transport_and_role_image(role):
    key=Secret('sk-'+secrets.token_urlsafe(32));seen=[]
    def transport(req,**kw):
        assert req.get_header('Authorization')=='Bearer '+key.reveal()
        body=json.loads(req.data) if req.data else None;seen.append(body)
        if body is None:return io.BytesIO(b'{"data":[]}')
        assert body['max_completion_tokens']==512
        assert body['tool_choice']=='auto'  # thinking models reject a forced named tool
        chunk=dict(choices=[dict(delta=dict(tool_calls=[dict(function=dict(name='ping',arguments='{}'))]))])
        return io.BytesIO(b'data: '+json.dumps(chunk).encode()+b'\n\ndata: [DONE]\n\n')
    result=probe(presets()['openai'],key,'m',role=role,transport=transport)
    assert result.ok and result.checks['vision']==('unknown' if role=='dev' else 'ok')
    content=seen[1]['messages'][0]['content']
    assert isinstance(content,str) if role=='dev' else content[1]['image_url']['url'].startswith('data:image/png;base64,')


@pytest.mark.parametrize('mode',['echo','mask','500','bad_json','drop','no_tools','exception','stream_error'])
def test_probe_failure_scrub(mode):
    key=Secret('sk-'+secrets.token_urlsafe(32))
    def transport(req,**kw):
        if mode in ('echo','mask','500'):
            raw=key.reveal() if mode=='echo' else 'Incorrect API key provided: sk-xxxx****abcd' if mode=='mask' else 'busy'
            raise urllib.error.HTTPError(req.full_url,500 if mode=='500' else 401,'failure',{},io.BytesIO(raw.encode()))
        if mode=='exception':raise RuntimeError(key.reveal())
        if req.data is None:return io.BytesIO(b'{"data":[]}')
        if mode=='bad_json':return io.BytesIO(b'data: invalid\n')
        if mode=='stream_error':return io.BytesIO(b'data: '+json.dumps(dict(error=key.reveal())).encode()+b'\n')
        return io.BytesIO(b'data: {"choices":[{"delta":{"content":"ok"}}]}\n'+(b'data: [DONE]\n' if mode=='no_tools' else b''))
    result=probe(presets()['openai'],key,'m',role='dev',transport=transport)
    assert not result.ok and result.errors
    assert not scan([key],roots=[],include_processes=False,extra_blobs=[json.dumps(result.errors)])
    if mode in ('echo','mask','exception','stream_error'):
        assert result.errors[0]['redacted'] and '[REDACTED:key]' in result.errors[0]['message']


@pytest.mark.parametrize('mode',[None,'echo_key_401','mask_key_401','http_500','drop_stream','bad_json','slow'])
def test_fake_provider_over_loopback(loopback,mode):
    key=Secret('sk-'+secrets.token_urlsafe(32))
    with FakeProvider(key.reveal(),mode=mode) as fake:
        spec=resolve_provider('openai-compatible',fake.base_url,allow_custom=True,allow_private_hosts=('127.0.0.1',))
        result=probe(spec,key,'fake-model',role='dev')
        assert result.ok==(mode in (None,'slow'))
        assert not scan([key],roots=[],include_processes=False,extra_blobs=[json.dumps(result.errors)])
        assert all(h=='Bearer '+key.reveal() for h in fake.authorizations)
