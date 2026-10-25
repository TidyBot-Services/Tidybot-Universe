"""C3 frozen configuration, legacy bytes, SDK identity and copies (no native runtime)."""
import copy
import hashlib
import json
import subprocess
from types import ModuleType
from pathlib import Path
import pytest
from curobo_v2 import execution_settings as es
from openarm365_dev_v2 import dev_config as d,formal_config
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment


def baseline(path):
    module=ModuleType('baseline');module.__file__=str(Path(path).resolve());module.__package__=path.rsplit('/',1)[0].replace('/','.')
    exec(compile(subprocess.check_output(['git','show','3344c6f:'+path],text=True),path,'exec'),module.__dict__)
    return module


def config(monkeypatch,**kw):
    monkeypatch.setattr(d,'verify_lock',lambda *a,**k:None)
    return d.make_dev_config('open_cabinet',102,{'path':'fixture','sha256':'fixture'},**kw)


def test_defaults_exact_and_boundary(monkeypatch,r3_deployment):
    c=config(monkeypatch)
    assert c['execution_settings']==es.R3_20261025.to_dict()
    assert d.validate_dev_config(c) is c
    for tier,boundary in [('legacy','dev_loop'),('legacy','formal')]:
        legacy=config(monkeypatch,budget_tier=tier,budget_boundary=boundary)
        assert 'execution_settings' not in legacy
        legacy['execution_settings']=c['execution_settings']
        with pytest.raises(ValueError,match='requires dev_loop'):d.validate_dev_config(legacy,budget_boundary=boundary)
    with pytest.raises(ValueError,match='requires dev_loop'):
        d.validate_dev_config(c,budget_boundary='formal')
    formal=formal_config.make_formal_config('open_cabinet',102,c['dev_lock'],attempt_wall_seconds=480,whole_case_wall_seconds=600,sdk_call_limit=80)
    formal['execution_settings']=c['execution_settings']
    with pytest.raises(ValueError):formal_config.validate_formal_config(formal)


@pytest.mark.parametrize('key',list(es.R3_20261025.__dataclass_fields__))
def test_missing_and_changed_fields(monkeypatch,r3_deployment,key):
    c=config(monkeypatch);del c['execution_settings'][key]
    with pytest.raises(ValueError):d.validate_dev_config(c)
    c=config(monkeypatch);c['execution_settings'][key]=None
    with pytest.raises(ValueError):d.validate_dev_config(c)


def test_missing_registration_never_falls_back(monkeypatch,tmp_path):
    from curobo_v2 import identity
    path=tmp_path/'trusted.json';path.write_text('{}')
    monkeypatch.setattr(identity,'DEPLOYMENT_REGISTRY',path)
    monkeypatch.setattr(identity,'_registry_digest',None)
    with pytest.raises(ValueError,match='registered deployment-06'):config(monkeypatch)
    assert 'execution_settings' not in config(monkeypatch,budget_tier='legacy')


@pytest.mark.parametrize('tier',['dev','legacy'])
def test_legacy_expected_config_and_memory_context_bytes(monkeypatch,tier):
    old=baseline('openarm365_dev_v2/dev_config.py')
    old.verify_lock=lambda *a,**k:None
    monkeypatch.setattr(d,'verify_lock',old.verify_lock)
    c=old.make_dev_config('open_cabinet',102,{'path':'fixture','sha256':'fixture'},budget_tier=tier)
    wire=json.dumps(c).encode()
    assert d.validate_dev_config(c) is c and json.dumps(c).encode()==wire
    from benchmarks.attention_harness import dev_planner_memory as memory
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    c['scene_contract']=registered_contract_ref()
    identity=dict(contract='fixed contract',prompt='fixed prompt',implementation={},planner_lock='fixed')
    prior=baseline('benchmarks/attention_harness/dev_planner_memory.py');prior.sdk_identity=lambda _:identity
    monkeypatch.setattr(memory,'sdk_identity',lambda _:identity)
    assert json.dumps(memory.context(c,'test')).encode()==json.dumps(prior.context(c,'test')).encode()


def test_profile_context_sdk_interface_and_fork(monkeypatch,r3_deployment):
    from benchmarks.attention_harness import dev_planner_memory as memory,dev_fork
    from benchmarks.attention_harness.dev_tasks import dev_sdk,dev_sdk_prompt
    from openarm365_dev_v2.scene_contract import registered_contract_ref
    c=config(monkeypatch);c['scene_contract']=registered_contract_ref()
    old=copy.deepcopy(c);del old['execution_settings']
    def identity(c):return dict(contract=dev_sdk(c),prompt=dev_sdk_prompt(c),implementation={},planner_lock='fixed')
    monkeypatch.setattr(memory,'sdk_identity',identity)
    before=memory.context(old,'test');after=memory.context(c,'test')
    assert after==dict(before,execution_profile=es.PROFILE)
    assert memory.sdk_interface_sha256(identity(old))==memory.sdk_interface_sha256(identity(c))
    preview=dev_fork.preview_config(old)
    assert preview['execution_settings']==c['execution_settings']
    rows=dev_fork.checks(old,None,preview,carry='code',executed=True,valid=True)
    assert any(r['id']=='execution_settings' and r['level']=='warn' and 'legacy → r3-20261025' in r['title'] for r in rows)


def test_identity_lock_selection(monkeypatch,tmp_path,r3_deployment):
    monkeypatch.setattr(d,'model_sources',lambda *a,**k:{})
    for settings in [None,r3_deployment]:
        ref=d.make_lock(tmp_path/('legacy.json' if settings is None else 'r3.json'),execution_settings=settings)
        lock=d.verify_lock(ref)
        expected={'path':str(d.LOCK),'sha256':d.LOCK_SHA} if settings is None else dict(path=settings.planner_deployment['lock_path'],sha256=settings.planner_deployment['sha256'])
        assert lock['planner_lock']==expected and lock['files'][expected['path']]==expected['sha256']
        if settings is not None:assert d.make_dev_config('open_cabinet',102,ref)['execution_settings']==settings.to_dict()
        else:
            with pytest.raises(ValueError,match='planner lock differs'):d.make_dev_config('open_cabinet',102,ref)


@pytest.mark.parametrize('r3',[False,True])
@pytest.mark.parametrize('cross',[False,True])
def test_copy_target_bytes(monkeypatch,tmp_path,r3_deployment,r3,cross):
    from benchmarks.attention_harness.memory_validation_provision import target_config,target_config_text
    from benchmarks.attention_harness.dev_v2_loop import immutable
    c=config(monkeypatch)
    c['scene_contract']={'uri':'origin','sha256':'y'}
    if not r3:del c['execution_settings']
    origin=dict(config=immutable(tmp_path/'config.json',c))
    if cross:origin['cross_seed']=dict(seed=114,contract={'uri':'target','sha256':'x'})
    target=target_config(origin);text=target_config_text(origin)
    assert target==json.loads(text)
    assert target.get('execution_settings')==c.get('execution_settings')
    if not cross:assert text==Path(origin['config']['uri']).read_text()
    else:assert target==dict(c,seed=114,global_random_seed=114,scene_contract={'uri':'target','sha256':'x'})


@pytest.mark.parametrize('r3',[False,True])
def test_source_revision_lock_keeps_config_deployment(monkeypatch,tmp_path,r3_deployment,r3):
    import ast,inspect
    from unittest.mock import Mock
    from benchmarks.attention_harness import dev_integration
    tree=ast.parse(inspect.getsource(dev_integration.rebind_prepared))
    names={'execution_settings','new_ref'}
    statements=[n for n in ast.walk(tree) if isinstance(n,ast.Assign) and
                any(isinstance(t,ast.Name) and t.id in names for t in n.targets)]
    c={'execution_settings':r3_deployment.to_dict()} if r3 else {}
    constructor=Mock(return_value={'path':'fake','sha256':'fake'})
    scope=dict(old_manifest={'configs':{'task:seed':c}},from_config=es.from_config,
               make_lock=constructor,lock_path=tmp_path/'new-lock.json')
    exec(compile(ast.Module(body=statements,type_ignores=[]),'revision-lock','exec'),scope)
    constructor.assert_called_once_with(scope['lock_path'],**(dict(execution_settings=r3_deployment) if r3 else {}))
