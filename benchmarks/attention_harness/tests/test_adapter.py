from __future__ import annotations

import numpy as np
import pytest

from benchmarks.attention_harness.robosuite_adapter import RobosuiteRobotBackend
from robosuite_sim.client import ClientStep, ReferenceResult


class FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.last_action = None
        self.success = False
        self.step_info = {"backend": "fake"}

    @staticmethod
    def _observation():
        return {
            "agentview_image": np.zeros((4, 4, 3), dtype=np.uint8),
            "agentview_depth": np.ones((4, 4, 1), dtype=np.float32),
            "robot0_joint_pos": np.arange(7, dtype=np.float64),
            "robot0_eef_pos": np.zeros(3),
        }

    def reset(self, **_request):
        return self._observation(), np.full(7, -1.0), np.full(7, 1.0), {
            "service": "fake_robosuite_sim", "task_id": "cube_lift", "seed": 101
        }

    def attach(self):
        return self.reset()

    def step(self, action):
        self.last_action = np.asarray(action)
        return ClientStep(self._observation(), 0.0, False, dict(self.step_info))

    def observe(self):
        return self._observation()

    def native_success(self):
        return self.success

    def metadata(self):
        return {"service": "fake_robosuite_sim"}

    def run_reference(self, _timeout_seconds):
        return ReferenceResult(self._observation(), [{"step": 0, "action": [0.0] * 7}])

    def close_environment(self):
        self.closed = True


def make_adapter() -> tuple[RobosuiteRobotBackend, FakeClient]:
    client = FakeClient()
    adapter = RobosuiteRobotBackend("cube_lift", client=client)
    return adapter, client


def test_reset_uses_service_contract() -> None:
    adapter, _ = make_adapter()
    observation = adapter.reset(101)
    assert set(observation) == {
        "agentview_image",
        "agentview_depth",
        "robot0_joint_pos",
        "robot0_eef_pos",
    }
    assert adapter.action_shape == (7,)
    assert adapter.metadata["service"] == "fake_robosuite_sim"


def test_action_validation_and_clipping() -> None:
    adapter, client = make_adapter()
    adapter.reset(101)
    adapter.step(np.full(7, 2.0))
    assert np.array_equal(client.last_action, np.ones(7))
    with pytest.raises(ValueError, match="shape"):
        adapter.step(np.zeros(6))
    with pytest.raises(ValueError, match="finite"):
        adapter.step(np.full(7, np.nan))


def test_depth_recovery_receipt_reaches_independent_trace() -> None:
    adapter, client = make_adapter()
    adapter.reset(101)
    receipt = {"first_frame_sha256": "a" * 64, "repeat_frame_sha256": "b" * 64,
               "simulation_time_unchanged": True, "qpos_unchanged": True}
    client.step_info = {"depth_recovery": receipt,
                        "depth_recovered_without_physics_step": True}
    adapter.step(np.zeros(7))
    assert adapter.trace[0]["depth_recovery"] == receipt


def test_reference_trace_native_evaluator_and_close_are_delegated() -> None:
    adapter, client = make_adapter()
    adapter.reset(101)
    adapter.run_reference_policy()
    assert len(adapter.trace) == 1
    client.success = True
    assert adapter.native_success() is True
    adapter.close()
    assert client.closed is True


def test_attach_and_refresh_do_not_reset_active_session() -> None:
    adapter, client = make_adapter()
    observation = adapter.attach()
    assert observation["robot0_eef_pos"].tolist() == [0.0, 0.0, 0.0]
    assert adapter.action_shape == (7,)
    assert adapter.refresh()["robot0_joint_pos"].shape == (7,)


def test_executed_http_failure_keeps_receipt_in_trace_and_safety():
    from robosuite_sim.client import ServiceError
    from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend, SafetyViolation
    adapter, client = make_adapter()
    adapter.reset(101)
    receipt = {'action': [0.0]*6+[-1.0], 'reward': 2.5, 'done': True, 'action_executed': True}
    error = ServiceError('depth failed', payload={'action_executed': True, 'receipt': receipt,
                         'stop': {'environment_closed': True}}, status=500)
    error.request_id = 'failed-request'
    def fail(action):
        raise error
    client.step = fail
    monitor = SafetyMonitorBackend(adapter)
    with pytest.raises(SafetyViolation):
        monitor.set_gripper(-1.0, settle_steps=1)
    assert adapter.trace[0]['action_receipt'] == receipt
    assert adapter.trace[0]['request_id'] == 'failed-request'
    assert monitor.violations[0]['kind'] == 'observation_unavailable_after_executed_action'
    assert monitor.violations[0]['action_receipt'] == receipt
