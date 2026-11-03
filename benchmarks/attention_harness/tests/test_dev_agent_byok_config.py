"""Public role configuration and legacy byte contracts."""
import hashlib
import json
from pathlib import Path
import subprocess
import types
import pytest
from benchmarks.attention_harness import dev_agent_config as config,dev_experiment
from benchmarks.attention_harness.dev_ledger import DevV2Store
from benchmarks.attention_harness.tests.c21_fixtures import base,r4_deployment,offline_dev_identity

BASE=dict(dev_mode='agent',tasks=['open_cabinet'],seeds=[102],policies=['autonomous'],mode='interactive',request_key='byok-golden')


def roles(**changes):
    dev=dict(credential='mine',provider='openai',model='custom-model');dev.update(changes)
    return dict(roles=dict(dev=dev))


def test_old_selection_config_hash_and_manifest(tmp_path,offline_dev_identity):
    def original(name):
        path='benchmarks/attention_harness/'+name+'.py'
        module=types.ModuleType('benchmarks.attention_harness._byok_golden_'+name);module.__package__='benchmarks.attention_harness'
        exec(compile(subprocess.check_output(['git','show','HEAD:'+path]),path,'exec'),module.__dict__)
        return module
    old=original('dev_agent_config')
    for selection in (BASE,dict(BASE,dev_model='parcc/GLM')):
        before=json.dumps(old.configuration(selection),sort_keys=True).encode()
        after=json.dumps(config.configuration(selection),sort_keys=True).encode()
        assert before==after and hashlib.sha256(before).digest()==hashlib.sha256(after).digest()
        assert 'agent_provider' not in config.normalize(selection)
    previous=original('dev_experiment');store=DevV2Store(tmp_path/'db')
    first=previous.create(store,tmp_path/'runs',BASE,agent_policy_version='agent-policy-v1')
    before=Path(first['manifest']['uri']).read_bytes()
    (tmp_path/'db').unlink();store=DevV2Store(tmp_path/'db')
    current=dev_experiment.create(store,tmp_path/'runs',BASE,agent_policy_version='agent-policy-v1')
    assert Path(current['manifest']['uri']).read_bytes()==before


def test_public_manifest(tmp_path,offline_dev_identity):
    store=DevV2Store(tmp_path/'db')
    selection=dict(BASE,agent_provider=roles())
    exp=dev_experiment.create(store,tmp_path/'runs',selection,agent_policy_version='agent-policy-v1')
    manifest=dev_experiment.verify_manifest(exp)
    dev=manifest['agent_provider']['roles']['dev']
    assert dev==dict(credential='mine',provider='openai',model='custom-model',base_url_host='api.openai.com',key_source='user')
    assert manifest['dev_model']=='custom-model'
    assert config.configuration(selection)['agent_provider']==manifest['agent_provider']


@pytest.mark.parametrize('field',['key','api_key','secret','token','authorization','API_KEY'])
@pytest.mark.parametrize('level',['top','list','role'])
def test_secret_fields_rejected(field,level):
    selection=dict(BASE,agent_provider=roles())
    if level=='top':selection[field]='forbidden'
    elif level=='list':selection['nested']=[{'deeper':{field:'forbidden'}}]
    else:selection['agent_provider']['roles']['dev'][field]='forbidden'
    with pytest.raises(ValueError,match='agent_provider_secret_field_forbidden'):config.normalize(selection)


@pytest.mark.parametrize('change',[dict(credential='bad_name'),dict(provider='invalid'),dict(model=''),dict(model=' '),dict(model='a'*129),dict(model=3),dict(provider='openai',model='parcc/GLM')])
def test_bad_role(change):
    with pytest.raises(ValueError):config.normalize(dict(BASE,agent_provider=roles(**change)))


@pytest.mark.parametrize('shared',[False,True])
def test_three_role_matrix(shared):
    value=roles(credential='x')
    for role,name in (('eval','y'),('advisor','z')):
        value['roles'][role]=dict(credential='x' if shared else name,provider='openai',model='gpt-6.1-sol')
    assert config.normalize(dict(BASE,agent_provider=value))['agent_provider']['roles']['eval']['key_source']=='user'
    for role in ('eval','advisor'):value['roles'][role]['credential']='lab'
    result=config.normalize(dict(BASE,agent_provider=value))
    assert result['agent_provider']['roles']['dev']['credential']=='x'
    assert result['agent_provider']['roles']['eval']['key_source']=='lab'
    assert config.normalize(dict(BASE,agent_provider=roles(provider='parcc',model='parcc/GLM')))['dev_model']=='parcc/GLM'


def test_run_provider_binding(monkeypatch):
    import secrets
    from benchmarks.attention_harness import dev_agent_key_vault as vault
    from benchmarks.attention_harness.dev_agent_loop import provider_for_run
    from benchmarks.attention_harness.dev_agent_secret import Secret
    from benchmarks.attention_harness.dev_agent_provider import ProviderError
    key=Secret('sk-'+secrets.token_urlsafe(32));role=config.normalize(dict(BASE,agent_provider=roles()))['agent_provider']['roles']['dev']
    monkeypatch.setattr(vault,'_RUN_KEYS',{'mine':key});monkeypatch.setattr(vault,'_RUN_ROLES',{'dev':dict(role,base_url=None)})
    manifest=dict(agent_provider=dict(roles=dict(dev=role)))
    one=provider_for_run(manifest);two=provider_for_run(manifest)
    assert one.secret==key and one.spec.id=='openai' and one.token!=two.token
    assert provider_for_run({}) is None
    bad=dict(manifest,agent_provider=dict(roles=dict(dev=dict(role,model='different'))))
    with pytest.raises(ProviderError,match='differs'):provider_for_run(bad)
