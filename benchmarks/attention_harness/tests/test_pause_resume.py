"""Graceful Pause/Resume for Dev v2 runs: fake providers and runners only, no paid call or native run."""
import json
import time
from pathlib import Path

import pytest

from benchmarks.attention_harness import dev_experiment as experiments
from benchmarks.attention_harness.core.dev_store import DevConflict
from benchmarks.attention_harness.daily_dev_v2 import DailyDevV2
from benchmarks.attention_harness.dev_experiment import run, snapshot
from benchmarks.attention_harness.tests.test_eval_advisor_split import FrameRunner, RoutedClient, _loop_setup


class PausingClient(RoutedClient):
    """Presses Pause while the first Dev call is in flight, like an operator clicking mid-step."""
    def __init__(self, store, experiment_id, **kw):
        super().__init__(**kw);self.store=store;self.experiment_id=experiment_id;self.dev_calls=0
    def chat(self, **kw):
        prompt=kw['messages'][0]['content']
        if not prompt.startswith(('You are the system Eval agent','You are a human-like robotics advisor')):
            self.dev_calls+=1
            if self.dev_calls==1:experiments.request_pause(self.store,self.experiment_id)
        return super().chat(**kw)


def jobs(cell, kind):
    return [j for j in cell['jobs'] if j['kind']==kind]


def test_pause_finishes_the_current_step_then_resume_continues_with_the_next(tmp_path, monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);eid=experiment['experiment_id']
    client=PausingClient(store,eid)
    result=run(store,eid,max_cells=1,budget_file=budget,client=client,runner_factory=FrameRunner)
    cell=result['cells'][0]
    # The in-flight model call settled normally; nothing was cut off or left for review.
    assert result['state']=='paused' and result['owner'] is None and result['paused_at']
    assert cell['state']=='planned' and cell['reason']=='operator_paused'
    assert [j['state'] for j in jobs(cell,'model')]==['settled'] and len(cell['versions'])==1
    assert jobs(cell,'execute')==[] and not any(j['state'] in {'claimed','needs_review'} for j in cell['jobs'])
    goal=store.goal(experiment['goal_id'] if 'goal_id' in experiment else None)
    assert goal['paused_at'] is not None

    span=experiments.resume_paused(store,eid)
    assert span>=0 and snapshot(store,eid)['state']=='planned' and store.cell(cell['key'])['reason'] is None
    result=run(store,eid,max_cells=1,budget_file=budget,client=client,runner_factory=FrameRunner)
    cell=result['cells'][0]
    # Resume picks up at the next step: v1 runs on the robot; its model call is never repeated.
    assert result['state']=='completed' and cell['state']=='completed'
    assert [j['version'] for j in jobs(cell,'execute')][:1]==[1]
    assert [j['version'] for j in jobs(cell,'model')]==sorted({j['version'] for j in jobs(cell,'model')})
    assert len(result['pauses'])==1 and result['pauses'][0]['seconds']==span


def test_paused_time_does_not_use_the_wall_budget_or_move_the_deadline(tmp_path, monkeypatch):
    store,experiment,_=_loop_setup(tmp_path,monkeypatch)
    gid=experiments.experiment_goal(snapshot(store,experiment['experiment_id']))
    now=time.time()
    with store.tx() as db:
        g=store.get(db,'goals',gid);g.update(real_started_at=now-1000,authorized_until=now+500,paused_seconds=300,paused_at=now-100);store.put(db,'goals',gid,g)
    g=store.goal(gid)
    assert g['paused_seconds_total']==pytest.approx(400,abs=2)
    assert g['wall_used']==pytest.approx(600,abs=2)                      # 1000 s elapsed, 400 s of it paused
    # The fixed deadline (now+500) moves later by the 400 paused seconds.
    assert g['wall_remaining']==pytest.approx(min(g['wall_limit']-600,500+400),abs=2)
    frozen=store.goal(gid)['wall_used'];time.sleep(.3)
    assert store.goal(gid)['wall_used']==pytest.approx(frozen,abs=.05)    # the clock stands still while paused
    assert experiments.paused_seconds(store,gid)==pytest.approx(400,abs=2) and experiments.paused_seconds(store,None)==0


def test_keep_running_withdraws_a_pause_the_worker_has_not_reached(tmp_path, monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);eid=experiment['experiment_id']
    class WithdrawingClient(PausingClient):
        withdrawn=None
        def chat(self,**kw):
            reply=super().chat(**kw)
            if self.dev_calls==1 and self.withdrawn is None:self.withdrawn=experiments.withdraw_pause(self.store,self.experiment_id)
            return reply
    client=WithdrawingClient(store,eid)
    result=run(store,eid,max_cells=1,budget_file=budget,client=client,runner_factory=FrameRunner)
    assert client.withdrawn is True
    assert result['state']=='completed' and 'pause_requested_at' not in result and not result.get('pauses')
    assert experiments.withdraw_pause(store,eid) is False


def test_pause_needs_a_live_run_and_stop_while_paused_closes_the_pause(tmp_path, monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);eid=experiment['experiment_id']
    with pytest.raises(DevConflict,match='nothing to pause'):experiments.request_pause(store,eid)
    run(store,eid,max_cells=1,budget_file=budget,client=PausingClient(store,eid),runner_factory=FrameRunner)
    with pytest.raises(DevConflict,match='not running'):experiments.request_pause(store,eid)
    gid=experiments.experiment_goal(snapshot(store,eid))
    experiments.cancel(store,eid)
    g=store.goal(gid)
    assert g['paused_at'] is None and g['paused_seconds']>=0
    with pytest.raises(DevConflict,match='Only a paused run'):experiments.resume_paused(store,eid)


def controls(store, cell, tmp_path):
    bridge=object.__new__(DailyDevV2);bridge.workers={};bridge.root=tmp_path/'no-other-runs';bridge.root.mkdir(exist_ok=True)
    return bridge.run_controls(store,cell)


def test_controls_explain_paused_resumable_and_ended_runs(tmp_path, monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);eid=experiment['experiment_id']
    run(store,eid,max_cells=1,budget_file=budget,client=PausingClient(store,eid),runner_factory=FrameRunner)
    rc=controls(store,snapshot(store,eid)['cells'][0],tmp_path)
    assert rc['state']=='paused' and rc['can_resume'] and not rc['can_pause'] and rc['resume_blocked_reason'] is None
    experiments.cancel(store,eid)
    rc=controls(store,snapshot(store,eid)['cells'][0],tmp_path)
    assert rc['state']=='ended' and not rc['can_resume'] and 'ended with Stop run' in rc['resume_blocked_reason']
    assert rc['interrupted_by_stop'] is None                               # nothing was in flight when it was stopped


def test_controls_name_the_step_a_stop_cut_off(tmp_path, monkeypatch):
    store,experiment,_=_loop_setup(tmp_path,monkeypatch);eid=experiment['experiment_id']
    cell=snapshot(store,eid)['cells'][0]
    with store.tx() as db:
        db.execute("INSERT INTO v2_jobs(id,cell,version,kind,state,payload) VALUES (?,?,?,?,?,?)",
                   ('job-6',cell['key'],6,'model','needs_review',json.dumps({'job_id':'job-6','cell':cell['key'],'version':6,'kind':'model','reservation':{}})))
    store.update_cell(cell['key'],state='needs_review',reason='DevConflict: provider interrupted/deadline; paid request retained for review')
    experiments.cancel(store,eid)
    rc=controls(store,snapshot(store,eid)['cells'][0],tmp_path)
    assert rc['interrupted_by_stop']=={'kind':'model','version':6,'step':'writing v6'}
    assert 'ended with Stop run' in rc['resume_blocked_reason']


def test_a_worker_lost_while_pausing_can_still_be_resumed(tmp_path, monkeypatch):
    store,experiment,budget=_loop_setup(tmp_path,monkeypatch);eid=experiment['experiment_id']
    run(store,eid,max_cells=1,budget_file=budget,client=PausingClient(store,eid),runner_factory=FrameRunner)
    with store.tx() as db:   # As if the worker died before parking: planned, request still set.
        e=store.get(db,'experiments',eid);e.update(state='planned');e.pop('paused_at');store.put(db,'experiments',eid,e)
    rc=controls(store,snapshot(store,eid)['cells'][0],tmp_path)
    assert rc['state']=='idle' and rc['can_resume'] and rc['resume_blocked_reason'] is None
    assert experiments.withdraw_pause(store,eid) is True and 'pause_requested_at' not in snapshot(store,eid)
