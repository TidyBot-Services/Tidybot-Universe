"""Small, service-free contract tests for AttentionBench architecture edges."""

from __future__ import annotations

import asyncio
import importlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import websockets  # keep the legacy test helper from installing a global stub


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from attentionbench_bridge import build_attention_command
from service_discovery import resolve_service


def _config(**changes):
    value = {
        "approved_trusted_policy": True,
        "suite": "robosuite", "task": "cube_lift", "seed": 101,
        "attention_policy": "reactive_help", "robot_policy": "lab_policy:run",
    }
    value.update(changes)
    return value


def test_bridge_rejects_unapproved_and_heldout_policy():
    with pytest.raises(ValueError, match="operator-approved"):
        build_attention_command(_config(approved_trusted_policy=False), repo_root=ROOT)
    with pytest.raises(ValueError, match="development seeds"):
        build_attention_command(_config(seed=1001), repo_root=ROOT)
    with pytest.raises(ValueError, match="module:function"):
        build_attention_command(_config(robot_policy="/tmp/generated.py"), repo_root=ROOT)


def test_bridge_builds_shell_free_dual_suite_commands():
    command = build_attention_command(_config(), repo_root=ROOT)
    assert command[:3] == [sys.executable, "-m", "benchmarks.attention_harness.sim_gt_attention_cli"]
    assert command[command.index("--robot-policy") + 1] == "lab_policy:run"
    assert "--confirm-simulator-agent" not in command
    with pytest.raises(ValueError, match="simulator Agent Server"):
        build_attention_command(_config(suite="robocasa", task="counter_to_sink"), repo_root=ROOT)
    casa = build_attention_command(_config(
        suite="robocasa", task="counter_to_sink", confirm_simulator_agent=True,
    ), repo_root=ROOT)
    assert "--confirm-simulator-agent" in casa


def test_service_discovery_keeps_catalog_and_runtime_distinct(tmp_path, monkeypatch):
    from service_discovery import _running_services

    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"services": [
        {"name": "yolo", "client": "yolo.client:Client"},
    ]}))
    missing = resolve_service("grasp", catalog_path=catalog)
    assert missing.state == "requested" and missing.endpoint is None
    known = resolve_service("yolo", catalog_path=catalog)
    assert known.state == "catalog_only" and known.endpoint is None
    monkeypatch.setattr("service_discovery._running_services", lambda url, timeout: {
        "yolo": {"name": "yolo", "host": "http://127.0.0.1:18000", "status": "healthy"},
    })
    ready = resolve_service("yolo", catalog_path=catalog,
                            deploy_agent_url="http://127.0.0.1:9000")
    assert ready.state == "ready" and ready.endpoint == "http://127.0.0.1:18000"


def test_attention_orchestrator_native_verdict_cannot_be_overridden(tmp_path, monkeypatch):
    from test_orchestrator_pipeline import import_orchestrator, make_graph, make_entry

    entry = make_entry("lift")
    entry["attentionbench"] = _config()
    orch = import_orchestrator(make_graph([entry]))
    orch._load_entries()
    sys.modules["agent_orchestrator"] = orch
    sys.modules.pop("attention_orchestrator", None)
    adapter = importlib.import_module("attention_orchestrator")
    adapter.install()
    monkeypatch.setattr(orch, "broadcast_full_sync", AsyncMock())
    monkeypatch.setattr(orch, "ws_broadcast_agent_msg", AsyncMock())
    artifact_dir = tmp_path / "attempt"
    artifact_dir.mkdir()
    (artifact_dir / "attention_run.json").write_text("{}")

    async def run_job(config, *, repo_root):
        return {
            "artifact_dir": str(artifact_dir), "store": str(tmp_path / "memory.sqlite3"),
            "native_success": False, "stopped_reason": "attempt_failed",
            "suite": "robosuite", "task_id": "cube_lift", "seed": 101,
        }

    monkeypatch.setattr(adapter, "run_attention_job", run_job)
    monkeypatch.setattr(adapter, "_diagnose_attention", AsyncMock(return_value="I think it passed"))
    state = orch.AgentState(agent_id="dev-test", skill="lift", status="done")
    asyncio.run(orch._handle_agent_done(state))
    current = orch._find_entry("lift")
    assert current["status"] == "review"
    assert current["attentionbench_last_run"]["native_success"] is False
    assert current["attentionbench_last_run"]["run_id"] == "run:attempt"
    assert "Native success=False" in orch._last_feedback["lift"]


def test_formal_failure_dispatches_memory_candidate_task(tmp_path, monkeypatch):
    from test_orchestrator_pipeline import import_orchestrator, make_graph, make_entry

    entry = make_entry("lift")
    entry["attentionbench"] = _config(runner_boundary="formal")
    orch = import_orchestrator(make_graph([entry]))
    orch._load_entries()
    sys.modules["agent_orchestrator"] = orch
    sys.modules.pop("attention_orchestrator", None)
    adapter = importlib.import_module("attention_orchestrator")
    adapter.install()
    monkeypatch.setattr(orch, "broadcast_full_sync", AsyncMock())
    monkeypatch.setattr(orch, "ws_broadcast_agent_msg", AsyncMock())
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "attention_run.json").write_text("{}")

    async def run_job(config, *, repo_root):
        return {
            "artifact_dir": str(artifact_dir), "store": str(tmp_path / "memory.sqlite3"),
            "native_success": False, "suite": "robosuite", "task_id": "cube_lift",
            "seed": 101, "runner_boundary": {"mode": "formal"},
            "requests": [{"request_id": "request:1", "candidate_memory_id": "candidate:1"}],
            "attempts": [],
        }

    monkeypatch.setattr(adapter, "run_attention_job", run_job)
    monkeypatch.setattr(adapter, "dispatch_memory_candidates", lambda *args, **kwargs: [
        {"memory_id": "candidate:1", "status": "awaiting_approved_repair", "state_path": "task.json"},
    ])
    monkeypatch.setattr(adapter, "_diagnose_attention", AsyncMock(return_value="failure diagnosed"))
    state = orch.AgentState(agent_id="dev-test", skill="lift", status="done")
    asyncio.run(orch._handle_agent_done(state))
    current = orch._find_entry("lift")
    assert current["status"] == "review"
    assert current["attentionbench_last_run"]["memory_validation_tasks"][0]["memory_id"] == "candidate:1"
