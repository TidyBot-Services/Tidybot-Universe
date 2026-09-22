from __future__ import annotations

import os

import pytest

from benchmarks.attention_harness.sandbox import PolicyValidationError, _sanitized_environment, validate_policy


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
