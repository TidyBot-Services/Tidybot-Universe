from __future__ import annotations

import numpy as np
import pytest

from benchmarks.attention_harness.robocasa_native.agent_actions import (
    AgentServerActionBackend,
    AgentServerActionError,
)


class AgentTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, payload, timeout):
        path = "/" + url.split("/", 3)[-1]
        self.calls.append((method, path, payload))
        if path == "/state":
            pose = np.eye(4).flatten(order="F").tolist()
            pose[12:15] = [0.4, 0.2, 0.3]
            return {"arm": {"q": [0.0] * 7, "ee_pose": pose},
                    "gripper": {"position_mm": 70.0}}
        if path == "/code/submit":
            return {"job_id": "job-1"}
        if path == "/code/jobs/job-1":
            return {"status": "completed", "result": {"exit_code": 0}}
        raise AssertionError(path)


def test_action_backend_uses_agent_server_without_reset_or_evaluator():
    transport = AgentTransport()
    backend = AgentServerActionBackend(transport=transport, simulator_attested=True)
    observation = backend.observe()
    assert observation["robot0_eef_pos"].tolist() == [0.4, 0.2, 0.3]
    assert observation["robot0_gripper_width"].tolist() == [0.07]
    backend.move_arm_to_position(0.1, 0.2, 0.3, tolerance=0.01, max_steps=50)
    backend.set_gripper(1.0, settle_steps=10)
    submissions = [row[2] for row in transport.calls if row[1] == "/code/submit"]
    assert len(submissions) == 2
    assert all(row["reset_env"] is False for row in submissions)
    assert all("/task/success" not in row["code"] for row in submissions)
    assert "gripper.close()" in submissions[1]["code"]


def test_action_backend_rejects_nonfinite_or_failed_jobs():
    transport = AgentTransport()
    backend = AgentServerActionBackend(transport=transport, simulator_attested=True)
    with pytest.raises(ValueError, match="finite"):
        backend.move_arm_delta(float("nan"), 0, 0, (0, 0, 0))
    assert not any(path == "/code/submit" for _, path, _ in transport.calls)

    def failed(method, url, payload, timeout):
        if url.endswith("/code/submit"):
            return {"job_id": "job-1"}
        return {"status": "failed", "result": {"exit_code": 1, "error": "arm stopped"}}

    backend = AgentServerActionBackend(transport=failed, simulator_attested=True)
    with pytest.raises(AgentServerActionError, match="arm stopped"):
        backend.set_gripper(-1.0, settle_steps=1)


def test_action_backend_requires_simulator_attestation():
    with pytest.raises(ValueError, match="simulator-only"):
        AgentServerActionBackend(simulator_attested=False, transport=AgentTransport())
