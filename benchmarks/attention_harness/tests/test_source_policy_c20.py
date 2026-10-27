import copy
import hashlib
import json
from pathlib import Path
import pytest
from openarm365_dev_v2.source_policy import source_limit,drift_record
from benchmarks.attention_harness.memory_validation_generation import parse_generation

DEV={'budget_tier':'dev','runner_boundary':'dev_loop'}

MISSING = object()


@pytest.mark.parametrize('tier', [MISSING, None, '', 'legacy', 'dev', 'formal', 'unknown'])
@pytest.mark.parametrize('runner', [MISSING, None, '', 'dev_loop', 'formal', 'unknown'])
@pytest.mark.parametrize('boundary', [MISSING, None, '', 'dev_loop', 'formal', 'unknown'])
@pytest.mark.parametrize('recorded', [MISSING, None, {}, {'unrelated': 32768}] + [
    {'source_size_limit_bytes': value}
    for value in (0, 1, 16383, 16384, 16385, 20000, 32767, 32768, 32769, None, '16384')
])
def test_main_and_service_source_rules_agree(tier, runner, boundary, recorded):
    from attention_memory_service import source_policy as service
    from openarm365_dev_v2 import source_policy as main

    config = {key: value for key, value in (
        ('budget_tier', tier), ('runner_boundary', runner), ('budget_boundary', boundary)
    ) if value is not MISSING}
    manifest = None if recorded is MISSING else recorded
    before = copy.deepcopy((config, manifest))
    expected_dev = tier == 'dev' and (runner if boundary is MISSING else boundary) == 'dev_loop'
    assert main.dev_tier(config) == service.dev_tier(config) == expected_dev
    expected_limit = (manifest['source_size_limit_bytes']
                      if manifest is not None and 'source_size_limit_bytes' in manifest
                      else 32768 if expected_dev else 16384)
    if recorded is MISSING:
        assert main.source_limit(config) == service.source_limit(config) == expected_limit
    else:
        assert main.source_limit(config, manifest) == service.source_limit(config, manifest) == expected_limit
    assert (config, manifest) == before


def test_dev_imports_and_policy_validation_without_service(tmp_path):
    import os
    import subprocess
    import sys
    import textwrap

    root = Path(__file__).resolve().parents[3]
    code = textwrap.dedent('''
        import sys
        class NoMemoryService:
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split('.')[0] == 'attention_memory_service':
                    raise AssertionError('Dev imported Memory service: ' + fullname)
        sys.meta_path.insert(0, NoMemoryService())
        import openarm365_dev.policy_validation as validation
        import openarm365_dev_v2.dev_config
        import openarm365_dev.sandbox
        import openarm365_dev_v2.runner
        import benchmarks.attention_harness.dev_experiment
        import benchmarks.attention_harness.dev_v2_memory_import
        import benchmarks.attention_harness.memory_source_drift
        from openarm365_dev_v2.source_policy import drift_record
        config = dict(budget_tier='dev', runner_boundary='dev_loop')
        source = 'from robot_sdk import sensors\\n#'
        source += 'x' * (32768 - len(source.encode()))
        validation.validate_generated_policy(source, config=config)
        for cfg, manifest in ((config, None), (config, {'source_size_limit_bytes': 16384}), ({}, None)):
            rejected = source + 'x' if manifest is None and cfg else source
            try:
                validation.validate_generated_policy(rejected, config=cfg, manifest=manifest)
            except validation.GeneratedPolicyError:
                pass
            else:
                raise AssertionError('source cap not enforced')
        validation.validate_generated_policy('from robot_sdk import sensors')
        assert drift_record(config, {}, {}, 'a', 'a') is None

        # Exercise the diagnostic make_lock/make_dev_config call chain with
        # synthetic resources. No installed robot/model or simulator is read.
        import json
        from pathlib import Path
        from unittest.mock import patch
        from openarm365_dev_v2 import dev_config as dc, environment
        root = Path.cwd() / 'synthetic-source'
        root.mkdir()
        for name in ('openarm365_dev_v2', 'openarm365_dev', 'openarm365', 'curobo_v2'):
            folder = root / name
            folder.mkdir()
            (folder / 'sdk.py').write_text('# synthetic SDK source')
        harness = root / 'benchmarks/attention_harness'
        for name in (
            'v2_evaluator.py', 'daily_dev_v2.py', 'core/policies.py', 'core/assessment.py',
            'core/models.py', 'core/trace.py', 'core/dev_store.py', 'formal_runner_boundary.py',
            'policy_input.py', 'parcc_client.py', 'dev_attention.py', 'ui_server.py',
            'ui/live.js', 'ui/index.html', 'ui_history.py', 'ui_observability.py',
            'ui_media.py', 'robocasa_native/policy_sandbox.py', 'robocasa365/recording.py',
            'robocasa365/human_camera.py', 'dev_failure_rules.json',
        ):
            path = harness / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        for name in ('planner.json', 'python.txt', 'assets-manifest.json',
                     'assets-installation-lock.json', 'assets-installed-manifest.json',
                     'openarm365/configs/robot_parameters_v1.json', 'resource.txt'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{}')
        identity = root / 'openarm365/model/identity.json'
        identity.parent.mkdir()
        resource = root / 'resource.txt'
        identity.write_text(json.dumps({'sources': {str(resource): dc.sha(resource)}}))
        with patch.multiple(dc, ROOT=root, LOCK=root/'planner.json',
                            LOCK_SHA=dc.sha(root/'planner.json'), SIM_PYTHON=root/'python.txt',
                            ASSETS=root/'assets-manifest.json', ROBocasa=root/'robocasa',
                            ROBOSUITE=root/'robosuite'), patch.object(
                                environment, 'initial_joints', return_value={'left': [0.] * 7, 'right': [0.] * 7}):
            ref = dc.make_lock(root/'lock.json')
            frozen = dc.make_dev_config('open_cabinet', 101, ref, budget_tier='legacy')
            assert dc.validate_dev_config(frozen) == frozen
            # Older Dev configs need no r3 planner; still validate their origin lock.
            dev = dc._make_dev_config('open_cabinet', 101, ref, budget_tier='dev')
            dev['origin_dev_lock'] = ref
            assert dc.validate_dev_config(dev) == dev
        assert not any(name.split('.')[0] == 'attention_memory_service' for name in sys.modules)
    ''')
    result = subprocess.run([sys.executable, '-B', '-c', code], cwd=tmp_path,
                            env=dict(os.environ, PYTHONPATH=str(root), PYTHONDONTWRITEBYTECODE='1'),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr

def source(size):
    base='from robot_sdk import sensors\nsensors.get_observation()\n#'
    return base+'x'*(size-len(base.encode()))

@pytest.mark.parametrize('config,size,accepted',[(DEV,20000,True),(DEV,32768,True),(DEV,32769,False),({'budget_tier':'legacy'},16385,False),({'budget_tier':'dev','budget_boundary':'formal'},16385,False),({},16384,True)])
def test_sizes(config,size,accepted):
    _,_,error=parse_generation(json.dumps(dict(source=source(size),reason='test')),config)
    assert (error is None)==accepted
    if not accepted:assert error=='source_size_limit_exceeded'

def test_historical_manifest_limit_wins():
    manifest={'source_size_limit_bytes':16384}
    assert source_limit(DEV,manifest)==16384
    assert parse_generation(json.dumps(dict(source=source(20000),reason='test')),DEV,manifest)[2]=='source_size_limit_exceeded'
    assert manifest=={'source_size_limit_bytes':16384}

def test_input_headroom():
    from benchmarks.attention_harness.dev_configuration import MAX_INPUT_BYTES
    assert MAX_INPUT_BYTES==150000
    assert MAX_INPUT_BYTES-2*source_limit(DEV)==84464

def locks(tmp_path):
    name=str(tmp_path/'openarm365_dev_v2/sdk.py')
    old=dict(source_root=str(tmp_path),files={name:'a'*64},source_sets={str(Path(name).parent):[name]},planner_lock={'path':str(tmp_path/'planner.json'),'sha256':'c'*64})
    new=copy.deepcopy(old);new['files'][name]='b'*64
    return old,new,name

def test_exact_drift(tmp_path):
    from attention_memory_service import source_policy as service
    old,new,name=locks(tmp_path)
    record=drift_record(DEV,old,new,'c'*64,'d'*64)
    assert record==service.drift_record(DEV,old,new,'c'*64,'d'*64)
    assert record['files']==[dict(path=name,origin_sha256='a'*64,current_sha256='b'*64)]
    assert record['origin_lock_sha256']=='c'*64 and record['current_lock_sha256']=='d'*64
    for cfg in ({},{'budget_tier':'legacy'},{'budget_tier':'dev'},{'budget_tier':'dev','budget_boundary':'formal'}):
        for implementation in (drift_record, service.drift_record):
            with pytest.raises(ValueError,match='source evidence digest drift'):implementation(cfg,old,new,'c'*64,'d'*64)
    new['planner_lock']['sha256']='e'*64
    for implementation in (drift_record, service.drift_record):
        with pytest.raises(ValueError,match='non-source identity drift'):implementation(DEV,old,new,'c'*64,'d'*64)

@pytest.mark.parametrize('recorded',[16384,32768])
def test_validation_generation_uses_manifest_bound(tmp_path,monkeypatch,recorded):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from benchmarks.attention_harness import memory_validation_generation as gen,dev_v2_loop as loop
    from benchmarks.attention_harness.tests.test_dev_memory_prompt_bound import CONFIG
    cfg={**CONFIG,**DEV}
    loop.immutable(tmp_path/'config.json',cfg)
    origin=loop.immutable(tmp_path/'origin.py',source(16205))
    manifest=loop.immutable(tmp_path/'manifest.json',dict(source_size_limit_bytes=recorded))
    cell=dict(artifact_dir=str(tmp_path),experiment_id='exp',budget={'dev_calls':1})
    store=SimpleNamespace(cell=lambda k:cell,tx=lambda:nullcontext(None),get=lambda *a:dict(selection={'model_source':'codex'},manifest=manifest),version=lambda *a:None)
    monkeypatch.setattr(loop,'get_task',lambda _:SimpleNamespace(system_prompt=lambda c:'Write a public SDK policy.'))
    def provider(store,key,version,kind,messages,owner,directory,client):
        assert json.loads(messages[1]['content'])['source_size_limit_bytes']==recorded
        request=loop.immutable(directory/'request.json',dict(messages=messages))
        text=json.dumps(dict(source=source(20000),reason='retry'))
        response=loop.immutable(directory/'response.json',dict(content=text))
        return text,request,response,{}
    monkeypatch.setattr(gen,'provider_call',provider)
    record=gen.generate(store,'cell',cfg,origin,{},None)
    assert record['valid']==(recorded==32768)


def test_two_32kb_sources_and_feedback_fit_input(monkeypatch):
    from types import SimpleNamespace
    from benchmarks.attention_harness import dev_v2_loop as loop
    from benchmarks.attention_harness.dev_feedback_projection import input_bound
    from benchmarks.attention_harness.dev_configuration import MAX_INPUT_BYTES
    from benchmarks.attention_harness.tests.test_dev_memory_prompt_bound import CONFIG
    monkeypatch.setattr(loop,'get_task',lambda _:SimpleNamespace(system_prompt=lambda c:'Write a public SDK policy.'))
    messages=loop.model_messages({**CONFIG,**DEV},source(32768),{'public_error':'TimeoutError','prior_source':source(32768),'action_trace':[{'message':'feedback '*1000}]},{'memory':[]},{},source_size_limit_bytes=32768)
    assert input_bound(messages)<MAX_INPUT_BYTES
