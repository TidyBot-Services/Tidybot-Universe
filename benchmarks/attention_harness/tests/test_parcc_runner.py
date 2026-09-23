from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from benchmarks.attention_harness import parcc_runner
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.parcc_client import ParccResponse
from benchmarks.attention_harness.sandbox import SandboxResult


class FakeAdapter:
    metadata = {"backend": "fake-robosuite"}

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def reset(self, _seed):
        return self.observe()

    def observe(self):
        return {
            "agentview_image": np.zeros((2, 2, 3), dtype=np.uint8),
            "robot0_eef_pos": np.zeros(3, dtype=np.float64),
        }

    def refresh(self):
        return self.observe()

    def native_success(self):
        return False

    def close(self):
        pass


class FakeParccClient:
    def __init__(self, **_kwargs) -> None:
        pass

    def chat(self, **kwargs):
        return ParccResponse(
            model=kwargs["model"],
            content=(
                "```python\nfrom robot_sdk import sensors\n"
                "obs = sensors.get_observation()\n```"
            ),
            reasoning=None,
            latency_seconds=0.1,
            attempts=1,
            usage={"total_tokens": 12},
        )


def test_parcc_runner_automatically_projects_failed_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    sdk_event = {
        "event_id": "sdk-0",
        "sequence": 0,
        "timestamp": 0.1,
        "source": "robot_sdk.sensors",
        "event_type": "sdk.sensor_read",
        "operation": "get_observation",
        "status": "completed",
        "duration_ms": 1.0,
        "arguments": {},
        "result": {"agentview_image": {"shape": [2, 2, 3], "dtype": "uint8"}},
        "error": None,
    }
    sandbox = SandboxResult(
        status="completed",
        exit_code=0,
        timed_out=False,
        stdout="observed scene\n",
        stderr="",
        trace=[],
        error=None,
        sdk_trace=[sdk_event],
    )
    monkeypatch.setattr(parcc_runner, "RobosuiteRobotBackend", FakeAdapter)
    monkeypatch.setattr(parcc_runner, "ParccClient", FakeParccClient)
    monkeypatch.setattr(parcc_runner, "execute_policy", lambda **_kwargs: sandbox)

    result = parcc_runner.run_parcc_episode(
        task_id="cube_lift",
        seed=101,
        artifact_root=tmp_path,
        service_url="http://fake-service",
        skip_review=True,
    )

    link = result["attention_trace"]
    store = AttentionStore(Path(link["store"]))
    raw = store.get_raw_trace(link["raw_trace_id"])
    advisor = store.get_trace(link["advisor_trace_id"])
    assert raw["outcome"]["native_success"] is False
    assert advisor["failure"]["error_type"] == "TaskOutcomeFailure"
    assert "native_success" not in str(advisor).lower()
    assert store.resource_status(link["run_id"])["tokens"]["used"] == 12


def test_parcc_retry_prompt_receives_advisor_guidance(tmp_path: Path, monkeypatch) -> None:
    sandbox = SandboxResult("completed", 0, False, "", "", [], None)
    monkeypatch.setattr(parcc_runner, "RobosuiteRobotBackend", FakeAdapter)
    monkeypatch.setattr(parcc_runner, "ParccClient", FakeParccClient)
    monkeypatch.setattr(parcc_runner, "execute_policy", lambda **_kwargs: sandbox)

    result = parcc_runner.run_parcc_episode(
        task_id="cube_lift",
        seed=101,
        artifact_root=tmp_path,
        service_url="http://fake-service",
        skip_review=True,
        advisor_guidance="Align with the public RGB-D view.",
        previous_policy="from robot_sdk import sensors\n",
    )
    request = json.loads(
        (Path(result["artifact_dir"]) / "developer_request.json").read_text()
    )
    retry_prompt = request["messages"][-1]["content"]
    assert "Align with the public RGB-D view." in retry_prompt
    assert "from robot_sdk import sensors" in retry_prompt
