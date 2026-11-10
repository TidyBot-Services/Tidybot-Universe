"""Lab PARCC as an Agent source: the same bridges as a user's key, with the lab key, and no Codex quota."""
import json
import secrets
import time
import urllib.error
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_advisor_proxy, dev_agent_config as config, dev_agent_eval as eval_module
from benchmarks.attention_harness import dev_agent_key_vault as vault, dev_agent_parcc_bridge as bridge_module
from benchmarks.attention_harness import dev_agent_provider as provider_module, dev_agent_run_launch as launch
from benchmarks.attention_harness import dev_agent_view
from benchmarks.attention_harness.canary_scan import scan
from benchmarks.attention_harness.dev_agent_byok_client import ByokChatClient
from benchmarks.attention_harness.dev_agent_loop import provider_for_run
from benchmarks.attention_harness.dev_agent_secret import Secret
from benchmarks.attention_harness.dev_agent_workspace import Workspace
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment, offline_dev_identity
from benchmarks.attention_harness.tests.fakes.byok_http import MemoryServer
from benchmarks.attention_harness.tests.test_daily_byok_keys import (call, control, create_memory, fake_probe_transport, fake_process,
    materialize, memory_service, selection)
from benchmarks.attention_harness.tests.test_dev_agent_byok_config import BASE
from benchmarks.attention_harness.tests.test_dev_agent_byok_p2 import FakeTransport
from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG
from benchmarks.attention_harness.tests.test_dev_agent_d4 import eval_factory, fake_view, seed_record


def lab(advisor_model='parcc/GLM',eval_model='parcc/GLM'):
    role=lambda model:dict(credential='lab',provider='parcc',model=model)
    return dict(roles=dict(dev=role('parcc/GLM'),eval=role(eval_model),advisor=role(advisor_model)))


def lab_key(monkeypatch):
    key='sk-canary-'+secrets.token_hex(24)
    monkeypatch.setattr(bridge_module,'parcc_key',lambda:key)
    return key


def test_all_three_roles_can_run_on_the_lab_key():
    chosen=config.normalize(dict(BASE,agent_provider=lab()))
    assert [chosen[role+'_model'] for role in ('dev','eval','advisor')]==['parcc/GLM']*3
    assert all(row['key_source']=='lab' for row in chosen['agent_provider']['roles'].values())
    assert config.display_source(chosen)=='parcc'


@pytest.mark.parametrize('role,change,error',[
    ('dev',dict(model='parcc/Qwen'),'unsupported_agent_model'),
    ('dev',dict(model='gpt-6.1-sol'),'unsupported_agent_model'),
    ('dev',dict(model='openai/gpt-oss-120b'),'unsupported_agent_model'),
    ('eval',dict(model='openai/gpt-oss-120b'),'unsupported_agent_model'),
    ('eval',dict(model='anything-else'),'unsupported_agent_model'),
    ('eval',dict(provider='openai'),'unsupported_agent_model'),
    ('advisor',dict(provider='deepseek'),'lab_provider_not_supported')])
def test_the_lab_key_serves_only_the_lab_models(role,change,error):
    value=lab();value['roles'][role].update(change)
    with pytest.raises(ValueError,match=error):config.normalize(dict(BASE,agent_provider=value))


def test_eval_may_run_on_qwen_and_the_advisor_on_gpt_oss_dev_stays_on_glm():
    # 2026-11-11 (user): Eval may use Qwen (it reads the frames); gpt-oss-120b is a text-only Advisor.
    chosen=config.normalize(dict(BASE,agent_provider=lab('openai/gpt-oss-120b',eval_model='parcc/Qwen')))
    roles=chosen['agent_provider']['roles']
    assert [roles[r]['model'] for r in ('dev','eval','advisor')]==['parcc/GLM','parcc/Qwen','openai/gpt-oss-120b']
    assert not config.text_only(roles['eval']) and config.text_only(roles['advisor'])
    assert config.display_source(chosen)=='parcc'


def test_only_three_lab_parcc_roles_are_labelled_parcc():
    mixed=lab();mixed['roles']['eval']=dict(credential='lab',provider='openai',model='gpt-6.1-sol')
    assert config.display_source(dict(BASE,model_source='codex',agent_provider=mixed))=='codex'
    assert dev_agent_view.clean(dict(BASE,model_source='codex',agent_provider=mixed))['model_source']=='codex'
    assert dev_agent_view.clean(dict(BASE,model_source='codex',agent_provider=lab()))['model_source']=='parcc'
    assert dev_agent_view.clean(dict(model_source='codex'))['model_source']=='codex'


def test_glm_advisor_uses_the_text_prompt_and_qwen_keeps_the_frames():
    text=config.configuration(dict(BASE,agent_provider=lab(),advisor_proxy={'enabled':True}))
    assert text['eval_agent']['model']=='parcc/GLM' and text['eval_agent']['model_source']=='parcc'
    advisor=text['advisor_proxy']
    assert (advisor['model'],advisor['model_source'],advisor['mode'],advisor['max_attempts'])==('parcc/GLM','parcc','text_only',1)
    assert advisor['prompt_version']==dev_advisor_proxy.OLD_PROMPT_VERSION and 'cache_namespace' not in advisor
    seeing=config.configuration(dict(BASE,agent_provider=lab('parcc/Qwen'),advisor_proxy={'enabled':True}))
    assert (seeing['advisor_proxy']['model'],seeing['advisor_proxy']['mode'])==('parcc/Qwen','vision')
    assert seeing['eval_agent']['model']=='parcc/GLM'


def test_vault_gives_every_role_the_lab_key_without_a_saved_credential(monkeypatch):
    key=lab_key(monkeypatch);roles=config.normalize(dict(BASE,agent_provider=lab()))['agent_provider']['roles']
    taken=vault.ProviderKeyVault().take_for_run('owner',roles)
    assert set(taken._keys)=={'lab'} and taken._keys['lab'].reveal()==key
    assert set(taken.roles)=={'dev','eval','advisor'} and all(row['key_source']=='lab' for row in taken.roles.values())


def test_a_host_without_the_lab_key_refuses_the_run(monkeypatch):
    def missing():raise bridge_module.BridgeError('parcc_key_missing')
    monkeypatch.setattr(bridge_module,'parcc_key',missing)
    roles=config.normalize(dict(BASE,agent_provider=lab()))['agent_provider']['roles']
    with pytest.raises(provider_module.ProviderError,match='Missing'):vault.ProviderKeyVault().take_for_run('owner',roles)


def test_lab_codex_roles_still_take_no_key():
    value=lab();value['roles']={role:dict(credential='lab',provider='openai',model='gpt-6.1-sol') for role in ('dev','eval','advisor')}
    roles=config.normalize(dict(BASE,agent_provider=value))['agent_provider']['roles']
    assert vault.ProviderKeyVault().take_for_run('owner',roles).roles=={}


@pytest.mark.parametrize('model,vision',[('parcc/GLM',False),('parcc/Qwen',True),('openai/gpt-oss-120b',False)])
def test_the_image_flag_follows_the_model(monkeypatch,model,vision):
    key=Secret(lab_key(monkeypatch));roles=config.normalize(dict(BASE,agent_provider=lab(model)))['agent_provider']['roles']
    monkeypatch.setattr(vault,'_RUN_KEYS',{'lab':key});monkeypatch.setattr(vault,'_RUN_ROLES',{r:dict(row,base_url=None) for r,row in roles.items()})
    manifest=dict(agent_provider=dict(roles=roles))
    resolved=provider_for_run(manifest,role='advisor')
    assert (resolved.spec.id,resolved.model,resolved.vision,resolved.secret)==('parcc',model,vision,key)
    assert provider_for_run(manifest,role='eval').vision is False


def test_a_users_key_is_excused_from_the_image_check_only_when_declared():
    mine=dict(credential='mine',provider='parcc',model='parcc/GLM')
    assert not config.text_only(mine) and config.routed(mine)
    assert config.text_only(dict(mine,vision=False))


def test_a_declared_text_only_role_is_frozen_and_shown():
    role=dict(credential='mine',provider='deepseek',model='blind-model',vision=False)
    roles=dict(dev=dict(role,vision=None) and dict(credential='mine',provider='deepseek',model='blind-model'),eval=role,advisor=role)
    frozen=config.normalize(dict(BASE,agent_provider=dict(roles=roles)))['agent_provider']['roles']
    assert frozen['eval']['vision'] is False and frozen['advisor']['vision'] is False and 'vision' not in frozen['dev']


def test_a_text_only_advisor_on_a_run_key_passes_the_scheduler_check():
    from benchmarks.attention_harness import dev_advisor_proxy
    dev=dict(credential='run-parcc',provider='parcc',model='parcc/GLM')
    blind=dict(dev,vision=False);roles=dict(dev=dev,eval=blind,advisor=blind)
    made=config.configuration(dict(BASE,agent_provider=dict(roles=roles)))
    checked=dev_advisor_proxy.normalize(made['advisor_proxy'],'codex',agent=True,provider_role=blind)
    assert (checked['mode'],checked['model'],checked['model_source'])==('text_only','parcc/GLM','byok')
    from benchmarks.attention_harness.dev_configuration import independent_eval_advisor
    assert independent_eval_advisor(made['eval_agent'],checked)
    with pytest.raises(ValueError):independent_eval_advisor(made['eval_agent'],{**checked,'prompt_version':made['eval_agent']['prompt_version']})
    with pytest.raises(ValueError):independent_eval_advisor(made['eval_agent'],{**checked,'cache_version':made['eval_agent']['cache_version']})


@pytest.mark.parametrize('role,value',[('dev',False),('eval',True),('eval','no')])
def test_only_eval_or_advisor_can_declare_text_only(role,value):
    roles=dict(dev=dict(credential='mine',provider='deepseek',model='m'))
    roles[role]=dict(credential='mine',provider='deepseek',model='m',vision=value)
    with pytest.raises(ValueError,match='vision'):config.normalize(dict(BASE,agent_provider=dict(roles=roles)))


@pytest.mark.parametrize('roles,expected',[(lab(),['dev','dev','advisor-text']),(lab('parcc/Qwen'),['dev','dev','advisor']),
    (lab('openai/gpt-oss-120b',eval_model='parcc/Qwen'),['dev','eval','advisor-text'])])
def test_preflight_asks_only_a_reader_of_images_to_read_one(monkeypatch,roles,expected):
    lab_key(monkeypatch);seen=[]
    def probe(spec,secret,model,*,role):
        seen.append(role);return provider_module.ProbeResult(dict.fromkeys(('auth','chat','streaming','tools','vision'),'ok'),[])
    roles=config.normalize(dict(BASE,agent_provider=roles))['agent_provider']['roles']
    launch.preflight(lambda chosen:vault.ProviderKeyVault().take_for_run('owner',chosen),roles,
        lambda provider,base:provider_module.presets()[provider],probe_provider=probe)
    assert seen==expected


@pytest.mark.parametrize('vision',[False,True])
def test_eval_bridge_gets_the_image_flag(tmp_path,monkeypatch,vision):
    monkeypatch.setattr(bridge_module.http.server,'ThreadingHTTPServer',MemoryServer)
    seen=[];real=bridge_module.ChatBridge
    def bridge(*args,**kwargs):seen.append(kwargs['vision']);return real(*args,**kwargs)
    monkeypatch.setattr(bridge_module,'ChatBridge',bridge)
    ws=Workspace(tmp_path/'agent').prepare(dict(task_id='fake',task_prompt='Move.'),CONFIG)
    resolved=SimpleNamespace(spec=provider_module.presets()['parcc'],secret=Secret('sk-canary-'+secrets.token_hex(24)),model='parcc/GLM',
        vision=vision,token=secrets.token_urlsafe(32),token_env='ATTENTION_BYOK_BRIDGE_TOKEN')
    evaluator=eval_module.Evaluator(ws,provider=resolved,model='ignored',command_factory=eval_factory(ws))
    assert evaluator.evaluate(seed_record(ws),deadline=time.monotonic()+10)['status']=='completed'
    assert seen==[vision]


def test_glm_advisor_is_sent_no_frames_and_records_its_source(tmp_path,monkeypatch):
    resolved=SimpleNamespace(spec=provider_module.presets()['parcc'],secret=Secret('sk-canary-'+secrets.token_hex(24)),model='parcc/GLM')
    transport=FakeTransport(resolved.secret,None);monkeypatch.setattr(provider_module,'open_request',transport)
    settings=config.configuration(dict(BASE,agent_provider=lab(),advisor_proxy={'enabled':True}))['advisor_proxy']
    record=dev_advisor_proxy.ask(fake_view(),settings,ByokChatClient(resolved),cache_dir=tmp_path)
    sent=transport.fake.requests[-1][1]
    assert sent['model']=='parcc/GLM' and all(isinstance(message['content'],str) for message in sent['messages'])
    assert record['mode']=='text_only' and record['model_source']=='parcc' and record['frames']==[] and record['feedback']
    assert not scan([resolved.secret],roots=[tmp_path],include_processes=True)


def test_parcc_keeps_tool_calls_by_asking_for_the_schema_in_words():
    # Real check 2026-11-05: with response_format on every turn GLM answered at once, without reading a file.
    schema=dict(type='object',properties=dict(passed=dict(type='boolean')))
    body=dict(model='parcc/GLM',instructions='Review.',input=[dict(role='user',content='Go')],
              tools=[dict(type='function',name='exec_command',parameters={})],text=dict(format=dict(type='json_schema',name='verdict',schema=schema,strict=True)))
    chat=bridge_module.to_chat(body,provider_module.presets()['parcc'])
    assert 'response_format' not in chat and chat['tools']
    assert json.dumps(schema,separators=(',',':')) in chat['messages'][0]['content'] and chat['messages'][0]['role']=='system'
    assert 'response_format' in bridge_module.to_chat(body,provider_module.presets()['deepseek'])


@pytest.mark.parametrize('model,effort,sent',[('parcc/Qwen','high',None),('parcc/Qwen','low','low'),('parcc/GLM','high','high')])
def test_the_advisor_never_sends_qwen_the_effort_it_rejects(monkeypatch,model,effort,sent):
    resolved=SimpleNamespace(spec=provider_module.presets()['parcc'],secret=Secret('sk-canary-'+secrets.token_hex(24)),model=model)
    transport=FakeTransport(resolved.secret,None);monkeypatch.setattr(provider_module,'open_request',transport)
    ByokChatClient(resolved).chat(model=model,messages=[dict(role='user',content='Go')],max_tokens=8,reasoning_effort=effort)
    assert transport.fake.requests[-1][1].get('reasoning_effort')==sent


@pytest.mark.parametrize('model,sent',[('parcc/Qwen',None),('parcc/GLM','high'),('openai/gpt-oss-120b','high')])
def test_the_bridge_never_sends_qwen_the_effort_it_rejects(model,sent):
    # Real 2026-11-11: PARCC answers 400 "Unexpected reasoning effort high" for Qwen; every agent role asks for high.
    body=dict(model=model,input=[dict(role='user',content='Go')],reasoning=dict(effort='high'))
    assert bridge_module.to_chat(body,provider_module.presets()['parcc']).get('reasoning_effort')==sent


@pytest.mark.parametrize('model,systems',[('parcc/Qwen',1),('parcc/GLM',2)])
def test_qwen_gets_one_leading_system_message_other_models_are_unchanged(model,systems):
    # Real 2026-11-11: Qwen answered 400 "System message must be at the beginning" to Codex's instructions + developer message.
    body=dict(model=model,instructions='Be the Eval.',input=[dict(role='developer',content=[dict(type='input_text',text='Sandbox rules.')]),
        dict(role='user',content=[dict(type='input_text',text='Review it.')])])
    messages=bridge_module.to_chat(body,provider_module.presets()['parcc'])['messages']
    assert [m['role'] for m in messages].count('system')==systems and messages[0]['role']=='system'
    if model=='parcc/Qwen':
        assert messages[0]['content'].startswith('Be the Eval.\n\nSandbox rules.') and messages[1]=={'role':'user','content':'Review it.'}


def parcc_selection(**choices):
    return dict(selection(byok=False),agent_provider=lab(**choices))


def test_daily_start_hands_the_lab_key_through_the_pipe(tmp_path,monkeypatch,materialize):
    key=Secret(lab_key(monkeypatch));service=memory_service(tmp_path,monkeypatch,vault.ProviderKeyVault());token=control(service)
    monkeypatch.setattr(provider_module,'open_request',fake_probe_transport());launched=fake_process(monkeypatch)
    status,made=create_memory(service,token,parcc_selection())
    assert status==200,made
    assert made['selection']['model_source']=='parcc'
    eid=made['experiment_id'];cell=made['cells'][0]
    status,view=call(service,token,'GET',f'/api/dev-v2/experiments/{eid}/cells/{cell["key"]}/view')
    assert status==200 and view['agent']['model_source']=='parcc',view
    assert [view['agent'][name] for name in ('dev_model','eval_model','advisor_model')]==['parcc/GLM']*3
    status,value=call(service,token,'POST',f'/api/dev-v2/experiments/{eid}/run',{})
    assert status==200 and value['worker_started'] and len(launched)==1,value
    argv,kwargs=launched[0]
    assert '--key-fd' in argv and kwargs['pass_fds']
    directory=tmp_path/'daily'/eid
    assert not scan([key],roots=[directory],sqlite_paths=[directory/'ledger.sqlite3'],
        extra_blobs=[json.dumps(argv),json.dumps(kwargs['env'])],include_processes=False)


def test_an_unreachable_parcc_refuses_the_start_and_the_run_stays_startable(tmp_path,monkeypatch,materialize):
    lab_key(monkeypatch);service=memory_service(tmp_path,monkeypatch,vault.ProviderKeyVault());token=control(service)
    def down(request,**kwargs):raise urllib.error.URLError('timed out')
    monkeypatch.setattr(provider_module,'open_request',down);launched=fake_process(monkeypatch)
    status,made=create_memory(service,token,parcc_selection())
    assert status==200,made
    eid=made['experiment_id']
    status,value=call(service,token,'POST',f'/api/dev-v2/experiments/{eid}/run',{})
    assert status==409 and value['detail']['code']=='provider_preflight_failed' and 'timed out' in value['error'],value
    assert not launched
    monkeypatch.setattr(provider_module,'open_request',fake_probe_transport())
    status,value=call(service,token,'POST',f'/api/dev-v2/experiments/{eid}/run',{})
    assert status==200 and value['worker_started'] and len(launched)==1,value


@pytest.mark.parametrize('advisor_model',['parcc/Qwen','parcc/GLM'])
def test_the_worker_accepts_what_a_lab_run_froze(advisor_model):
    """Every start re-validates the frozen config; a lab Eval or Advisor used to stop the worker there."""
    from benchmarks.attention_harness import dev_advisor_proxy, eval_agent
    from benchmarks.attention_harness.dev_agent_config import configuration, provider_roles
    role=lambda model:dict(credential='lab',provider='parcc',model=model)
    value=dict(roles=dict(dev=role('parcc/GLM'),eval=role('parcc/GLM'),advisor=role(advisor_model)))
    frozen=configuration(dict(dev_mode='agent',tasks=['t'],seeds=[1],policies=['reactive_help'],mode='interactive',agent_provider=value))
    roles=provider_roles(value)['roles']
    assert eval_agent.normalize(frozen['eval_agent'],'codex',agent=True,provider_role=roles['eval'])['model']=='parcc/GLM'
    assert dev_advisor_proxy.normalize(frozen['advisor_proxy'],'codex',agent=True,provider_role=roles['advisor'])['model']==advisor_model
