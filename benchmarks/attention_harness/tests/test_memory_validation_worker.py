import json,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from benchmarks.attention_harness.memory_validation_worker import tick,set_enabled,weekly_quota,atomic,run_activity,contract_catalog
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
    return SimpleNamespace(root=root,runner=runner,client=client,quota=lambda:10,clock=lambda:1792713600,activity=lambda current:None,save=lambda state:atomic(root/'state.json',state))


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


@pytest.mark.parametrize('quota,baseline,cap,expected',[(70,10,0,'70%'),(69,63,0,'5 points'),(68,63,20,'20 runs'),(None,10,0,'unreadable')])
def test_budget_stops(tmp_path,quota,baseline,cap,expected):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};tick(state,d);state.pop('current');state['runs_today']=cap;state['day_start_quota']=baseline;d.runner.started=[];d.quota=lambda:quota
    tick(state,d);assert expected in state['status'] and not d.runner.started


def test_exact_five_points_allowed_and_daily_reset(tmp_path):
    d=deps(tmp_path);set_enabled(tmp_path,True);state={};tick(state,d);state.pop('current');state['day_start_quota']=10;d.quota=lambda:15
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


def quota_session(root,name,values,*,mtime,weekly='secondary'):
    import os
    p=root/'.codex/sessions'/name;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text('\n'.join(json.dumps({'payload':{'rate_limits':{weekly:{'window_minutes':10080,'used_percent':v},('primary' if weekly=='secondary' else 'secondary'):{'window_minutes':300,'used_percent':99}}}}) for v in values)+'\n')
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
    d.quota=lambda:weekly_quota(tmp_path,now=d.clock());state={};tick(state,d)
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
