"""CPU-only cost and fail-closed checks for ordinary R4 actions."""
import io
import json
import time
from types import ModuleType, SimpleNamespace as NS
import sys

import numpy as np
import pytest

from curobo_v2 import execution_settings as es
from curobo_v2.proxy_departure import support_departure
from openarm365_dev import executor as ex
from benchmarks.attention_harness.tests.test_ik_path import fake_core
from benchmarks.attention_harness.tests.c21_fixtures import base, r4_deployment


def test_singleton_model_options_match_r3_and_batch_is_explicit(monkeypatch):
    cls, core, q, targets, _ = fake_core(monkeypatch, .1)
    core.settings = {}; core._attachment_key = None; core.attachment = None
    core._config = {'kinematics': {}}; core._models = {}; core._device = 'cpu'
    core.use_cuda_graph = True; core.interpolation_dt = .025
    core.check_selection = lambda *a: (['joint'], ('tool',), {})
    core._scene = lambda: {}
    from curobo_v2.service.curobo_service import attachment_config
    monkeypatch.setattr(attachment_config, 'configure_carried_body', lambda *a: {'kinematics': {}})
    calls = []
    class Captured(Exception): pass
    def create(**kw):
        calls.append(kw)
        raise Captured
    motion = ModuleType('curobo.motion_planner')
    motion.MotionPlanner = object; motion.MotionPlannerCfg = NS(create=create)
    collision = ModuleType('curobo.collision_checking')
    collision.RobotCollisionChecker = object; collision.RobotCollisionCheckerCfg = object
    types = ModuleType('curobo.types'); types.DeviceCfg = lambda **kw: kw
    for module in (motion, collision, types): monkeypatch.setitem(sys.modules, module.__name__, module)
    for settings, batch in [(es.R3_20261025, False), (es.R4_20261027, False), (es.R4_20261027, True)]:
        core.execution_settings = settings
        with pytest.raises(Captured): cls._select(core, q, heading_batch=batch)
    assert calls[0] == calls[1]
    assert 'max_batch_size' not in calls[1]
    assert calls[2] == dict(calls[1], max_batch_size=8)


class Journal(io.StringIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def fileno(self): return 0
    def open(self, *a): return self


def logging_executor(settings):
    obj = NS(execution_settings=settings, base_hold=np.zeros(3), journal=Journal())
    obj.record_drift_sample = lambda: None
    obj.drift_receipt = lambda *a: {}
    obj.log = ex.NativeExecutor.log.__get__(obj)
    return obj


def test_750_physics_rows_authenticate_at_action_boundaries(monkeypatch, r4_deployment, record_property):
    calls = []
    real = ex.receipt_fields
    def authenticate(settings):
        calls.append(time.perf_counter())
        return real(settings)
    monkeypatch.setattr(ex, 'receipt_fields', authenticate)
    monkeypatch.setattr(ex.os, 'fsync', lambda _: None)
    obj = logging_executor(r4_deployment)
    @ex.record_base_drift
    def move(self, *, request_id):
        for i in range(750): self.log(self.journal, dict(physics_step=i))
        return {}
    start = time.perf_counter()
    move(obj, request_id='fake')
    elapsed = time.perf_counter() - start
    record_property("750_rows_seconds", elapsed)
    record_property("identity_verifications", len(calls))
    assert len(calls) == 2  # Independent of physics sample count; used to be 751.
    rows = [json.loads(line) for line in obj.journal.getvalue().splitlines()]
    assert len(rows) == 751
    assert all(row['execution_profile'] == 'r4-20261027' and row['formal_eligible'] is False for row in rows)
    assert elapsed < 2.


@pytest.mark.parametrize('when', ['before', 'during'])
def test_action_snapshot_mutation_never_returns_success(monkeypatch, base, r4_deployment, when):
    from curobo_v2.identity import IdentityError
    monkeypatch.setattr(ex.os, 'fsync', lambda _: None)
    obj = logging_executor(r4_deployment)
    def corrupt(): base.registry.write_text('{}')
    @ex.record_base_drift
    def move(self, *, request_id):
        self.log(self.journal, dict(physics_step=1))
        corrupt()
        return {'status': 'completed'}
    if when == 'before': corrupt()
    with pytest.raises(IdentityError): move(obj, request_id='fake')
    assert obj.failed
    assert not hasattr(obj, '_drift_max')
    if when == 'before': assert not obj.journal.getvalue()


def test_dense_carried_diagnostic_cost_and_late_floor_breach(record_property):
    # 4 supports x 2000 dense samples x 128 payload spheres: no sampling skipped.
    values = np.zeros((2000, 128, 4))
    values[..., 2] = np.linspace(.019, .2, len(values))[:, None]
    values[..., 3] = .02
    world = [dict(name=str(i), center=[0, 0, -.1], half_size=[1, 1, .1]) for i in range(4)]
    start = time.perf_counter()
    result = support_departure(values, world, {str(i) for i in range(4)})
    elapsed = time.perf_counter() - start
    record_property('dense_certificate_seconds', elapsed)
    record_property('sphere_support_samples', 4 * 2000 * 128)
    assert result['collision_free'] and len(result['supports']) == 4
    assert elapsed < 3.
    values[-1, -1, 2] -= .001
    assert support_departure(values, world, {'0'})['collision_free']
    values[-1, -1, 2] = .018
    rejected = support_departure(values, world, {'0'})
    assert not rejected['collision_free'] and rejected['sample'] == 1999


def test_one_snapshot_binds_lock_and_all_new_configs(monkeypatch, tmp_path, r4_deployment):
    from curobo_v2 import dev_local
    from openarm365_dev_v2 import dev_config as d
    settings = d.new_execution_settings()
    def forbidden(*a, **kw): raise AssertionError('selected snapshot must not be rebuilt')
    monkeypatch.setattr(dev_local, 'materialize', forbidden)
    source = dict(kind='dev-local', source_root=str(d.ROOT))
    ref = d.make_lock(tmp_path/'identity.json', execution_settings=settings, planner_source=source)
    for seed in (101, 102):
        config = d.make_dev_config('open_cabinet', seed, ref,
            planner_source=source, execution_settings=settings)
        assert config['execution_settings'] == settings.to_dict()
        assert d.validate_dev_config(config) is config


def test_formal_and_legacy_never_receive_r4_fields(monkeypatch):
    from openarm365_dev_v2 import dev_config as d, formal_config
    monkeypatch.setattr(d, 'verify_lock', lambda *a, **kw: None)
    ref = dict(path='fixture', sha256='fixture')
    formal = formal_config.make_formal_config('open_cabinet', 102, ref,
        attempt_wall_seconds=480, whole_case_wall_seconds=600, sdk_call_limit=80)
    legacy = d.make_dev_config('open_cabinet', 102, ref, budget_tier='legacy')
    for config in (formal, legacy):
        assert not {'execution_settings', 'planner_source', 'carried_proxy_support_departure',
                    'grasp_heading_search'} & config.keys()
    assert formal_config.validate_formal_config(formal) is formal
    assert d.validate_dev_config(legacy) is legacy
