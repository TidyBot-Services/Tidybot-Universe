"""M2 identity, approval and formal-only dispatch boundaries."""

from __future__ import annotations

import hashlib
import asyncio
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(HERE), str(HERE.parents[1])]

import m2_gate
from m2_operator_approval import record_approval
from attentionbench_bridge import (build_attention_command, _write_m2_process_evidence,
                                   _m2_child_env)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    graph = tmp_path / "graph"
    graph.mkdir()
    policy = root / "benchmarks/attention_harness/protocol/v2/policies/p.py"
    policy.parent.mkdir(parents=True)
    code = "from robot_sdk import sensors\nsensors.find_objects()\n"
    policy.write_text(code)
    sim_config = policy.parents[1] / "formal.json"
    sim_config.write_text("{}\n")
    service = tmp_path / "service"
    service.mkdir()
    raw = graph / "dev_generation_responses.json"
    prompt = "prompt"
    raw.write_text(json.dumps({"prompt": prompt, "responses": [{
        "format_attempt": 1, "content": code.strip(), "usage": {"total_tokens": 8},
        "provider_attempts": 1,
    }]}))
    receipt = graph / "dev_generation.json"
    receipt.write_text(json.dumps({
        "schema_version": "attentionbench.bounded-dev-generation.v1",
        "model": "parcc/GLM", "source": str(policy), "sha256": sha(policy.read_bytes()),
        "usage": {"total_tokens": 8}, "format_attempts": 1,
        "provider_attempts": 1, "prompt_sha256": sha(prompt.encode()),
        "hypothesis": "unknown", "responses_artifact": str(raw),
        "responses_sha256": sha(raw.read_bytes()),
    }))
    config = {
        "m2_gate": True, "m2_graph_dir": str(graph),
        "suite": "robosuite", "task": "cube_lift", "seed": 101,
        "attention_policy": "autonomous", "runner_boundary": "formal",
        "generated_policy_file": str(policy.relative_to(root)),
        "formal_config_file": str(sim_config.relative_to(root)),
        "service_source_root": str(service),
        "dev_generation_artifact": str(receipt),
        "max_attempts": 1, "assistance_credits": 0,
    }
    task = {"schema_version": "attentionbench.m2-task-lock.v1",
            **{key: config.get(key) for key in (
                "suite", "task", "seed", "attention_policy", "generated_policy_file",
                "formal_config_file", "max_attempts", "assistance_credits", "token_limit",
                "human_deadline_seconds", "overall_deadline_seconds")},
            "config_sha256": sha(sim_config.read_bytes())}
    task["sha256"] = sha(json.dumps(task, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode())
    lock_file = graph / "task_lock.json"
    lock_file.write_text(json.dumps(task))
    config["m2_task_lock_file"] = str(lock_file)
    config["m2_task_lock_sha256"] = task["sha256"]

    def lock(**kwargs):
        identity = {"suite": kwargs["suite"], "task_id": kwargs["task_id"],
                    "seed": kwargs["seed"],
                    "approved_policy_sha256": kwargs["approved_policy_sha256"],
                    "approved_config_sha256": kwargs["approved_config_sha256"]}
        return {"sha256": sha(json.dumps(identity, sort_keys=True).encode()), **identity}, None

    monkeypatch.setattr(m2_gate, "inspect_formal_entry", lock)
    frozen = m2_gate.candidate(config, graph_dir=graph, repo_root=root)
    config["m2_candidate"] = frozen
    return root, graph, policy, sim_config, receipt, config


def approve(tmp_path, config):
    frozen = config["m2_candidate"]
    record = tmp_path / "operator-approval.json"
    record.write_text(json.dumps({
        "schema_version": "attentionbench.m2-human-approval.v1",
        "decision": "approve", "operator": "test-operator", "approved_at": "test-time",
        "approval_reference": "test-only-record",
        **{key: frozen[key] for key in ("suite", "task", "seed", "source_sha256",
                                          "config_sha256", "generation_sha256", "entry_sha256")},
    }))
    config.update(m2_approval_file=str(record), approved_generated_policy=True,
                  approved_policy_sha256=frozen["source_sha256"],
                  approved_config_sha256=frozen["config_sha256"])
    return record


def test_unapproved_graph_boolean_cannot_dispatch(tmp_path, monkeypatch):
    root, _, _, _, _, config = fixture(tmp_path, monkeypatch)
    config.update(approved_generated_policy=True,
                  approved_policy_sha256=config["m2_candidate"]["source_sha256"],
                  approved_config_sha256=config["m2_candidate"]["config_sha256"])
    with pytest.raises(ValueError, match="human approval"):
        build_attention_command(config, repo_root=root)


def test_approved_dispatch_uses_formal_cli_and_lock(tmp_path, monkeypatch):
    root, _, _, _, _, config = fixture(tmp_path, monkeypatch)
    approve(tmp_path, config)
    cmd = build_attention_command(config, repo_root=root)
    assert "benchmarks.attention_harness.formal_attention_cli" in cmd
    assert "benchmarks.attention_harness.sim_gt_attention_cli" not in cmd
    assert cmd[cmd.index("--expected-entry-sha256") + 1] == config["m2_candidate"]["entry_sha256"]
    assert cmd[cmd.index("--dev-generation-artifact") + 1] == config["dev_generation_artifact"]
    config["m2_approval_record_sha256"] = sha(Path(config["m2_approval_file"]).read_bytes())
    Path(config["m2_approval_file"]).write_text(Path(config["m2_approval_file"]).read_text() + " ")
    with pytest.raises(ValueError, match="record changed"):
        build_attention_command(config, repo_root=root)


@pytest.mark.parametrize("change", ["source", "config", "suite", "task", "seed",
                                     "receipt", "approval", "candidate"])
def test_approved_identity_changes_fail_closed(tmp_path, monkeypatch, change):
    root, _, policy, sim_config, receipt, config = fixture(tmp_path, monkeypatch)
    record = approve(tmp_path, config)
    if change == "source":
        policy.write_text(policy.read_text() + "# edit\n")
    elif change == "config":
        sim_config.write_text('{"edit": true}\n')
    elif change == "suite":
        config["suite"] = "robocasa"
    elif change == "task":
        config["task"] = "cube_stack"
    elif change == "seed":
        config["seed"] = 102
    elif change == "receipt":
        receipt.write_text(receipt.read_text() + " ")
    elif change == "approval":
        data = json.loads(record.read_text())
        data["operator"] = ""
        record.write_text(json.dumps(data))
    else:
        config["m2_candidate"] = {**config["m2_candidate"], "entry_sha256": "0" * 64}
    with pytest.raises((ValueError, KeyError)):
        build_attention_command(config, repo_root=root)


def test_receipt_raw_mismatch_and_path_escape_fail(tmp_path, monkeypatch):
    root, graph, policy, _, receipt, config = fixture(tmp_path, monkeypatch)
    raw = graph / "dev_generation_responses.json"
    raw.write_text(raw.read_text().replace("sensors.find_objects()", "gripper.close()"))
    data = json.loads(receipt.read_text())
    data["responses_sha256"] = sha(raw.read_bytes())
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="raw Dev response"):
        m2_gate.candidate(config, graph_dir=graph, repo_root=root)
    config["generated_policy_file"] = "../escape.py"
    with pytest.raises(ValueError, match="path escapes protocol"):
        m2_gate.candidate(config, graph_dir=graph, repo_root=root)
    config["m2_task_lock_file"] = str(tmp_path / "outside.json")
    with pytest.raises(ValueError, match="escapes graph"):
        m2_gate.candidate(config, graph_dir=graph, repo_root=root)


def test_response_and_config_paths_cannot_escape(tmp_path, monkeypatch):
    root, graph, _, _, receipt, config = fixture(tmp_path, monkeypatch)
    data = json.loads(receipt.read_text())
    data["responses_artifact"] = str(tmp_path / "outside-responses.json")
    receipt.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="response path escapes graph"):
        m2_gate.candidate(config, graph_dir=graph, repo_root=root)
    config["formal_config_file"] = "../outside.json"
    with pytest.raises(ValueError, match="path escapes protocol"):
        m2_gate.candidate(config, graph_dir=graph, repo_root=root)


def test_invalid_policy_rejected_before_formal_dispatch(tmp_path, monkeypatch):
    root, _, policy, _, _, config = fixture(tmp_path, monkeypatch)
    approve(tmp_path, config)
    policy.write_text("import os\n")
    with pytest.raises(ValueError):
        build_attention_command(config, repo_root=root)


def test_invalid_policy_with_matching_receipt_and_approval_still_rejected(tmp_path, monkeypatch):
    root, graph, policy, _, receipt, config = fixture(tmp_path, monkeypatch)
    invalid = "import os\n"
    policy.write_text(invalid)
    raw = graph / "dev_generation_responses.json"
    raw_data = json.loads(raw.read_text())
    raw_data["responses"][0]["content"] = invalid.strip()
    raw.write_text(json.dumps(raw_data))
    receipt_data = json.loads(receipt.read_text())
    receipt_data["sha256"] = sha(policy.read_bytes())
    receipt_data["responses_sha256"] = sha(raw.read_bytes())
    receipt.write_text(json.dumps(receipt_data))
    (graph / "m2_entry_lock_robosuite.json").unlink()
    config["m2_candidate"] = m2_gate.candidate(config, graph_dir=graph, repo_root=root)
    approve(tmp_path, config)
    with pytest.raises(Exception, match="import|policy|allowed"):
        build_attention_command(config, repo_root=root)


def test_graph_pauses_then_claims_once_and_restart_does_not_repeat(tmp_path, monkeypatch):
    from test_orchestrator_pipeline import import_orchestrator, make_entry, make_graph

    entry = make_entry("m2")
    entry["attentionbench"] = {"m2_gate": True, "runner_boundary": "formal"}
    entry["m2_stage"] = "awaiting_approval"
    entry["m2_candidate"] = {"source_sha256": "a" * 64}
    orch = import_orchestrator(make_graph([entry]))
    orch._load_entries()
    sys.modules["agent_orchestrator"] = orch
    sys.modules.pop("attention_orchestrator", None)
    adapter = importlib.import_module("attention_orchestrator")
    adapter.install()
    monkeypatch.setattr(orch, "broadcast_full_sync", AsyncMock())
    monkeypatch.setattr(orch, "ws_broadcast_agent_msg", AsyncMock())
    calls = []

    async def run_job(*args, **kwargs):
        calls.append("run")
        raise RuntimeError("deliberate test stop")

    monkeypatch.setattr(adapter, "run_attention_job", run_job)
    state = orch.AgentState(agent_id="dev-m2", skill="m2", status="done")
    asyncio.run(orch._handle_agent_done(state))
    assert orch._find_entry("m2")["status"] == "review"
    assert calls == []
    orch._update_entry("m2", {"m2_stage": "approved", "status": "planned"})
    monkeypatch.setattr(adapter, "require_m2_approval", lambda *args, **kwargs: {
        "record": str(tmp_path / "approval"), "record_sha256": "b" * 64,
        "candidate": entry["m2_candidate"],
    })
    asyncio.run(orch._handle_agent_done(state))
    assert calls == ["run"]
    assert orch._find_entry("m2")["m2_dispatch"]["record_sha256"] == "b" * 64
    orch._load_entries()
    assert orch._find_entry("m2")["status"] == "review"
    asyncio.run(orch._handle_agent_done(state))
    assert calls == ["run"]


def test_operator_record_requires_exact_reviewed_hashes(tmp_path, monkeypatch):
    root, graph, _, _, _, config = fixture(tmp_path, monkeypatch)
    entry = {"name": "m2", "status": "review", "m2_stage": "awaiting_approval",
             "m2_candidate": config["m2_candidate"], "attentionbench": config}
    (graph / "graph.json").write_text(json.dumps({"entries": [entry]}))
    current = config["m2_candidate"]
    fields = dict(graph_dir=graph, repo_root=root, approval_file=tmp_path / "approval.json",
                  operator="human-test", approval_reference="explicit-test-decision",
                  source_sha256=current["source_sha256"],
                  config_sha256=current["config_sha256"],
                  entry_sha256=current["entry_sha256"])
    with pytest.raises(ValueError, match="human-approved SHA"):
        record_approval(**{**fields, "source_sha256": "0" * 64})
    with pytest.raises(ValueError, match="outside repository"):
        record_approval(**{**fields, "approval_file": root / "forged.json"})
    assert not fields["approval_file"].exists()
    record_approval(**fields)
    updated = json.loads((graph / "graph.json").read_text())["entries"][0]
    assert updated["m2_stage"] == "approved" and updated["status"] == "planned"
    assert m2_gate.require_approval(updated["attentionbench"], graph_dir=graph,
                                    repo_root=root, expected=current)["operator"] == "human-test"
    with pytest.raises(ValueError, match="not awaiting"):
        record_approval(**fields)


def _formal_result(config):
    frozen = config["m2_candidate"]
    lock = json.loads(Path(frozen["entry_lock"]).read_text())
    return {
        "suite": frozen["suite"], "task_id": frozen["task"], "seed": frozen["seed"],
        "approved_policy_sha256": frozen["source_sha256"],
        "approved_config_sha256": frozen["config_sha256"],
        "entry_lock": lock, "scheduler_config": {"entry_sha256": frozen["entry_sha256"]},
        "runner_boundary": {"mode": "formal"}, "formal_eligible": False,
        "native_success": False, "artifact_dir": "/tmp/m2-test-artifact", "store": "/tmp/m2-test-store",
        "attempts": [{
            "attention_trace": {"run_id": "run:one", "attempt_id": "attempt:one"},
            "formal_runner_result": {
                "entry_lock": lock, "policy_sha256": frozen["source_sha256"],
                "config_sha256": frozen["config_sha256"],
                "run_id": "run:one", "attempt_id": "attempt:one",
            },
        }],
    }


def test_m2_handoff_checks_lock_and_attempt_identity(tmp_path, monkeypatch):
    _, _, _, _, _, config = fixture(tmp_path, monkeypatch)
    result = _formal_result(config)
    m2_gate.validate_handoff(config, result)
    bad = json.loads(json.dumps(result))
    bad["entry_lock"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="approved lock"):
        m2_gate.validate_handoff(config, bad)
    bad = json.loads(json.dumps(result))
    bad["attempts"][0]["formal_runner_result"]["attempt_id"] = "attempt:other"
    with pytest.raises(ValueError, match="run identity"):
        m2_gate.validate_handoff(config, bad)
    bad = json.loads(json.dumps(result))
    bad["attempts"].append(bad["attempts"][0])
    with pytest.raises(ValueError, match="exactly one"):
        m2_gate.validate_handoff(config, bad)


def test_m2_graph_handoff_stops_before_memory_and_eval(tmp_path, monkeypatch):
    from test_orchestrator_pipeline import import_orchestrator, make_entry, make_graph

    _, _, _, _, _, config = fixture(tmp_path, monkeypatch)
    config["m2_approval_record_sha256"] = "b" * 64
    entry = make_entry("m2")
    entry.update(attentionbench=config, m2_candidate=config["m2_candidate"],
                 m2_stage="dispatching", m2_dispatch={"record_sha256": "b" * 64})
    orch = import_orchestrator(make_graph([entry]))
    orch._load_entries()
    sys.modules["agent_orchestrator"] = orch
    sys.modules.pop("attention_orchestrator", None)
    adapter = importlib.import_module("attention_orchestrator")
    adapter.install()
    monkeypatch.setattr(orch, "broadcast_full_sync", AsyncMock())
    monkeypatch.setattr(adapter, "require_m2_approval", lambda *args, **kwargs: {
        "record_sha256": "b" * 64,
    })
    monkeypatch.setattr(adapter, "dispatch_memory_candidates", lambda *args, **kwargs: (
        pytest.fail("M2 must not schedule Memory")))
    monkeypatch.setattr(adapter, "_diagnose_attention", AsyncMock(side_effect=AssertionError(
        "M2 must not schedule Eval")))
    state = orch.AgentState(agent_id="dev-m2", skill="m2", status="done")
    asyncio.run(adapter._continue_attention_result(state, config, _formal_result(config)))
    current = orch._find_entry("m2")
    assert current["m2_stage"] == "dispatched" and current["status"] == "review"
    assert current["attentionbench_last_run"]["attempt_ids"] == ["attempt:one"]


def test_bridge_logs_redact_credentials_without_losing_command_identity(tmp_path):
    secret_like = b"sk-" + b"x" * 20
    _write_m2_process_evidence(tmp_path, stdout=b"result " + secret_like,
                               stderr=b"diagnostic", returncode=1,
                               command_sha256="a" * 64)
    process = json.loads((tmp_path / "bridge_process.json").read_text())
    assert process["credentials_redacted"] is True
    assert secret_like not in (tmp_path / "bridge_stdout.log").read_bytes()
    assert process["command_sha256"] == "a" * 64


def test_m2_formal_child_does_not_inherit_glm_credentials(monkeypatch):
    monkeypatch.setenv("PARCC_API_KEY", "test-only-secret")
    monkeypatch.setenv("LITELLM_KEY", "another-test-secret")
    monkeypatch.setenv("PARCC_URL", "https://example.invalid/v1/chat/completions")
    child = _m2_child_env()
    assert "PARCC_API_KEY" not in child and "LITELLM_KEY" not in child
    assert child["PARCC_URL"] == "https://example.invalid/v1/chat/completions"
