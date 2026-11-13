"""Planner 503 regression: measured lock check compares in float32 (no CUDA)."""
import sys
import types

import numpy as np


def _module(monkeypatch):
    import curobo_v2.identity as identity
    monkeypatch.setattr(identity, '_service_context', ({'assets_root': '/tmp'}, ''), raising=False)
    monkeypatch.setitem(sys.modules, 'torch', types.ModuleType('torch'))
    from curobo_v2.service.curobo_service import planner_core
    return planner_core


def _locked_value():
    # A value whose float32 rounding error is above the 1e-7 tolerance.
    for v in np.linspace(-2.5035, -2.5025, 20001):
        if abs(float(np.float32(v)) - float(v)) > 1e-7:
            return float(v)
    raise AssertionError('no value found')


def test_float32_stored_lock_passes(monkeypatch):
    core = _module(monkeypatch)
    v = _locked_value()
    expected = {'openarm_left_joint2': v, 'openarm_right_finger': 0.02}
    stored = np.asarray([v, 0.02], dtype=np.float32)
    names = ['openarm_left_joint2', 'openarm_right_finger']
    # Old float64 comparison falsely failed on exactly this value.
    assert not np.allclose(stored.astype(np.float64), [v, 0.02], rtol=0., atol=1e-7)
    assert core.lock_propagated(names, stored, expected)


def test_real_mismatch_still_rejected(monkeypatch):
    core = _module(monkeypatch)
    names = ['openarm_left_joint2', 'openarm_right_finger']
    expected = {'openarm_left_joint2': -2.503, 'openarm_right_finger': 0.02}
    # One float32 ulp at 2.5 is 2.4e-7, above the unchanged 1e-7 bound.
    off = np.asarray([np.nextafter(np.float32(-2.503), np.float32(0)), 0.02], dtype=np.float32)
    assert not core.lock_propagated(names, off, expected)
    assert not core.lock_propagated(names, np.asarray([-2.4, 0.02], np.float32), expected)
    assert not core.lock_propagated(names[:1], np.asarray([-2.503], np.float32), expected)
    assert not core.lock_propagated(['openarm_left_joint2', 'x'], np.asarray([-2.503, 0.02], np.float32), expected)
