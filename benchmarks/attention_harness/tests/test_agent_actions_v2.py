from __future__ import annotations

import numpy as np
import pytest

from benchmarks.attention_harness.robocasa_native.agent_actions import (
    AgentServerActionBackend,
    AgentServerActionError,
)
from benchmarks.attention_harness.robocasa_native.safety_monitor import (
    SafetyMonitorBackend,
    SafetyViolation,
)
from benchmarks.attention_harness.core.control import EmergencyInterrupt
from tidybot_sdk.perception import ModeBoundPerceptionBackend
from benchmarks.attention_harness.robocasa_native.mobile_sdk import RobocasaMobileSDK


class AgentTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, payload, timeout):
        path = "/" + url.split("/", 3)[-1]
        self.calls.append((method, path, payload))
        if path == "/state":
            pose = np.eye(4).flatten(order="F").tolist()
            pose[12:15] = [0.4, 0.2, 0.3]
            return {"base": {"pose": [1.0, 2.0, 0.0]},
                    "arm": {"q": [0.0] * 7, "ee_pose": pose},
                    "gripper": {"position_mm": 70.0}}
        if path == "/code/capabilities":
            return {"job_cancellation": True, "cancel_auth": "per_job_token"}
        if path == "/code/submit":
            return {"job_id": "job-1", "cancel_token": "owner-token"}
        if path == "/code/jobs/job-1":
            return {"status": "completed", "result": {"exit_code": 0}}
        raise AssertionError(path)


def test_action_backend_uses_agent_server_without_reset_or_evaluator():
    transport = AgentTransport()
    backend = AgentServerActionBackend(transport=transport, simulator_attested=True)
    observation = backend.observe()
    assert observation["robot0_eef_pos"].tolist() == [0.4, 0.2, 0.3]
    assert observation["robot0_gripper_width"].tolist() == [0.07]
    assert observation["robot0_base_pose"].tolist() == [1.0, 2.0, 0.0]
    backend.move_arm_to_position(0.1, 0.2, 0.3, tolerance=0.01, max_steps=50)
    backend.set_gripper(1.0, settle_steps=10)
    backend.move_base_delta(0.1, -0.1, 0.0, frame="local")
    submissions = [row[2] for row in transport.calls if row[1] == "/code/submit"]
    assert len(submissions) == 3
    assert all(row["reset_env"] is False for row in submissions)
    assert all("/task/success" not in row["code"] for row in submissions)
    assert "gripper.close(speed=64)" in submissions[1]["code"]
    assert "base.move_delta" in submissions[2]["code"]


def test_action_backend_rejects_nonfinite_or_failed_jobs():
    transport = AgentTransport()
    backend = AgentServerActionBackend(transport=transport, simulator_attested=True)
    with pytest.raises(ValueError, match="finite"):
        backend.move_arm_delta(float("nan"), 0, 0, (0, 0, 0))
    assert not any(path == "/code/submit" for _, path, _ in transport.calls)

    def failed(method, url, payload, timeout):
        if url.endswith("/code/capabilities"):
            return {"job_cancellation": True, "cancel_auth": "per_job_token"}
        if url.endswith("/code/submit"):
            return {"job_id": "job-1", "cancel_token": "owner-token"}
        return {"status": "failed", "result": {"exit_code": 1, "error": "arm stopped"}}

    backend = AgentServerActionBackend(transport=failed, simulator_attested=True)
    with pytest.raises(AgentServerActionError, match="arm stopped"):
        backend.set_gripper(-1.0, settle_steps=1)


def test_arm_nonconvergence_keeps_unknown_outcome_and_projects_bounded_cause():
    calls = []
    healthy = AgentTransport()

    def failed_arm(method, url, payload, timeout):
        calls.append(url)
        if url.endswith("/code/jobs/job-1"):
            return {
                "status": "failed", "error": "Process exited with code 1",
                "result": {
                    "exit_code": 1,
                    "stderr": (
                        "Traceback (most recent call last):\n"
                        "robot_sdk.arm.ArmError: Timeout: arm did not converge "
                        "(error=0.3588 m) — likely hitting an obstacle\n"
                        "UNTRUSTED EXTRA STDERR MUST NOT ENTER TRACE\n"
                    ),
                },
            }
        return healthy(method, url, payload, timeout)

    monitor = SafetyMonitorBackend(AgentServerActionBackend(
        simulator_attested=True, transport=failed_arm,
    ))
    with pytest.raises(SafetyViolation, match="action_outcome_unknown"):
        monitor.move_arm_to_position(0.8, 0.5, 0.6, tolerance=0.004, max_steps=100)
    violation = monitor.violations[0]
    assert violation["kind"] == "action_outcome_unknown"
    assert "sdk_arm_nonconvergence_error_m=0.3588" in violation["error"]
    assert "partial motion possible" in violation["error"]
    assert "UNTRUSTED EXTRA STDERR" not in violation["error"]
    assert not any(url.endswith("/cancel") for url in calls)


def test_action_backend_requires_simulator_attestation():
    with pytest.raises(ValueError, match="simulator-only"):
        AgentServerActionBackend(simulator_attested=False, transport=AgentTransport())


def test_old_agent_server_is_rejected_before_action_submission():
    calls = []

    def old_service(method, url, payload, timeout):
        calls.append(url)
        return {"job_cancellation": False}

    backend = AgentServerActionBackend(simulator_attested=True, transport=old_service)
    with pytest.raises(AgentServerActionError, match="lacks per-job cancellation"):
        backend.set_gripper(1.0, settle_steps=1)
    assert not any(url.endswith("/code/submit") for url in calls)


def test_timed_out_action_is_cancelled_and_confirmed():
    calls = []

    def slow_service(method, url, payload, timeout):
        calls.append((method, url, payload))
        if url.endswith("/code/capabilities"):
            return {"job_cancellation": True, "cancel_auth": "per_job_token"}
        if url.endswith("/code/submit"):
            return {"job_id": "job-1", "cancel_token": "owner-token"}
        if url.endswith("/code/jobs/job-1/cancel"):
            assert payload == {"cancel_token": "owner-token"}
            return {"status": "running", "cancelled": False}
        if url.endswith("/code/jobs/job-1"):
            if any(path.endswith("/cancel") for _, path, _ in calls):
                return {"status": "cancelled"}
            return {"status": "running"}
        raise AssertionError(url)

    backend = AgentServerActionBackend(
        simulator_attested=True, transport=slow_service,
        timeout_seconds=0.02, poll_seconds=0.005,
    )
    with pytest.raises(TimeoutError, match="deadline"):
        backend.set_gripper(1.0, settle_steps=1)
    assert any(url.endswith("/code/jobs/job-1/cancel") for _, url, _ in calls)
    assert backend.cancellation_receipts == [{
        "job_id": "job-1", "job_status": "cancelled",
        "execution_status": "unknown",
    }]


def test_operator_interrupt_cancels_active_agent_server_job():
    calls = []
    stop = {"requested": False}

    def service(method, url, payload, timeout):
        calls.append((method, url, payload))
        if url.endswith("/code/capabilities"):
            return {"job_cancellation": True, "cancel_auth": "per_job_token"}
        if url.endswith("/code/submit"):
            return {"job_id": "job-1", "cancel_token": "owner-token"}
        if url.endswith("/code/jobs/job-1/cancel"):
            assert payload == {"cancel_token": "owner-token"}
            return {"status": "running"}
        if url.endswith("/code/jobs/job-1"):
            if any(path.endswith("/cancel") for _, path, _ in calls):
                return {"status": "cancelled"}
            stop["requested"] = True
            return {"status": "running"}
        raise AssertionError(url)

    backend = AgentServerActionBackend(simulator_attested=True, transport=service,
                                       poll_seconds=0.001)
    backend.set_interrupt_check(lambda: stop["requested"])
    with pytest.raises(EmergencyInterrupt, match="operator interrupted"):
        backend.set_gripper(1.0, settle_steps=1)
    assert backend.cancellation_receipts[0]["job_status"] == "cancelled"


def test_base_is_exposed_through_shared_sdk_and_traced():
    class Provider:
        def find_objects(self, **kwargs):
            return []

    events = []
    action = AgentServerActionBackend(
        simulator_attested=True, transport=AgentTransport()
    )
    sdk = RobocasaMobileSDK(
        ModeBoundPerceptionBackend(
            action, mode="sim_gt", target="robocasa_sim", provider=Provider()
        ),
        action_backend=action,
        event_sink=events.append,
    )
    assert sdk.describe()["base"] == ("move_delta",)
    sdk.base.move_delta(0.1, -0.1)
    assert events[-1]["event_type"] == "sdk.base_command"
    assert events[-1]["status"] == "completed"
