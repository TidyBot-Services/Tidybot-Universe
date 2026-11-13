"""Try speed-up item 5: parallel continuous-certificate merge equals the serial counters (no PhysX)."""
import pytest

from curobo_v2 import sweep


def part(checks, leaves, depth, bound, step, closure=(0., 0.), failure=None, gaps=None):
    return dict(checks=checks, leaves=leaves, depth_max=depth, bound_max=bound, step_max=step,
                closure_max=list(closure), failure=failure, gaps=gaps or {})


def test_merge_sums_counts_and_takes_maxima_and_minimum_gaps():
    total = sweep.merge_stats([part(10, 7, 3, .01, 1e-6, (.001, .002), gaps={'world:a': -.0002}),
                               part(5, 4, 5, .005, 2e-6, (.003, .001), gaps={'world:a': -.0001, 'world:b': .0})])
    assert total == part(15, 11, 5, .01, 2e-6, (.003, .002), gaps={'world:a': -.0002, 'world:b': .0})


def test_merge_stops_at_the_first_failure_in_serial_order():
    failure = dict(reason='continuous_geometry_or_closure_uncertified', interval=[.1, .2])
    total = sweep.merge_stats([part(3, 3, 1, .1, 0.), part(4, 2, 2, .2, 0., failure=failure), part(99, 99, 9, 9., 9.)])
    assert total['checks'] == 7 and total['leaves'] == 5 and total['depth_max'] == 2 and total['failure'] == failure


class FakePool:
    def __init__(self, parts): self.parts = parts; self.calls = []
    def submit(self, fn, spec, response, lo, hi):
        self.calls.append((lo, hi))
        class Done:
            def result(inner, timeout=None): return self.parts[lo:hi]
        return Done()
    def shutdown(self, **kw): pass


def test_parallel_stats_merges_chunks_in_order(monkeypatch):
    parts = [part(i + 1, 1, i, i / 10, 0.) for i in range(10)]
    pool = FakePool(parts); monkeypatch.setattr(sweep, '_get_pool', lambda: pool)
    total = sweep.parallel_stats({'execution_settings': None}, {}, 10, workers=2)
    assert total == sweep.merge_stats(parts) and [lo for lo, _ in pool.calls] == sorted(lo for lo, _ in pool.calls)


@pytest.mark.parametrize('workers,count', [(1, 10), (4, 1)])
def test_single_worker_or_single_interval_stays_serial(workers, count):
    assert sweep.parallel_stats({}, {}, count, workers=workers) is None


def test_budget_or_worker_error_falls_back_to_the_serial_search(monkeypatch):
    big = [part(sweep.MAX_QUERIES, 1, 1, 0., 0.), part(1, 1, 1, 0., 0.)]
    monkeypatch.setattr(sweep, '_get_pool', lambda: FakePool(big))
    assert sweep.parallel_stats({'execution_settings': None}, {}, 2, workers=2) is None
    def broken(): raise OSError('no forkserver')
    monkeypatch.setattr(sweep, '_get_pool', broken)
    assert sweep.parallel_stats({'execution_settings': None}, {}, 2, workers=2) is None


def test_unpicklable_job_falls_back_before_any_worker(monkeypatch):
    pool = FakePool([]); monkeypatch.setattr(sweep, '_get_pool', lambda: pool)
    assert sweep.parallel_stats({'execution_settings': None, 'x': lambda: 0}, {}, 2, workers=2) is None and not pool.calls


def test_settings_reach_workers_as_a_presence_marker(monkeypatch):
    seen = []
    class Settings:
        def to_dict(self): return {'profile': 'r5', 'planner_deployment': {'lock_path': '/x', 'sha256': 'y'}}
    class Pool(FakePool):
        def submit(self, fn, spec, response, lo, hi):
            seen.append(spec['execution_settings']); return super().submit(fn, spec, response, lo, hi)
    monkeypatch.setattr(sweep, '_get_pool', lambda: Pool([part(1, 1, 0, 0., 0.)] * 2))
    sweep.parallel_stats({'execution_settings': Settings()}, {}, 2, workers=2)
    assert seen and all(isinstance(s, sweep.SettingsPresent) and s.values['profile'] == 'r5' for s in seen)
