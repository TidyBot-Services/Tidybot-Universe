"""Offline coverage for arm_motion long (2026-11-12): one long planned move, a pause that ends when the arm is
still, an 8 mrad endpoint bound. A config without the key keeps every earlier value."""
import copy
from types import SimpleNamespace as NS

import numpy as np
import pytest

from openarm365_dev import executor
from openarm365_dev_v2.tests.test_fixture_phase1g import settings, r4_deployment, base, fake_mujoco_if_unavailable  # noqa: F401
from openarm365_dev_v2.tests.test_motion_tracking_diagnostics import fake_apply


def config(monkeypatch, settings, **kw):
    from openarm365_dev_v2 import dev_config as dc
    monkeypatch.setattr(dc, 'verify_lock', lambda *a, **k: None)
    return dc, dc.make_dev_config('open_cabinet', 101, {'path': 'fixture', 'sha256': 'fixture'}, execution_settings=settings, **kw)


def test_without_the_option_nothing_changes(monkeypatch, settings):
    from benchmarks.attention_harness.dev_tasks import dev_sdk_prompt, dev_sdk
    dc, plain = config(monkeypatch, settings)
    dc2, short = config(monkeypatch, settings, arm_motion='short')
    assert plain == short and 'arm_motion' not in plain
    assert '<=0.05m' in dev_sdk_prompt(plain) and '<=0.30m' not in dev_sdk_prompt(plain)
    assert 'the 4 mrad endpoint bound' in dev_sdk_prompt(plain) and 'arm_motion' not in dev_sdk(plain)


def test_long_is_recorded_sealed_and_described(monkeypatch, settings):
    from benchmarks.attention_harness.dev_tasks import dev_sdk_prompt, dev_sdk, dev_sdk_seal
    dc, plain = config(monkeypatch, settings)
    dc, c = config(monkeypatch, settings, arm_motion='long')
    assert c['arm_motion'] == 'long' and dc.validate_dev_config(c) is c
    assert all(c[key] == value for key, value in dev_sdk_seal(c).items())
    assert c['dev_sdk_prompt_sha256'] != plain['dev_sdk_prompt_sha256'] and c['dev_sdk_sha256'] != plain['dev_sdk_sha256']
    text = dev_sdk_prompt(c)
    assert '<=0.30m' in text and '<=0.05m' not in text and 'ONE call' in text
    assert 'the 12 mrad endpoint bound' in text and 'the 4 mrad endpoint bound' not in text and '10 mm/3 mrad uncommanded base drift' in text
    assert 'Optional speed=s (0.25..1.0' in text
    assert dev_sdk(c)['arm_motion'] == dict(mode='long', translation_limit_m=.30, endpoint_bound_rad=.012, settle='adaptive', speed_range=[.25, 1.0],
                                             base_drift_m=.010, held_object_m=.025, held_object_rad=.25)
    assert {k: v for k, v in c.items() if k not in ('arm_motion', 'dev_sdk_sha256', 'dev_sdk_prompt_sha256', 'dev_sdk_metadata_sha256')} == \
           {k: v for k, v in plain.items() if k not in ('dev_sdk_sha256', 'dev_sdk_prompt_sha256', 'dev_sdk_metadata_sha256')}


def test_a_changed_or_dropped_option_fails_validation(monkeypatch, settings):
    dc, c = config(monkeypatch, settings, arm_motion='long')
    dropped = copy.deepcopy(c); del dropped['arm_motion']
    with pytest.raises(ValueError): dc.validate_dev_config(dropped)
    renamed = copy.deepcopy(c); renamed['arm_motion'] = 'short'
    with pytest.raises(ValueError): dc.validate_dev_config(renamed)
    with pytest.raises(ValueError, match='arm_motion'): dc.make_dev_config('open_cabinet', 101, {'path': 'f', 'sha256': 'f'}, execution_settings=settings, arm_motion='huge')


def test_probe_uses_the_runs_limit():
    from openarm365_dev_v2.agent_probe import validate
    action = lambda dx: dict(action=dict(group='left_arm', operation='move_delta', args=[dx, 0, 0], kwargs={}))
    try: validate('plan_dry_run', action(.04))
    except ValueError as exc: pytest.skip(f'plan_dry_run payload shape differs here: {exc}')
    with pytest.raises(ValueError, match='translation_limit'): validate('plan_dry_run', action(.06))
    validate('plan_dry_run', action(.30), config=dict(arm_motion='long'))
    with pytest.raises(ValueError, match='translation_limit'): validate('plan_dry_run', action(.31), config=dict(arm_motion='long'))


def test_agent_executor_limit_follows_the_option():
    from openarm365_dev_v2.agent_executor import AgentExecutor
    from openarm365_dev_v2.agent_rejection import RecoverablePlanRejection
    e = AgentExecutor.__new__(AgentExecutor); e.failed = False; e.check_live = lambda: None
    e.planner = NS(client=NS(attest=lambda: None), ledger=NS(invalidate=lambda _: None))
    with pytest.raises(RecoverablePlanRejection, match='action_delta_limit'): e.move_delta('left', .051, 0, 0, request_id='r')
    e.arm_delta_limit = .30
    with pytest.raises(RecoverablePlanRejection, match='action_delta_limit'): e.move_delta('left', .301, 0, 0, request_id='r')
    with pytest.raises(RecoverablePlanRejection, match='action_delta_limit'): e.move_delta('left', 0, 0, 0, [.6, 0, 0], request_id='r')   # rotation cap unchanged


def test_native_limit_message_and_value():
    e = NS(check_live=lambda: None, arm_delta_limit=.30, bimanual_collaboration='none')
    executor.NativeExecutor.handoff_motion_gate = staticmethod(lambda self: None) if False else executor.NativeExecutor.handoff_motion_gate
    with pytest.raises(ValueError, match=r'limited to \.30m/\.5rad'):
        executor.NativeExecutor.move_delta(NS(check_live=lambda: None, arm_delta_limit=.30, handoff=None, bimanual_collaboration='none'), 'left', .31, 0, 0, request_id='r')
    with pytest.raises(ValueError, match=r'limited to \.05m/\.5rad'):
        executor.NativeExecutor.move_delta(NS(check_live=lambda: None, handoff=None, bimanual_collaboration='none'), 'left', .06, 0, 0, request_id='r')


def test_pause_ends_once_the_arm_is_still(monkeypatch):
    obj, calls, events, _ = fake_apply(monkeypatch, position=[.011, .02])
    obj.arm_motion = 'long'; obj.env.sim.data.qvel = np.array([.002, -.001])       # still from the first step
    receipt = executor.NativeExecutor.apply(obj, {'plan_id': 'p'}, request_id='req')
    assert calls[0]['step_budget'] == 22 and calls[0]['settle_steps'] == 20          # the planned budget is unchanged
    assert obj.sequence == 2 + executor.ADAPTIVE_MIN_STEPS                          # two drive steps, then the shortest pause
    assert receipt['status'] == 'completed' and receipt['sequence'] == obj.sequence


def test_a_moving_arm_gets_the_whole_pause(monkeypatch):
    obj, _, _, _ = fake_apply(monkeypatch, position=[.011, .02])
    obj.arm_motion = 'long'; obj.env.sim.data.qvel = np.array([.05, 0.])
    executor.NativeExecutor.apply(obj, {'plan_id': 'p'}, request_id='req')
    assert obj.sequence == 22


def test_short_keeps_the_fixed_pause_and_4_mrad(monkeypatch):
    obj, _, _, _ = fake_apply(monkeypatch, position=[.016, .02])                     # 6 mrad from the target
    obj.env.sim.data.qvel = np.array([0., 0.])
    with pytest.raises(RuntimeError, match='exceeds 4mrad endpoint bound'):
        executor.NativeExecutor.apply(obj, {'plan_id': 'p'}, request_id='req')
    assert obj.sequence == 22


def test_long_accepts_9_mrad_and_refuses_13(monkeypatch):
    obj, _, _, _ = fake_apply(monkeypatch, position=[.016, .02]); obj.arm_motion = 'long'
    obj.env.sim.data.qvel = np.array([0., 0.])
    assert executor.NativeExecutor.apply(obj, {'plan_id': 'p'}, request_id='req')['status'] == 'completed'
    assert obj.sequence == 22            # still but 6 mrad away: accepted, yet the pause runs its full length (no early exit)
    obj, _, _, _ = fake_apply(monkeypatch, position=[.019, .02]); obj.arm_motion = 'long'
    obj.env.sim.data.qvel = np.array([0., 0.])
    assert executor.NativeExecutor.apply(obj, {'plan_id': 'p'}, request_id='req')['status'] == 'completed'
    obj, _, events, _ = fake_apply(monkeypatch, position=[.023, .02]); obj.arm_motion = 'long'
    obj.env.sim.data.qvel = np.array([0., 0.])
    with pytest.raises(RuntimeError, match='exceeds 12mrad endpoint bound'):
        executor.NativeExecutor.apply(obj, {'plan_id': 'p'}, request_id='req')
    assert obj.sequence == 22                                                        # out of bound: the pause was not cut short
    assert any(row['event'] == 'tracking_error' and row['bound'] == .012 for row in events)


def test_launch_selection_accepts_only_the_two_values():
    from benchmarks.attention_harness import dev_experiment as de
    assert 'arm_motion' in de.ALLOWED_SELECTION_FIELDS


def test_probe_owner_passes_the_runs_limit(monkeypatch):
    # 11-12 real run: the owner validated dry runs without arm_motion and refused -0.20 m as translation_limit.
    from openarm365_dev_v2 import agent_probe
    seen = []
    monkeypatch.setattr(agent_probe, 'validate', lambda op, payload, *, config=None: seen.append(config) or (_ for _ in ()).throw(RuntimeError('stop')))
    for motion, expected in (('long', 'long'), ('short', None)):
        e = NS(execution_settings=NS(profile='r5-fixture-20261031'), arm_motion=motion)
        with pytest.raises(RuntimeError, match='stop'): agent_probe.probe(None, e, None, None, dict(op='plan_dry_run', payload={}))
        assert seen[-1].get('arm_motion') == expected


def test_tools_md_states_the_same_limit():
    from benchmarks.attention_harness.dev_agent_workspace import TOOLS, TOOLS_V2
    for text in (TOOLS, TOOLS_V2):
        assert 'Safety: arm coordinates <= 0.05 m / 0.5 rad per action,' in text   # the line the long run replaces


def test_long_executor_uses_the_looser_physical_bounds(settings):
    from curobo_v2.attachment import effective_held_envelope
    from openarm365_dev.base_motion import base_drift_limits
    import inspect
    assert 'execution_settings=long_motion_settings(execution_settings)' in inspect.getsource(executor.NativeExecutor.__init__)
    long_ = executor.long_motion_settings(settings)
    assert base_drift_limits(settings) == (.003, .003) and effective_held_envelope(settings) == (.010, .10)
    assert base_drift_limits(long_) == (.010, .003) and effective_held_envelope(long_) == (.025, .25)
    assert type(long_) is type(settings) and long_.profile == settings.profile
    assert settings.base_hold_drift_m == .003 and settings.held_envelope_m == .010          # the shared profile object is untouched


def test_speed_slows_the_same_path(monkeypatch):
    obj, calls, events, _ = fake_apply(monkeypatch, position=[.011, .02], travel_seconds=.04)
    obj.arm_motion = 'long'; obj.env.sim.data.qvel = np.array([.05, 0.])
    executor.NativeExecutor.apply(obj, {'plan_id': 'p'}, request_id='req', speed=.5)
    assert calls[0]['dt'] == pytest.approx(.005) and calls[0]['step_budget'] == 8 + 20     # 0.04 s at half speed: 8 steps
    intent = next(row for row in events if row['event'] == 'intent')
    assert intent['command_curve']['time_scale'] == .5


@pytest.mark.parametrize('motion,speed,ok', [('long', .25, True), ('long', 1, True), ('long', .2, False), ('long', 1.5, False), ('short', .5, False), ('short', 1., True)])
def test_speed_is_checked(motion, speed, ok):
    e = NS(arm_motion=motion)
    if ok: assert executor.NativeExecutor.checked_speed(e, speed) == float(speed)
    else:
        with pytest.raises(ValueError, match='speed'): executor.NativeExecutor.checked_speed(e, speed)
