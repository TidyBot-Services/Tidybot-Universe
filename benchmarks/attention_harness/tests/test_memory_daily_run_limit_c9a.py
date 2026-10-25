"""C9a: configurable daily admission, with no real execution or services."""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.tests.test_memory_validation_worker import deps
from benchmarks.attention_harness.tests.test_memory_cross_seed import worker_deps


def state_today(d,used):
    return dict(day=datetime.fromtimestamp(d.clock()).astimezone().date().isoformat(),runs_today=used)


def configure(root,limit):
    worker.set_daily_run_limit(root,limit)
    worker.set_enabled(root,True)


@pytest.mark.parametrize('existing',[False,True])
def test_absent_limit_defaults_to_twenty(tmp_path,existing):
    if existing:worker.atomic(tmp_path/'autovalidate.json',dict(enabled=True))
    assert worker.daily_run_stop({'runs_today':19},tmp_path) is None
    assert '20 runs' in worker.daily_run_stop({'runs_today':20},tmp_path)
    assert worker.daily_run_stop({'runs_today':10},tmp_path,10) is None
    assert worker.daily_run_stop({'runs_today':11},tmp_path,10)
    assert automatic_checks(tmp_path)['daily_run_limit']==20


@pytest.mark.parametrize('limit,used,allowed',[(None,1000,True),(31,21,True),(31,22,False),(1,0,False),(31,31,False)])
def test_same_scene_batch_admission(tmp_path,limit,used,allowed):
    d=deps(tmp_path);configure(tmp_path,limit);state=state_today(d,used)
    worker.tick(state,d)
    assert bool(d.runner.started) is allowed
    assert state['runs_today']==used+int(allowed)
    if not allowed:assert 'tomorrow' in state['status'] or 'done for today' in state['status']


def test_unlimited_same_scene_finishes_above_old_cap(tmp_path):
    d=deps(tmp_path);configure(tmp_path,None);state=state_today(d,1000)
    d.client.evaluate=lambda mid:dict(status='trusted')
    d.client.decision=lambda mid:dict(reason='trusted')
    for _ in range(12):
        worker.tick(state,d);d.runner.phase='completed'
        if state.get('results'):break
    assert state['results'][0]['status']=='trusted'
    assert len(d.runner.started)==10 and state['runs_today']==1010


@pytest.mark.parametrize('limit,used,allowed',[(None,1000,True),(7,6,True),(7,7,False)])
def test_origin_reservation_uses_limit(tmp_path,limit,used,allowed):
    d=deps(tmp_path);configure(tmp_path,limit);state=state_today(d,used)
    state['weekly_quota_at']=d.clock()
    d.runner.origin_needed=lambda n:dict(kind='origin',origin_key='origin',title='Note')
    worker.prepare_origin(state,d,{},lambda:None,lambda text:text)
    assert bool(d.runner.started) is allowed
    assert state['runs_today']==used+int(allowed)


@pytest.mark.parametrize('cross',[False,True])
def test_running_arm_finishes_but_next_arm_respects_new_limit(tmp_path,cross):
    d=worker_deps(tmp_path) if cross else deps(tmp_path)
    configure(tmp_path,None);state={};worker.tick(state,d)
    if cross:
        d.runner.phase='completed';worker.tick(state,d);worker.tick(state,d)
    before=len(d.runner.started);used=state['runs_today']
    worker.set_daily_run_limit(tmp_path,used)
    d.runner.phase='completed';worker.tick(state,d)
    assert len(d.runner.started)==before and state['runs_today']==used
    assert state['current']['active'] is False
    assert f'({used} runs)' in state['status']
    worker.set_daily_run_limit(tmp_path,None);worker.tick(state,d)
    assert len(d.runner.started)==before+1 and state['runs_today']==used+1


@pytest.mark.parametrize('role,needed',[('transfer',10),('neutral',6)])
@pytest.mark.parametrize('limit,remaining',[(None,0),(37,0),(37,1)])
def test_cross_seed_whole_batch(tmp_path,role,needed,limit,remaining):
    d=worker_deps(tmp_path,role);configure(tmp_path,limit)
    state={};worker.tick(state,d);d.runner.phase='completed';worker.tick(state,d)
    used=1000 if limit is None else limit-needed+remaining
    state['runs_today']=used;worker.tick(state,d)
    allowed=limit is None or remaining==0
    assert d.runner.cross==([None,needed//2] if allowed else [None])
    assert state['runs_today']==used+int(allowed)
    if not allowed:assert 'whole batch' in state['status'] and 'tomorrow' in state['status']
    else:
        for _ in range(needed):worker.tick(state,d)
        assert state['current'] is None
        assert state['runs_today']==used+needed


@pytest.mark.parametrize('limit,used,allowed',[(None,1000,True),(37,36,True),(37,37,False)])
def test_cross_seed_probe_admission(tmp_path,limit,used,allowed):
    d=worker_deps(tmp_path);configure(tmp_path,limit);state=state_today(d,used)
    worker.tick(state,d)
    assert bool(d.runner.started) is allowed
    assert state['runs_today']==used+int(allowed)


@pytest.mark.parametrize('limit',[0,-1,True,False,1.5,20.0,'20','none',[],{}])
def test_invalid_settings_refused_without_mutation(tmp_path,limit):
    worker.atomic(tmp_path/'autovalidate.json',dict(enabled=True,daily_run_limit=limit))
    before=(tmp_path/'autovalidate.json').read_bytes()
    with pytest.raises(ValueError,match='daily_run_limit'):worker.settings(tmp_path)
    with pytest.raises(ValueError,match='daily_run_limit'):worker.set_enabled(tmp_path,False)
    assert (tmp_path/'autovalidate.json').read_bytes()==before


def cli(root,*args):
    return subprocess.run([sys.executable,'-m',worker.__name__,'--state-dir',str(root),*args],capture_output=True,text=True,timeout=10)


def test_cli_round_trip_preserves_keys_and_switch(tmp_path):
    cross=dict(enabled=True,candidate_seeds=[114],contracts={'114':'/fake/114.json'})
    worker.atomic(tmp_path/'autovalidate.json',dict(enabled=False,cross_seed=cross))
    for argument,limit in [('none',None),('31',31),('none',None)]:
        assert cli(tmp_path,'--set-daily-run-limit',argument).returncode==0
        assert worker.settings(tmp_path)==dict(enabled=False,cross_seed=cross,daily_run_limit=limit)
        for enabled in ['true','false']:
            assert cli(tmp_path,'--set-enabled',enabled).returncode==0
            assert worker.settings(tmp_path)==dict(enabled=enabled=='true',cross_seed=cross,daily_run_limit=limit)
    assert sorted(p.name for p in tmp_path.iterdir())==['autovalidate.json']


@pytest.mark.parametrize('argument',['0','-1','1.5','null','true','invalid'])
def test_cli_rejects_invalid_limit(tmp_path,argument):
    configure(tmp_path,None);before=(tmp_path/'autovalidate.json').read_bytes()
    result=cli(tmp_path,'--set-daily-run-limit',argument)
    assert result.returncode==2 and 'positive integer' in result.stderr
    assert (tmp_path/'autovalidate.json').read_bytes()==before


def test_setting_write_is_atomic_on_replace_failure(tmp_path,monkeypatch):
    configure(tmp_path,None);before=(tmp_path/'autovalidate.json').read_bytes()
    def fail(*args):raise OSError('interrupted replacement')
    monkeypatch.setattr(worker.os,'replace',fail)
    with pytest.raises(OSError):worker.set_daily_run_limit(tmp_path,5)
    assert (tmp_path/'autovalidate.json').read_bytes()==before
    assert sorted(p.name for p in tmp_path.iterdir())==['autovalidate.json']


@pytest.mark.parametrize('limit',[None,31])
def test_monitor_projects_current_setting_and_count_while_off(tmp_path,limit):
    worker.set_daily_run_limit(tmp_path,limit)
    worker.atomic(tmp_path/'state.json',dict(runs_today=43,daily_run_limit=999))
    value=automatic_checks(tmp_path)
    assert value['daily_run_limit']==limit and value['runs_today']==43 and value['enabled'] is False


def test_ui_text():
    script=Path(__file__).with_suffix('.cjs')
    result=subprocess.run(['node','--test',str(script)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


@pytest.mark.parametrize('cross',[False,True])
@pytest.mark.parametrize('gate',['quota','quota_delta','unreadable','user_run'])
def test_unlimited_keeps_existing_dispatch_gates(tmp_path,cross,gate):
    d=worker_deps(tmp_path) if cross else deps(tmp_path)
    configure(tmp_path,None);state=state_today(d,1000)
    state['day_start_quota']=10
    if gate=='quota':d.quota=lambda:dict(used_percent=70,observed_at=d.clock())
    elif gate=='quota_delta':d.quota=lambda:dict(used_percent=16,observed_at=d.clock())
    elif gate=='unreadable':d.quota=lambda:None
    else:d.activity=lambda current:'Another run is active'
    worker.tick(state,d)
    assert not d.runner.started and state['runs_today']==1000
    expected={'quota':'70%','quota_delta':'5 points','unreadable':'could not be read','user_run':'Another run is active'}
    assert expected[gate] in state['status']


@pytest.mark.parametrize('cross',[False,True])
def test_unlimited_pauses_for_user_and_resumes_without_recount(tmp_path,cross):
    d=worker_deps(tmp_path) if cross else deps(tmp_path)
    configure(tmp_path,None);state=state_today(d,1000);worker.tick(state,d)
    d.activity=lambda current:'Another run is active';worker.tick(state,d)
    assert d.runner.pauses==1 and state['runs_today']==1001
    d.activity=lambda current:None;worker.tick(state,d)
    assert d.runner.resumes==1 and state['runs_today']==1001
