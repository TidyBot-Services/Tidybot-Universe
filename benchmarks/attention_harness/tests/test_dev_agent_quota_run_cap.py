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
