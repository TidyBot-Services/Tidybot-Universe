import time

import pytest

from benchmarks.attention_harness.dev_agent_quota import QuotaMonitor


class Ledger:
    def __init__(self):self.samples=[]
    def quota(self, sample):self.samples.append(sample)


def monitor(tmp_path, readings, **kw):
    now=time.time();it=iter(readings)
    reader=lambda:dict(used_percent=next(it),window_minutes=10080,resets_at=now+86400,observed_at=time.time())
    return QuotaMonitor(tmp_path, Ledger(), stop_percent=77, reader=reader, **kw)


def test_a_run_stops_after_five_points_of_its_own():
    m=monitor(tmp_path:=__import__('pathlib').Path(__import__('tempfile').mkdtemp()), [20, 22, 24.9, 25])
    for _ in range(3):m.check('step')
    with pytest.raises(ValueError, match='quota_stop'):m.check('step')
    last=[l for l in (tmp_path/'quota.jsonl').read_text().splitlines()][-1]
    assert '"run_cap_reached": true' in last and '"run_points": 5' in last


def test_the_count_survives_a_restart_and_the_shared_line_still_applies(tmp_path):
    m=monitor(tmp_path, [40, 43])
    m.check('a');m.check('b')
    again=monitor(tmp_path, [44.5, 45])
    again.check('c')
    with pytest.raises(ValueError):again.check('d')
    shared=monitor(tmp_path/'x', [76, 77]) if (tmp_path/'x').mkdir() is None else None
    shared.check('e')
    with pytest.raises(ValueError):shared.check('f')


def staged(tmp_path, rows, **kw):
    """rows: (used_percent, seconds the reading is older than now)."""
    now=time.time();it=iter(rows)
    def reader():
        used,age=next(it)
        return dict(used_percent=used,window_minutes=10080,resets_at=now+86400,observed_at=time.time()-age)
    return QuotaMonitor(tmp_path, Ledger(), stop_percent=77, reader=reader, **kw)


def test_an_old_reading_at_the_start_does_not_count_against_the_run(tmp_path):
    # 11-11: the newest reading on disk was hours old (26 %); the first reading of the run itself said 34 %.
    m=staged(tmp_path, [(26, 3*3600), (34, 0), (36, 0), (39, 0)])
    assert m.check('start') is None and m.baseline['provisional'] is True
    m.check('first turn')
    assert m.baseline['used_percent']==34 and m.baseline['provisional'] is False and m.baseline['replaced_used_percent']==26
    m.check('later')
    with pytest.raises(ValueError, match='quota_stop'):m.check('five points of its own')


def test_the_start_is_moved_only_once_and_survives_a_restart(tmp_path):
    m=staged(tmp_path, [(26, 3600), (34, 0), (36, 0)])
    m.check('a');m.check('b');m.check('c')
    assert m.baseline['used_percent']==34
    again=staged(tmp_path, [(38, 0), (39, 0)])
    again.check('d')
    assert again.baseline['used_percent']==34
    with pytest.raises(ValueError, match='quota_stop'):again.check('e')


def test_the_shared_line_still_stops_a_run_that_opened_on_an_old_reading(tmp_path):
    m=staged(tmp_path, [(26, 3600), (77, 0)])
    m.check('a')
    with pytest.raises(ValueError, match='quota_stop'):m.check('b')
