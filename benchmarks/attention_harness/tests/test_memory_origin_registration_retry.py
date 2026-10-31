"""Synthetic post-validation failures and operator recovery; no native execution."""
import fcntl
import json
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarks.attention_harness import memory_origin_evidence as origin
from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness import memory_independent_validator as validator
from benchmarks.attention_harness import dev_v2_memory_import as exporter
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.memory_origin_registration import seal
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.tests.agent_origin_fakes import build, replay
from benchmarks.attention_harness.tests.autovalidate_fakes import outcome, synthetic_creation


def snapshot(root):
    return {str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file() and p.name!='worker.lock'}


@pytest.fixture
def failed(tmp_path,monkeypatch):
    f=build(tmp_path,monkeypatch,validation=True)
    outcome(f.service,f.note['memory_id'],0,ctx=dict(f.context,namespace='offline-agent-origin'))
    monkeypatch.setattr(validator,'GpuMeter',lambda:SimpleNamespace(start=lambda:None,
        stop=lambda:dict(gpu_seconds=1.,wall_seconds=1.,samples=1)))
    def submit(fn,*args):
        future=Future()
        try:future.set_result(fn(*args))
        except BaseException as exc:future.set_exception(exc)
        return future
    runner=SimpleNamespace(provision=f.provision,validator_root=f.native.validator,futures={},pool=SimpleNamespace(submit=submit),
        replay=Mock(side_effect=lambda *a,**kw:replay(f,*a,**kw)))
    preparation=origin.OriginPreparation(runner)
    note=f.service.validation_queue()[0];current=preparation.needed(note)
    real_prepare=exporter.prepare
    def fail_source(*args,**kwargs):
        if kwargs.get('source'):
            out=Path(args[4]);out.mkdir(parents=True);(out/'partial.txt').write_text('incomplete source')
            raise RuntimeError('Controlled post-validation export failure')
        return real_prepare(*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(exporter,'prepare',fail_source)
        preparation.start(current)
    receipt=preparation.finish(current)
    assert receipt['status']=='error' and runner.replay.call_count==2
    root=f.provision.state_root;directory=preparation.directory(current)
    assert json.loads((directory/'validation.json').read_bytes())['status']=='passed'
    state=dict(current=current,runs_today=1,counted={current['origin_key']:'old-day'},results=[],
        origin_errors={current['origin_key']:dict(receipt,title=current['title'],memory_id=note['memory_id'])},note_errors={note['memory_id']:dict(memory_id=note['memory_id'],reason='old error')},
        check_errors=[dict(memory_id=note['memory_id'],reason='old error')])
    worker.atomic(root/'state.json',state)
    worker.atomic(root/'runtime.json',dict(f.provision.config,validator_root=str(f.native.validator)))
    # No replay entry point may be invoked by any retry or refusal.
    no_replay=Mock(side_effect=AssertionError('retry called independent validation'))
    monkeypatch.setattr(origin,'validate_origin',no_replay)
    runner.replay.reset_mock()
    return SimpleNamespace(f=f,root=root,directory=directory,current=current,state=state,
        receipt=receipt,no_replay=no_replay,replay_mock=runner.replay,export=f.native.root/'memory-origin-imports'/current['export_key'])


def retry(x,reason='Reviewed post-validation export failure'):
    return worker.retry_origin_registration(x.root,x.f.note['memory_id'],reason,client=x.f.service)


def test_retry_archives_registers_and_preserves_validation_without_replay(failed):
    x=failed;before=snapshot(x.directory);source=snapshot(x.export/'source');execution=snapshot(x.export/'execution')
    assert worker.main(['--state-dir',str(x.root),'--retry-origin-registration',x.f.note['memory_id'],
        '--reason','Reviewed post-validation export failure'],client=x.f.service)==0
    receipt=json.loads((x.directory/'receipt.json').read_bytes());state=json.loads((x.root/'state.json').read_bytes())
    event=state['origin_registration_retries'][0];archive=Path(event['archive'])
    assert receipt['status']=='passed' and receipt['registration']['memory_id']==x.current['origin_key']+'-candidate'
    assert (x.directory/'validation.json').read_bytes()==before['validation.json']
    assert (archive/'receipt.json').read_bytes()==before['receipt.json'] and snapshot(archive/'source')==source
    assert not (x.export/'source').exists() and snapshot(x.export/'execution')==execution
    assert event['status']=='registered' and event['actor'] and isinstance(event['uid'],int)
    assert event['executor_catalog']['uri']==str(x.export/'execution/catalog.json')
    assert event['export_keys'][0]!=x.current['export_key']
    assert state['current'] is None and state['runs_today']==1 and state['counted']==x.state['counted']
    assert state['pending']['note']['memory_id']==x.f.note['memory_id']
    assert state['pending']['note']['channel']=='dev_agent'
    assert not state['origin_errors'] and not state['note_errors'] and not state['check_errors']
    for gateway in (None,x.f.service):
        view=automatic_checks(x.root,gateway)
        assert view['events'][-1]['event']=='origin_registration_retried'
        assert str(x.root.parent) not in json.dumps(view) and 'actor' not in view['events'][-1]
    assert x.f.provision.resolve(x.f.note)['agent_registration']==receipt['registration']
    before=snapshot(x.root);retry(x)
    assert snapshot(x.root)==before
    x.no_replay.assert_not_called()
    x.replay_mock.assert_not_called()


@pytest.mark.parametrize('fault',[
    'validation_failed','validation_refused','validation_seal','missing_validation','receipt_refused','receipt_passed',
    'registration','catalogs','candidate','linked_candidate','result','different_current','missing_reason','lock',
    'validator_file','validator_descriptor','receipt_link','source_link','dispatch_link','invalid_export_key',
])
def test_refusals_preserve_evidence(failed,monkeypatch,fault):
    x=failed;receipt=dict(x.receipt);state=x.state;reason='Reviewed'
    if fault.startswith('validation_') and fault!='validation_seal':
        value=json.loads((x.directory/'validation.json').read_bytes());value['status']=fault.removeprefix('validation_')
        worker.atomic(x.directory/'validation.json',value);receipt['validation']=seal(x.directory/'validation.json')
    elif fault=='validation_seal':receipt['validation']['sha256']='0'*64
    elif fault=='missing_validation':(x.directory/'validation.json').unlink()
    elif fault.startswith('receipt_') and fault!='receipt_link':receipt['status']=fault.removeprefix('receipt_')
    elif fault=='registration':receipt['registration']={}
    elif fault=='catalogs':receipt['catalogs']={'something':{}}
    elif fault in ('candidate','linked_candidate'):
        rows=x.f.service.list_dev_v2_memories()
        if fault=='candidate':rows.append(dict(memory_id=x.f.note['memory_id'],status='candidate'))
        else:rows.append(dict(memory_id='another',status='rejected',from_provisional=x.f.note['memory_id']))
        monkeypatch.setattr(x.f.service,'list_dev_v2_memories',lambda:rows)
    elif fault=='result':state['results']=[dict(memory_id=x.f.note['memory_id'],status='inconclusive')]
    elif fault=='different_current':state['current']=dict(kind='begin')
    elif fault=='missing_reason':reason='  '
    elif fault in ('validator_file','validator_descriptor'):
        validation=json.loads((x.directory/'validation.json').read_bytes())
        p=Path(validation['descriptor']['uri']) if fault=='validator_descriptor' else Path(validation['catalog']['root'])/'safety.json'
        p.chmod(0o644);p.write_text('{}')
    elif fault=='source_link':
        source=x.export/'source';source.rename(x.export/'source-copy');source.symlink_to(x.export/'source-copy',target_is_directory=True)
    elif fault=='dispatch_link':
        p=x.directory/'dispatch.json';p.rename(x.directory/'dispatch-copy.json');p.symlink_to(x.directory/'dispatch-copy.json')
    elif fault=='invalid_export_key':
        dispatch=json.loads((x.directory/'dispatch.json').read_bytes());dispatch['export_key']='../outside'
        worker.atomic(x.directory/'dispatch.json',dispatch)
    worker.atomic(x.directory/'receipt.json',receipt);worker.atomic(x.root/'state.json',state)
    if fault=='receipt_link':
        p=x.directory/'receipt.json';p.rename(x.directory/'receipt-copy.json');p.symlink_to(x.directory/'receipt-copy.json')
    before=snapshot(x.root);history=snapshot(x.f.native.root);validator_files=snapshot(x.f.native.validator)
    with (x.root/'worker.lock').open('a') as lock:
        if fault=='lock':
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            monkeypatch.setattr(worker,'origin_command_config',Mock(side_effect=AssertionError('lock not held')))
        with pytest.raises((DevConflict,FileNotFoundError)):retry(x,reason)
    assert snapshot(x.root)==before and snapshot(x.f.native.root)==history and snapshot(x.f.native.validator)==validator_files
    x.no_replay.assert_not_called()
    x.replay_mock.assert_not_called()


@pytest.mark.parametrize('reason',[None,'','  '])
def test_cli_requires_reason(failed,reason):
    x=failed;before=snapshot(x.root)
    args=['--state-dir',str(x.root),'--retry-origin-registration',x.f.note['memory_id']]
    if reason is not None:args+=['--reason',reason]
    with pytest.raises(SystemExit) as exc:worker.main(args,client=x.f.service)
    assert exc.value.code==2 and snapshot(x.root)==before


@pytest.mark.parametrize('point',['before_receipt','after_receipt','after_source','after_register','after_passed_receipt','during_source',
    'before_passed_receipt','during_catalogs','during_source_catalog'])
def test_crash_recovery_resumes_same_journal(failed,monkeypatch,point):
    x=failed;real_rename=Path.rename;real_atomic=worker.atomic;real_prepare=exporter.prepare
    def crash_rename(path,target):
        if path==x.directory/'receipt.json' and point=='before_receipt':raise OSError('Controlled crash')
        result=real_rename(path,target)
        if (path==x.directory/'receipt.json' and point=='after_receipt') or (path==x.export/'source' and point=='after_source'):
            raise OSError('Controlled crash')
        return result
    def crash_save(path,value):
        if path==x.root/'state.json' and value.get('origin_registration_retries',[{}])[-1].get('status')=='registered':
            raise OSError('Controlled crash')
        return real_atomic(path,value)
    def crash_prepare(*args,**kwargs):
        if kwargs.get('source'):
            out=Path(args[4])
            if point=='during_source_catalog':
                real_prepare(*args,**kwargs);(out/'catalog.json').chmod(0o644);(out/'catalog.json').write_text('{')
            else:out.mkdir(parents=True);(out/'partial.txt').write_text('retry interrupted')
            raise OSError('Controlled crash')
        return real_prepare(*args,**kwargs)
    from benchmarks.attention_harness import memory_agent_origin
    from benchmarks.attention_harness import memory_origin_registration as recovery
    real_replace=recovery.os.replace
    def crash_replace(source,target):
        if (point=='before_passed_receipt' and Path(target)==x.directory/'receipt.json'
                or point=='during_catalogs' and Path(target)==x.directory/(x.current['origin_key']+'-executor.json')):
            raise OSError('Controlled crash')
        return real_replace(source,target)
    real_register=memory_agent_origin.register
    def crash_register(*args,**kwargs):
        real_register(*args,**kwargs)
        raise OSError('Controlled crash')
    with monkeypatch.context() as m:
        m.setattr(Path,'rename',crash_rename)
        if point=='after_passed_receipt':m.setattr(worker,'atomic',crash_save)
        elif point=='after_register':m.setattr(memory_agent_origin,'register',crash_register)
        elif point in ('during_source','during_source_catalog'):m.setattr(exporter,'prepare',crash_prepare)
        elif point in ('before_passed_receipt','during_catalogs'):m.setattr(recovery.os,'replace',crash_replace)
        with pytest.raises(OSError,match='Controlled crash'):retry(x,'Original reason')
    state=json.loads((x.root/'state.json').read_bytes());event=state['origin_registration_retries'][0]
    assert event['status']=='retrying' and event['reason']=='Original reason'
    assert any('recovery is incomplete' in e for e in automatic_checks(x.root)['errors'])
    worker.tick(state,SimpleNamespace(root=x.root,clock=lambda:1700000000,save=lambda s:worker.atomic(x.root/'state.json',s)))
    assert 'registration retry is incomplete' in state['status']
    retry(x,'Rerun reason')
    state=json.loads((x.root/'state.json').read_bytes());events=state['origin_registration_retries']
    assert len(events)==1 and events[0]['status']=='registered' and events[0]['reason']=='Original reason'
    assert json.loads((x.directory/'receipt.json').read_bytes())['status']=='passed'
    assert state['runs_today']==1
    if point=='during_source':assert (Path(events[0]['archive'])/'export-0/source/partial.txt').exists()
    if point=='during_source_catalog':assert (Path(events[0]['archive'])/'export-0/source/catalog.json').read_text()=='{'
    x.no_replay.assert_not_called()
    x.replay_mock.assert_not_called()


def test_invalid_executor_export_is_preserved_and_reexported(failed):
    x=failed;p=x.export/'execution/catalog.json';p.chmod(0o644)
    catalog=json.loads(p.read_bytes());catalog['files']['no-such-file']='0'*64;worker.atomic(p,catalog)
    before=snapshot(x.export/'execution');retry(x)
    event=json.loads((x.root/'state.json').read_bytes())['origin_registration_retries'][0]
    assert event['executor_catalog'] is None and snapshot(x.export/'execution')==before
    assert (x.f.native.root/'memory-origin-imports'/event['export_keys'][0]/'execution/catalog.json').exists()
    x.no_replay.assert_not_called()
    x.replay_mock.assert_not_called()


def test_empty_current_is_accepted(failed):
    failed.state['current']=None;worker.atomic(failed.root/'state.json',failed.state)
    retry(failed)
    assert json.loads((failed.directory/'receipt.json').read_bytes())['status']=='passed'


def test_executor_reuse_verifies_declared_same_root_symlink(failed):
    x=failed;path=x.export/'execution/catalog.json';catalog=json.loads(path.read_bytes())
    target=x.export/'execution/descriptor.json';alias=x.export/'execution/descriptor-alias.json'
    alias.symlink_to(target.name)
    name=str(alias.relative_to(x.f.native.root));destination=str(target.relative_to(x.f.native.root))
    catalog['files'][name]=catalog['files'][destination];catalog.setdefault('symlinks',{})[name]=destination
    worker.atomic(path,catalog);retry(x)
    event=json.loads((x.root/'state.json').read_bytes())['origin_registration_retries'][0]
    assert event['executor_catalog']['uri']==str(path)
    x.no_replay.assert_not_called();x.replay_mock.assert_not_called()


def test_next_tick_provisions_ten_arms(failed,monkeypatch):
    x=failed;retry(x);batches=[]
    from benchmarks.attention_harness import dev_disk
    monkeypatch.setattr(dev_disk,'status',lambda root:dict(dev_disk.thresholds(),free_gb=200,used_percent=1))
    x.f.native.records['failed']=x.f.native.records['cell']
    assert not x.f.service.validation_queue()  # Linked candidate replaced queue eligibility.
    def begin(note,operation):
        with synthetic_creation(x.f.native):batches.append(x.f.provision(note,operation))
        raise RuntimeError('Controlled stop after provisioning')
    runner=SimpleNamespace(origin_needed=origin.OriginPreparation(SimpleNamespace(provision=x.f.provision)).needed,begin=begin)
    deps=SimpleNamespace(root=x.root,runner=runner,client=x.f.service,clock=lambda:1700000000,
        activity=lambda current:None,quota=lambda:dict(used_percent=1,observed_at=1700000000),
        save=lambda state:worker.atomic(x.root/'state.json',state))
    worker.set_enabled(x.root,True);state=json.loads((x.root/'state.json').read_bytes())
    worker.tick(state,deps)
    assert len(batches)==1 and len(batches[0]['cells'])==10,state['status']
    assert 'Controlled stop after provisioning' in state['status']
    x.no_replay.assert_not_called()
    x.replay_mock.assert_not_called()


def test_normal_agent_path_matches_base_bytes(tmp_path,monkeypatch):
    import shutil
    import subprocess
    import tempfile
    import types
    import uuid
    f=build(tmp_path,monkeypatch,validation=True)
    monkeypatch.setattr(validator,'GpuMeter',lambda:SimpleNamespace(start=lambda:None,
        stop=lambda:dict(gpu_seconds=1.,wall_seconds=1.,samples=1)))
    monkeypatch.setattr(uuid,'uuid4',lambda:SimpleNamespace(hex='a'*32))
    def directory(suffix=None,prefix=None,dir=None):
        path=Path(dir or tmp_path)/((prefix or 'tmp')+'golden'+(suffix or ''));path.mkdir();return str(path)
    monkeypatch.setattr(tempfile,'mkdtemp',directory)
    code=subprocess.check_output(['git','show','eb68d09:benchmarks/attention_harness/memory_origin_evidence.py'],text=True)
    baseline=types.ModuleType(origin.__package__+'._registration_baseline');baseline.__package__=origin.__package__
    exec(compile(code,'origin-registration-baseline','exec'),baseline.__dict__)
    def submit(fn,*args):
        future=Future()
        try:future.set_result(fn(*args))
        except BaseException as exc:future.set_exception(exc)
        return future
    runner=SimpleNamespace(provision=f.provision,validator_root=f.native.validator,futures={},pool=SimpleNamespace(submit=submit),
        replay=lambda *a,**kw:replay(f,*a,**kw))
    results=[]
    for module in (baseline,origin):
        preparation=module.OriginPreparation(runner);current=preparation.needed(f.note);preparation.start(current)
        assert preparation.finish(current)['status']=='passed'
        results.append(tuple(snapshot(root) for root in (f.provision.state_root,f.native.root/'memory-origin-imports',f.native.validator/'arm-golden')))
        for root in (f.provision.state_root,f.native.root/'memory-origin-imports',f.native.validator/'arm-golden'):shutil.rmtree(root)
        runner.futures.clear()
    assert results[0]==results[1]
