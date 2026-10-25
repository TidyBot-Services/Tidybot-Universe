"""C11 quota configuration, exercised only with disposable state and fake runners."""
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.memory_monitor import automatic_checks
from benchmarks.attention_harness.tests.test_memory_daily_run_limit_c9a import cli
from benchmarks.attention_harness.tests.test_memory_validation_worker import deps, freshness_deps

WEEKLY='weekly_quota_stop_percent'
DAILY='daily_quota_rise_stop_points'
KINDS=['arm','origin','cross_probe','cross_pairs','cross_origin']


def baseline(state,d):
    state.update(day=datetime.fromtimestamp(d.clock()).astimezone().date().isoformat(),day_start_quota=10)
    state.setdefault('runs_today',0)


@pytest.mark.parametrize('existing',[False,True])
def test_absent_settings_keep_old_defaults(tmp_path,existing):
    if existing:worker.atomic(tmp_path/'autovalidate.json',dict(enabled=False))
    d=deps(tmp_path);state={'day_start_quota':64}
    for used,expected in [(69,None),(69.1,'5 points'),(70,'stop at 70%')]:
        d.quota=lambda:dict(used_percent=used,observed_at=d.clock())
        reason=worker.quota_stop(state,d,lambda:None)
        assert reason is None if expected is None else expected in reason
    monitor=automatic_checks(tmp_path)
    assert monitor[WEEKLY]==70 and monitor[DAILY]==5


@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('used,stopped',[(59.5,False),(60,True),(61,True)])
def test_sixty_percent_and_disabled_daily_rule_at_every_dispatch(tmp_path,kind,used,stopped):
    d,state=freshness_deps(tmp_path,kind);baseline(state,d)
    worker.set_quota_setting(tmp_path,WEEKLY,60)
    worker.set_quota_setting(tmp_path,DAILY,None)
    d.quota=lambda:dict(used_percent=used,observed_at=d.clock())
    worker.tick(state,d)
    assert bool(d.runner.started) is not stopped
    if stopped:assert state['status']==f'Automatic checks paused: Codex weekly use is at {used:g}% (stop at 60%).'


@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('used,stopped',[(12.5,False),(12.6,True)])
def test_custom_daily_rule_boundary_and_message(tmp_path,kind,used,stopped):
    d,state=freshness_deps(tmp_path,kind);baseline(state,d)
    worker.set_quota_setting(tmp_path,DAILY,2.5)
    d.quota=lambda:dict(used_percent=used,observed_at=d.clock())
    worker.tick(state,d)
    assert bool(d.runner.started) is not stopped
    if stopped:assert 'more than 2.5 points today' in state['status']


@pytest.mark.parametrize('kind',KINDS)
@pytest.mark.parametrize('result',['unreadable','stale',59,61])
def test_refresh_and_fail_closed_behavior_with_daily_disabled(tmp_path,kind,result):
    d,state=freshness_deps(tmp_path,kind);baseline(state,d)
    worker.set_quota_setting(tmp_path,WEEKLY,60)
    worker.set_quota_setting(tmp_path,DAILY,None)
    now=d.clock();calls=[]
    d.quota=lambda:dict(used_percent=10,observed_at=now-601)
    def refresh():
        calls.append('refresh')
        d.quota=lambda:None if result=='unreadable' else dict(used_percent=10 if result=='stale' else result,observed_at=now-601 if result=='stale' else now)
    d.refresh_quota=refresh
    worker.tick(state,d)
    assert calls==['refresh']
    assert bool(d.runner.started) is (result==59)
    if isinstance(result,str):assert state['status']==worker.QUOTA_UNREADABLE
    elif result==61:assert 'stop at 60%' in state['status']


@pytest.mark.parametrize('kind',['arm','origin','cross_probe'])
def test_dispatch_refresh_after_slow_preparation_uses_config(tmp_path,kind):
    d,state=freshness_deps(tmp_path,kind)
    worker.set_quota_setting(tmp_path,WEEKLY,60)
    worker.set_quota_setting(tmp_path,DAILY,None)
    now=d.clock();d.quota=lambda:dict(used_percent=10,observed_at=now)
    method='origin_needed' if kind=='origin' else 'begin_cross' if kind=='cross_probe' else 'begin'
    begin=getattr(d.runner,method)
    def delayed(*args,**kwargs):
        d.clock=lambda:now+601
        return begin(*args,**kwargs)
    setattr(d.runner,method,delayed)
    def refresh():d.quota=lambda:dict(used_percent=61,observed_at=d.clock())
    d.refresh_quota=refresh;worker.tick(state,d)
    assert not d.runner.started and 'stop at 60%' in state['status']


@pytest.mark.parametrize('key', [WEEKLY,DAILY])
@pytest.mark.parametrize('value',[0,-1,True,False,'60','none',[],{},float('nan'),float('inf'),float('-inf')])
def test_invalid_settings_and_setters_refused(tmp_path,key,value):
    worker.atomic(tmp_path/'autovalidate.json',dict(enabled=False))
    before=(tmp_path/'autovalidate.json').read_bytes()
    with pytest.raises(ValueError,match=key):worker.set_quota_setting(tmp_path,key,value)
    assert (tmp_path/'autovalidate.json').read_bytes()==before
    (tmp_path/'autovalidate.json').write_text(json.dumps(dict(enabled=False,**{key:value})))
    before=(tmp_path/'autovalidate.json').read_bytes()
    with pytest.raises(ValueError,match=key):worker.settings(tmp_path)
    with pytest.raises(ValueError,match=key):worker.set_enabled(tmp_path,True)
    assert (tmp_path/'autovalidate.json').read_bytes()==before


@pytest.mark.parametrize('value',[None,100.1,101])
def test_weekly_range_refused(tmp_path,value):
    worker.atomic(tmp_path/'autovalidate.json',dict(enabled=False,**{WEEKLY:value}))
    with pytest.raises(ValueError,match=WEEKLY):worker.settings(tmp_path)


@pytest.mark.parametrize('key,value',[(WEEKLY,0.1),(WEEKLY,60.5),(WEEKLY,100),(DAILY,None),(DAILY,0.1),(DAILY,101)])
def test_valid_settings(tmp_path,key,value):
    worker.set_quota_setting(tmp_path,key,value)
    assert worker.settings(tmp_path)[key]==value


def test_cli_round_trips_preserve_every_other_key(tmp_path):
    expected=dict(enabled=False,daily_run_limit=None,cross_seed=dict(enabled=True,candidate_seeds=[114],contracts={'114':'/fake/114.json'}))
    worker.atomic(tmp_path/'autovalidate.json',expected)
    for flag,key,argument,value in [
        ('--set-weekly-quota-stop',WEEKLY,'60',60),
        ('--set-daily-quota-rise-stop',DAILY,'none',None),
        ('--set-weekly-quota-stop',WEEKLY,'60.5',60.5),
        ('--set-daily-quota-rise-stop',DAILY,'2.5',2.5),
        ('--set-enabled','enabled','true',True),
        ('--set-daily-run-limit','daily_run_limit','31',31),
        ('--set-daily-quota-rise-stop',DAILY,'none',None),
    ]:
        result=cli(tmp_path,flag,argument)
        assert result.returncode==0,result.stderr
        expected[key]=value
        assert worker.settings(tmp_path)==expected
    assert sorted(p.name for p in tmp_path.iterdir())==['autovalidate.json']


@pytest.mark.parametrize('flag',['--set-weekly-quota-stop','--set-daily-quota-rise-stop'])
@pytest.mark.parametrize('argument',['0','-1','true','null','nan','inf','invalid'])
def test_invalid_cli_preserves_settings(tmp_path,flag,argument):
    worker.set_enabled(tmp_path,False);before=(tmp_path/'autovalidate.json').read_bytes()
    result=cli(tmp_path,flag,argument)
    assert result.returncode==2
    assert (tmp_path/'autovalidate.json').read_bytes()==before


@pytest.mark.parametrize('argument',['none','100.1'])
def test_cli_rejects_weekly_range(tmp_path,argument):
    assert cli(tmp_path,'--set-weekly-quota-stop',argument).returncode==2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('key,value',[(WEEKLY,60),(DAILY,None)])
def test_quota_setting_write_is_atomic(tmp_path,monkeypatch,key,value):
    worker.set_enabled(tmp_path,False);before=(tmp_path/'autovalidate.json').read_bytes()
    def fail(*args):raise OSError('interrupted replacement')
    monkeypatch.setattr(worker.os,'replace',fail)
    with pytest.raises(OSError):worker.set_quota_setting(tmp_path,key,value)
    assert (tmp_path/'autovalidate.json').read_bytes()==before
    assert sorted(p.name for p in tmp_path.iterdir())==['autovalidate.json']


def test_monitor_uses_current_config_even_while_off(tmp_path):
    worker.set_quota_setting(tmp_path,WEEKLY,60)
    worker.set_quota_setting(tmp_path,DAILY,None)
    worker.atomic(tmp_path/'state.json',dict(weekly_quota=61,**{WEEKLY:70,DAILY:5}))
    value=automatic_checks(tmp_path)
    assert value[WEEKLY]==60 and value[DAILY] is None
    assert value['weekly_quota']==61 and value['enabled'] is False


def test_ui_and_example():
    example=Path(__file__).resolve().parents[3]/'scripts/memory-autovalidate.example.json'
    value=json.loads(example.read_text())
    assert value[WEEKLY]==60 and value[DAILY] is None and value['enabled'] is False
    result=subprocess.run(['node','--test',str(Path(__file__).with_suffix('.cjs'))],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
