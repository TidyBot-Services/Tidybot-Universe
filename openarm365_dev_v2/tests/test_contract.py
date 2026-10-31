import copy,json
import hashlib
from pathlib import Path
from unittest.mock import patch,MagicMock
import pytest
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment
from openarm365_dev_v2 import dev_config
from benchmarks.attention_harness import dev_tasks

@pytest.fixture
def lock(tmp_path_factory, r4_deployment):
    settings=r4_deployment
    return dev_config.make_lock(tmp_path_factory.mktemp('lock')/'identity.json',execution_settings=settings)
@pytest.mark.parametrize('task',list(dev_tasks.TASKS))
@pytest.mark.parametrize('seed',[101,102,125])
def test_task_config(lock,task,seed):
    c=dev_config.make_dev_config(task,seed,lock);assert dev_config.validate_dev_config(c)==c
    t=dev_tasks.get_task(task);prompt=t.system_prompt()
    assert t.task_prompt in prompt and 'WORLD' in prompt
    if task=='open_cabinet':assert 'sink' not in prompt and 'Panda' not in prompt
    assert c['robot']=='OpenArm365' and c['execution_arm']=='both'
    assert c['budget_tier']=='dev' and c['sdk_call_limit']==320 and c['horizon']==9600
    assert c['contact_exempt_objects']=='movable_objects'

def test_contact_exemption_is_frozen_and_hashed(monkeypatch):
    # The worktree intentionally omits some copied native model files; config
    # field validation can be checked without weakening production lock checks.
    monkeypatch.setattr(dev_config,'verify_lock',lambda *args,**kwargs: None)
    c=dev_config.make_dev_config('pick_place_counter_to_sink',102,{'path':'fixture','sha256':'fixture'})
    disabled=dict(c,contact_exempt_objects='none')
    assert dev_config.validate_dev_config(disabled)==disabled
    legacy=dict(disabled)
    for key in dev_config.PLANNER_CONFIG_KEYS:legacy.pop(key)
    assert dev_config.validate_dev_config(legacy)==legacy
    assert dev_config.validate_dev_config(c)==c
    digest=lambda x:hashlib.sha256(json.dumps(x,sort_keys=True,allow_nan=False).encode()).hexdigest()
    assert digest(c)!=digest(disabled)
    assert digest(c)!=digest(legacy)
    with pytest.raises(ValueError,match='contact_exempt_objects'):
        dev_config.validate_dev_config(dict(c,contact_exempt_objects='fixture'))
@pytest.mark.parametrize('seed',[-1,0,100,126,True,101.0,'101',None])
def test_seed_rejected(lock,seed):
    with pytest.raises(ValueError):dev_config.make_dev_config('open_cabinet',seed,lock)
@pytest.mark.parametrize('kwargs',[{'layout_id':12},{'style_id':35},{'task_id':'absent'}])
def test_unregistered_conditions(lock,kwargs):
    values=dict(task_id='open_cabinet',seed=101,lock=lock);values.update(kwargs)
    with pytest.raises(ValueError):dev_config.make_dev_config(**values)
@pytest.mark.parametrize('key,value',[('seed',True),('horizon',2401),('task_prompt','other'),('formal_eligible',True),('execution_arm','left')])
def test_tampering(lock,key,value):
    c=dev_config.make_dev_config('open_cabinet',101,lock);c[key]=value
    with pytest.raises(ValueError):dev_config.validate_dev_config(c)
def test_incomplete_registration():
    with pytest.raises(ValueError):dev_tasks.register_dev_task({})
    with pytest.raises(ValueError):dev_tasks.register_dev_task(dev_tasks.DevTask('open_cabinet','wrong','x','none'))
def test_legacy_config_rejects_refrozen_model():
    from openarm365_dev.config import validate_config,make_config
    validate_config(make_config())
    old=dev_config.ROOT/'openarm365_dev/configs/dev_counter_sink_seed101.json'
    with pytest.raises(ValueError,match='frozen configuration mismatch'):
        validate_config(json.loads(old.read_text()))
def test_owner_launch(tmp_path):
    from openarm365_dev_v2.owner import NativeOwnerV2
    owner=NativeOwnerV2(python='python',config=tmp_path/'config',evidence=tmp_path,deadline=100,cancel_check=lambda:False)
    process=MagicMock();process.pid=999999;process.args=[]
    with patch('openarm365_dev_v2.owner.subprocess.Popen',return_value=process) as spawn, patch('openarm365_dev_v2.owner.selectors.DefaultSelector'),patch.object(owner,'read',return_value={'kind':'ready'}):
        owner.start()
    args=spawn.call_args;assert 'openarm365_dev_v2.worker' in args.args[0]
    root=dev_config.ROOT
    assert args.kwargs['cwd']==root and args.kwargs['env']['PYTHONPATH']==str(root)
    assert not any(k for k in args.kwargs['env'] if any(s in k.upper() for s in ['KEY','TOKEN','SECRET','PASSWORD']))
def test_no_retired_or_fixed_owner_routing():
    for name in ['owner.py','runner.py','worker.py']:
        text=(dev_config.ROOT/'openarm365_dev_v2'/name).read_text()
        assert 'fixed_owner(' not in text and 'DedicatedService(' not in text

def test_transitive_native_model_identity(lock):
    saved=json.loads(Path(lock['path']).read_bytes())
    assert len(saved['model_sources'])>=182
    assert all(saved['files'][k]==v for k,v in saved['model_sources'].items())

def test_native_model_copy_tampering_rejected(tmp_path):
    from openarm365_dev_v2.dev_config import model_sources,sha
    relative=Path('openarm365/model/robot.xml');copied=tmp_path/relative
    copied.parent.mkdir(parents=True,exist_ok=True);copied.write_text('unchanged model')
    identity=tmp_path/'identity.json';identity.write_text(json.dumps({'sources':{relative.as_posix():sha(copied)}}))
    assert model_sources(identity,runtime_root=tmp_path)=={str(copied):sha(copied)}
    identity.write_text(json.dumps({'sources':{str(copied):sha(copied)}}))
    with pytest.raises(ValueError,match='repository-relative'):model_sources(identity,runtime_root=tmp_path)
    identity.write_text(json.dumps({'sources':{relative.as_posix():sha(copied)}}))
    copied.write_text('tampered geometry')
    with pytest.raises(ValueError,match='native robot model identity drift'):model_sources(identity,runtime_root=tmp_path)


@pytest.fixture(autouse=True)
def offline_r4_snapshot(r4_deployment):
    """New dev defaults bind an isolated authenticated snapshot."""
