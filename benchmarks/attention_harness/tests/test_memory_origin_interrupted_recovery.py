"""Operator abandonment uses only temporary state and synthetic evidence."""
import fcntl
import json
from datetime import datetime
from pathlib import Path

import pytest

from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.tests.r3_fixtures import r3_deployment
from benchmarks.attention_harness.tests.test_memory_origin_c9 import fake, snapshot

pytestmark = pytest.mark.usefixtures('r3_deployment')


@pytest.fixture
def interrupted(fake):
    root=fake.provision.state_root;note=fake.service.validation_queue()[0]
    current=fake.origin_needed(note);key=current['origin_key']
    current['export_key']=key+'-old-attempt'
    directory=root/'origins'/key
    worker.atomic(directory/'dispatch.json',dict(origin_key=key,note=current['note'],export_key=current['export_key']))
    export=fake.f.root/'memory-origin-imports'/current['export_key']
    worker.atomic(export/'execution/partial.json',dict(controlled_test=True))
    replay=fake.f.validator/'arm-interrupted'
    policy=replay/'policy-replays/0/policy.py';policy.parent.mkdir(parents=True)
    policy.write_bytes(Path(fake.failed['record']['source']['uri']).read_bytes())
    worker.atomic(policy.parent/'executions/partial/recording_context.json',dict(
        run_id='run:origin-replay:failed',attempt_id='origin-replay:failed:1'))
    deps=fake.deps(root)
    state=dict(current=current,pending=dict(note=note),runs_today=2,
        day=datetime.fromtimestamp(deps.clock()).astimezone().date().isoformat(),counted={key:'old-day'},
        note_errors={fake.mid:dict(memory_id=fake.mid,reason='old error')},
        check_errors=[dict(memory_id=fake.mid,reason='old error')],
        origin_errors={key:dict(title='Note',reason='unknown outcome')},origin_failures={key:dict(reason='old error')})
    worker.atomic(root/'state.json',state)
    return fake,root,directory,export,replay,state


def abandon(case,reason='Operator reviewed interrupted replay'):
    fake,root,*_=case
    return worker.abandon_interrupted_origin(root,fake.mid,reason,client=fake.service)


def test_cli_archives_requeues_and_next_tick_uses_fresh_artifacts(interrupted):
    fake,root,directory,export,replay,old=interrupted
    before=snapshot(directory);export_before=snapshot(export);replay_before=snapshot(replay)
    service_before=fake.service.store.path.read_bytes()
    assert worker.main(['--state-dir',str(root),'--abandon-interrupted-origin',fake.mid,'--reason','Reviewed merge-window interruption'],client=fake.service)==0
    state=json.loads((root/'state.json').read_bytes());event=state['origin_interruptions'][0]
    archive=Path(event['archive'])
    assert archive.parent==root/'origins-archive' and snapshot(archive)==before and not directory.exists()
    assert event['event']=='origin_interrupted_abandoned' and event['status']=='abandoned'
    assert event['actor'] and isinstance(event['uid'],int) and event['at']>0
    assert event['reason']=='Reviewed merge-window interruption'
    assert event['export_key']==old['current']['export_key'] and event['partial_export_dir']==str(export)
    assert event['validator_replay_dirs']==[str(replay)]
    assert event['run_reservation']=='Previous reservation remains counted; the next attempt reserves a new run.'
    assert state['current'] is None and 'pending' not in state
    assert state['runs_today']==2 and state['counted']=={archive.name:'old-day'}
    assert [n['memory_id'] for n in state['queue']]==[fake.mid]
    assert not state['note_errors'] and not state['origin_errors'] and not state['origin_failures'] and not state['check_errors']
    assert fake.service.store.path.read_bytes()==service_before and fake.replays==0
    # The public projection preserves the event even with the service feed, and
    # never discloses evidence paths or operator identity.
    for gateway in (None,fake.service):
        view=automatic_checks(root,gateway)
        assert view['events'][-1]['event']=='origin_interrupted_abandoned'
        assert view['events'][-1]['memory_id']==fake.mid
        assert not any('old error' in e or 'unknown outcome' in e or 'recovery is incomplete' in e for e in view['errors'])
        if gateway is None:assert not view['errors']
        assert str(root.parent) not in json.dumps(view) and 'archive' not in view['events'][-1]
    deps=fake.deps(root);worker.set_enabled(root,True);worker.tick(state,deps)
    assert state['runs_today']==3 and fake.replays==2
    new=json.loads((directory/'dispatch.json').read_bytes())
    assert new['export_key']!=event['export_key']
    assert (fake.f.root/'memory-origin-imports'/new['export_key']/'execution/catalog.json').exists()
    fresh_arms=[p for p in fake.f.validator.glob('arm-*') if p!=replay]
    assert len(fresh_arms)==1 and (fresh_arms[0]/'verdict.json').exists()
    assert snapshot(export)==export_before and snapshot(replay)==replay_before


@pytest.mark.parametrize('refusal',[
    'receipt','receipt_link','dispatch_link','invalid_export_key','no_current','not_origin','different_memory','different_origin','different_dispatch',
    'no_dispatch','no_queue','candidate','linked_candidate','validation_file','verdict_file','trial_file',
    'dispatch_result','validator_result','replay_result','state_result','missing_reason','lock',
])
def test_refusals_leave_evidence_and_state_untouched(interrupted,monkeypatch,refusal):
    fake,root,directory,export,replay,state=interrupted
    reason='Reviewed'
    if refusal=='receipt':worker.atomic(directory/'receipt.json',dict(status='error'))
    elif refusal=='receipt_link':(directory/'receipt.json').symlink_to(directory/'absent.json')
    elif refusal=='dispatch_link':
        (directory/'dispatch.json').rename(directory/'dispatch-copy.json')
        (directory/'dispatch.json').symlink_to(directory/'dispatch-copy.json')
    elif refusal=='invalid_export_key':
        dispatch=json.loads((directory/'dispatch.json').read_bytes());dispatch['export_key']='../elsewhere'
        worker.atomic(directory/'dispatch.json',dispatch)
    elif refusal=='no_current':state['current']=None
    elif refusal=='not_origin':state['current']['kind']='begin'
    elif refusal=='different_memory':state['current']['memory_id']='another'
    elif refusal=='different_origin':state['current']['origin_key']='origin-'+'0'*32
    elif refusal=='different_dispatch':worker.atomic(directory/'dispatch.json',dict(origin_key='another'))
    elif refusal=='no_dispatch':(directory/'dispatch.json').unlink()
    elif refusal=='no_queue':monkeypatch.setattr(fake.service,'validation_queue',lambda:[])
    elif refusal in ('candidate','linked_candidate'):
        rows=fake.service.list_dev_v2_memories()
        if refusal=='candidate':rows[0]['status']='candidate'
        else:rows.append(dict(memory_id='candidate',status='rejected',from_provisional=fake.mid))
        monkeypatch.setattr(fake.service,'list_dev_v2_memories',lambda:rows)
    elif refusal in ('validation_file','verdict_file','trial_file'):
        worker.atomic(directory/(refusal.replace('_file','')+'.json'),{})
    elif refusal=='dispatch_result':
        dispatch=json.loads((directory/'dispatch.json').read_bytes());dispatch['validator_result']={}
        worker.atomic(directory/'dispatch.json',dispatch)
    elif refusal=='validator_result':
        worker.atomic(fake.f.validator/'arm-result/verdict.json',dict(execution_import_id=state['current']['origin_key']))
    elif refusal=='replay_result':worker.atomic(replay/'verdict.json',dict(execution_import_id='other'))
    elif refusal=='state_result':state['results']=[dict(memory_id=fake.mid,status='trusted')]
    elif refusal=='missing_reason':reason='  '
    worker.atomic(root/'state.json',state)
    before=snapshot(root);history=snapshot(fake.f.root);validator=snapshot(fake.f.validator)
    with (root/'worker.lock').open('a') as lock:
        if refusal=='lock':
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            def forbidden(*a,**kw):raise AssertionError('The locked command read runtime configuration or constructed a client')
            monkeypatch.setattr(worker,'origin_command_config',forbidden)
        with pytest.raises((DevConflict,FileNotFoundError)):abandon(interrupted,reason)
    after=snapshot(root);after.pop('worker.lock',None);before.pop('worker.lock',None)
    assert after==before and snapshot(fake.f.root)==history and snapshot(fake.f.validator)==validator


@pytest.mark.parametrize('reason',[None,'','  '])
def test_cli_requires_nonempty_reason_without_state_writes(interrupted,reason):
    fake,root,*_=interrupted;before=snapshot(root)
    args=['--state-dir',str(root),'--abandon-interrupted-origin',fake.mid]
    if reason is not None:args+=['--reason',reason]
    with pytest.raises(SystemExit) as exc:worker.main(args,client=fake.service)
    assert exc.value.code==2 and snapshot(root)==before


@pytest.mark.parametrize('point',['before_rename','after_rename'])
def test_crash_recovery_resumes_same_intent_and_blocks_worker(interrupted,monkeypatch,point):
    fake,root,directory,export,replay,old=interrupted;before=snapshot(directory)
    real_atomic=worker.atomic;real_rename=Path.rename
    def crash_rename(path,target):
        if path==directory:
            saved=json.loads((root/'state.json').read_bytes())
            assert saved['origin_interruptions'][0]['status']=='archiving'
            assert saved['current']==old['current']
            raise OSError('Controlled crash before rename')
        return real_rename(path,target)
    def crash_save(path,value):
        if path==root/'state.json' and value.get('origin_interruptions',[{}])[-1].get('status')=='abandoned':
            raise OSError('Controlled crash after rename')
        return real_atomic(path,value)
    with monkeypatch.context() as m:
        if point=='before_rename':m.setattr(Path,'rename',crash_rename)
        else:m.setattr(worker,'atomic',crash_save)
        with pytest.raises(OSError,match='Controlled crash'):abandon(interrupted,'Original reviewed reason')
    state=json.loads((root/'state.json').read_bytes())
    assert state['origin_interruptions'][0]['status']=='archiving' and state['runs_today']==2
    assert any(e.endswith('rerun the operator recovery command.') for e in automatic_checks(root)['errors'])
    deps=fake.deps(root);worker.set_enabled(root,True);worker.tick(state,deps)
    assert fake.replays==0 and 'abandonment is incomplete' in state['status']
    abandon(interrupted,'Rerun reason')
    state=json.loads((root/'state.json').read_bytes());events=state['origin_interruptions']
    assert len(events)==1 and events[0]['reason']=='Original reviewed reason' and events[0]['status']=='abandoned'
    assert snapshot(Path(events[0]['archive']))==before and not directory.exists()
    assert state['current'] is None and state['runs_today']==2 and len(state['counted'])==1
    assert [n['memory_id'] for n in state['queue']]==[fake.mid]
    # A completed rerun cannot archive an unrelated or future attempt.
    with pytest.raises(DevConflict,match='Current work'):abandon(interrupted)
