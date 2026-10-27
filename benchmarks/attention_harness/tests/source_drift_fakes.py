"""C20 synthetic SDK drift; no model files, provider or simulator."""
from contextlib import contextmanager,ExitStack
from pathlib import Path
import json
from unittest.mock import patch
from . import autovalidate_fakes as fakes
from benchmarks.attention_harness import memory_independent_validator as validator
from benchmarks.attention_harness.memory_validation_worker import tick,set_enabled

@contextmanager
def scenario(root):
    original=fakes.Fixture.make
    write=fakes.Fixture.write
    holder={}
    def make(f,name,*args,**kwargs):
        f.config_overrides=dict(getattr(f,'config_overrides',{}),budget_tier='dev',runner_boundary='dev_loop',dev_lock=dict(path=f.lock['uri'],sha256=f.lock['sha256']))
        runner=holder.get('runner');drift=None
        if runner is not None and hasattr(runner,'batch'):
            batch=runner.batch
            config=json.loads(Path(batch['config']['uri']).read_bytes())
            f.config_overrides=dict(config)
            from benchmarks.attention_harness.dev_ledger import DevV2Store
            store=DevV2Store(next(iter(batch['cells'].values()))['ledger'])
            with store.tx() as db:exp=store.get(db,'experiments',batch['experiment_id'])
            manifest=json.loads(Path(exp['manifest']['uri']).read_bytes());drift=manifest['source_drift']
            f.manifest_overrides.update(identity_lock=config['dev_lock'],source_drift=drift)
        result=original(f,name,*args,**kwargs)
        if drift:
            result['descriptor']['source_drift']=drift
            f.write(f.root/name/'execution.json',result['descriptor'])
        return result
    def write_config(path,value):
        runner=holder.get('runner')
        if runner is not None and hasattr(runner,'batch') and Path(path).name=='config.json':
            raw=Path(runner.batch['config']['uri']).read_text()
            if value==json.loads(raw):value=raw
        return write(path,value)
    real_validate=validator.validate_arm
    def validate(**kw):
        runner=holder['runner']
        record=runner.f.records.get(kw['cell'],{})
        if record.get('descriptor',{}).get('source_drift'):
            kw['source_drift']=record['descriptor']['source_drift']
        with fakes.synthetic_creation(runner.f):return real_validate(**kw)
    with ExitStack() as stack:
        stack.enter_context(patch.object(fakes.Fixture,'make',make))
        stack.enter_context(patch.object(fakes.Fixture,'write',staticmethod(write_config)))
        runner=fakes.AutomaticOriginFakeRunner(root/'evidence',root/'worker');holder['runner']=runner
        stack.enter_context(patch.object(validator,'validate_arm',validate))
        stack.enter_context(patch.object(fakes,'validate_arm',validate))
        sdk=root/'evidence/sources/openarm365_dev_v2/sdk.py'
        old=sdk.read_bytes();sdk.write_bytes(old+b'# current code\n')
        yield runner,sdk,old


def complete(runner):
    deps=runner.deps(runner.provision.state_root);state={};set_enabled(deps.root,True)
    for _ in range(35):
        tick(state,deps)
        if state.get('results'):break
        if state.get('status','').startswith('Automatic checks paused:'):raise AssertionError(state['status'])
        if (state.get('current') or {}).get('active'):runner.phase='completed'
    assert state.get('results'),state.get('status')
    assert state['results'][0]['status']=='trusted',state.get('status')
    assert runner.replays==2 and len(runner.starts)==10
    return state['results'][0]['decision']
