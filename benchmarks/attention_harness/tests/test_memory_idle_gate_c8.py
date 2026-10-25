"""C8: isolated ledgers and process receipts; no scheduler or simulator launch."""
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness import dev_loop, memory_validation_worker as worker
from benchmarks.attention_harness.tests.test_memory_validation_worker import deps


@pytest.fixture
def empty_proc(monkeypatch):
    monkeypatch.setattr(worker, 'Path', lambda p: SimpleNamespace(iterdir=lambda: [])
                        if str(p) == '/proc' else Path(p))


def ledger(root, *, state='needs_review', job='needs_review', age=7*3600, tier='dev', receipt='dead'):
    directory=root/'devexp-abandoned';directory.mkdir(parents=True)
    path=directory/'ledger.sqlite3'
    with sqlite3.connect(path) as db:
        db.executescript('CREATE TABLE v2_experiments(payload TEXT); '
                         'CREATE TABLE v2_jobs(state TEXT,payload TEXT); '
                         'CREATE TABLE v2_cells(id TEXT,payload TEXT);')
        db.execute('INSERT INTO v2_experiments VALUES (?)', (json.dumps(dict(
            experiment_id=directory.name, state=state, selection={'mode':'experiment','budget_tier':tier},
            started_at='2026-10-23T00:00:00Z')),))
        db.execute('INSERT INTO v2_cells VALUES (?,?)', ('cell',json.dumps({'experiment_id':directory.name})))
        if job:db.execute('INSERT INTO v2_jobs VALUES (?,?)', (job,json.dumps({'cell':'cell'})))
    identity=dev_loop.process_identity()
    if receipt=='dead':identity['pid']=999999999
    if receipt=='boot':identity['boot_id']='previous-boot'
    if receipt=='reused':identity['start_ticks']=str(int(identity['start_ticks'])+1)
    if receipt!='missing':
        (directory/f"scheduler-process-{identity['pid']}.json").write_text(
            '{invalid' if receipt=='unreadable' else json.dumps(identity))
    os.utime(path,(worker.time.time()-age,)*2)
    return path


@pytest.mark.parametrize('state,job', [('running',None),('claimed',None),('planned',None),
                                     ('needs_review','needs_review'),('completed','claimed'),('completed',None)])
def test_live_scheduler_blocks_and_names_experiment(tmp_path,empty_proc,state,job):
    path=ledger(tmp_path,state=state,job=job,receipt='live')
    reason=worker.run_activity([path])
    assert 'devexp-abandoned' in reason and 'started 2026-10-23' in reason
    assert ('unresolved work' if job else 'is active') in reason
    assert worker.run_activity([path],own_experiment='devexp-abandoned') is None


@pytest.mark.parametrize('receipt',['dead','boot','reused'])
@pytest.mark.parametrize('age',[0,7*3600])
@pytest.mark.parametrize('state,job',[('needs_review','needs_review'),('running','claimed'),('planned',None)])
def test_verified_dead_scheduler_does_not_block(tmp_path,empty_proc,receipt,age,state,job):
    path=ledger(tmp_path,receipt=receipt,age=age,state=state,job=job)
    ignored=[]
    for _ in range(2):assert worker.run_activity([path],ignored_stale_runs=ignored) is None
    assert ignored==([{'id':'devexp-abandoned','reason':'scheduler not running'}] if job else [])


@pytest.mark.parametrize('receipt',['missing','unreadable'])
@pytest.mark.parametrize('state,job',[('planned',None),('running',None),('claimed',None),('needs_review','needs_review')])
@pytest.mark.parametrize('age,blocked',[(6*3600-1,True),(6*3600,False),(7*3600,False)])
def test_unexplained_ledger_six_hour_boundary(tmp_path,empty_proc,receipt,state,job,age,blocked):
    path=ledger(tmp_path,receipt=receipt,state=state,job=job,age=age)
    assert bool(worker.run_activity([path])) is blocked


def test_wal_modification_counts_as_recent(tmp_path,empty_proc):
    path=ledger(tmp_path,receipt='missing')
    # Preserve a valid uncheckpointed WAL with an open writer.
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('INSERT INTO v2_jobs VALUES (?,?)',('claimed',json.dumps({'cell':'cell'})))
        db.commit()
        os.utime(path,(worker.time.time()-7*3600,)*2)
        assert 'devexp-abandoned' in worker.run_activity([path])


def test_main_matrix_planned_blocks_even_with_dead_scheduler(tmp_path,empty_proc):
    path=ledger(tmp_path,tier='formal',state='planned',job=None)
    assert worker.run_activity([path])=='The frozen main matrix is planned or running: devexp-abandoned'


def test_live_worker_command_blocks_and_names_experiment(tmp_path,monkeypatch):
    proc=tmp_path/'proc';process=proc/'123';process.mkdir(parents=True)
    (process/'cmdline').write_bytes(b'python\0-m\0benchmarks.attention_harness.daily_dev_v2\0--worker\0devexp-user\0')
    monkeypatch.setattr(worker,'Path',lambda p:proc if str(p)=='/proc' else Path(p))
    assert worker.run_activity([])=='Another run is active: devexp-user'
    assert worker.run_activity([],own_pids=(123,)) is None


def test_unreadable_ledger_pauses_existing_work(tmp_path,empty_proc):
    d=deps(tmp_path);worker.set_enabled(tmp_path,True);state={};worker.tick(state,d)
    path=tmp_path/'devexp-broken'/'ledger.sqlite3';path.parent.mkdir();path.write_text('broken')
    d.activity=lambda current:worker.run_activity([path])
    worker.tick(state,d)
    assert d.runner.pauses==1 and d.runner.started==['c0']
    assert 'ledger unreadable: devexp-broken/ledger.sqlite3' in state['status']


def test_stale_runs_persist_once_and_new_run_pauses(tmp_path,empty_proc):
    from benchmarks.attention_harness.memory_monitor import automatic_checks
    path=ledger(tmp_path/'runs');d=deps(tmp_path);worker.set_enabled(tmp_path,True)
    def activity(current):
        activity.ignored_stale_runs=[]
        return worker.run_activity([path],ignored_stale_runs=activity.ignored_stale_runs)
    d.activity=activity;state={}
    worker.tick(state,d);worker.tick(state,d)
    state=json.loads((tmp_path/'state.json').read_text());worker.tick(state,d)
    assert state['ignored_stale_runs']==[{'id':'devexp-abandoned','reason':'scheduler not running'}]
    assert automatic_checks(tmp_path)['ignored_stale_runs']==state['ignored_stale_runs']
    assert d.runner.started==['c0']
    identity=dev_loop.process_identity()
    (path.parent/f"scheduler-process-{identity['pid']}.json").write_text(json.dumps(identity))
    worker.tick(state,d)
    assert d.runner.pauses==1 and 'devexp-abandoned' in state['status']


def test_runtime_collects_stale_runs(tmp_path,empty_proc,monkeypatch):
    from benchmarks.attention_harness import memory_validation_runtime as runtime
    from benchmarks.attention_harness.memory_service_client import MemoryServiceClient
    path=ledger(tmp_path/'runs');root=tmp_path/'state';root.mkdir()
    worker.atomic(root/'runtime.json',dict(base_url='http://unused',key_file='unused',
        experiments_root=str(path.parent.parent),validator_root=str(tmp_path/'validator'),
        activity_ledger_roots=[str(path.parent.parent)],activity_ledgers=[]))
    monkeypatch.setattr(MemoryServiceClient,'offline',lambda *a,**kw:object())
    d=runtime.build_runtime(root)
    try:
        assert d.activity(None) is None
        assert d.activity.ignored_stale_runs==[{'id':'devexp-abandoned','reason':'scheduler not running'}]
    finally:d.runner.pool.shutdown()
