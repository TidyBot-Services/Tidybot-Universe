from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.tests.test_formal_attention_chain import FakeFormalRunner
from benchmarks.attention_harness.ui_launch import launch_formal_run, load_catalog
from benchmarks.attention_harness.v2_advisor import SimGTAdvisorProxy, semantic_advisor_cache_key


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_shared_advisor_cache_reuses_only_matching_visible_conditions(tmp_path):
    calls = []
    packet = {"trace_id": "trace:a", "run_id": "run:a", "attempt_id": "attempt:a",
              "created_at": 1, "evidence": [{"evidence_id": "frame:a", "sha256": "a" * 64}],
              "experiment": {"suite": "robosuite", "task_id": "cube_lift", "seed": 101,
                             "config_sha256": "b" * 64},
              "code": {"sha256": "c" * 64}, "hypothesis_status": "unknown"}

    def answer(store_name, current):
        store = AttentionStore(tmp_path / f"{store_name}.sqlite3")
        proxy = SimGTAdvisorProxy(store, transport=lambda request: calls.append(request) or "safe hint",
                                  cache_path=tmp_path / "shared.sqlite3", sleeper=lambda _: None)
        return proxy.answer(request_type="hint", trace_packet=current)

    first = answer("run-a", packet)
    second = answer("run-b", {**packet, "trace_id": "trace:b", "run_id": "run:b",
                               "attempt_id": "attempt:b", "created_at": 20,
                               "evidence": [{"evidence_id": "frame:b", "sha256": "a" * 64}]})
    assert first.cached is False and second.cached is True and len(calls) == 1
    changed = answer("run-c", {**packet, "experiment": {**packet["experiment"], "seed": 102}})
    assert changed.cached is False and len(calls) == 2
    changed_evidence = answer("run-d", {**packet, "evidence": [{"sha256": "d" * 64}]})
    assert changed_evidence.cached is False and len(calls) == 3
    changed_mode = answer("run-e", {**packet, "experiment": {
        **packet["experiment"], "assistance_mode": "live_human_first"}})
    assert changed_mode.cached is False and len(calls) == 4
    history = [{"attempt_index": 0, "run_id": "run:old", "timestamp": 1,
                "failure": {"observed_symptom": "missed"},
                "code_sha256": "c" * 64,
                "evidence_refs": [{"evidence_id": "frame:old", "sha256": "e" * 64,
                                   "content": "gripper stayed open"}]}]
    historical_packet = {**packet, "failure_history": history}
    assert answer("history-a", historical_packet).cached is False
    volatile_history = deepcopy(historical_packet)
    volatile_history.update(trace_id="trace:new", run_id="run:new", attempt_id="attempt:new",
                            created_at=42)
    volatile_history["failure_history"][0].update(run_id="run:new", timestamp=42)
    volatile_history["failure_history"][0]["evidence_refs"][0]["evidence_id"] = "frame:new"
    assert answer("history-b", volatile_history).cached is True
    changed_history_sha = deepcopy(volatile_history)
    changed_history_sha["failure_history"][0]["evidence_refs"][0]["sha256"] = "f" * 64
    assert answer("history-c", changed_history_sha).cached is False
    changed_history_content = deepcopy(volatile_history)
    changed_history_content["failure_history"][0]["failure"]["observed_symptom"] = "slipped"
    assert answer("history-d", changed_history_content).cached is False
    changed_history_source = deepcopy(volatile_history)
    changed_history_source["failure_history"][0]["evidence_refs"][0]["source_sha256"] = "1" * 64
    assert answer("history-e", changed_history_source).cached is False
    changed_evidence_content = deepcopy(volatile_history)
    changed_evidence_content["failure_history"][0]["evidence_refs"][0]["content"] = "gripper closed"
    assert answer("history-f", changed_evidence_content).cached is False
    assert len(calls) == 9
    original = calls[0]
    for field in ("model", "max_tokens"):
        altered = deepcopy(original)
        altered[field] = "different" if field == "model" else altered[field] + 1
        assert semantic_advisor_cache_key(altered) != semantic_advisor_cache_key(original)
    altered = deepcopy(original)
    altered["messages"][0]["content"] += " New prompt version."
    assert semantic_advisor_cache_key(altered) != semantic_advisor_cache_key(original)


def test_formal_packet_has_actual_or_unknown_hypothesis_and_bounded_history(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text("{}")
    captured = []
    runner = FakeFormalRunner("robosuite")
    result = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101, artifact_root=tmp_path / "runs",
        policy_id="reactive_help", policy_code_path=code, approved_policy_sha256=_sha(code),
        config_path=config, approved_config_sha256=_sha(config), runner=runner,
        max_attempts=3, assistance_credits=2, dev_hypothesis=None,
        advisor_transport=lambda request: captured.append(json.loads(request["messages"][1]["content"]))
        or json.dumps({"schema_version": "attentionbench.advisor-advice.v1", "request_type": "hint",
                       "diagnosis": "visible failure", "guidance": "retry safely", "caution": "stay safe",
                       "confidence": 0.5}),
        sleeper=lambda _: None,
    )
    assert result["formal_eligible"] is False
    assert result["dev_hypothesis"] == {"status": "unknown", "content": None,
                                        "evidence": None}
    assert len(runner.requests) == 3 and len(captured) == 2
    current = captured[1]["trace_packet"]
    assert current["hypothesis_status"] == "unknown"
    assert current["experiment"]["config_sha256"] == _sha(config)
    assert len(current["failure_history"]) == 1
    assert current["failure_history"][0]["code_sha256"] == _sha(code)
    assert "native_success" not in json.dumps(captured)
    store = AttentionStore(Path(result["store"]))
    first_attempt = result["attempts"][0]["attention_trace"]["attempt_id"]
    events = store.events()
    terminal = next(i for i, event in enumerate(events) if event["event_key"] == f"finish:{first_attempt}")
    projection = next(i for i, event in enumerate(events) if event["event_type"] == "trace.created")
    assert terminal < projection
    request_id = result["requests"][0]["request_id"]
    with pytest.raises(ValueError, match="safety constraints"):
        store.record_waiting_work(request_id, work_type="robot_motion", evidence=[])


def test_formal_cli_rejects_unbound_dev_hypothesis_receipt(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text("{}")
    receipt = tmp_path / "dev_generation.json"
    receipt.write_text(json.dumps({"source": str(code), "sha256": "0" * 64,
                                   "hypothesis": "I think the grasp may fail"}))
    result = subprocess.run([
        sys.executable, "-m", "benchmarks.attention_harness.formal_attention_cli",
        "--suite", "robosuite", "--task", "cube_lift", "--seed", "101",
        "--attention-policy", "autonomous", "--code", str(code),
        "--approved-policy-sha256", _sha(code), "--config", str(config),
        "--approved-config-sha256", _sha(config), "--artifact-root", str(tmp_path),
        "--service-source-root", str(tmp_path),
        "--dev-generation-artifact", str(receipt),
    ], capture_output=True, text=True)
    assert result.returncode == 2
    assert "Dev generation receipt does not match approved source" in result.stderr


def test_ui_launch_locks_selection_and_rejects_other_targets(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_bytes((Path(__file__).resolve().parents[1] / "protocol/v2/formal_robosuite_cube_lift_seed101.json").read_bytes())
    service = tmp_path / "service"
    service.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"profiles": [{
        "id": "lift", "suite": "robosuite", "task": "cube_lift",
        "execution_target": "robosuite_sim", "code": str(code),
        "approved_policy_sha256": _sha(code), "config": str(config),
        "approved_config_sha256": _sha(config), "service_source_root": str(service),
    }]}))
    profiles = load_catalog(catalog)
    selection = {"profile_id": "lift", "seed": 101, "attention_policy": "autonomous",
                 "execution_target": "robosuite_sim", "assistance_mode": "benchmark_proxy",
                 "max_attempts": 2, "assistance_credits": 1, "token_limit": 100,
                 "human_deadline_seconds": 30, "overall_deadline_seconds": 90}
    store = AttentionStore(tmp_path / "store.sqlite3")

    class Process:
        pid = 12345

    commands = []
    launched = launch_formal_run(selection, profiles=profiles, store=store,
                                 artifact_root=tmp_path / "artifacts",
                                 popen=lambda command, **kwargs: commands.append(command) or Process())
    assert launched["locked"]["formal_eligible"] is False
    assert store.list_launches()[0]["locked"] == launched["locked"]
    assert "--seed" in commands[0] and "101" in commands[0]
    assert "benchmarks.attention_harness.ui_launch_worker" in commands[0]
    with pytest.raises(ValueError, match="target"):
        launch_formal_run({**selection, "execution_target": "real_robot"}, profiles=profiles,
                          store=store, artifact_root=tmp_path / "artifacts")
    with pytest.raises(PermissionError):
        launch_formal_run({**selection, "seed": 1001}, profiles=profiles,
                          store=store, artifact_root=tmp_path / "artifacts")
    code.write_text("changed\n")
    with pytest.raises(ValueError, match="changed"):
        launch_formal_run(selection, profiles=profiles, store=store,
                          artifact_root=tmp_path / "artifacts")


def test_ui_launch_worker_persists_terminal_receipt_across_store_reopen(tmp_path):
    store_path = tmp_path / "store.sqlite3"
    store = AttentionStore(store_path)
    launch_id = "launch:test"
    store.record_launch(launch_id, locked={"formal_eligible": False},
                        log_path=str(tmp_path / "launch.log"))
    summary_path = tmp_path / "summary.json"
    script = ("import json,sys; from pathlib import Path; "
              "Path(sys.argv[1]).write_text(json.dumps("
              "{'artifact_dir': sys.argv[2], 'native_success': False, "
              "'formal_eligible': False})); sys.exit(1)")
    run_dir = tmp_path / "attention-robosuite-cube_lift-seed101-test"
    command = [sys.executable, "-m", "benchmarks.attention_harness.ui_launch_worker",
               "--store-path", str(store_path), "--launch-id", launch_id,
               "--summary-path", str(summary_path), "--", sys.executable, "-c", script,
               str(summary_path), str(run_dir)]
    assert subprocess.run(command, check=False).returncode == 1
    store.record_launch_started(launch_id, pid=12345)
    receipt = AttentionStore(store_path).list_launches()[0]
    assert receipt["state"] == "completed"
    assert receipt["exit_code"] == 1
    assert receipt["native_success"] is False
    assert receipt["run_id"] == f"run:{run_dir.name}"
    assert receipt["formal_eligible"] is False


def test_ui_launch_worker_records_failed_process_without_summary(tmp_path):
    store_path = tmp_path / "store.sqlite3"
    store = AttentionStore(store_path)
    store.record_launch("launch:failure", locked={"formal_eligible": False},
                        log_path=str(tmp_path / "launch.log"))
    command = [sys.executable, "-m", "benchmarks.attention_harness.ui_launch_worker",
               "--store-path", str(store_path), "--launch-id", "launch:failure",
               "--summary-path", str(tmp_path / "missing.json"), "--",
               sys.executable, "-c", "import sys; sys.exit(3)"]
    assert subprocess.run(command, check=False).returncode == 3
    receipt = AttentionStore(store_path).list_launches()[0]
    assert receipt["state"] == "failed"
    assert receipt["exit_code"] == 3
    assert receipt["run_id"] is None
