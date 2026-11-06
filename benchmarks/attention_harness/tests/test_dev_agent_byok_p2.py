"""Offline multi-role BYOK contracts, including socket-free integration and canaries."""
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
import types
import urllib.error
import urllib.parse
from dataclasses import replace
from types import SimpleNamespace

import pytest
from benchmarks.attention_harness import dev_agent_config as config
from benchmarks.attention_harness import dev_agent_key_vault as vault
from benchmarks.attention_harness import dev_agent_provider as provider_module
from benchmarks.attention_harness import dev_agent_parcc_bridge as bridge_module
from benchmarks.attention_harness import dev_agent_eval as eval_module
from benchmarks.attention_harness import dev_experiment, dev_advisor_proxy
from benchmarks.attention_harness.dev_agent_byok_client import ByokChatClient
from benchmarks.attention_harness.dev_agent_parcc_bridge import to_chat, ChatBridge, BridgeError, IMAGE_NOTICE
from benchmarks.attention_harness.dev_agent_loop import providers_for_run, advisor_client
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.tests.fakes.fake_provider import FakeProvider
from benchmarks.attention_harness.tests.fakes.byok_http import MemoryServer, exchange, loopback
from benchmarks.attention_harness.tests.test_dev_agent_byok_config import BASE
from benchmarks.attention_harness.tests.test_dev_agent_byok_ui import server, control
from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG
from benchmarks.attention_harness.tests.test_dev_agent_d4 import seed_record, eval_factory, VERDICT
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity

IMAGE='data:image/png;base64,aW1hZ2U='


def roles(users=('dev','eval','advisor'), provider='openai'):
    return {'roles':{r:dict(credential=r if r in users else 'lab',provider=provider if r in users else 'openai',
                            model='custom-'+r if r in users else 'gpt-6.1-sol') for r in ('dev','eval','advisor')}}


def provider(name='openai', key=None, model='custom-eval'):
    return SimpleNamespace(spec=provider_module.presets()[name],secret=key or Secret('sk-canary-'+secrets.token_hex(24)),
        model=model,token=secrets.token_urlsafe(32),token_env='ATTENTION_BYOK_BRIDGE_TOKEN')


@pytest.fixture
def memory_bridge(monkeypatch):
    monkeypatch.setattr(bridge_module.http.server,'ThreadingHTTPServer',MemoryServer)


@pytest.mark.parametrize('kind',['input_image','image_url'])
def test_vision_and_dev_unchanged(kind):
    part=dict(type=kind,image_url=IMAGE if kind=='input_image' else dict(url=IMAGE))
    body=dict(model='m',input=[dict(role='user',content=[dict(type='input_text',text='frame'),part])])
    assert to_chat(body)['messages'][0]['content']=='frame\n'+IMAGE_NOTICE
    assert to_chat(body,vision=True)['messages'][0]['content']==[
        dict(type='text',text='frame'),dict(type='image_url',image_url=dict(url=IMAGE))]


@pytest.mark.parametrize('url',['https://example.org/image.png','file:///tmp/image.png','data:image/gif;base64,aW1hZ2U=',
    'data:image/png;base64,???','data:image/png;base64,',None])
def test_vision_rejects_other_sources(url):
    with pytest.raises(BridgeError,match='unsupported_image_source'):
        to_chat(dict(model='m',input=[dict(role='user',content=[dict(type='input_image',image_url=url)])]),vision=True)


def test_tool_images_are_promoted_after_all_tool_outputs_in_call_order():
    items=[dict(type='function_call',name='view_image',call_id=cid,arguments='{}') for cid in ('a','b')]
    for cid,text,url in [('b','',IMAGE),('a','first','data:image/jpeg;base64,YQ==')]:
        items.append(dict(type='function_call_output',call_id=cid,output=[dict(type='input_text',text=text),dict(type='input_image',image_url=url)]))
    items.append(dict(role='user',content='continue'))
    messages=to_chat(dict(model='m',input=items),vision=True)['messages']
    assert [m['role'] for m in messages]==['assistant','tool','tool','user','user']
    assert messages[1]['content']=='[image attached in the next message]'
    assert messages[2]['content']=='first'
    assert [p['image_url']['url'] for p in messages[3]['content']]==['data:image/jpeg;base64,YQ==',IMAGE]
    assert [m['tool_call_id'] for m in messages[1:3]]==['b','a']


@pytest.mark.parametrize('preset',['openai','deepseek'])
@pytest.mark.parametrize('system',[False,True])
def test_response_formats(preset,system):
    schema=dict(type='object',properties={'ok':{'type':'boolean'}})
    fmt=dict(type='json_schema',name='answer',schema=schema,strict=True)
    body=dict(model='m',input=[],text=dict(format=fmt))
    if system:body['instructions']='original'
    result=to_chat(body,provider_module.presets()[preset])
    if preset=='openai':assert result['response_format']==dict(type='json_schema',json_schema={k:v for k,v in fmt.items() if k!='type'})
    else:
        assert result['response_format']==dict(type='json_object')
        message=result['messages'][0]
        assert message['role']=='system' and 'JSON' in message['content']
        assert json.dumps(schema,separators=(',',':')) in message['content']
        if system:assert message['content'].startswith('original\n')


@pytest.mark.parametrize('fmt',[None,{},dict(type='text'),dict(type='json_object'),dict(type='json_schema',name='a',schema={},extra=True),dict(type='json_schema',name='a',schema=[]),dict(type='json_schema',schema={})])
def test_unknown_format_fails_closed(fmt):
    with pytest.raises(BridgeError,match='unsupported_text_format'):to_chat(dict(model='m',input=[],text=dict(format=fmt)))


@pytest.mark.parametrize('users',[('dev',),('eval',),('advisor',),('dev','eval','advisor'),()])
def test_config_vault_provider_matrix(users,monkeypatch):
    chosen=config.normalize(dict(BASE,agent_provider=roles(users),eval_model='ignored' if 'eval' in users else 'gpt-6.1-sol',advisor_model='ignored'))
    keeper=vault.ProviderKeyVault();key=Secret('sk-canary-'+secrets.token_hex(24))
    for r in users:keeper.put('owner',r,'openai',key)
    keys=keeper.take_for_run('owner',chosen['agent_provider']['roles'])
    monkeypatch.setattr(vault,'_RUN_KEYS',keys._keys);monkeypatch.setattr(vault,'_RUN_ROLES',keys.roles)
    resolved=providers_for_run(chosen)
    assert {r for r,v in resolved.items() if v is not None}==set(users)
    for r in users:
        assert resolved[r].model=='custom-'+r and resolved[r].secret==key
        bad=dict(chosen,agent_provider={'roles':{**chosen['agent_provider']['roles'],r:{**chosen['agent_provider']['roles'][r],'model':'other'}}})
        with pytest.raises(provider_module.ProviderError,match='differs'):providers_for_run(bad)
    assert chosen['eval_model']==('custom-eval' if 'eval' in users else 'gpt-6.1-sol')
    assert chosen['advisor_model']==('custom-advisor' if 'advisor' in users else 'gpt-6.1-sol')


@pytest.mark.parametrize('role',['eval','advisor'])
@pytest.mark.parametrize('preset',list(provider_module.presets()))
def test_any_provider_and_model_for_user(role,preset):
    value=roles();value['roles'][role].update(provider=preset,model='任意 model / '+('x'*100))
    assert config.normalize(dict(BASE,agent_provider=value))[role+'_model']==value['roles'][role]['model']
    value['roles'][role]['credential']='lab'
    with pytest.raises(ValueError,match='lab_provider_not_supported|unsupported_agent_model'):config.normalize(dict(BASE,agent_provider=value))


@pytest.mark.parametrize('role',['eval','advisor'])
def test_vault_mismatch_and_missing(role):
    keeper=vault.ProviderKeyVault();desc=roles()['roles'][role]
    with pytest.raises(provider_module.ProviderError,match='Missing'):keeper.take_for_run('owner',{role:desc})
    keeper.put('owner',role,'deepseek',Secret('fake'))
    with pytest.raises(provider_module.ProviderError,match='differs'):keeper.take_for_run('owner',{role:desc})


@pytest.mark.parametrize('users',[('dev',),('eval',),('advisor',),('dev','eval','advisor')])
def test_manifest_roundtrip_arbitrary_models(tmp_path,offline_dev_identity,users):
    chosen=dict(BASE,agent_provider=roles(users),advisor_proxy={'enabled':True})
    store=DevV2Store(tmp_path/'db');exp=dev_experiment.create(store,tmp_path/'runs',chosen,agent_policy_version='agent-policy-v1')
    manifest=dev_experiment.verify_manifest(exp)
    dev_experiment.validate_fairness(manifest)
    c=next(iter(manifest['configs'].values()))
    for r,field in [('eval','eval_agent'),('advisor','advisor_proxy')]:
        assert c[field]['model']==('custom-'+r if r in users else 'gpt-6.1-sol')
        if r in users:assert c[field]['model_source']=='byok'


def test_user_models_never_get_free_lab_resource_exemptions():
    from benchmarks.attention_harness.free_models import limited_resources,TOKEN_RESOURCES
    from benchmarks.attention_harness.eval_agent import default_config
    user=roles(('eval',))['roles'];user['eval']['model']='parcc/Qwen'
    limited=limited_resources(model_source='codex',eval_config=default_config(True),model_names=('parcc/GLM',),provider_roles=user)
    assert set(TOKEN_RESOURCES)<=set(limited) and 'eval_calls' in limited
    assert not set(TOKEN_RESOURCES)&set(limited_resources(model_source='codex'))


class FakeTransport:
    """Use FakeProvider.handle unchanged, without binding sockets."""
    def __init__(self,key,mode=None):
        self.fake=FakeProvider.__new__(FakeProvider)
        self.fake.expected=key.reveal();self.fake.mode=mode;self.fake.tools=True
        self.fake.authorizations=[];self.fake.requests=[]
    def __call__(self,request,**kwargs):
        response=io.BytesIO();response.headers={}
        class Handler:
            command=request.get_method();path=urllib.parse.urlsplit(request.full_url).path
            headers={**request.headers,'Content-Length':str(len(request.data or b''))};rfile=io.BytesIO(request.data or b'');wfile=response
            def send_response(self,status):self.status=status
            def send_header(self,k,v):response.headers[k]=v
            def end_headers(self):pass
        handler=Handler();self.fake.handle(handler);response.seek(0)
        if handler.status>=400:raise urllib.error.HTTPError(request.full_url,handler.status,'error',response.headers,response)
        return response


@pytest.mark.parametrize('preset',['openai','deepseek','parcc'])
def test_chat_parameters_usage_and_image(preset,monkeypatch):
    p=provider(preset);transport=FakeTransport(p.secret);monkeypatch.setattr(provider_module,'open_request',transport)
    messages=[dict(role='user',content=[dict(type='image_url',image_url=dict(url=IMAGE))])]
    result=ByokChatClient(p).chat(model='parcc/Qwen',messages=messages,max_tokens=17,reasoning_effort='high')
    body=transport.fake.requests[-1][1]
    assert body['messages']==messages and body[p.spec.token_param]==17
    assert ('reasoning_effort' in body)==(bool(p.spec.reasoning_param) and preset!='parcc')  # PARCC's Qwen rejects high.
    assert result.usage==dict(prompt_tokens=3,completion_tokens=2,total_tokens=5) and result.model=='parcc/Qwen'
    assert result.attempts==1 and result.content=='ok'


@pytest.mark.parametrize('status,attempts',[(401,1),(403,1),(429,3),(500,3),(503,3),(400,1)])
def test_chat_error_retry_redaction(tmp_path,monkeypatch,status,attempts):
    p=provider();calls=[]
    def fail(req,**kwargs):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url,status,'error',{},io.BytesIO(p.secret.reveal().encode()))
    monkeypatch.setattr(provider_module,'open_request',fail)
    with pytest.raises(Exception) as exc:ByokChatClient(p).chat(model='parcc/Qwen',messages=[],max_tokens=5)
    assert len(calls)==attempts and '[REDACTED:key]' in str(exc.value) and exc.value.__suppress_context__
    (tmp_path/'advisor-error.txt').write_text(str(exc.value))
    assert not scan([p.secret],roots=[tmp_path],include_processes=True)


def test_chat_timeout_retry_and_response_model(monkeypatch):
    p=provider();calls=[]
    def send(req,**kwargs):
        calls.append(1)
        if len(calls)<3:raise TimeoutError(p.secret.reveal())
        result=io.BytesIO(json.dumps(dict(model='actual',choices=[dict(message=dict(content='ok'))],usage=dict(prompt_tokens=2,completion_tokens=1))).encode());result.headers={};return result
    monkeypatch.setattr(provider_module,'open_request',send)
    response=ByokChatClient(p).chat(model='requested',messages=[],max_tokens=1)
    assert response.model=='actual' and response.attempts==3 and response.usage['total_tokens']==3
    assert not scan([p.secret],roots=[],extra_blobs=[json.dumps(response.artifact())])


@pytest.mark.parametrize('status',[401,429,500])
def test_eval_bridge_errors_are_scrubbed(tmp_path,memory_bridge,status):
    p=provider()
    def fail(*args,**kwargs):raise urllib.error.HTTPError('fake',status,'error',{},io.BytesIO(p.secret.reveal().encode()))
    bridge=ChatBridge(tmp_path/'private/byok-bridge-eval',p.spec,p.secret,token=p.token,role='eval',vision=True,upstream=fail)
    try:code,body,_=exchange(bridge.server,'POST','/v1/responses',dict(model='m',input=[]),{'Authorization':'Bearer '+p.token})
    finally:bridge.close()
    assert code==(400 if status==401 else status) and b'[REDACTED:key]' in body
    row=json.loads((bridge.log_dir/'calls.jsonl').read_text());assert row['role']=='eval' and row['redacted']
    assert not scan([p.secret],roots=[tmp_path],extra_blobs=[body],include_processes=True)


def original(name):
    path='benchmarks/attention_harness/'+name+'.py'
    result=types.ModuleType('benchmarks.attention_harness._p2_golden_'+name);result.__package__='benchmarks.attention_harness'
    exec(compile(subprocess.check_output(['git','show','HEAD:'+path]),path,'exec'),result.__dict__)
    return result


def test_evaluator_no_provider_popen_is_byte_identical(tmp_path,monkeypatch):
    ws=Workspace(tmp_path/'agent').prepare(dict(task_id='fake',task_prompt='Move.'),CONFIG)
    execution=seed_record(ws);calls=[]
    class Process:
        pid=99999999
        def __init__(self,argv,**kwargs):
            calls.append((argv,{k:str(v.name) if k=='stderr' else v for k,v in kwargs.items()}))
            self.stdout=io.BytesIO((json.dumps(dict(type='item.completed',item=dict(type='agent_message',text=json.dumps(VERDICT))))+'\n'+json.dumps(dict(type='turn.completed'))+'\n').encode())
        def wait(self,**kwargs):return 0
    factory=lambda root,prompt:['codex','exec','-C',str(root),'--json','-m','old','-c','model_reasoning_effort="low"',prompt]
    before=original('dev_agent_eval')
    monkeypatch.setattr(eval_module.subprocess,'Popen',Process)
    monkeypatch.setattr(eval_module,'build_workspace',lambda *a:ws.ws)
    monkeypatch.setattr(before,'build_workspace',lambda *a:ws.ws)
    (ws.private/'eval/v0001').mkdir(parents=True)
    for cls in (before.Evaluator,eval_module.Evaluator):
        assert cls(ws,command_factory=factory).evaluate(execution,deadline=time.monotonic()+10)['status']=='completed'
    assert calls[0]==calls[len(calls)//2] and 'env' not in calls[0][1]


def test_advisor_lab_client_is_unchanged():
    old=original('dev_agent_loop')
    for source in ('codex','parcc'):
        sentinel=object()
        assert advisor_client(sentinel,dict(model_source=source)) is old.advisor_client(sentinel,dict(model_source=source)) is sentinel
    a=advisor_client(None,dict(model_source='codex'));b=old.advisor_client(None,dict(model_source='codex'))
    assert vars(a)==vars(b) and type(a) is type(b)
    assert isinstance(advisor_client(None,{},provider=provider()),ByokChatClient)


def test_eval_real_child_environment_reuse_and_cleanup(tmp_path,monkeypatch,memory_bridge):
    ws=Workspace(tmp_path/'agent').prepare(dict(task_id='fake',task_prompt='Move.'),CONFIG);p=provider()
    factory=eval_factory(ws)
    script=ws.private/'fake-eval.py'
    script.write_text(script.read_text().replace('import json,time,sys','import json,time,sys,os\nfrom pathlib import Path\nPath("child-env.json").write_text(json.dumps(dict(os.environ)))'))
    for name in vault.KEY_ENV:monkeypatch.setenv(name,p.secret.reveal())
    evaluator=eval_module.Evaluator(ws,provider=p,model='ignored',command_factory=factory)
    for i in (1,2):
        execution=seed_record(ws,f'v{i:04d}',f'e{i:06d}')
        result=evaluator.evaluate(execution,deadline=time.monotonic()+10)
        assert result['status']=='completed' and evaluator.bridge is None
        root=ws.private/'eval'/execution['version_id'];env=json.loads((root/'ws/child-env.json').read_text())
        assert not set(vault.KEY_ENV)&set(env) and env[p.token_env]==p.token and p.secret.reveal() not in json.dumps(env)
        command=json.loads((root/'command.json').read_text())
        assert command.index('model_provider="parcc"')<command.index('-m')
        assert command[command.index('-m')+1]==p.model and 'model_reasoning_effort="high"' in command
    assert not scan([p.secret],roots=[tmp_path],include_processes=True)


@pytest.mark.parametrize('failing',[None,'eval','advisor'])
def test_ui_preflight_all_roles(tmp_path,monkeypatch,offline_dev_identity,failing):
    from benchmarks.attention_harness import dev_ui_server as ui
    seen=[];launches=[];p=provider();keeper=vault.ProviderKeyVault()
    def probe(spec,secret,model,*,role):
        seen.append(role);assert secret==p.secret
        return provider_module.ProbeResult(dict.fromkeys(('auth','chat','streaming','tools','vision'),'fail' if role==failing else 'ok'),
            [dict(code='bad',message='[REDACTED:key]',redacted=True)] if role==failing else [])
    def launch(argv,keys,**kw):
        launches.append((argv,keys,kw));return SimpleNamespace(pid=os.getpid(),poll=lambda:0)
    monkeypatch.setattr(provider_module,'probe',probe);monkeypatch.setattr(vault,'launch_run_with_keys',launch)
    service=server(tmp_path,monkeypatch,vault=keeper);headers={'X-Attention-Token':control(service)}
    for role in ('dev','eval','advisor'):
        assert exchange(service,'PUT','/api/provider/credentials/'+role,dict(provider='openai',key=p.secret.reveal()),headers)[0]==200
    status,raw,_=exchange(service,'POST','/api/dev/experiments',dict(BASE,agent_provider=roles()),headers)
    assert status==201,raw
    status,raw,_=exchange(service,'POST','/api/dev/experiments/'+json.loads(raw)['experiment_id']+'/run',dict(max_cells=1,remove_credentials_after_launch=True),headers)
    assert status==(409 if failing else 202),raw
    if failing:assert json.loads(raw)['error']['role']==failing and not launches
    else:
        assert sorted(seen)==['advisor','dev','eval'] and set(launches[0][1]._keys)=={'dev','eval','advisor'}
        assert json.loads(exchange(service,'GET','/api/provider/credentials',headers=headers)[1])==[]
    assert not scan([p.secret],roots=[tmp_path],sqlite_paths=[tmp_path/'db'],extra_blobs=[raw],include_processes=True)


@pytest.mark.parametrize('error_role,status',[(None,None),('eval',401),('eval',429),('eval',500),('advisor',401),('advisor',429),('advisor',500)])
def test_all_role_fake_run_canary(tmp_path,monkeypatch,error_role,status):
    """Real controllers and HTTP handlers, in-memory sockets; all persisted surfaces scanned."""
    from benchmarks.attention_harness.tests.fakes.byok_p2_codex import request_body,events
    keys={r:Secret('sk-canary-'+secrets.token_hex(24)) for r in ('dev','eval','advisor')}
    keeper=vault.ProviderKeyVault()
    for r,key in keys.items():keeper.put('owner',r,'openai',key)
    chosen=config.normalize(dict(BASE,agent_provider=roles()))
    bundle=keeper.take_for_run('owner',chosen['agent_provider']['roles'])
    monkeypatch.setattr(vault,'_RUN_KEYS',bundle._keys);monkeypatch.setattr(vault,'_RUN_ROLES',bundle.roles)
    resolved=providers_for_run(chosen)
    transports={r:FakeTransport(key,'echo_key_'+str(status) if r==error_role else None) for r,key in keys.items()}
    def transport(req,**kwargs):
        body=json.loads(req.data);return transports[body['model'].removeprefix('custom-')](req,**kwargs)
    monkeypatch.setattr(provider_module,'open_request',transport)
    servers=[]
    def memory_server(*args):
        server=MemoryServer(*args);servers.append(server);return server
    monkeypatch.setattr(bridge_module.http.server,'ThreadingHTTPServer',memory_server)
    child_envs=[]
    class Process:
        pid=99999999
        def __init__(self,argv,**kwargs):
            self.stdin=io.BytesIO();root=Path(kwargs['cwd']);env=kwargs['env'];child_envs.append(env)
            (root/'child-env.json').write_text(json.dumps(env))
            code,body,_=exchange(servers[-1],'POST','/v1/responses',request_body(argv,root),{'Authorization':'Bearer '+env['ATTENTION_BYOK_BRIDGE_TOKEN']})
            if code==200:self.stdout=io.BytesIO(events(body))
            else:
                kwargs['stderr'].write(body)
                self.stdout=io.BytesIO((json.dumps(dict(type='turn.failed',error=body.decode()))+'\n').encode())
        def wait(self,**kw):return 0
    monkeypatch.setattr(eval_module.subprocess,'Popen',Process)
    ws=Workspace(tmp_path/'artifacts/agent').prepare(dict(task_id='fake',task_prompt='Move.'),CONFIG)
    factory=lambda root,prompt:['fake-codex','exec','-C',str(root),'--json','-m','old','-c','model_reasoning_effort="low"',prompt]
    dev=CodexAgentSession(ws.ws,ws.private,provider=resolved['dev'],model='custom-dev',command_factory=factory)
    list(dev.start('Inspect text'))
    evaluator=eval_module.Evaluator(ws,provider=resolved['eval'],command_factory=factory)
    verdict=evaluator.evaluate(seed_record(ws),deadline=time.monotonic()+10)
    assert verdict['status']==('unavailable' if error_role=='eval' else 'completed')
    assert evaluator.bridge is None and dev.bridge is None and all(s.stop.is_set() for s in servers)
    client=advisor_client(None,{},provider=resolved['advisor'])
    kwargs=dict(model='custom-advisor',messages=[dict(role='user',content=[dict(type='image_url',image_url=dict(url=IMAGE))])],max_tokens=32)
    if error_role=='advisor':
        with pytest.raises(Exception) as exc:client.chat(**kwargs)
        advisor=dict(error=str(exc.value));assert '[REDACTED:key]' in advisor['error']
    else:advisor=client.chat(**kwargs).artifact()
    (tmp_path/'artifacts/advisor.json').write_text(json.dumps(advisor))
    store=DevV2Store(tmp_path/'store.sqlite')
    with store.tx() as db:store.event(db,'fake_run_completed',dict(eval=verdict,advisor=advisor))
    for env in child_envs:
        assert not set(env)&set(vault.KEY_ENV)
        assert 'ATTENTION_BYOK_BRIDGE_TOKEN' in env
    bridge_rows=[json.loads(line) for path in ws.private.rglob('calls.jsonl') for line in path.read_text().splitlines()]
    assert {row['role'] for row in bridge_rows}=={'dev','eval'}
    if error_role=='eval':assert any(row.get('redacted') for row in bridge_rows)
    assert not scan(keys.values(),roots=[tmp_path,tmp_path/'artifacts/agent/private/eval',ws.private/'byok-bridge-eval'],
        sqlite_paths=[tmp_path/'store.sqlite'],extra_blobs=[json.dumps(child_envs)],include_processes=True)


def test_real_loopback_all_role_pipe_run(tmp_path,loopback):
    key=Secret('sk-canary-'+secrets.token_hex(24));keeper=vault.ProviderKeyVault()
    with FakeProvider(key.reveal()) as fake:
        selected=roles(provider='openai-compatible')['roles']
        for r in selected:keeper.put('owner',r,'openai-compatible',key,base_url=fake.base_url)
        bundle=keeper.take_for_run('owner',selected)
        script=Path(__file__).parent/'fakes/byok_p2_run.py'
        process=vault.launch_run_with_keys([sys.executable,'-B',str(script),'--output',str(tmp_path/'run')],bundle,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            out,err=process.communicate(timeout=40)
            assert process.returncode==0 and not out and not err
        finally:
            if process.poll() is None:process.kill();process.wait()
        for path in (tmp_path/'run').rglob('child-env.json'):
            env=json.loads(path.read_text());assert not set(env)&set(vault.KEY_ENV) and 'ATTENTION_BYOK_BRIDGE_TOKEN' in env
        assert not scan([key],roots=[tmp_path],sqlite_paths=[tmp_path/'run/run.sqlite'],include_processes=True,extra_blobs=[out,err])
        requests=[body for _,body in fake.requests]
        assert {b['model'] for b in requests}=={'custom-dev','custom-eval','custom-advisor'}
        assert any(b.get('response_format') for b in requests)


def test_eval_cancel_closes_child_reader_and_bridge(tmp_path,monkeypatch,memory_bridge):
    import threading
    ws=Workspace(tmp_path/'agent').prepare(dict(task_id='fake',task_prompt='Move.'),CONFIG)
    evaluator=eval_module.Evaluator(ws,provider=provider(),command_factory=eval_factory(ws,sleep=10))
    result=[]
    worker=threading.Thread(target=lambda:result.append(evaluator.evaluate(seed_record(ws),deadline=time.monotonic()+15)))
    worker.start();until=time.monotonic()+5
    while evaluator.process is None and worker.is_alive() and time.monotonic()<until:time.sleep(.01)
    bridge=evaluator.bridge
    assert bridge is not None
    evaluator.cancel();worker.join(5)
    assert not worker.is_alive() and not bridge.thread.is_alive()
    assert evaluator.bridge is None and evaluator.process.poll() is not None
    assert not evaluator.reader.is_alive() and result[0]['status']=='unavailable'


def test_budget_preview_labels_user_roles():
    from benchmarks.attention_harness.dev_ui_server import budget_preview
    preview=budget_preview(dict(BASE,agent_provider=roles(('eval','advisor'))))
    assert preview['eval_accounting']=='User provider; usage recorded'
    assert preview['model_token_accounting']==dict(dev='Recorded; no token cap',eval='User provider; usage recorded',advisor='User provider; usage recorded')


@pytest.mark.parametrize('mode',[None,'echo_key_401','echo_key_429','echo_key_500'])
def test_advisor_public_view_bounded_retries_and_record(tmp_path,monkeypatch,mode):
    from benchmarks.attention_harness.tests.test_dev_agent_d4 import fake_view
    p=provider(model='custom-advisor');transport=FakeTransport(p.secret,mode)
    monkeypatch.setattr(provider_module,'open_request',transport)
    settings=config.configuration(dict(BASE,agent_provider=roles(('advisor',)),advisor_proxy={'enabled':True}))['advisor_proxy']
    if mode:
        with pytest.raises(dev_advisor_proxy.AdvisorUnavailable) as exc:
            dev_advisor_proxy.ask(fake_view(),settings,ByokChatClient(p),cache_dir=tmp_path)
        assert '[REDACTED:key]' in str(exc.value)
        assert len(transport.fake.requests)==(1 if mode=='echo_key_401' else 3)
    else:
        record=dev_advisor_proxy.ask(fake_view(),settings,ByokChatClient(p),cache_dir=tmp_path)
        assert record['model_source']=='byok' and record['model']=='custom-advisor'
        assert 'quota_before' not in record and record['usage']['total_tokens']==5
        assert any(part['type']=='image_url' for part in transport.fake.requests[-1][1]['messages'][-1]['content'])
    assert not scan([p.secret],roots=[tmp_path],include_processes=True)


@pytest.mark.parametrize('users',[('dev',),('eval',),('advisor',),('dev','eval','advisor')])
def test_run_agent_cell_role_wiring_and_quota(tmp_path,monkeypatch,users):
    from benchmarks.attention_harness import dev_agent_loop as loop,dev_agent_isolation as isolation
    from benchmarks.attention_harness.dev_agent_prompts import manifest_fields
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import SOURCE
    options=config.normalize(dict(BASE,agent_provider=roles(users)))
    c=dict(CONFIG,task_id='fake',task_prompt='Move.',model_source='codex',advisor_proxy=config.configuration(dict(BASE,agent_provider=roles(users)))['advisor_proxy'])
    manifest=dict(configs={'fake:102':c},selection={'help_budget':1},memory={'entries':[]},
        initial_sources={'fake':{'source':SOURCE.decode()}},policies={'reactive_help':{}},demos={'fake':{'content':'Example'}},**options,**manifest_fields())
    keeper=vault.ProviderKeyVault()
    for r in users:keeper.put('o',r,'openai',provider().secret)
    keys=keeper.take_for_run('o',options['agent_provider']['roles'])
    monkeypatch.setattr(vault,'_RUN_KEYS',keys._keys);monkeypatch.setattr(vault,'_RUN_ROLES',keys.roles)
    class Store:
        path=tmp_path/'ledger.sqlite'
        def cell(self,key):return dict(task_id='fake',seed=102,policy_id='reactive_help',artifact_dir=str(tmp_path/'run/cells'/key))
        def update_cell(self,*args,**kw):pass
        def version(self,*args):pass
    class Listener:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def bind(self,*args):pass
        def listen(self):pass
        def getsockname(self):return ('127.0.0.1',12345)
    monkeypatch.setattr(loop.socket,'socket',Listener)
    monkeypatch.setattr(isolation,'verify_runtime',lambda *a,**kw:None)
    monkeypatch.setattr(isolation,'codex_identity',lambda:dict(version='fixture'))
    def checked(workspace,session,ledger,**kw):
        ev=kw['evaluator'];helper=kw['helper']
        assert (session.provider is not None)==('dev' in users)
        assert (ev.provider is not None)==('eval' in users)
        assert isinstance(helper.client,ByokChatClient)==('advisor' in users)
        assert (kw['quota'] is None)==('dev' in users)
        assert (ev.quota is None)==('eval' in users)
        assert (helper.quota is None)==('advisor' in users)
        assert ev.model==options['eval_model'] and session.model==options['dev_model']
        return dict(state='completed',reason='checked',resources=ledger.snapshot())
    monkeypatch.setattr(loop,'drive',checked)
    assert loop.run_agent_cell(Store(),{'artifact_dir':str(tmp_path/'run')},manifest,'cell')['reason']=='checked'


def test_provider_flags_forwarded_for_every_user_role(monkeypatch):
    options=config.normalize(dict(BASE,agent_provider=roles()))
    keys={r:provider().secret for r in ('dev','eval','advisor')};seen=[]
    monkeypatch.setattr(vault,'_RUN_KEYS',keys);monkeypatch.setattr(vault,'_RUN_ROLES',{})
    def resolve(name,base,**kwargs):seen.append(kwargs);return provider_module.presets()[name]
    monkeypatch.setattr(provider_module,'resolve_provider',resolve)
    providers_for_run(options,allow_private_hosts=('127.0.0.1',),public_gateway_mode=True)
    assert seen==[dict(allow_custom=True,allow_private_hosts=('127.0.0.1',),public_gateway_mode=True)]*3


@pytest.mark.parametrize('users',[('dev',),('eval',),('advisor',),()])
def test_ui_does_not_probe_lab_roles(tmp_path,monkeypatch,offline_dev_identity,users):
    seen=[]
    def probe(spec,key,model,*,role):
        seen.append(role)
        return provider_module.ProbeResult(dict.fromkeys(('auth','chat','streaming','tools','vision'),'ok'),[])
    monkeypatch.setattr(provider_module,'probe',probe)
    monkeypatch.setattr(vault,'launch_run_with_keys',lambda *a,**kw:SimpleNamespace(pid=os.getpid(),poll=lambda:0))
    service=server(tmp_path,monkeypatch);headers={'X-Attention-Token':control(service)}
    for role in users:assert exchange(service,'PUT','/api/provider/credentials/'+role,dict(provider='openai',key=provider().secret.reveal()),headers)[0]==200
    status,raw,_=exchange(service,'POST','/api/dev/experiments',dict(BASE,agent_provider=roles(users)),headers)
    assert status==201,raw
    status,raw,_=exchange(service,'POST','/api/dev/experiments/'+json.loads(raw)['experiment_id']+'/run',dict(max_cells=1),headers)
    assert status==202,raw
    assert sorted(seen)==sorted(users)


def test_chat_omits_reasoning_effort_unless_asked(monkeypatch):
    # A non-reasoning model on a provider with a reasoning parameter rejects it; the Advisor never asks.
    p=provider('openai');transport=FakeTransport(p.secret);monkeypatch.setattr(provider_module,'open_request',transport)
    ByokChatClient(p).chat(model='gpt-x',messages=[dict(role='user',content='hi')],max_tokens=8)
    assert 'reasoning_effort' not in transport.fake.requests[-1][1]


def test_bridge_close_returns_while_upstream_is_stalled(tmp_path,loopback):
    import threading,urllib.request
    release=threading.Event()
    def stalled(chat,key,*,url):
        yield dict(_request_id='stalled')
        release.wait(30)
    p=provider()
    bridge=ChatBridge(tmp_path,p.spec,p.secret,token=p.token,upstream=stalled,vision=True,role='eval')
    request=urllib.request.Request(bridge.base_url+'/responses',method='POST',
        data=json.dumps(dict(model='m',input=[dict(role='user',content='hi')])).encode(),
        headers={'Authorization':'Bearer '+p.token,'Content-Type':'application/json'})
    def call():
        try:urllib.request.urlopen(request,timeout=30).read()
        except Exception:pass
    client=threading.Thread(target=call,daemon=True);client.start()
    try:
        time.sleep(.5)
        started=time.monotonic();bridge.close()
        assert time.monotonic()-started<3 and not bridge.thread.is_alive()
    finally:
        release.set();client.join(5)
