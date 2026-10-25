"""C8 quota-window regressions using local synthetic session receipts."""
import json
import os
from datetime import datetime, timezone

import pytest

from benchmarks.attention_harness import memory_validation_worker as worker
from benchmarks.attention_harness.codex_quota import read_quota
from benchmarks.attention_harness.tests.test_memory_validation_worker import deps


OLD_RESET=1792943895
NEW_RESET=1793548695


def session(root,name,used,reset,now,*,age=0,weekly='secondary'):
    path=root/'.codex/sessions'/f'rollout-{name}.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(dict(timestamp=datetime.fromtimestamp(now-age,timezone.utc).isoformat(),
        payload={'rate_limits':{weekly:dict(window_minutes=10080,used_percent=used,resets_at=reset)}}))+'\n')
    os.utime(path,(now,now))
    return path


@pytest.mark.parametrize('weekly',['primary','secondary'])
def test_current_window_maximum_and_ui_reader(tmp_path,weekly):
    now=1793000000
    session(tmp_path,'old',47,OLD_RESET,now,weekly=weekly)
    session(tmp_path,'current',9,NEW_RESET,now,age=20,weekly=weekly)
    session(tmp_path,'lower',8,NEW_RESET,now,age=10,weekly=weekly)
    snapshot=worker.weekly_quota_snapshot(tmp_path,now=now)
    assert snapshot==dict(used_percent=9,observed_at=now-10,resets_at=NEW_RESET)
    assert worker.weekly_quota(tmp_path,now=now)==9
    assert read_quota(tmp_path,now=now)['used_percent']==9
    assert read_quota(tmp_path,now=now)['resets_at']==NEW_RESET


def test_stale_or_unknown_time_cannot_select_a_window(tmp_path):
    now=1793000000
    session(tmp_path,'current',9,NEW_RESET,now)
    session(tmp_path,'stale-future-window',90,NEW_RESET+604800,now,age=601)
    session(tmp_path,'stale-current',47,NEW_RESET,now,age=601)
    path=session(tmp_path,'unknown-time',99,NEW_RESET+2*604800,now)
    row=json.loads(path.read_text());row['timestamp']='bad';path.write_text(json.dumps(row))
    assert worker.weekly_quota_snapshot(tmp_path,now=now)==dict(used_percent=9,observed_at=now,resets_at=NEW_RESET)
    assert read_quota(tmp_path,now=now)['used_percent']==9
    assert read_quota(tmp_path,now=now)['resets_at']==NEW_RESET


def test_unknown_window_does_not_contaminate_identified_window(tmp_path):
    now=1793000000
    session(tmp_path,'unknown',99,None,now)
    session(tmp_path,'current',9,NEW_RESET,now)
    assert worker.weekly_quota_snapshot(tmp_path,now=now)['used_percent']==9


def test_stale_known_window_still_requires_refresh(tmp_path):
    now=1793000000
    session(tmp_path,'old',47,OLD_RESET,now,age=601)
    session(tmp_path,'current',9,NEW_RESET,now,age=602)
    value=worker.weekly_quota_snapshot(tmp_path,now=now)
    assert value==dict(used_percent=9,observed_at=now-602,resets_at=NEW_RESET)
    assert not worker.quota_fresh(value['observed_at'],now)


def test_midday_reset_persists_new_baseline_and_keeps_limits(tmp_path):
    d=deps(tmp_path);now=d.clock();worker.set_enabled(tmp_path,True)
    d.quota=lambda:worker.weekly_quota_snapshot(tmp_path,now=d.clock())
    session(tmp_path,'old',47,OLD_RESET,now)
    state={};worker.tick(state,d)
    assert state['day_start_quota']==47
    d.clock=lambda:now+1
    session(tmp_path,'new',9,NEW_RESET,d.clock())
    worker.tick(state,d)
    assert state['day_start_quota']==9 and state['day_start_quota_resets_at']==NEW_RESET
    assert state['weekly_quota']==9 and state['runs_today']==1
    # Restart in the same day/window must not reset the baseline again.
    state=json.loads((tmp_path/'state.json').read_text())
    session(tmp_path,'new',14,NEW_RESET,d.clock())
    worker.tick(state,d)
    assert state['day_start_quota']==9 and d.runner.pauses==0
    session(tmp_path,'new',15,NEW_RESET,d.clock())
    worker.tick(state,d)
    assert '5 points' in state['status'] and d.runner.pauses==1
    session(tmp_path,'new',70,NEW_RESET,d.clock())
    worker.tick(state,d)
    assert '70%' in state['status'] and state['day_start_quota']==9


def test_existing_unidentified_baseline_replaced_by_current_window(tmp_path):
    d=deps(tmp_path);worker.set_enabled(tmp_path,True);state={};worker.tick(state,d)
    state['day_start_quota']=47
    session(tmp_path,'new',9,NEW_RESET,d.clock())
    d.quota=lambda:worker.weekly_quota_snapshot(tmp_path,now=d.clock())
    worker.tick(state,d)
    assert state['day_start_quota']==9 and state['day_start_quota_resets_at']==NEW_RESET
