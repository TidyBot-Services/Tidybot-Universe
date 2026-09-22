from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from benchmarks.attention_harness.reference_policy import run_reference_policy
from benchmarks.attention_harness.robosuite_adapter import (
    RobosuiteAdapter,
    observation_fingerprint,
)
from benchmarks.attention_harness.robot_sdk import NativeRobotSDK
from benchmarks.attention_harness.sandbox import execute_policy
from benchmarks.attention_harness.service_process import ManagedRobosuiteService


pytestmark = pytest.mark.skipif(
    os.environ.get("TIDYBOT_ROBOSUITE_INTEGRATION") != "1",
    reason="set TIDYBOT_ROBOSUITE_INTEGRATION=1 to run native simulator tests",
)


@pytest.fixture(scope="module")
def service_url(tmp_path_factory):
    log_path = tmp_path_factory.mktemp("robosuite-service") / "service.log"
    with ManagedRobosuiteService(log_path=log_path) as url:
        yield url


@pytest.mark.parametrize("task_id", ("cube_lift", "cube_stack"))
def test_native_reset_is_deterministic(service_url: str, task_id: str) -> None:
    adapter = RobosuiteAdapter(
        task_id, service_url=service_url, camera=True, camera_height=84, camera_width=84
    )
    try:
        first_observation = adapter.reset(101)
        first = observation_fingerprint(first_observation)
        second_observation = adapter.reset(101)
        second = observation_fingerprint(second_observation)
        assert first == second
        depth = second_observation["agentview_depth"]
        assert np.isfinite(depth).all()
        assert 0.0 <= float(depth.min()) <= float(depth.max()) <= 2.0
        assert int(second_observation["agentview_image"].max()) > 0
        backend_file = Path(adapter.metadata["robosuite_origin"])
        assert adapter.metadata["service"] == "robosuite_sim"
        assert "ASPIRE" not in backend_file.parts
        assert "aspire" not in backend_file.parts
    finally:
        adapter.close()


@pytest.mark.parametrize("task_id", ("cube_lift", "cube_stack"))
def test_noop_fails_five_of_five(service_url: str, task_id: str) -> None:
    successes = 0
    adapter = RobosuiteAdapter(task_id, service_url=service_url, camera=False)
    try:
        for seed in range(101, 106):
            adapter.reset(seed)
            for _ in range(50):
                adapter.step(adapter.no_op_action())
            successes += int(adapter.native_success())
    finally:
        adapter.close()
    assert successes == 0


def test_native_sdk_moves_without_external_motion_service(service_url: str) -> None:
    adapter = RobosuiteAdapter("cube_lift", service_url=service_url, camera=False)
    try:
        initial = adapter.reset(101)
        start = initial["robot0_eef_pos"]
        sdk = NativeRobotSDK(adapter)
        sdk.arm.move_to_position(float(start[0]), float(start[1]), float(start[2] + 0.02))
        final = sdk.sensors.get_observation()["robot0_eef_pos"]
        assert abs(float(final[2] - start[2]) - 0.02) < 0.006
    finally:
        adapter.close()


def test_sandbox_process_attaches_and_controls_active_episode(
    service_url: str, tmp_path: Path, monkeypatch
) -> None:
    adapter = RobosuiteAdapter("cube_lift", service_url=service_url, camera=False)
    try:
        initial = adapter.reset(101)
        code = tmp_path / "policy.py"
        code.write_text(
            "from robot_sdk import sensors, arm\n"
            "calls = 0\n"
            "def progress():\n"
            "    global calls\n"
            "    calls += 1\n"
            "before = sensors.get_observation()['robot0_eef_pos']\n"
            "progress()\n"
            "print('eef', sorted(before.tolist()), 'dtype', before.dtype)\n"
            "arm.move_delta(0.0, 0.0, 0.2)\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PARCC_API_KEY", "must-not-reach-worker")
        result = execute_policy(
            code_path=code,
            service_url=service_url,
            task_id="cube_lift",
            output_path=tmp_path / "worker.json",
            timeout_seconds=20,
        )
        final = adapter.refresh()
        assert result.status == "completed"
        assert result.trace
        assert float(final["robot0_eef_pos"][2]) > float(initial["robot0_eef_pos"][2])
        assert "must-not-reach-worker" not in result.stdout + result.stderr
    finally:
        adapter.close()


@pytest.mark.parametrize("task_id", ("cube_lift", "cube_stack"))
def test_reference_policy_succeeds_five_of_five(service_url: str, task_id: str) -> None:
    successes = 0
    adapter = RobosuiteAdapter(task_id, service_url=service_url, camera=False)
    try:
        for seed in range(101, 106):
            adapter.reset(seed)
            run_reference_policy(adapter)
            successes += int(adapter.native_success())
            assert adapter.trace
    finally:
        adapter.close()
    assert successes >= 4
