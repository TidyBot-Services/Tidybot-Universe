"""The bridge accepts a persisted native failure without inventing success."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import attentionbench_bridge as bridge


class FakeProcess:
    def __init__(self, payload: bytes, exit_code: int):
        self.payload = payload
        self.returncode = exit_code

    async def communicate(self):
        return self.payload, b""


def _config(tmp_path):
    return {
        "approved_trusted_policy": True,
        "suite": "robosuite", "task": "cube_lift", "seed": 101,
        "attention_policy": "reactive_help", "robot_policy": "lab_policy:run",
        "artifact_root": str(tmp_path),
    }


def _summary(tmp_path, *, native_success=False, formal_eligible=False):
    run_dir = tmp_path / "attention-run"
    run_dir.mkdir(exist_ok=True)
    summary = {
        "schema_version": "attentionbench.sim-gt-attention-run.v1",
        "suite": "robosuite", "task_id": "cube_lift", "seed": 101,
        "native_success": native_success, "formal_eligible": formal_eligible,
        "runner_boundary": {"mode": "trusted_dev"},
        "artifact_dir": str(run_dir), "store": str(tmp_path / "attention.sqlite3"),
    }
    (run_dir / "attention_run.json").write_text(json.dumps(summary))
    return summary


def test_failed_native_run_is_preserved_as_development_evidence(tmp_path, monkeypatch):
    summary = _summary(tmp_path)

    async def create_process(*cmd, **kwargs):
        assert cmd[1:3] == ("-m", "benchmarks.attention_harness.sim_gt_attention_cli")
        return FakeProcess(json.dumps(summary).encode(), 1)

    monkeypatch.setattr(bridge.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(bridge, "_verify_approved_policy", lambda config, repo_root: None)
    result = asyncio.run(bridge.run_attention_job(_config(tmp_path), repo_root=ROOT))
    assert result["native_success"] is False
    assert result["formal_eligible"] is False


def test_formal_claim_from_runner_fails_closed(tmp_path, monkeypatch):
    summary = _summary(tmp_path, native_success=True, formal_eligible=True)

    async def create_process(*cmd, **kwargs):
        return FakeProcess(json.dumps(summary).encode(), 0)

    monkeypatch.setattr(bridge.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(bridge, "_verify_approved_policy", lambda config, repo_root: None)
    with pytest.raises(RuntimeError, match="mismatched summary"):
        asyncio.run(bridge.run_attention_job(_config(tmp_path), repo_root=ROOT))
