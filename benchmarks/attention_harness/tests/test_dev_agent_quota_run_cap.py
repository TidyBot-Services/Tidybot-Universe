import json
import time

import pytest

from benchmarks.attention_harness.dev_agent_quota import QuotaMonitor, rollout_tokens

M=1_000_000


class Ledger:
    def __init__(self):self.samples=[]
    def quota(self, sample):self.samples.append(sample)


def monitor(tmp_path, readings, **kw):
    now=time.time();it=iter(readings)
    reader=lambda:dict(used_percent=next(it),window_minutes=10080,resets_at=now+86400,observed_at=time.time())
    return QuotaMonitor(tmp_path, Ledger(), stop_percent=77, reader=reader, **kw)


def staged(tmp_path, rows, **kw):
    """rows: (used_percent, seconds the reading is older than now)."""
    now=time.time();it=iter(rows)
    def reader():
        used,age=next(it)
        return dict(used_percent=used,window_minutes=10080,resets_at=now+86400,observed_at=time.time()-age)
    return QuotaMonitor(tmp_path, Ledger(), stop_percent=77, reader=reader, **kw)


def last(tmp_path):return json.loads((tmp_path/'quota.jsonl').read_text().splitlines()[-1])


def test_other_runs_on_the_shared_counter_do_not_stop_this_run(tmp_path):
    # 11-11: three runs open together moved the counter 34 -> 39; each had used about 1.7 points itself.
    session={'dev':0}
    m=monitor(tmp_path, [34, 36, 39, 45], rollout_reader=lambda tid:session[tid])
    m.event({'type':'thread.started','thread_id':'dev'},session=True)
    for tokens in (0, 20*M, 44*M, 60*M):
        session['dev']=tokens;m.check('step')
    row=last(tmp_path)
    assert row['run_points']==pytest.approx(2.4) and row['account_points']==11 and row['run_points_source']=='own_tokens'
    assert row['run_tokens']==60*M and row['run_cap_reached'] is False and row['stopped'] is False


def test_a_run_stops_after_five_points_of_its_own(tmp_path):
    session={'dev':0}
    m=monitor(tmp_path, [20, 20, 20, 20], rollout_reader=lambda tid:session[tid])
    m.event({'type':'thread.started','thread_id':'dev'},session=True)
    m.check('start')
    session['dev']=120*M;m.check('dev alone is under the cap')
    # One-shot calls (Eval, Advisor) report their usage when they finish.
    m.event({'type':'turn.completed','usage':{'input_tokens':4*M,'cached_input_tokens':3*M,'output_tokens':M}})
    with pytest.raises(ValueError, match='quota_stop'):m.check('125 M = 5 points')
    row=last(tmp_path)
    assert row['run_points']==5 and row['run_cap_reached'] is True and row['account_points']==0


def test_a_finished_turn_of_the_session_is_not_counted_twice(tmp_path):
    m=monitor(tmp_path, [20], rollout_reader=lambda tid:30*M)
    m.event({'type':'thread.started','thread_id':'dev'},session=True)
    m.event({'type':'turn.completed','usage':{'input_tokens':30*M,'output_tokens':0}},session=True)
    m.check('a')
    assert last(tmp_path)['run_tokens']==30*M


def test_another_provider_is_not_counted(tmp_path):
    m=monitor(tmp_path, [20], rollout_reader=lambda tid:900*M)
    m.event({'type':'thread.started','thread_id':'glm'},session=True,count=False)
    m.event({'type':'turn.completed','usage':{'input_tokens':900*M,'output_tokens':0}},count=False)
    m.check('a')
    assert last(tmp_path)['run_tokens']==0


def test_the_count_survives_a_restart_and_never_goes_down(tmp_path):
    session={'dev':60*M}
    m=monitor(tmp_path, [40], rollout_reader=lambda tid:session[tid])
    m.event({'type':'thread.started','thread_id':'dev'},session=True)
    m.spent({'input_tokens':10*M,'output_tokens':0});m.check('a')
    session['dev']=None  # the session file is gone (cleaned up) after the restart
    again=monitor(tmp_path, [41, 41], rollout_reader=lambda tid:session[tid])
    again.check('b')
    assert last(tmp_path)['run_tokens']==70*M and last(tmp_path)['run_points_source']=='own_tokens'
    session['dev']=130*M
    with pytest.raises(ValueError, match='quota_stop'):again.check('c')


def test_without_a_count_of_its_own_the_run_falls_back_to_the_shared_rise(tmp_path):
    m=monitor(tmp_path, [20, 24.9, 25], rollout_reader=lambda tid:None)
    m.event({'type':'thread.started','thread_id':'dev'},session=True)
    m.check('a');m.check('b')
    assert last(tmp_path)['run_points_source']=='account_rise:session_file_missing' and last(tmp_path)['run_tokens'] is None
    with pytest.raises(ValueError, match='quota_stop'):m.check('c')


def test_the_shared_line_still_stops_every_run(tmp_path):
    m=monitor(tmp_path, [76, 77])
    m.check('e')
    with pytest.raises(ValueError, match='quota_stop'):m.check('f')
    assert last(tmp_path)['run_cap_reached'] is False


def test_an_old_reading_at_the_start_does_not_count_as_account_rise(tmp_path):
    # 11-11: the newest reading on disk was hours old (26 %); the first reading of the run itself said 34 %.
    m=staged(tmp_path, [(26, 3*3600), (34, 0), (36, 0)])
    assert m.check('start') is None and m.baseline['provisional'] is True
    m.check('first turn')
    assert m.baseline['used_percent']==34 and m.baseline['provisional'] is False and m.baseline['replaced_used_percent']==26
    m.check('later')
    assert last(tmp_path)['account_points']==2
    again=staged(tmp_path, [(38, 0)])
    again.check('after a restart')
    assert again.baseline['used_percent']==34 and last(tmp_path)['account_points']==4


def test_session_file_tokens_are_the_newest_total(tmp_path):
    day=tmp_path/'.codex'/'sessions'/'2026'/'11'/'11';day.mkdir(parents=True)
    row=lambda n:json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':n,'output_tokens':5,'total_tokens':n+5}}}})
    (day/'rollout-2026-11-11T01-18-03-abc.jsonl').write_text('\n'.join([row(10),json.dumps({'type':'event_msg','payload':{'type':'token_count','info':None}}),row(40),'{"type":"other","pad":"%s"}'%('x'*3000)])+'\n')
    assert rollout_tokens('abc', home=tmp_path)==45
    assert rollout_tokens('missing', home=tmp_path) is None
    import benchmarks.attention_harness.dev_agent_quota as q
    old=q.ROLLOUT_TAIL_BYTES;q.ROLLOUT_TAIL_BYTES=100  # the tail holds no count: the whole file is read
    try:assert rollout_tokens('abc', home=tmp_path)==45
    finally:q.ROLLOUT_TAIL_BYTES=old
