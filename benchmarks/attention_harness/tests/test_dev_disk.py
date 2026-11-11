from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_disk as disk,dev_experiment as experiments,home_status
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
from benchmarks.attention_harness.dev_main_bridge import DevMainBridge
from benchmarks.attention_harness.tests.test_eval_advisor_split import _loop_setup,FrameRunner,RoutedClient
from benchmarks.attention_harness.tests.test_pause_resume import controls
from benchmarks.attention_harness.tests.test_dev_agent_d2 import ws,fake_session,Executor,FakeProber,FakeEvaluator,AgentLedger,drive
from benchmarks.attention_harness.dev_agent_session import CodexAgentSession
from benchmarks.attention_harness.tests.test_dev_v2_history import frozen_creation

@pytest.fixture
def space(monkeypatch,frozen_creation):
    row=dict(free=100)
    monkeypatch.delenv('ATTENTION_DEV_V2_START_MIN_FREE_GB',raising=False)
    monkeypatch.delenv('ATTENTION_DEV_V2_RUN_MIN_FREE_GB',raising=False)
    monkeypatch.setattr(disk.shutil,'disk_usage',lambda path:SimpleNamespace(free=row['free']*1e9,used=(1000-row['free'])*1e9,total=1000e9))
    return row

@pytest.mark.parametrize('free,allowed',[(39.9,False),(40,True),(40.1,True)])
def test_start_boundary_and_home_share_threshold(tmp_path,space,free,allowed):
    space['free']=free
    if allowed:assert disk.require_start(tmp_path)['free_gb']==free
    else:
        with pytest.raises(DevConflict,match='低于 40 GB，未启动'):disk.require_start(tmp_path)
    result=home_status.readiness(dict(result=None),None,0,tmp_path)['disk']
    assert result['state']==('ready' if allowed else 'low')
    assert result['start_min_free_gb']==40 and result['run_min_free_gb']==15

def test_configurable_thresholds_and_new_root_filesystem(tmp_path,space,monkeypatch):
    monkeypatch.setenv('ATTENTION_DEV_V2_START_MIN_FREE_GB','60')
    monkeypatch.setenv('ATTENTION_DEV_V2_RUN_MIN_FREE_GB','20')
    assert disk.status(tmp_path/'not/created')['start_min_free_gb']==60
    space['free']=50
    with pytest.raises(DevConflict,match='60 GB'):disk.require_start(tmp_path)
    monkeypatch.setenv('ATTENTION_DEV_V2_RUN_MIN_FREE_GB','nan')
    with pytest.raises(ValueError,match='thresholds'):disk.status(tmp_path)

@pytest.mark.parametrize('bridge_type',[DailyDevV2,DevMainBridge])
@pytest.mark.parametrize('operation',['create','start'])
def test_ui_entry_points_refuse_before_any_write_or_dispatch(tmp_path,space,bridge_type,operation):
    space['free']=39
    bridge=object.__new__(bridge_type);bridge.root=bridge.artifacts=tmp_path
    with pytest.raises(DevConflict,match='未启动'):getattr(bridge,operation)({} if operation=='create' else 'missing')
    assert list(tmp_path.iterdir())==[]

def test_low_level_creation_refuses_both_modes(tmp_path,space):
    space['free']=0
    for mode in ('agent','one_call'):
        with pytest.raises(DevConflict,match='未启动'):experiments.create(None,tmp_path,dict(dev_mode=mode))
    assert list(tmp_path.iterdir())==[]

@pytest.mark.parametrize('boundary',['execute','eval'])
def test_old_loop_pauses_before_next_step_and_resumes_without_repeating(tmp_path,monkeypatch,space,boundary):
    store,exp,budget=_loop_setup(tmp_path,monkeypatch);eid=exp['experiment_id']
    class Client(RoutedClient):
        def chat(self,**kw):
            reply=super().chat(**kw)
            if boundary=='execute':space['free']=14
            return reply
    class Runner(FrameRunner):
        def execute(self,request):
            result=super().execute(request)
            if boundary=='eval':space['free']=14
            return result
    result=experiments.run(store,eid,max_cells=1,budget_file=budget,client=Client(),runner_factory=Runner)
    cell=result['cells'][0]
    assert result['state']=='paused' and result['pause_reason']=='disk_low' and not result['cancelled']
    assert cell['reason']=='operator_paused' and all(j['state']=='settled' for j in cell['jobs'])
    assert sum(j['kind']=='execute' for j in cell['jobs'])==(boundary=='eval')
    assert not any(j['kind']=='eval' for j in cell['jobs'])
    rc=controls(store,cell,tmp_path)
    assert rc['pause_reason']=='disk_low' and '磁盘不足' in rc['pause_detail'] and not rc['can_resume']
    with pytest.raises(DevConflict):experiments.resume_paused(store,eid)
    space['free']=100
    assert controls(store,cell,tmp_path)['can_resume']
    experiments.resume_paused(store,eid)
    result=experiments.run(store,eid,max_cells=1,budget_file=budget,client=RoutedClient(),runner_factory=FrameRunner)
    assert result['state']=='completed' and not result.get('pause_reason')
    jobs=result['cells'][0]['jobs']
    assert len([j for j in jobs if j['kind']=='model' and j['version']==1])==1
    assert len([j for j in jobs if j['kind']=='execute' and j['version']==1])==1
    assert len([j for j in jobs if j['kind']=='eval' and j['version']==1])==1

def test_run_threshold_is_strict_and_pause_keeps_first_request_time(tmp_path,monkeypatch,space):
    store,exp,_=_loop_setup(tmp_path,monkeypatch);eid=exp['experiment_id']
    space['free']=15
    assert not disk.pause_if_low(store,eid)
    space['free']=14.99
    assert disk.pause_if_low(store,eid)
    when=experiments.snapshot(store,eid)['pause_requested_at']
    assert disk.pause_if_low(store,eid) and experiments.snapshot(store,eid)['pause_requested_at']==when
    events=store.path
    import sqlite3
    with sqlite3.connect(events) as db:
        assert db.execute("SELECT count(*) FROM v2_events WHERE kind='experiment_pause_requested'").fetchone()[0]==1

def test_agent_control_throttles_disk_but_keeps_pause_and_boundary_checks(tmp_path,monkeypatch,space):
    from benchmarks.attention_harness import dev_agent_loop as loop,dev_agent_isolation as isolation
    from benchmarks.attention_harness.dev_agent_prompts import manifest_fields
    from benchmarks.attention_harness.tests.test_dev_agent_d2 import CONFIG,SOURCE
    store,exp,_=_loop_setup(tmp_path,monkeypatch);eid=exp['experiment_id'];key=exp['cells'][0]['key']
    cell=store.cell(key);task=cell['task_id'];policy=cell['policy_id']
    manifest=dict(configs={f"{task}:{cell['seed']}":dict(CONFIG,task_id=task,task_prompt='Move object.',model_source='codex')},
        selection={'help_budget':1},dev_model='gpt-6-sol',eval_model='gpt-6-astra',memory={'entries':[]},
        dev_reasoning_effort='high',quota_stop_percent=90,initial_sources={task:{'source':SOURCE.decode()}},
        policies={policy:{}},demos={task:{'content':'Example'}},**manifest_fields())
    class Listener:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def bind(self,*a):pass
        def listen(self):pass
        def getsockname(self):return ('127.0.0.1',12345)
    monkeypatch.setattr(loop.socket,'socket',Listener)
    monkeypatch.setattr(isolation,'verify_runtime',lambda *a,**kw:None)
    monkeypatch.setattr(isolation,'codex_identity',lambda:dict(version='fixture'))
    now=[0.];checks=[];usage=disk.shutil.disk_usage
    monkeypatch.setattr(loop,'time',SimpleNamespace(monotonic=lambda:now[0]))
    def disk_usage(path):checks.append(now[0]);return usage(path)
    monkeypatch.setattr(disk.shutil,'disk_usage',disk_usage)
    def checked_drive(workspace,session,ledger,*,control,before_step,**kw):
        start=now[0];checks.clear();space['free']=100
        assert control() is None
        for _ in range(100):assert control() is None
        now[0]=start+9.999
        assert control() is None and checks==[start]
        now[0]=start+10;space['free']=14
        assert control()=='pause' and checks==[start,start+10]
        space['free']=100
        for _ in range(100):assert control()=='pause'
        assert checks==[start,start+10]
        now[0]=start+20
        assert control()=='pause' and checks==[start,start+10,start+20]
        with store.tx() as db:
            current=store.get(db,'experiments',eid);current.pop('pause_requested_at');store.put(db,'experiments',eid,current)
        assert control() is None
        space['free']=14
        for kind in ('execute','eval'):
            assert not before_step(kind)
            assert control()=='pause'
        assert checks==[start,start+10,start+20,start+20,start+20]
        with store.tx() as db:
            current=store.get(db,'experiments',eid);current['cancelled']=True;store.put(db,'experiments',eid,current)
        assert control()=='cancel' and len(checks)==5
        with store.tx() as db:
            current=store.get(db,'experiments',eid);current['cancelled']=False
            for field in ('pause_requested_at','pause_reason','pause_detail'):current.pop(field,None)
            store.put(db,'experiments',eid,current)
        return dict(state='completed',reason='fixture',resources=ledger.snapshot())
    monkeypatch.setattr(loop,'drive',checked_drive)
    for _ in range(2):
        result=loop.run_agent_cell(store,exp,manifest,key)
        assert result['reason']=='fixture',result

@pytest.mark.parametrize('boundary',['execute','eval'])
def test_agent_disk_pause_and_resume_never_reexecutes(ws,tmp_path,monkeypatch,space,boundary):
    (tmp_path/'run').mkdir()
    store,exp,_=_loop_setup(tmp_path/'run',monkeypatch);eid=exp['experiment_id']
    ledger=AgentLedger(store.path,exp['cells'][0]['key'])
    class Eval(FakeEvaluator):
        calls=0
        def evaluate(self,*a,**kw):self.calls+=1;return super().evaluate(*a,**kw)
    class Moving(Executor):
        def execute(self,*a,**kw):
            result=super().execute(*a,**kw)
            if boundary=='eval':space['free']=14
            return result
    moving=Moving((1,));evaluator=Eval()
    drop=[True]
    def before_step(kind):
        if boundary=='execute' and kind=='execute' and drop[0]:space['free']=14
        return not disk.pause_if_low(store,eid)
    def control():return 'pause' if experiments.snapshot(store,eid).get('pause_requested_at') else None
    session=fake_session(ws,["tool('submit');time.sleep(20)"])
    result=drive(ws,session,ledger,executor=moving,prober=FakeProber(),evaluator=evaluator,control=control,before_step=before_step)
    assert result['reason']=='operator_paused' and ledger.snapshot()['paused_at'] is not None
    assert moving.calls==(boundary=='eval') and evaluator.calls==0
    assert not any(j['state'] in ('claimed','unknown') for j in ledger.jobs())
    if boundary=='eval':assert any(j['state']=='paused' for j in ledger.jobs())
    space['free']=100;drop[0]=False
    with store.tx() as db:
        current=store.get(db,'experiments',eid);current.pop('pause_requested_at');store.put(db,'experiments',eid,current)
    resumed=CodexAgentSession(ws.ws,ws.private,command_factory=session.command_factory)
    result=drive(ws,resumed,ledger,executor=Executor((1,)),prober=FakeProber(),evaluator=evaluator,control=control,before_step=before_step)
    assert result['reason']=='native_success' and evaluator.calls==1
    assert ledger.snapshot()['used']['moving_executions']==1 and ledger.snapshot()['used']['eval_runs']==1
    assert all(j['state']=='settled' for j in ledger.jobs())

def test_agent_run_page_shows_disk_pause_and_resume_state(ws,tmp_path,monkeypatch,space):
    from benchmarks.attention_harness.dev_agent_view import projection
    (tmp_path/'run').mkdir()
    store,exp,_=_loop_setup(tmp_path/'run',monkeypatch);eid=exp['experiment_id'];key=exp['cells'][0]['key']
    store.update_cell(key,dev_mode='agent',artifact_dir=str(ws.private.parent.parent),state='planned',reason='operator_paused')
    space['free']=14;disk.pause_if_low(store,eid)
    with store.tx() as db:
        current=store.get(db,'experiments',eid);current.update(state='paused',paused_at=1);store.put(db,'experiments',eid,current)
    value=projection(store,store.cell(key))
    rc=value['dev_v2_compat']['run_controls']
    assert rc['state']=='paused' and rc['pause_reason']=='disk_low' and '磁盘不足' in rc['pause_detail']
    assert not rc['can_resume'] and value['dev_v2_compat']['lifecycle'] is None
    space['free']=100
    assert projection(store,store.cell(key))['dev_v2_compat']['run_controls']['can_resume']


def test_a_running_agent_run_can_be_paused_from_its_page(ws,tmp_path,monkeypatch,space):
    """11-11: a real Remote agent run paused and resumed; the page had offered no Pause (can_pause was fixed at False)."""
    import os
    from benchmarks.attention_harness.dev_agent_view import projection
    from benchmarks.attention_harness.dev_loop import process_identity
    (tmp_path/'run').mkdir()
    store,exp,_=_loop_setup(tmp_path/'run',monkeypatch);eid=exp['experiment_id'];key=exp['cells'][0]['key']
    store.update_cell(key,dev_mode='agent',artifact_dir=str(ws.private.parent.parent),state='running')
    def put(**fields):
        with store.tx() as db:
            current=store.get(db,'experiments',eid);current.update(fields);store.put(db,'experiments',eid,current)
    put(state='running',owner=process_identity(os.getpid()))
    compat=projection(store,store.cell(key))['dev_v2_compat'];rc=compat['run_controls']
    assert rc['state']=='running' and rc['can_pause'] and not rc['can_resume'] and not rc['can_keep_running']
    assert compat['lifecycle'] and compat['lifecycle']['can_stop']  # Stop run stays.
    put(pause_requested_at=1)
    rc=projection(store,store.cell(key))['dev_v2_compat']['run_controls']
    assert rc['state']=='pausing' and rc['can_keep_running'] and not rc['can_pause']
    put(owner=None)  # A worker that is gone cannot be paused.
    put(pause_requested_at=None)
    rc=projection(store,store.cell(key))['dev_v2_compat']['run_controls']
    assert not rc['can_pause']
