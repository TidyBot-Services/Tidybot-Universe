import json,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness.memory_validation_worker import tick,set_enabled,weekly_quota,weekly_quota_snapshot,atomic,run_activity,contract_catalog
from benchmarks.attention_harness.tests.autovalidate_fakes import FakeRunner


class Runner:
    def __init__(self):self.started=[];self.phase='running';self.pauses=0;self.resumes=0;self.begun=[]
    def begin(self,note,operation):
        self.begun.append(note['memory_id']);return dict(memory_id=note['memory_id'],plan={'pairs':[{'control_cell':f'c{i}','treatment_cell':f't{i}'} for i in range(5)]})
    def start(self,current,id):self.started.append(id)
    def poll(self,current):return self.phase
    def pause(self,current):self.pauses+=1;self.phase='paused'
    def resume(self,current):self.resumes+=1;self.phase='running'
    def finish(self,current):return {'trial_import_id':'trial-'+current['arm_id']}


def deps(root):
    runner=Runner();client=SimpleNamespace(validation_queue=lambda:[dict(memory_id='head',version=1,title='Head',uses=4,failure_gone=2),dict(memory_id='later',version=1,title='Later',uses=1,failure_gone=0)],record_dev_v2_pair=lambda **kw:None)
    d=SimpleNamespace(root=root,runner=runner,client=client,clock=lambda:1792713600,activity=lambda current:None,save=lambda state:atomic(root/'state.json',state))
    d.quota=lambda:dict(used_percent=10,observed_at=d.clock())
    d.refresh_quota=lambda:(_ for _ in ()).throw(RuntimeError('fake refresh unavailable'))
    return d


def test_default_off_no_external_calls(tmp_path):
    d=deps(tmp_path);d.quota=lambda:pytest.fail('quota read when Off');d.activity=lambda c:pytest.fail('activity read when Off')
    state={};tick(state,d);assert state['status']=='Off' and not d.runner.started


def test_queue_head_restart_and_no_double_count(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};tick(state,d)
    assert d.runner.begun==['head'] and d.runner.started==['c0'] and state['runs_today']==1
    restored=json.loads((tmp_path/'state.json').read_bytes());tick(restored,d)
    assert d.runner.started==['c0'] and restored['runs_today']==1
    d.runner.phase='completed';tick(restored,d);assert restored['runs_today']==2 and d.runner.started==['c0','t0']


def test_idle_only_and_pause_resume(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};d.activity=lambda c:'Another run is active';tick(state,d)
    assert not d.runner.started
    d.activity=lambda c:None;tick(state,d);assert d.runner.started==['c0']
    d.activity=lambda c:'Another run is active';tick(state,d);tick(state,d);assert d.runner.pauses==1
    d.activity=lambda c:None;tick(state,d);assert d.runner.resumes==1 and state['runs_today']==1


@pytest.mark.parametrize('quota,baseline,cap,expected',[(70,10,0,'70%'),(69,63,0,'5 points'),(68,63,20,'20 runs'),(None,10,0,'could not be read')])
def test_budget_stops(tmp_path,quota,baseline,cap,expected):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};tick(state,d);state.pop('current');state['runs_today']=cap;state['day_start_quota']=baseline;d.runner.started=[];d.quota=lambda:dict(used_percent=quota,observed_at=d.clock())
    tick(state,d);assert expected in state['status'] and not d.runner.started


def test_exact_five_points_allowed_and_daily_reset(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};tick(state,d);state.pop('current');state['day_start_quota']=10;d.quota=lambda:dict(used_percent=15,observed_at=d.clock())
    tick(state,d);assert state['current']
    state.pop('current');state['runs_today']=20;d.clock=lambda:1792800000
    tick(state,d);assert state['runs_today']==0 and state['day_start_quota']==15  # existing arm slot never counted twice


@pytest.mark.parametrize('weekly',['primary','secondary'])
def test_weekly_window_and_empty_newest_session(tmp_path,weekly):
    root=tmp_path/'.codex/sessions';root.mkdir(parents=True)
    limits={weekly:{'window_minutes':10080,'used_percent':72},('secondary' if weekly=='primary' else 'primary'):{'window_minutes':300,'used_percent':1}}
    (root/'a.jsonl').write_text(json.dumps({'payload':{'rate_limits':limits}})+'\n')
    assert weekly_quota(tmp_path)==72
    (root/'b.jsonl').write_text('{}\n')
    assert weekly_quota(tmp_path)==72


def test_registered_contract_catalog_exact_file():
    catalog=contract_catalog({});assert catalog['role']=='contract' and len(catalog['files'])==1
    assert Path(catalog['root']).name=='scene_contracts'


@pytest.mark.parametrize('treatments,expected',[((False,)*5,'trusted'),((True,)*5,'rejected')])
def test_worker_real_service_lifecycle(tmp_path,treatments,expected):
    runner=FakeRunner(tmp_path/'evidence',treatments=treatments);d=runner.deps(tmp_path/'state');set_enabled(d.root,True);state={}
    for _ in range(30):
        tick(state,d)
        if state.get('results'):break
        runner.phase='completed'
    assert state.get('results'),state.get('status')
    assert state['results'][0]['status']==expected and len(runner.starts)==10 and state['runs_today']==10
    assert runner.service.get_memory(runner.mid).status.value=='disabled'
    if expected=='trusted':runner.demote()
    events=runner.service.memory_events();assert any(e['event']==('demoted' if expected=='trusted' else 'rejected') for e in events)


def test_restart_after_dispatch_intent_retries_same_id_without_count(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};original=d.runner.start
    d.runner.start=lambda *a:(_ for _ in ()).throw(RuntimeError('interrupted before launch'))
    tick(state,d);assert state['current']['dispatch_pending'] and state['runs_today']==1
    state=json.loads((tmp_path/'state.json').read_bytes());d.runner.start=original
    tick(state,d);assert d.runner.started==['c0'] and state['runs_today']==1 and not state['current']['dispatch_pending']


def test_pending_note_survives_queue_removal(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};original=d.runner.begin
    d.runner.begin=lambda *a:(_ for _ in ()).throw(RuntimeError('source created; reply lost'))
    tick(state,d);assert state['pending']['note']['memory_id']=='head'
    d.client.validation_queue=lambda:[];d.runner.begin=original
    tick(state,d);assert state['current'] and d.runner.begun==['head']


def test_run_activity_ledger_and_frozen_main_matrix(tmp_path,monkeypatch):
    import sqlite3
    from benchmarks.attention_harness import memory_validation_worker as worker
    real_path=Path
    monkeypatch.setattr(worker,'Path',lambda p:SimpleNamespace(iterdir=lambda:[]) if str(p)=='/proc' else real_path(p))
    ledger=tmp_path/'ledger'
    with sqlite3.connect(ledger) as db:
        db.executescript('CREATE TABLE v2_experiments(payload TEXT); CREATE TABLE v2_jobs(state TEXT,payload TEXT);')
        db.execute('INSERT INTO v2_experiments VALUES (?)',(json.dumps(dict(experiment_id='matrix',selection={'budget_tier':'formal'},state='planned')),))
    assert 'matrix' in run_activity([ledger])
    with sqlite3.connect(ledger) as db:db.execute('UPDATE v2_experiments SET payload=?',(json.dumps(dict(experiment_id='user',selection={'budget_tier':'dev'},state='running')),))
    assert run_activity([ledger])=='Another run is active'
    assert run_activity([ledger],own_experiment='user') is None


@pytest.mark.parametrize('arm,check,expected', [('t0','safety','rejected'),('c0','safety','inconclusive'),('c0','scene','inconclusive'),('t0','scene','inconclusive')])
def test_failed_independent_check_rejected_without_accepting_trial(tmp_path,arm,check,expected):
    import sqlite3
    from benchmarks.attention_harness.memory_monitor import automatic_checks
    runner=FakeRunner(tmp_path/'evidence',failed_check=(arm,check));d=runner.deps(tmp_path/'state');set_enabled(d.root,True);state={}
    for _ in range(5):
        tick(state,d)
        if state.get('results'):break
        runner.phase='completed'
    assert state.get('results'),state['status']
    result=state['results'][0];assert result['status']==expected and state['current'] is None
    decision=result['decision'];assert decision['report']['paired_runs']==0
    assert runner.service.get_memory(runner.mid).status.value==('disabled' if expected=='rejected' else 'provisional')
    assert runner.service.get_memory('m1').status.value==('rejected' if expected=='rejected' else 'candidate')
    assert any(e['event']==expected for e in runner.service.memory_events())
    from benchmarks.attention_harness.notices import NoticeReader
    notices=NoticeReader(memory_view=SimpleNamespace(source='controlled_test',gateway=runner.service),quota=lambda:{},probe=lambda:True).read()['notices']
    wording='Note rejected after check:' if expected=='rejected' else 'Could not finish the check:'
    assert any(wording in n['title'] for n in state['notices'])
    assert any(wording in n['title'] for n in notices)
    public=automatic_checks(d.root,runner.service);assert public['results'][0]['status']==expected
    if expected=='inconclusive':assert public['results'][0]['reason'].startswith(wording)
    assert runner.service.record_dev_v2_failed_check(**runner.finished[arm]['failed_check'])==decision
    with sqlite3.connect(runner.service.store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM dev_v2_imports WHERE kind='trial'").fetchone()[0]==int(arm=='t0')
    assert runner.service.validation_queue()==[]
    if expected=='inconclusive':
        from benchmarks.attention_harness.tests.autovalidate_fakes import outcome
        ctx={**runner.failed['context'],'seed':101,'scene_contract_ref':runner.f.contract}
        outcome(runner.service,runner.mid,1,ctx=ctx)
        assert runner.service.validation_queue()[0]['memory_id']==runner.mid


def test_service_gap_persisted_and_worker_continues(tmp_path):
    runner=FakeRunner(tmp_path/'evidence',failed_check=('c0','scene'));d=runner.deps(tmp_path/'state');set_enabled(d.root,True);state={};tick(state,d)
    runner.phase='completed'
    d.client.record_dev_v2_failed_check=lambda **kw:(_ for _ in ()).throw(RuntimeError('failed-check route unavailable'))
    tick(state,d)
    assert state['current'] is None and 'route unavailable' in state['service_gaps'][0]['reason']
    tick(state,d);assert state['status']=='Automatic checks waiting for used notes'


def test_restart_pending_never_dispatches_while_user_active(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};tick(state,d);d.runner.phase='pending';d.activity=lambda c:'User run active'
    tick(state,d);assert d.runner.started==['c0']


def quota_session(root,name,values,*,mtime,weekly='secondary',observed_at=None):
    import os
    from datetime import datetime,timezone
    p=root/'.codex/sessions'/name;p.parent.mkdir(parents=True,exist_ok=True)
    stamp=datetime.fromtimestamp(mtime if observed_at is None else observed_at,timezone.utc).isoformat()
    p.write_text('\n'.join(json.dumps({'timestamp':stamp,'payload':{'rate_limits':{weekly:{'window_minutes':10080,'used_percent':v},('primary' if weekly=='secondary' else 'secondary'):{'window_minutes':300,'used_percent':99}}}}) for v in values)+'\n')
    os.utime(p,(mtime,mtime));return p


def test_quota_maximum_of_latest_receipts_in_recent_files(tmp_path):
    now=1792713600
    quota_session(tmp_path,'older.jsonl',[92,68],mtime=now-100)
    quota_session(tmp_path,'fresh.jsonl',[12],mtime=now)
    quota_session(tmp_path,'expired.jsonl',[98],mtime=now-86401)
    assert weekly_quota(tmp_path,now=now)==68
    quota_session(tmp_path,'other-account.jsonl',[72],mtime=now-20,weekly='primary')
    assert weekly_quota(tmp_path,now=now)==72


@pytest.mark.parametrize('values,age',[([],0),([40],86401),([True],0),([float('nan')],0),([101],0),([40,None],0)])
def test_quota_no_recent_valid_latest_receipt_fails_closed(tmp_path,values,age):
    now=1792713600;quota_session(tmp_path,'one.jsonl',values,mtime=now-age)
    with pytest.raises(ValueError):weekly_quota(tmp_path,now=now)


def test_daily_baseline_and_resume_use_same_cross_session_maximum(tmp_path):
    d=deps(tmp_path);now=d.clock();set_enabled(tmp_path,True)
    quota_session(tmp_path,'existing.jsonl',[60],mtime=now-100)
    quota_session(tmp_path,'fresh.jsonl',[10],mtime=now)
    d.quota=lambda:weekly_quota_snapshot(tmp_path,now=d.clock());state={};tick(state,d)
    assert state['day_start_quota']==60 and state['weekly_quota']==60
    quota_session(tmp_path,'existing.jsonl',[60,66],mtime=now+1)
    state=json.loads((tmp_path/'state.json').read_bytes());tick(state,d)
    assert state['day_start_quota']==60 and '5 points' in state['status'] and d.runner.pauses==1
    d.clock=lambda:now+86400
    quota_session(tmp_path,'new-day.jsonl',[20],mtime=d.clock())
    quota_session(tmp_path,'existing.jsonl',[66],mtime=d.clock()-100)
    tick(state,d);assert state['day_start_quota']==66 and d.runner.resumes==1


def test_new_use_after_inconclusive_gets_new_durable_batch_id(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);ops=[];begin=d.runner.begin
    d.runner.begin=lambda note,op:ops.append(op) or begin(note,op)
    state={};tick(state,d);state['current']=None
    old=d.client.validation_queue();old[0]['uses']+=1;d.client.validation_queue=lambda:old
    tick(state,d);assert len(ops)==2 and ops[0]!=ops[1]


def freshness_deps(root,kind):
    if kind.startswith('cross'):
        from benchmarks.attention_harness.tests.test_memory_cross_seed import worker_deps
        d=worker_deps(root)
    else:
        d=deps(root);set_enabled(root,True)
    if kind.endswith('origin'):
        d.runner.origin_needed=lambda n:dict(kind='origin',origin_key='origin-test',memory_id=n['memory_id'],title='Origin',active=True)
    state={}
    if kind=='cross_pairs':
        tick(state,d);d.runner.phase='completed';tick(state,d);d.runner.started=[]
    return d,state


@pytest.mark.parametrize('kind',['arm','origin','cross_probe','cross_pairs','cross_origin'])
@pytest.mark.parametrize('age,refreshes',[(599,0),(600,0),(601,1)])
def test_quota_freshness_before_every_dispatch(tmp_path,kind,age,refreshes):
    d,state=freshness_deps(tmp_path,kind);now=d.clock()
    quota_session(tmp_path,'rollout-worker.jsonl',[10],mtime=now,observed_at=now-age)
    d.quota=lambda:weekly_quota_snapshot(tmp_path,now=d.clock());calls=[]
    def refresh():
        assert not d.runner.started
        persisted=json.loads((tmp_path/'state.json').read_bytes())
        assert persisted['quota_refreshes'][-1]['status']=='refreshing'
        calls.append('refresh')
        quota_session(tmp_path,'rollout-worker.jsonl',[11],mtime=now)
    d.refresh_quota=refresh
    tick(state,d)
    assert len(calls)==refreshes and len(d.runner.started)==1,state['status']
    assert state['day_start_quota']==10
    assert len(state.get('quota_refreshes',[]))==refreshes
    if refreshes:
        assert state['quota_refreshes']==[dict(at=now,finished_at=now,before_percent=10,after_percent=11,status='refreshed')]
        assert state['weekly_quota_at']==now


@pytest.mark.parametrize('kind',['arm','origin','cross_probe','cross_pairs','cross_origin'])
@pytest.mark.parametrize('result,expected',[
    ('failure','could not be read'),('still_stale','could not be read'),('missing','could not be read'),
    (70,'70%'),(71,'71%'),(16,'5 points'),(15,None)])
def test_refreshed_quota_stops_are_shared(tmp_path,kind,result,expected):
    d,state=freshness_deps(tmp_path,kind);now=d.clock();calls=[]
    before=dict(used_percent=10,observed_at=now-601)
    d.quota=lambda:before
    def refresh():
        calls.append('refresh')
        if result=='failure':raise RuntimeError('fake failure')
        if result=='still_stale':return
        d.quota=lambda:None if result=='missing' else dict(used_percent=result,observed_at=now)
    d.refresh_quota=refresh;tick(state,d)
    assert calls==['refresh'] and state['day_start_quota']==10
    if expected:
        assert not d.runner.started and expected in state['status']
        if 'could not' in expected:assert state['status']=='Automatic checks paused: Codex weekly use could not be read.'
    else:assert len(d.runner.started)==1
    assert state['quota_refreshes'][0]['status']==('failed' if isinstance(result,str) else 'refreshed')


@pytest.mark.parametrize('kind',['arm','origin','cross_probe'])
@pytest.mark.parametrize('pending',[False,True])
def test_stale_quota_pauses_running_or_pending_work_on_restart(tmp_path,kind,pending):
    d,state=freshness_deps(tmp_path,kind);tick(state,d)
    state=json.loads((tmp_path/'state.json').read_bytes());d.runner.phase='pending' if pending else 'running'
    d.quota=lambda:dict(used_percent=10,observed_at=d.clock()-601)
    tick(state,d)
    assert d.runner.started==(['origin-test'] if kind=='origin' else ['probe'] if kind=='cross_probe' else ['c0'])
    assert state['status']=='Automatic checks paused: Codex weekly use could not be read.'
    assert d.runner.pauses==(0 if pending and kind!='origin' else 1)


@pytest.mark.parametrize('kind',['arm','origin','cross_probe'])
def test_provisioning_cannot_age_out_dispatch_gate(tmp_path,kind):
    d,state=freshness_deps(tmp_path,kind);now=d.clock()
    d.quota=lambda:dict(used_percent=10,observed_at=now)
    method='origin_needed' if kind=='origin' else 'begin_cross' if kind=='cross_probe' else 'begin'
    begin=getattr(d.runner,method)
    def delayed(*args,**kwargs):
        d.clock=lambda:now+601
        return begin(*args,**kwargs)
    setattr(d.runner,method,delayed)
    tick(state,d)
    assert not d.runner.started and state['status']=='Automatic checks paused: Codex weekly use could not be read.'


def test_recent_file_and_unrelated_events_do_not_freshen_quota(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);now=d.clock()
    path=quota_session(tmp_path,'rollout-old.jsonl',[10],mtime=now,observed_at=now-601)
    with path.open('a') as f:f.write(json.dumps({'timestamp':'2026-10-23T00:00:00Z','type':'turn.completed'})+'\n')
    (path.parent/'rollout-empty.jsonl').write_text('{}\n')
    d.quota=lambda:weekly_quota_snapshot(tmp_path,now=now)
    state={};tick(state,d)
    assert state['weekly_quota_at']==now-601 and not d.runner.started
    assert len(state['quota_refreshes'])==1


@pytest.mark.parametrize('stamp',[None,'nonsense','2026-10-23T00:00:00','2099-01-01T00:00:00Z'])
def test_unknown_or_future_event_time_is_not_fresh(tmp_path,stamp):
    d=deps(tmp_path);set_enabled(tmp_path,True)
    path=quota_session(tmp_path,'rollout-bad-time.jsonl',[10],mtime=d.clock())
    row=json.loads(path.read_text());row['timestamp']=stamp;path.write_text(json.dumps(row)+'\n')
    d.quota=lambda:weekly_quota_snapshot(tmp_path,now=d.clock());state={};tick(state,d)
    assert state['weekly_quota_at'] is None and not d.runner.started
    assert state['status']=='Automatic checks paused: Codex weekly use could not be read.'


def test_missing_reading_can_recover_without_assumed_baseline(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);d.quota=lambda:None
    d.refresh_quota=lambda:setattr(d,'quota',lambda:dict(used_percent=20,observed_at=d.clock()))
    state={};tick(state,d)
    assert state['day_start_quota']==20 and d.runner.started==['c0']
    assert state['quota_refreshes'][0]['before_percent'] is None


def test_busy_worker_does_not_launch_quota_probe(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True)
    d.activity=lambda c:'Another run is active'
    d.quota=lambda:dict(used_percent=10,observed_at=d.clock()-601)
    d.refresh_quota=lambda:pytest.fail('quota refresh while another run is active')
    state={};tick(state,d)
    assert state['status']=='Another run is active' and not d.runner.started
    assert 'quota_refreshes' not in state


def test_refresh_command_is_non_ephemeral_bounded_and_isolated(tmp_path,monkeypatch):
    import subprocess
    from benchmarks.attention_harness.memory_validation_worker import refresh_quota
    calls=[]
    def fake_run(command,**kw):
        assert command==['codex','exec','-m','gpt-6-astra','-s','read-only','--skip-git-repo-check','Reply with the single word OK.']
        assert not list(Path(kw['cwd']).iterdir()) and Path(kw['cwd'])!=tmp_path
        assert kw['stdin']==kw['stdout']==kw['stderr']==subprocess.DEVNULL
        assert kw['timeout']==60 and kw['check'] is True
        assert kw['env']['CODEX_HOME']==str(tmp_path/'.codex')
        calls.append(kw['cwd'])
    monkeypatch.setattr(subprocess,'run',fake_run)
    refresh_quota(home=tmp_path)
    assert len(calls)==1 and not Path(calls[0]).exists()


@pytest.mark.parametrize('failure',[None,'exit','timeout'])
def test_injected_fake_refresh_command_and_real_session_reread(tmp_path,failure):
    import sys
    from benchmarks.attention_harness.memory_validation_worker import refresh_quota
    d=deps(tmp_path);d.clock=time.time;set_enabled(tmp_path,True)
    quota_session(tmp_path,'rollout-before.jsonl',[10],mtime=d.clock(),observed_at=d.clock()-601)
    d.quota=lambda:weekly_quota_snapshot(tmp_path,now=d.clock())
    code="""import json,os,sys,time
from pathlib import Path
from datetime import datetime,timezone
assert sys.stdin.read()==''
assert not list(Path.cwd().iterdir())
root=Path(os.environ['CODEX_HOME'])/'sessions'
root.mkdir(parents=True,exist_ok=True)
(root/'rollout-refreshed.jsonl').write_text(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),'payload':{'rate_limits':{'secondary':{'window_minutes':10080,'used_percent':11}}}})+'\\n')
"""
    if failure=='exit':code='raise SystemExit(1)'
    if failure=='timeout':code='import time; time.sleep(10)'
    d.refresh_quota=lambda:refresh_quota(home=tmp_path,command=[sys.executable,'-c',code],timeout=.1 if failure=='timeout' else 5)
    state={};tick(state,d)
    assert len(state['quota_refreshes'])==1
    if failure:
        assert not d.runner.started and state['status']=='Automatic checks paused: Codex weekly use could not be read.'
    else:
        assert d.runner.started==['c0'] and state['weekly_quota']==11 and state['day_start_quota']==10


def test_monitor_exposes_live_reading_age_and_safe_refresh_fields(tmp_path,monkeypatch):
    from benchmarks.attention_harness import memory_monitor
    set_enabled(tmp_path,True)
    atomic(tmp_path/'state.json',dict(weekly_quota=11,weekly_quota_at=1000,quota_refreshes=[
        dict(at=1000,finished_at=1001,before_percent=10,after_percent=11,status='refreshed',command='private')]))
    monkeypatch.setattr(memory_monitor.time,'time',lambda:1060)
    value=memory_monitor.automatic_checks(tmp_path)
    assert value['weekly_quota_age_seconds']==60 and value['weekly_quota']==11
    assert value['quota_refreshes']==[dict(at=1000,finished_at=1001,before_percent=10,after_percent=11,status='refreshed')]
    monkeypatch.setattr(memory_monitor.time,'time',lambda:1700)
    assert memory_monitor.automatic_checks(tmp_path)['weekly_quota_age_seconds']==700
