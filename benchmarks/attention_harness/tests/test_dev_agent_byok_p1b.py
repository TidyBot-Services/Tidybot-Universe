"""P1b service hardening, gateway isolation and explicit provider permissions."""
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import resource
import secrets
import subprocess
import sys
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness import dev_ui_server as ui, dev_agent_provider as providers, dev_agent_key_vault as keys
from benchmarks.attention_harness import dev_agent_loop as loop, dev_experiment as experiments
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.tests.fakes.byok_http import exchange, loopback
from benchmarks.attention_harness.tests.test_dev_agent_byok_ui import server, control, selection
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity


def canary():return Secret(secrets.token_urlsafe(40))


def signature(secret,owner,method,path,raw=b'',ts=1000):
    message='\n'.join((owner,str(ts),method,path,hashlib.sha256(raw).hexdigest()))
    return 'v1.%s.%s'%(ts,hmac.new(secret.reveal().encode(),message.encode(),hashlib.sha256).hexdigest())


def gateway(tmp_path,monkeypatch,**kwargs):
    secret=canary()
    service=server(tmp_path,monkeypatch,public_gateway_mode=True,gateway_secret=secret,gateway_clock=lambda:1000,**kwargs)
    def call(method,path,body=None,owner='alice',token=None,raw=None,**extra):
        raw=raw if raw is not None else (json.dumps(body).encode() if body is not None else b'')
        headers={'X-Attention-Owner':owner,'X-Attention-Gateway-Auth':signature(secret,owner,method,path,raw),**extra}
        if token:headers['X-Attention-Token']=token
        return exchange(service,method,path,headers=headers,raw=raw)
    _,html,_=call('GET','/')
    import re
    token=re.search(rb'name="control-token" content="([^"]+)"',html)[1].decode()
    return service,secret,token,call


def test_service_hardens_before_vault(tmp_path,monkeypatch):
    events=[]
    harden=ui.harden_process
    monkeypatch.setattr(ui,'harden_process',lambda:(events.append('harden'),harden()))
    original=keys.ProviderKeyVault
    monkeypatch.setattr(keys,'ProviderKeyVault',lambda:(events.append('vault'),original())[1])
    server(tmp_path,monkeypatch)
    assert events==['harden','vault']
    assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0)
    ui.harden_process()
    assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0)


def test_main_hardens_and_reads_secret_fd_once(tmp_path,monkeypatch):
    secret=canary();r,w=os.pipe();os.write(w,secret.reveal().encode());os.close(w)
    events=[];blobs=[]
    monkeypatch.setattr(ui,'harden_process',lambda:events.append('harden'))
    monkeypatch.setattr(ui,'DevV2Store',lambda path:events.append('store'))
    def serve(*args,**kwargs):
        events.append('serve');assert kwargs['gateway_secret']==secret
        assert kwargs['require_no_swap'] and kwargs['public_gateway_mode']
        with pytest.raises(OSError):os.fstat(r)
        return SimpleNamespace(serve_forever=lambda:events.append('serve_forever'))
    monkeypatch.setattr(ui,'serve',serve)
    monkeypatch.setattr(ui,'immutable',lambda path,value:blobs.append(json.dumps(value)))
    monkeypatch.setattr(sys,'argv',['dev_ui_server','--store',str(tmp_path/'db'),'--artifacts',str(tmp_path),'--port','0',
                                   '--public-gateway-mode','--gateway-secret-fd',str(r),'--require-no-swap'])
    ui.main()
    assert events==['harden','store','serve','serve_forever']
    assert not scan([secret],roots=[tmp_path],extra_blobs=blobs+[json.dumps(sys.argv),json.dumps(dict(os.environ))])


@pytest.mark.parametrize('raw',[b'',b'\xff'])
def test_gateway_fd_invalid_is_closed(raw):
    r,w=os.pipe();os.write(w,raw);os.close(w)
    with pytest.raises(ValueError,match='Invalid gateway secret fd'):ui.read_gateway_secret(r)
    with pytest.raises(OSError):os.fstat(r)


def test_gateway_requires_secret(tmp_path,monkeypatch):
    with pytest.raises(ValueError,match='requires a gateway secret'):server(tmp_path,monkeypatch,public_gateway_mode=True)


def test_swap_rechecked_on_every_put(tmp_path,monkeypatch):
    vault=keys.ProviderKeyVault();key=canary();reads=[]
    swaps=['Filename Type Size Used Priority\n','Filename Type Size Used Priority\n/swapfile file 99 0 -2\n']
    def reader():reads.append(True);return swaps[len(reads)-1]
    service=server(tmp_path,monkeypatch,vault=vault,require_no_swap=True,swaps_reader=reader)
    headers={'X-Attention-Token':control(service)};body=dict(provider='openai',key=key.reveal())
    assert exchange(service,'PUT','/api/provider/credentials/one',body,headers)[0]==200
    status,raw,head=exchange(service,'PUT','/api/provider/credentials/two',body,headers)
    assert status==400 and json.loads(raw)['error']==dict(code='swap_enabled_key_refused',message='host swap is enabled; key refused',redacted=False)
    assert len(reads)==2 and [r['credential_id'] for r in vault.list('local')]==['one']
    assert not scan([key],roots=[tmp_path],extra_blobs=[raw,head])


def test_swap_default_off(tmp_path,monkeypatch):
    def forbidden():pytest.fail('swap reader must be opt-in')
    service=server(tmp_path,monkeypatch,swaps_reader=forbidden)
    assert exchange(service,'PUT','/api/provider/credentials/one',dict(provider='openai',key=canary().reveal()),{'X-Attention-Token':control(service)})[0]==200


@pytest.mark.parametrize('public',[False,True])
@pytest.mark.parametrize('codex',[False,True])
@pytest.mark.parametrize('lab',['absent','empty','file','PARCC_API_KEY','LITELLM_KEY'])
def test_defaults_matrix(tmp_path,public,codex,lab):
    key=canary()
    if codex:(tmp_path/'.codex').mkdir();(tmp_path/'.codex/auth.json').touch()
    if lab in ('file','empty'):(tmp_path/'.litellm-key').write_text('presence-only' if lab=='file' else '')
    value=ui.provider_defaults(public_gateway_mode=public,home=tmp_path,getenv=lambda name:key.reveal() if name==lab else None)
    assert value['mode']==('public-gateway' if public else 'local')
    for role in ('eval','advisor') if public else ('dev','eval','advisor'):
        assert value['roles'][role]['provider']=='sol' and value['roles'][role]['model']=='gpt-6.1-sol'
        assert value['roles'][role]['available']==codex
    parcc=value['providers']['parcc'];assert parcc['available']==(lab not in ('absent','empty'))
    if not parcc['available']:assert parcc['reason']=='no PARCC key on this machine; add your own key'
    if public:assert value['roles']['dev']==parcc
    assert not scan([key],roots=[tmp_path],extra_blobs=[json.dumps(value)])


def test_defaults_http_control_and_no_store(tmp_path,monkeypatch):
    service=server(tmp_path,monkeypatch,defaults_home=tmp_path,defaults_getenv=lambda name:None)
    assert exchange(service,'GET','/api/provider/defaults')[0]==403
    status,body,head=exchange(service,'GET','/api/provider/defaults',headers={'X-Attention-Token':control(service)})
    assert status==200 and json.loads(body)['mode']=='local' and b'Cache-Control: no-store' in head


@pytest.mark.parametrize('header',['X-Attention-Owner','X-Attention-Gateway-Auth'])
@pytest.mark.parametrize('path',['/','/api/provider/credentials','/api/dev/options'])
def test_local_rejects_owner_headers(tmp_path,monkeypatch,header,path):
    service=server(tmp_path,monkeypatch)
    status,body,_=exchange(service,'GET',path,headers={header:''})
    assert status==400 and json.loads(body)['error']['code']=='owner_header_not_accepted'


@pytest.mark.parametrize('failure',['missing','malformed','wrong','expired','body','owner','method','path','invalid-owner'])
def test_gateway_rejects_invalid_auth(tmp_path,monkeypatch,failure):
    service,secret,token,call=gateway(tmp_path,monkeypatch)
    path='/api/provider/credentials/mine';raw=json.dumps(dict(provider='openai',key=canary().reveal())).encode()
    auth=signature(secret,'alice','PUT',path,raw)
    headers={'X-Attention-Owner':'alice','X-Attention-Gateway-Auth':auth,'X-Attention-Token':token}
    if failure=='missing':del headers['X-Attention-Gateway-Auth']
    elif failure=='malformed':headers['X-Attention-Gateway-Auth']='v1.invalid'
    elif failure=='wrong':headers['X-Attention-Gateway-Auth']=signature(canary(),'alice','PUT',path,raw)
    elif failure=='expired':headers['X-Attention-Gateway-Auth']=signature(secret,'alice','PUT',path,raw,ts=969)
    elif failure=='body':raw+=b' '
    elif failure=='owner':headers['X-Attention-Owner']='bob'
    elif failure=='invalid-owner':headers['X-Attention-Owner']='Alice!'
    elif failure=='method':headers['X-Attention-Gateway-Auth']=signature(secret,'alice','POST',path,raw)
    elif failure=='path':headers['X-Attention-Gateway-Auth']=signature(secret,'alice','PUT',path+'?x=1',raw)
    status,body,head=exchange(service,'PUT',path,raw=raw,headers=headers)
    assert status==401 and json.loads(body)['error']['code']==('gateway_auth_expired' if failure=='expired' else 'gateway_auth_invalid')
    assert not scan([secret],roots=[tmp_path],extra_blobs=[body,head])


@pytest.mark.parametrize('delta',[-30,30])
def test_gateway_time_window_and_raw_query(tmp_path,monkeypatch,delta):
    service,secret,token,call=gateway(tmp_path,monkeypatch)
    path='/api/dev/options?x=%2F'
    status,_,_=call('GET',path,token=token,**{'X-Attention-Gateway-Auth':signature(secret,'alice','GET',path,ts=1000+delta)})
    assert status==200


def test_gateway_rejects_nonloopback(tmp_path,monkeypatch):
    service,secret,token,call=gateway(tmp_path,monkeypatch)
    from email.message import Message
    request=SimpleNamespace(server=service,headers=Message(),client_address=('192.0.2.1',1))
    with pytest.raises(ui.OwnerError) as exc:ui.owner_of(request)
    assert exc.value.status==403 and exc.value.code=='gateway_peer_not_loopback'


def test_gateway_owner_isolation_and_control(tmp_path,monkeypatch):
    vault=keys.ProviderKeyVault();service,secret,token,call=gateway(tmp_path,monkeypatch,vault=vault,defaults_home=tmp_path,defaults_getenv=lambda name:None)
    a,b=canary(),canary();replies=[]
    for owner,key in [('alice',a),('bob',b)]:
        status,body,head=call('PUT','/api/provider/credentials/'+owner,dict(provider='openai',key=key.reveal()),owner=owner,token=token)
        assert status==200;replies.extend([body,head])
    assert call('GET','/api/provider/defaults')[0]==403
    assert call('GET','/api/provider/defaults',token=token)[0]==200
    for method,path,body in [('GET','/api/provider/credentials/bob',None),('DELETE','/api/provider/credentials/bob',None),('POST','/api/provider/credentials/bob/test',dict(model='fake'))]:
        status,raw,head=call(method,path,body,token=token);replies.extend([raw,head])
        if method=='GET':assert status==200 and json.loads(raw)==[]
        elif method=='DELETE':assert json.loads(raw)['cleared']==0
        else:assert status==400 and json.loads(raw)['error']['code']=='provider_key_missing'
    assert [r['credential_id'] for r in vault.list('bob')]==['bob']
    assert not scan([secret,a,b],roots=[tmp_path],extra_blobs=replies+[json.dumps(sys.argv),json.dumps(dict(os.environ))])


@pytest.mark.parametrize('host',['127.0.0.1','::1','localhost'])
def test_run_private_hosts_are_explicit(monkeypatch,host):
    role=dict(credential='mine',provider='openai-compatible',model='fake',base_url_host=host)
    url='https://'+('['+host+']' if ':' in host else host)+'/v1'
    monkeypatch.setattr(keys,'_RUN_KEYS',{'mine':canary()});monkeypatch.setattr(keys,'_RUN_ROLES',{'dev':dict(role,base_url=url)})
    monkeypatch.setattr(providers.socket,'getaddrinfo',lambda *a,**kw:[(None,None,None,None,(host if host!='localhost' else '127.0.0.1',443))])
    manifest=dict(agent_provider=dict(roles=dict(dev=role)))
    with pytest.raises(providers.ProviderError,match='public addresses'):loop.provider_for_run(manifest)
    assert loop.provider_for_run(manifest,allow_private_hosts=(host,)).spec.chat_url==url+'/chat/completions'
    with pytest.raises(providers.ProviderError):loop.provider_for_run(manifest,allow_private_hosts=('another-host',))


def test_run_cli_propagates_provider_permissions(tmp_path,monkeypatch):
    seen={}
    monkeypatch.setattr(experiments,'DevV2Store',lambda path:None)
    def run(*args,**kwargs):seen.update(kwargs);return {}
    monkeypatch.setattr(experiments,'run',run)
    monkeypatch.setattr(sys,'argv',['dev_experiment','run','--store',str(tmp_path/'db'),'--artifacts',str(tmp_path),
                                  '--allow-private-provider-hosts','127.0.0.1, ::1,localhost','--public-gateway-mode'])
    experiments.main()
    assert seen['allow_private_hosts']==('127.0.0.1','::1','localhost') and seen['public_gateway_mode']


@pytest.mark.parametrize('public',[False,True])
@pytest.mark.parametrize('remove',[None,False,True])
@pytest.mark.parametrize('allowed',[(),('127.0.0.1','::1')])
def test_launch_removal_and_allowlist(tmp_path,monkeypatch,offline_dev_identity,remove,allowed,public):
    vault=keys.ProviderKeyVault();key=canary();launched=[]
    monkeypatch.setenv('PARCC_URL','https://127.0.0.1/v1/chat/completions')
    def launch(argv,bundle,**kwargs):
        launched.append(argv);assert bundle._keys['mine']==key
        assert ('PARCC_URL' in kwargs['env'])==(not public)
        return SimpleNamespace(pid=os.getpid(),poll=lambda:0)
    monkeypatch.setattr(keys,'launch_run_with_keys',launch)
    monkeypatch.setattr(providers,'probe',lambda *a,**kw:providers.ProbeResult(dict.fromkeys(('auth','chat','streaming','tools'),'ok')|dict(vision='unknown'),[]))
    # Keep the real manifest/source checks; only the child process is a test double.
    if public:
        service,secret,token,signed=gateway(tmp_path,monkeypatch,vault=vault,allow_private_hosts=allowed)
        call=lambda method,path,body:signed(method,path,body,token=token)
    else:
        service=server(tmp_path,monkeypatch,vault=vault,allow_private_hosts=allowed)
        headers={'X-Attention-Token':control(service)}
        call=lambda method,path,body:exchange(service,method,path,body,headers)
    owner='alice' if public else 'local'
    status,raw,_=call('POST','/api/dev/experiments',selection());assert status==201
    for name in ('mine','unused'):vault.put(owner,name,'openai',key)
    path='/api/dev/experiments/'+json.loads(raw)['experiment_id']+'/run'
    body=dict(max_cells=1)
    if remove is not None:body['remove_credentials_after_launch']=remove
    status,raw,_=call('POST',path,body)
    assert status==202,raw
    assert [r['credential_id'] for r in vault.list(owner)]==(['unused'] if remove else ['mine','unused'])
    assert ('--public-gateway-mode' in launched[0])==public
    if allowed:assert launched[0][launched[0].index('--allow-private-provider-hosts')+1]==','.join(allowed)
    else:assert '--allow-private-provider-hosts' not in launched[0]
    assert not scan([key],roots=[tmp_path],extra_blobs=[raw,json.dumps(launched)])


@pytest.mark.parametrize('failure',['missing','preflight','invalid-boolean','spawn'])
def test_failed_launch_keeps_credentials(tmp_path,monkeypatch,offline_dev_identity,failure):
    vault=keys.ProviderKeyVault();vault.put('local','unused','openai',canary())
    if failure!='missing':vault.put('local','mine','openai',canary())
    before=vault.list('local')
    monkeypatch.setattr(providers,'probe',lambda *a,**kw:providers.ProbeResult(dict.fromkeys(('auth','chat','streaming','tools','vision'),'fail'),[dict(message='offline failure',redacted=False)]))
    if failure=='spawn':
        monkeypatch.setattr(providers,'probe',lambda *a,**kw:providers.ProbeResult(dict.fromkeys(('auth','chat','streaming','tools','vision'),'ok'),[]))
        def fail(*args,**kwargs):raise OSError('offline spawn failure')
        monkeypatch.setattr(keys,'launch_run_with_keys',fail)
    service=server(tmp_path,monkeypatch,vault=vault);headers={'X-Attention-Token':control(service)}
    status,raw,_=exchange(service,'POST','/api/dev/experiments',selection(),headers);assert status==201
    path='/api/dev/experiments/'+json.loads(raw)['experiment_id']+'/run'
    status,raw,_=exchange(service,'POST',path,dict(max_cells=1,remove_credentials_after_launch='true' if failure=='invalid-boolean' else True),headers)
    assert status==(400 if failure=='invalid-boolean' else 409)
    assert vault.list('local')==before


def test_parcc_single_definition_and_override(monkeypatch):
    from benchmarks.attention_harness.parcc_client import DEFAULT_PARCC_URL,ParccClient
    from benchmarks.attention_harness.dev_agent_parcc_bridge import PARCC_CHAT_URL
    monkeypatch.delenv('PARCC_URL',raising=False)
    assert DEFAULT_PARCC_URL==PARCC_CHAT_URL==providers.resolve_provider('parcc').chat_url
    root=Path(__file__).resolve().parents[3]
    found=subprocess.check_output(['git','grep','-n','litellm.'+'parcc.upenn.edu','--','benchmarks/attention_harness/*.py'],cwd=root).decode().splitlines()
    assert found and all(line.split(':',1)[0]=='benchmarks/attention_harness/dev_agent_provider.py' for line in found)
    override='https://127.0.0.1/v1/chat/completions';monkeypatch.setenv('PARCC_URL',override)
    assert providers.resolve_provider('parcc').chat_url==override
    assert providers.resolve_provider('parcc',public_gateway_mode=True).chat_url==DEFAULT_PARCC_URL
    key=canary()
    assert ParccClient(api_key=key.reveal()).endpoint==override
    assert ParccClient(api_key=key.reveal(),public_gateway_mode=True).endpoint==DEFAULT_PARCC_URL


@pytest.mark.parametrize('url',['http://127.0.0.1/v1/chat/completions','https://name:password@example.test/v1/chat/completions','https://example.test/v1/chat/completions?x=1'])
def test_parcc_override_validation(monkeypatch,url):
    monkeypatch.setenv('PARCC_URL',url)
    with pytest.raises(providers.ProviderError):providers.resolve_provider('parcc')
    assert providers.resolve_provider('parcc',public_gateway_mode=True)==providers.presets()['parcc']


def test_gateway_human_approval_still_required(tmp_path,monkeypatch):
    service,secret,token,call=gateway(tmp_path,monkeypatch)
    path='/api/dev/experiments/devexp-'+'a'*24+'/approvals/planner-'+'b'*64+'/decision'
    status,raw,_=call('POST',path,{},token=token)
    assert status==403 and json.loads(raw)['error']=='separate human approval capability required'


@pytest.mark.parametrize('raw,content_type',[(b'not json','application/json'),(b'[]','application/json'),(b'{}','text/plain')])
def test_gateway_preserves_body_validation(tmp_path,monkeypatch,raw,content_type):
    service,secret,token,call=gateway(tmp_path,monkeypatch)
    status,body,_=call('PUT','/api/provider/credentials/mine',raw=raw,token=token,**{'Content-Type':content_type})
    assert status==400 and json.loads(body)['error']['code']=='provider_body_invalid'


def test_gateway_over_loopback(tmp_path,monkeypatch,loopback):
    import threading
    import urllib.request
    from benchmarks.attention_harness.dev_ledger import DevV2Store
    secret=canary();key=canary();vault=keys.ProviderKeyVault();replies=[]
    service=ui.serve(DevV2Store(tmp_path/'db'),tmp_path/'runs',0,vault=vault,public_gateway_mode=True,gateway_secret=secret,gateway_clock=lambda:1000)
    thread=threading.Thread(target=service.serve_forever,daemon=True);thread.start()
    def call(method,path,body=None,token=None):
        raw=json.dumps(body).encode() if body is not None else b''
        headers={'X-Attention-Owner':'alice','X-Attention-Gateway-Auth':signature(secret,'alice',method,path,raw),'Content-Type':'application/json'}
        if token:headers['X-Attention-Token']=token
        req=urllib.request.Request('http://127.0.0.1:%s%s'%(service.server_port,path),data=raw if body is not None else None,method=method,headers=headers)
        with urllib.request.urlopen(req,timeout=10) as response:
            raw=response.read();replies.extend([raw,str(response.headers)]);return raw
    try:
        import re
        token=re.search(rb'name="control-token" content="([^"]+)"',call('GET','/'))[1].decode()
        assert json.loads(call('PUT','/api/provider/credentials/mine',dict(provider='openai',key=key.reveal()),token))['key_set']
        assert json.loads(call('GET','/api/provider/credentials',token=token))[0]['credential_id']=='mine'
        assert vault.list('local')==[] and vault.list('alice')
        assert not scan([key,secret],roots=[tmp_path],extra_blobs=replies)
    finally:service.shutdown();service.server_close();thread.join()


def test_run_public_mode_ignores_parcc_override(monkeypatch):
    role=dict(credential='mine',provider='parcc',model='parcc/GLM',base_url_host=__import__('urllib.parse',fromlist=['urlsplit']).urlsplit(providers.presets()['parcc'].chat_url).hostname)
    monkeypatch.setattr(keys,'_RUN_KEYS',{'mine':canary()});monkeypatch.setattr(keys,'_RUN_ROLES',{'dev':dict(role,base_url=None)})
    monkeypatch.setenv('PARCC_URL','https://127.0.0.1/v1/chat/completions')
    manifest=dict(agent_provider=dict(roles=dict(dev=role)))
    assert loop.provider_for_run(manifest,public_gateway_mode=True).spec==providers.presets()['parcc']
    assert loop.provider_for_run(manifest).spec.chat_url=='https://127.0.0.1/v1/chat/completions'


def test_gateway_cannot_launch_with_another_owners_key(tmp_path,monkeypatch,offline_dev_identity):
    vault=keys.ProviderKeyVault();key=canary();vault.put('bob','mine','openai',key)
    service,secret,token,call=gateway(tmp_path,monkeypatch,vault=vault)
    status,raw,_=call('POST','/api/dev/experiments',selection(),token=token);assert status==201
    path='/api/dev/experiments/'+json.loads(raw)['experiment_id']+'/run'
    status,raw,head=call('POST',path,dict(max_cells=1,remove_credentials_after_launch=True),token=token)
    assert status==409 and json.loads(raw)['error']['code']=='provider_key_missing'
    assert vault.list('bob')[0]['credential_id']=='mine'
    assert not scan([key,secret],roots=[tmp_path],extra_blobs=[raw,head])


@pytest.mark.parametrize('provider',['parcc','openai','deepseek'])
def test_private_restriction_does_not_change_presets(monkeypatch,provider):
    monkeypatch.delenv('PARCC_URL',raising=False)
    def forbidden(*a,**kw):pytest.fail('preset must not require DNS or private allowlist')
    monkeypatch.setattr(providers.socket,'getaddrinfo',forbidden)
    assert providers.resolve_provider(provider)==providers.presets()[provider]


def test_gateway_probe_ignores_parcc_override(tmp_path,monkeypatch):
    monkeypatch.setenv('PARCC_URL','https://127.0.0.1/v1/chat/completions')
    seen=[]
    def probe(spec,*a,**kw):
        seen.append(spec.chat_url)
        return providers.ProbeResult(dict.fromkeys(('auth','chat','streaming','tools','vision'),'ok'),[])
    monkeypatch.setattr(providers,'probe',probe)
    service,secret,token,call=gateway(tmp_path,monkeypatch)
    assert call('PUT','/api/provider/credentials/mine',dict(provider='parcc',key=canary().reveal()),token=token)[0]==200
    assert call('POST','/api/provider/credentials/mine/test',dict(model='parcc/GLM'),token=token)[0]==200
    assert seen==[providers.presets()['parcc'].chat_url]
