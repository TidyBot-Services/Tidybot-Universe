from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path

import pytest

from benchmarks.attention_harness.sandbox import (
    PolicyValidationError,
    _sanitized_environment,
    execute_policy,
    validate_policy,
)


def test_valid_sdk_policy_passes() -> None:
    validate_policy(
        "from robot_sdk import sensors, arm, gripper\n"
        "obs = sensors.get_observation()\n"
        "print(len(obs))\n"
        "arm.move_delta(0.0, 0.0, 0.0)\n"
    )


@pytest.mark.parametrize(
    "code",
    [
        "import os\n",
        "from pathlib import Path\n",
        "from robot_sdk import sensors\nopen('/tmp/x')\n",
        "from robot_sdk import sensors\nsensors.__class__\n",
        "from robot_sdk import sensors\ngetattr(sensors, 'x')\n",
        "from robot_sdk import sensors\nsensors.get_observation()['x'].tofile('/tmp/x')\n",
        "from robot_sdk import sensors\ntry:\n sensors.get_observation()\nexcept Exception:\n pass\n",
    ],
)
def test_forbidden_capabilities_are_rejected(code) -> None:
    with pytest.raises(PolicyValidationError):
        validate_policy(code)


def test_sandbox_environment_drops_credentials(monkeypatch) -> None:
    monkeypatch.setenv("PARCC_API_KEY", "secret")
    monkeypatch.setenv("OTHER_TOKEN", "secret")
    clean = _sanitized_environment()
    assert "PARCC_API_KEY" not in clean
    assert "OTHER_TOKEN" not in clean
    assert clean["PATH"] == os.environ["PATH"]


def test_timeout_preserves_incrementally_written_sdk_trace(
    tmp_path: Path, monkeypatch
) -> None:
    code_path = tmp_path / "policy.py"
    code_path.write_text(
        "from robot_sdk import sensors\nsensors.get_observation()\n",
        encoding="utf-8",
    )
    output_path = tmp_path / "worker.json"
    partial_event = {
        "event_id": "sdk-0",
        "operation": "get_observation",
        "status": "completed",
    }

    def time_out(command, **kwargs):
        output_path.write_text(
            json.dumps(
                {
                    "status": "failed",
                    "error": None,
                    "trace": [{"step": 0}],
                    "sdk_trace": [partial_event],
                }
            ),
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="partial")

    monkeypatch.setattr(subprocess, "run", time_out)
    result = execute_policy(
        code_path=code_path,
        service_url="http://127.0.0.1:8082",
        task_id="cube_lift",
        output_path=output_path,
        timeout_seconds=1.0,
    )

    assert result.status == "timeout"
    assert result.trace == [{"step": 0}]
    assert result.sdk_trace == [partial_event]
