"""Security and approval gates for the independent RoboCasa formal path."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest
from benchmarks.attention_harness.robocasa_native.formal_runner import _config
from benchmarks.attention_harness.robosuite_memory.formal_sandbox import execute_formal_policy


def _request(tmp_path: Path, config: dict) -> FormalRunRequest:
    code = tmp_path / "policy.py"
    conf = tmp_path / "config.json"
    code.write_text("from robot_sdk import sensors\n", encoding="utf-8")
    conf.write_text(json.dumps(config), encoding="utf-8")
    return FormalRunRequest(
        suite="robocasa", task_id="counter_to_sink", seed=101,
        policy_code_path=code,
        policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=conf, config_sha256=hashlib.sha256(conf.read_bytes()).hexdigest(),
        artifact_root=tmp_path / "artifacts", overall_deadline_seconds=5,
    )


def test_robocasa_formal_config_rejects_wrong_reset_and_service_identity(tmp_path):
    config = json.loads((Path(__file__).parents[1] / "protocol/v2/formal_robocasa_counter_to_sink_seed101.json").read_text())
    assert _config(_request(tmp_path, config))["seed"] == 101
    config["seed"] = 102
    with pytest.raises(ValueError, match="task/seed/track"):
        _config(_request(tmp_path, config))
    config["seed"] = 101
    config["sim_service"]["tree_sha256"] = "not-a-digest"
    with pytest.raises(ValueError, match="tree_sha256"):
        _config(_request(tmp_path, config))


def test_robocasa_formal_config_requires_pinned_task_and_runtime(tmp_path):
    original = json.loads((Path(__file__).parents[1] / "protocol/v2/formal_robocasa_counter_to_sink_seed101.json").read_text())
    for field in ("task_source", "sim_runtime"):
        config = dict(original)
        del config[field]
        with pytest.raises(ValueError, match="missing or unexpected fields"):
            _config(_request(tmp_path, config))
    config = json.loads(json.dumps(original))
    config["sim_runtime"]["mani_skill_files"][
        "envs/tasks/mobile_manipulation/robocasa/kitchen.py"
    ] = "invalid"
    with pytest.raises(ValueError, match="runtime identity"):
        _config(_request(tmp_path, config))


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), -1, True])
def test_formal_request_rejects_unbounded_deadline(tmp_path, deadline):
    config = json.loads((Path(__file__).parents[1] / "protocol/v2/formal_robocasa_counter_to_sink_seed101.json").read_text())
    request = _request(tmp_path, config)
    request = FormalRunRequest(**{**request.__dict__, "overall_deadline_seconds": deadline})
    with pytest.raises(ValueError, match="finite positive overall deadline"):
        request.validate()


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap unavailable")
def test_robocasa_formal_worker_base_calls_use_parent_sdk(tmp_path):
    calls = []

    class Base:
        def move_delta(self, dx, dy, dtheta, *, frame):
            calls.append((dx, dy, dtheta, frame))

    outcome = execute_formal_policy(
        code="from robot_sdk import base\nbase.move_delta(0.1, 0.0, 0.0, frame='local')\n",
        sdk=SimpleNamespace(base=Base()), context={}, deadline=time.monotonic() + 3,
        stderr_path=tmp_path / "worker.stderr",
    )
    assert outcome.status == "completed"
    assert outcome.call_count == 1
    assert calls == [(0.1, 0.0, 0.0, "local")]
