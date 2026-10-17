"""Offline checks for the scripted reference ladder; no simulator or model."""

import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest
from scipy.spatial.transform import Rotation

from benchmarks.attention_harness.diagnostics import reference_ladder as ladder
from openarm365_dev.policy_validation import validate_generated_policy


@pytest.mark.parametrize("stage", ladder.STAGES)
@pytest.mark.parametrize("cap", [0.02, 0.025, 0.03, 0.04])
def test_policy_valid_and_calls_match(stage, cap, monkeypatch):
    source = ladder.build_policy(stage, cap)
    validate_generated_policy(source)
    position = list(ladder.INITIAL_EEF)
    events = []

    def observation():
        events.append("get_observation")
        return {"robot0_left_eef_pos": position[:], "robot0_left_eef_quat": ladder.INITIAL_QUAT}

    def objects():
        events.append("find_objects")
        return [{"name": "obj", "position": ladder.INITIAL_OBJ, "frame": "world"},
                {"name": "sink", "position": ladder.INITIAL_SINK, "frame": "world"}]

    def move(dx, dy, dz):
        assert (dx*dx + dy*dy + dz*dz) ** 0.5 <= cap + 1e-12
        for i, delta in enumerate((dx, dy, dz)):
            position[i] += delta
        events.append("move_delta")

    sdk = types.ModuleType("robot_sdk")
    sdk.sensors = types.SimpleNamespace(get_observation=observation, find_objects=objects)
    sdk.left_arm = types.SimpleNamespace(move_delta=move)
    sdk.left_gripper = types.SimpleNamespace(open=lambda: events.append("open"),
                                             close=lambda: events.append("close"))
    monkeypatch.setitem(sys.modules, "robot_sdk", sdk)
    exec(compile(source, "<policy>", "exec"), {"__builtins__": __builtins__})
    assert len(events) == ladder.call_plan(stage, cap)["total"]
    assert len(events) <= ladder.SDK_LIMIT


def test_cap_below_budget_is_rejected():
    with pytest.raises(ValueError, match="estimates .* SDK calls"):
        ladder.build_policy("release", 0.01)


def test_quaternion_axis_matches_scipy():
    import numpy as np
    rng = np.random.default_rng(101)
    for _ in range(100):
        q = rng.normal(size=4)
        expected = Rotation.from_quat(q).as_matrix()[:, 2]
        assert np.allclose(ladder.tool_z_axis(q), expected, atol=1e-14)


def test_summary_reads_fixture(tmp_path):
    run_dir = tmp_path / "run"
    version = run_dir / "artifacts" / "devexp-test" / "cells" / "cell" / "v1"
    execution = version / "executions" / "run-test"
    planner = execution / "planner"
    planner.mkdir(parents=True)
    plan = ladder.call_plan("lift", 0.02)
    completed = plan["by_stage"]["approach"] + plan["by_stage"]["grasp"]
    operations = ["get_observation", "find_objects", "open"] + ["move_delta"] * (completed - 4) + ["close"]
    assert len(operations) == completed
    trace = [{"operation": op, "status": "completed"} for op in operations]
    trace.append({"operation": "move_delta", "status": "failed",
                  "error": {"message": "PlanningRejected: budget"}})
    (version / "public-feedback.json").write_text(json.dumps({"action_trace": trace}))
    (planner / "continuous-001.json").write_text(json.dumps({"certificate": {
        "result": "PASS", "queries": 1210, "elapsed_seconds": 1.3, "failure": None}}))
    (planner / "continuous-002.json").write_text(json.dumps({"certificate": {
        "result": "未验证", "queries": 4096, "elapsed_seconds": 2.0,
        "failure": {"reason": "continuous_certificate_canceled_or_budget_exceeded"}}}))
    for name, eef, obj, grip in [
        ("initial", ladder.INITIAL_EEF, ladder.INITIAL_OBJ, [0.02, 0.02]),
        ("final", [0.42, -2.39, 1.08], [0.425, -2.394, 0.962], [0.0, 0.0]),
    ]:
        (execution / f"{name}_public_observation.json").write_text(json.dumps({
            "robot": {"robot0_left_eef_pos": eef, "robot0_left_eef_quat": ladder.INITIAL_QUAT,
                      "robot0_left_gripper_qpos": grip},
            "perception": {"values": {"obj_pos": obj, "sink_pos": ladder.INITIAL_SINK}}}))
    (execution / "native_result.json").write_text(json.dumps({"status": "failed", "native_success": False}))
    with (execution / "native-physics.jsonl").open("w") as stream:
        for x in [0.438, 0.42]:
            stream.write(json.dumps({"tcp": {"left": [[1, 0, 0, x], [0, 1, 0, -2.39],
                                                         [0, 0, 1, 1.08], [0, 0, 0, 1]]}}) + "\n")
    result = ladder.summarize_execution(run_dir, "lift", 0.02, 123.45)
    assert result["sdk_completed"] == completed
    assert result["sdk_failed"] == 1
    assert result["first_failure"] == {"operation": "move_delta", "reason": "PlanningRejected: budget"}
    assert result["certificates"][1]["failure_reason"] == "continuous_certificate_canceled_or_budget_exceeded"
    assert result["eef_start"][0] == 0.438
    assert result["eef_end"][0] == 0.42
    assert result["obj_start"] == list(ladder.INITIAL_OBJ)
    assert result["gripper_end"] == [0.0, 0.0]
    assert result["native_success"] is False
    assert result["reached_stage"] == "grasp"
    assert "认证" in ladder.markdown_table([result])


def test_dry_run_is_offline(tmp_path):
    out = tmp_path / "absent"
    env = os.environ.copy()
    env.pop("ATTN_DIAG_JOINT_KP", None)
    result = subprocess.run([sys.executable, "-m",
        "benchmarks.attention_harness.diagnostics.reference_ladder",
        "--runs", "approach:0.02,release:0.02", "--out", str(out), "--dry-run"],
        text=True, capture_output=True, env=env, check=True)
    assert "estimated SDK calls" in result.stdout
    assert "from robot_sdk import sensors" in result.stdout
    assert "export ATTN_DIAG_JOINT_KP=1500" in result.stdout
    assert "dry-run: no execution" in result.stdout
    assert not out.exists()


def test_parent_passes_environment_to_one_child(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ladder.subprocess, "run", fake_run)
    monkeypatch.setenv("ATTN_DIAG_JOINT_KP", "1500")
    out = tmp_path / "results"
    assert ladder.main(["--runs", "approach:0.02", "--out", str(out)]) == 0
    assert len(calls) == 1
    assert calls[0][0][2:4] == [ladder.MODULE_NAME, "--execute-one"]
    assert calls[0][1]["env"]["ATTN_DIAG_JOINT_KP"] == "1500"
    assert len(json.loads((out / "results.json").read_text())) == 1
    assert (out / "results.md").is_file()
