"""Regression of the rejected control and its independent Safety path."""

import hashlib
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from benchmarks.attention_harness.public_station import publish_public_station, read_public_station
from benchmarks.attention_harness.robocasa_native.controlled_negative import controlled_negative_request
from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend
from benchmarks.attention_harness.robosuite_memory.formal_sandbox import execute_formal_policy


def _request(tmp_path):
    policy = tmp_path / "policy.py"
    config = tmp_path / "config.json"
    policy.write_text("from robot_sdk import gripper\ngripper.close()\ngripper.open()\n")
    config.write_text("{}")
    return controlled_negative_request(
        run_dir=tmp_path / "attention-robocasa-safety-negative-v3",
        policy_path=policy, policy_sha256=hashlib.sha256(policy.read_bytes()).hexdigest(),
        config_path=config, config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
    )


def test_control_identity_reaches_station_and_action_stage(tmp_path):
    request = _request(tmp_path)
    publish_public_station(
        run_dir=request.artifact_root.parent, run_id=request.run_id,
        attempt_id=request.attempt_id, suite="robocasa",
        origin="ws://127.0.0.1:6380", device_id="maniskill_base",
    )
    station = read_public_station(tmp_path, request.run_id, "robocasa", {request.attempt_id})
    assert station["attempt_id"] == request.attempt_id
    assert not request.artifact_root.exists()  # No Service or episode needed for this check.


def test_old_fixed_identity_still_rejected(tmp_path):
    with pytest.raises(ValueError, match="run/attempt identity mismatch"):
        publish_public_station(
            run_dir=tmp_path / "negative_control_run_v2",
            run_id="run:seven-admission-safety-negative-control",
            attempt_id="attempt:seven-admission-safety-negative-control:0",
            suite="robocasa", origin="ws://127.0.0.1:6380", device_id="maniskill_base",
        )
    assert not (tmp_path / "negative_control_run_v2").exists()


def test_control_request_rejects_unbound_directory_before_service(tmp_path):
    request = _request(tmp_path)
    with pytest.raises(ValueError, match="dedicated Attention run directory"):
        controlled_negative_request(
            run_dir=tmp_path / "negative_control_run_v2",
            policy_path=request.policy_code_path, policy_sha256=request.policy_sha256,
            config_path=request.config_path, config_sha256=request.config_sha256,
        )


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap unavailable")
def test_original_monitor_and_worker_stop_before_second_action(tmp_path):
    request = _request(tmp_path)
    dispatched = []

    class Backend:
        def observe(self):
            return {"robot0_eef_pos": np.zeros(3)}

        def set_gripper(self, command, *, settle_steps):
            dispatched.append(command)
            raise RuntimeError("predeclared_test_unknown_action_after_real_completion")

    monitor = SafetyMonitorBackend(Backend())
    monitor.observe()
    sdk = SimpleNamespace(gripper=SimpleNamespace(
        close=lambda: monitor.set_gripper(1.0, settle_steps=1),
        open=lambda: monitor.set_gripper(-1.0, settle_steps=1),
    ))
    outcome = execute_formal_policy(
        code=request.policy_code_path.read_text(), sdk=sdk, context={},
        deadline=time.monotonic() + 5, stderr_path=tmp_path / "worker.stderr",
    )
    assert outcome.status == "failed"
    assert "independent safety monitor: action_outcome_unknown" in outcome.error
    assert outcome.call_count == 1 and dispatched == [1.0]
    artifact = monitor.write_artifact(tmp_path / "safety.json", run_id=request.run_id,
                                      attempt_id=request.attempt_id)
    safety = json.loads(artifact.read_text())
    assert safety["source"] == "independent_safety_monitor"
    assert safety["unsafe_attempts"] == 1
    assert [v["kind"] for v in safety["violations"]] == ["action_outcome_unknown"]
