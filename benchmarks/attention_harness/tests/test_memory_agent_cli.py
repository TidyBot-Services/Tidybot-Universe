"""CLI routing for generated-policy memory validation, without live services."""

from __future__ import annotations

import json
import hashlib
import sys

from benchmarks.attention_harness import memory_agent_cli


def test_validate_cli_routes_generated_robocasa_code(monkeypatch, tmp_path, capsys):
    code = tmp_path / "policy.py"
    code.write_text("from robot_sdk import gripper\n", encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    captured = {}

    class Gateway:
        def provenance(self, memory_id):
            return {
                "artifact": {"applicability": {"suite": "robocasa"}},
                "source_policy_id": "generated-source-policy",
            }

    class Agent:
        def __init__(self, service):
            self.service = service

        def run_validation(self, memory_id, *, executor, cases, assistance_credits):
            captured.update(
                memory_id=memory_id, executor=executor,
                cases=cases, assistance_credits=assistance_credits,
            )
            return {"paired_dev_seeds": 0}

    monkeypatch.setattr(memory_agent_cli, "MemoryServiceClient", lambda *args, **kwargs: Gateway())
    monkeypatch.setattr(memory_agent_cli, "MemoryAgent", Agent)
    monkeypatch.setattr(sys, "argv", [
        "memory_agent_cli", "validate", "memory-1", "--suite", "robocasa",
        "--cases", str(cases), "--code", str(code), "--timeout-seconds", "12",
        "--artifact-root", str(tmp_path), "--store-path", str(tmp_path / "memory.sqlite3"),
        "--confirm-simulator-agent",
    ])
    assert memory_agent_cli.main() == 0
    assert captured["memory_id"] == "memory-1"
    assert captured["executor"].policy_code_path == code.resolve()
    assert captured["executor"].policy_id == "generated-source-policy"
    assert captured["executor"].timeout_seconds == 12.0
    assert json.loads(capsys.readouterr().out)["paired_dev_seeds"] == 0


def test_preflight_cli_hashes_code_without_running_validation(monkeypatch, tmp_path, capsys):
    code = tmp_path / "policy.py"
    code.write_text("from robot_sdk import gripper\n", encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text("[]", encoding="utf-8")
    captured = {}

    class Gateway:
        def provenance(self, memory_id):
            return {
                "artifact": {"applicability": {"suite": "robocasa"}},
                "source_policy_id": "generated-source-policy",
            }

    class Agent:
        def __init__(self, service):
            self.service = service

        def preflight_validation(self, memory_id, *, cases, assistance_credits, validation_policy_sha256):
            captured.update(
                memory_id=memory_id, cases=cases, assistance_credits=assistance_credits,
                validation_policy_sha256=validation_policy_sha256,
            )
            return {"simulator_executed": False}

        def run_validation(self, *args, **kwargs):
            raise AssertionError("preflight must not run trials")

    monkeypatch.setattr(memory_agent_cli, "MemoryServiceClient", lambda *args, **kwargs: Gateway())
    monkeypatch.setattr(memory_agent_cli, "MemoryAgent", Agent)
    monkeypatch.setattr(sys, "argv", [
        "memory_agent_cli", "preflight", "memory-1", "--suite", "robocasa",
        "--cases", str(cases), "--code", str(code),
    ])
    assert memory_agent_cli.main() == 0
    assert captured["validation_policy_sha256"] == hashlib.sha256(code.read_bytes()).hexdigest()
    assert captured["memory_id"] == "memory-1"
    assert json.loads(capsys.readouterr().out)["simulator_executed"] is False
