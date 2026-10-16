"""The reset-region order must not depend on object addresses (see environment.canonical_regions)."""
import itertools

import pytest

from openarm365_dev_v2.environment import canonical_regions

LEFT = {'offset': [-0.784, -0.0, 0.46], 'size': [0.821, 0.65], 'height': 0.0}
RIGHT = {'offset': [0.831, -0.0, 0.46], 'size': [0.725, 0.65], 'height': 0.0}


def test_order_is_fixed_by_geometry_whichever_region_comes_first():
    results = []
    for first, second in itertools.permutations([LEFT, RIGHT]):
        out = canonical_regions({'geom_0': first, 'geom_1': second})
        results.append(out)
        assert list(out) == ['geom_0', 'geom_1']
    assert results[0] == results[1]
    assert results[0]['geom_0'] == LEFT and results[0]['geom_1'] == RIGHT


def test_numpy_values_and_tiny_float_noise_do_not_change_the_order():
    np = pytest.importorskip('numpy')
    a = {'offset': np.array([0.831, 0.0, 0.46]), 'size': np.array([0.725, 0.65])}
    b = {'offset': np.array([-0.784, 0.0, 0.46]), 'size': np.array([0.821, 0.65])}
    noisy = {'offset': np.array([-0.784 + 1e-9, 0.0, 0.46]), 'size': np.array([0.821, 0.65])}
    assert list(canonical_regions({'geom_0': a, 'geom_1': b}).values())[0] is b
    assert list(canonical_regions({'geom_0': noisy, 'geom_1': a}).values())[0] is noisy


def test_single_region_and_non_dict_pass_through():
    assert canonical_regions({'geom_0': LEFT}) == {'geom_0': LEFT}
    assert canonical_regions(None) is None
    assert canonical_regions({}) == {}


def test_three_regions_get_a_stable_total_order():
    mid = {'offset': [0.0, 0.0, 0.46], 'size': [0.5, 0.65]}
    outs = {tuple(map(id, canonical_regions(dict(enumerate(p))).values()))
            for p in itertools.permutations([LEFT, mid, RIGHT])}
    assert len(outs) == 1


def test_counter_patch_is_scoped_and_orders_the_returned_regions(monkeypatch):
    import sys
    import types
    from openarm365_dev_v2.environment import deterministic_counter_regions

    class Counter:
        order = ['geom_0', 'geom_1']

        def get_reset_regions(self, *args, **kwargs):
            regions = {'geom_0': RIGHT, 'geom_1': LEFT}
            return {name: regions[name] for name in self.order}

    for name in ('robocasa', 'robocasa.models', 'robocasa.models.fixtures', 'robocasa.models.fixtures.counter'):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules['robocasa.models.fixtures.counter'].Counter = Counter
    original = Counter.get_reset_regions
    with deterministic_counter_regions():
        results = []
        for order in (['geom_0', 'geom_1'], ['geom_1', 'geom_0']):
            Counter.order = order
            results.append(Counter().get_reset_regions(ref='sink', loc='left_right'))
        assert results[0] == results[1] and results[0]['geom_0'] == LEFT
    assert Counter.get_reset_regions is original  # restored on exit


def test_the_context_is_a_no_op_without_the_simulator():
    from openarm365_dev_v2.environment import deterministic_counter_regions
    with deterministic_counter_regions():
        pass
