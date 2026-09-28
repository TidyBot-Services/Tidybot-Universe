"""The shared scheduler must consume validated formal attempts, not dev callbacks."""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

from benchmarks.attention_harness.core.policies import POLICY_IDS
from benchmarks.attention_harness.core.models import MemoryRecord, MemoryStatus
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from demo_fixture import approved_demo

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "skill-agent-setup/claude-code"))
from attention_eval import build_eval_packet, diagnose_attention, record_eval_failure
from benchmarks.attention_harness.ui_eval import eval_snapshot


class FakeFormalRunner:
    def __init__(self, suite: str, *, mismatch: bool = False):
        self.suite = suite
        self.mismatch = mismatch
        self.requests = []
        self.cancel_event = None

    def execute(self, request):
        self.requests.append(request)
        episode = request.artifact_root / f"formal-{len(self.requests)}"
        episode.mkdir(parents=True)
        identity = {"run_id": request.run_id,
                    "attempt_id": "attempt:wrong" if self.mismatch else request.attempt_id}
        values = {
            "trace": {"schema_version": "attentionbench.formal-trace.v1",
                      "suite": self.suite, "task_id": request.task_id, "seed": request.seed,
                      "policy_sha256": request.policy_sha256,
                      "config_sha256": request.config_sha256,
                      "status": "completed", "sdk_events": [{
                          "event_type": "robot.action", "operation": "find_objects",
                          "status": "completed", "timestamp": 1.0}], **identity},
            "safety": {"source": "independent_safety_monitor", "unsafe_attempts": 0,
                       **identity},
            "sandbox_receipt": {"elapsed_seconds": 0.1, "service_stop": (
                {"reason": "normal_cleanup", "leader_reaped": True, "process_group_gone": True}
                if self.suite == "robosuite" else
                {"reason": "normal_cleanup", "services": {
                    "simulator": {"leader_reaped": True, "process_group_gone": True},
                    "agent": {"leader_reaped": True, "process_group_gone": True}}}), **identity},
            "native_result": {"status": "completed", "native_success": False,
                              "evaluated": True, **identity},
        }
        refs = {}
        for name, value in values.items():
            path = episode / f"{name}.json"
            path.write_text(json.dumps(value))
            refs[name] = {"uri": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        result = {
            "schema_version": "attentionbench.formal-runner-result.v1",
            "suite": self.suite, "task_id": request.task_id, "seed": request.seed,
            "policy_sha256": request.policy_sha256,
            "config_sha256": request.config_sha256,
            "status": "completed", "native_success": False,
            "native_evaluator": {"native_success": False, "evaluated": True},
            "sandbox": {"process_isolated": True, "sdk_rpc_only": True,
                        "deadline_enforced": True, "action_cancellation_verified": True},
            "artifact_dir": str(episode), "artifacts": refs, "formal_eligible": False,
            **identity,
        }
        (episode / "result.json").write_text(json.dumps(result))
        return result


def test_interrupt_after_formal_completion_is_reported_too_late(tmp_path):
    class LateRequestRunner(FakeFormalRunner):
        def execute(self, request):
            result = super().execute(request)
            store = AttentionStore(request.artifact_root.parent / "attention.sqlite3")
            store.request_interrupt(request.run_id, event_key="late-request",
                                    requested_at=time.time())
            return result

    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text("{}")
    summary = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101,
        artifact_root=tmp_path / "runs", policy_id="autonomous",
        policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config,
        approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=LateRequestRunner("robosuite"), max_attempts=1, assistance_credits=0,
    )
    run_id = f"run:{Path(summary['artifact_dir']).name}"
    store = AttentionStore(Path(summary["store"]))
    ack = store.interrupt_status(run_id)
    assert ack["state"] == "too_late" and "confirmed_at" not in ack
    assert ack["service_stop"]["reason"] == "normal_cleanup"
    assert summary["stopped_reason"] == "interrupt_too_late"
    assert store.get_run(run_id)["status"] == "failed"
    assert build_eval_packet(Path(summary["artifact_dir"]) / "attention_run.json")[
        "stopped_reason"] == "interrupt_too_late"


def test_eval_rejects_false_formal_interrupt_completion(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text("{}")
    summary = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101,
        artifact_root=tmp_path / "runs", policy_id="autonomous",
        policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config,
        approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=FakeFormalRunner("robosuite"), max_attempts=1, assistance_credits=0,
    )
    artifact = Path(summary["artifact_dir"]) / "attention_run.json"
    run = json.loads(artifact.read_text())
    run["stopped_reason"] = "emergency_interrupt"
    artifact.write_text(json.dumps(run))
    with pytest.raises(ValueError, match="interrupt differs"):
        build_eval_packet(artifact)


@pytest.mark.parametrize("suite,task", [("robocasa", "counter_to_sink"),
                                         ("robosuite", "cube_lift")])
@pytest.mark.parametrize("policy_id", POLICY_IDS)
def test_seven_policies_call_suite_formal_runner_and_eval_reads_evidence(
    tmp_path, suite, task, policy_id,
):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text('{}')
    runner = FakeFormalRunner(suite)
    summary = run_formal_attention(
        suite=suite, task_id=task, seed=101, artifact_root=tmp_path / "runs",
        policy_id=policy_id, policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=runner, max_attempts=1, assistance_credits=0,
        demo_prior=(demo := approved_demo(tmp_path, suite, task)) if policy_id == "demo_first" else None,
        approved_demo_sha256=hashlib.sha256(demo.encode()).hexdigest() if policy_id == "demo_first" else None,
        policy_config={"target_request_count": 0} if policy_id == "budget_matched_random_escalation" else None,
    )
    assert len(runner.requests) == 1
    attempt = summary["attempts"][0]
    assert attempt["formal_runner_result"]["boundary_checked"] is True
    assert len(summary["scheduler_config_sha256"]) == 64
    assert attempt["attention_trace"]["run_id"] == f"run:{Path(summary['artifact_dir']).name}"
    assert AttentionStore(Path(summary["store"])).get_raw_trace(
        attempt["attention_trace"]["raw_trace_id"])["metadata"]["runtime"]["config_sha256"] \
        == summary["approved_config_sha256"]
    packet = build_eval_packet(Path(summary["artifact_dir"]) / "attention_run.json")
    assert packet["attempts"][0]["events"][-1]["event_id"] == "formal-native-result"
    assert summary["formal_eligible"] is False


@pytest.mark.parametrize("suite,task", [("robocasa", "counter_to_sink"),
                                         ("robosuite", "cube_lift")])
def test_formal_identity_mismatch_aborts_without_dev_fallback(tmp_path, suite, task):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text('{}')
    runner = FakeFormalRunner(suite, mismatch=True)
    with pytest.raises(RuntimeError, match="formal attempt rejected"):
        run_formal_attention(
            suite=suite, task_id=task, seed=101, artifact_root=tmp_path / "runs",
            policy_id="reactive_help", policy_code_path=code,
            approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
            config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
            runner=runner, max_attempts=2, assistance_credits=0,
        )
    assert len(runner.requests) == 1


def test_eval_rejects_modified_formal_trace(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text('{}')
    summary = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101, artifact_root=tmp_path / "runs",
        policy_id="reactive_help", policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=FakeFormalRunner("robosuite"), max_attempts=1, assistance_credits=0,
    )
    trace = Path(summary["attempts"][0]["formal_runner_result"]["artifacts"]["trace"]["uri"])
    trace.write_text(trace.read_text() + " ")
    with pytest.raises(ValueError, match="digest mismatch"):
        build_eval_packet(Path(summary["artifact_dir"]) / "attention_run.json")


def test_diagnostic_eval_consumes_formal_artifacts(tmp_path):
    from types import SimpleNamespace

    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text('{}')
    summary = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101, artifact_root=tmp_path / "runs",
        policy_id="reactive_help", policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=FakeFormalRunner("robosuite"), max_attempts=1, assistance_credits=0,
    )
    attempt_id = summary["attempts"][0]["attention_trace"]["attempt_id"]

    class EvalClient:
        def chat(self, **kwargs):
            assert "formal-native-result" in kwargs["messages"][0]["content"]
            return SimpleNamespace(content=f"{attempt_id}: formal-native-result reports failure.",
                                   usage={"total_tokens": 10}, attempts=1)

    result = diagnose_attention(Path(summary["artifact_dir"]) / "attention_run.json",
                                client=EvalClient())
    assert result["native_success"] is False
    assert Path(result["artifact"]).is_file()


def test_eval_checks_fourth_attempt_and_service_recovery_semantics(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text("{}")
    summary = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101, artifact_root=tmp_path / "runs",
        policy_id="reactive_help", policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=FakeFormalRunner("robosuite"), max_attempts=1, assistance_credits=0,
    )
    artifact = Path(summary["artifact_dir"]) / "attention_run.json"
    receipt_ref = summary["attempts"][0]["formal_runner_result"]["artifacts"]["sandbox_receipt"]
    receipt_path = Path(receipt_ref["uri"])
    receipt = json.loads(receipt_path.read_text())
    receipt["service_stop"] = {"leader_reaped": False, "process_group_gone": False}
    receipt_path.write_text(json.dumps(receipt))
    receipt_ref["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    artifact.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="Service recovery"):
        build_eval_packet(artifact)
    receipt["service_stop"] = {"leader_reaped": True, "process_group_gone": True}
    receipt_path.write_text(json.dumps(receipt))
    receipt_ref["sha256"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    summary["attempts"].extend([summary["attempts"][0]] * 2)
    summary["attempts"].append({"attention_trace": {"run_id": "wrong", "attempt_id": "attempt:four"}})
    artifact.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match="identity or approval"):
        build_eval_packet(artifact)


def test_failed_diagnostic_receipt_is_ui_visible_without_changing_native_result(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text("{}")
    summary = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101, artifact_root=tmp_path / "runs",
        policy_id="reactive_help", policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=FakeFormalRunner("robosuite"), max_attempts=1, assistance_credits=0,
    )
    artifact = Path(summary["artifact_dir"]) / "attention_run.json"
    receipt = record_eval_failure(artifact, TimeoutError("diagnostic deadline"))
    assert receipt["status"] == "failed"
    run_id = f"run:{Path(summary['artifact_dir']).name}"
    visible = eval_snapshot(tmp_path / "runs", run_id,
                            {"suite": "robosuite", "task_id": "cube_lift", "seed": 101,
                             "status": "failed"}, Path(summary["store"]))
    assert visible["status"] == "failed" and visible["native_success"] is False
    assert "artifacts" not in visible and "trace" not in visible
    receipt["native_success"] = True
    (artifact.parent / "eval_diagnosis.json").write_text(json.dumps(receipt))
    assert eval_snapshot(tmp_path / "runs", run_id,
                         {"suite": "robosuite", "task_id": "cube_lift", "seed": 101,
                          "status": "failed"}, Path(summary["store"]))["status"] == "evidence_invalid"


def test_formal_scheduler_links_advisor_response_to_next_formal_attempt(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text('{}')
    runner = FakeFormalRunner("robosuite")

    def advisor(request):
        return json.dumps({
            "schema_version": "attentionbench.advisor-advice.v1",
            "request_type": "hint", "diagnosis": "grasp absent",
            "guidance": "close after alignment", "caution": "stay within limits",
            "confidence": 0.8,
        })

    summary = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101, artifact_root=tmp_path / "runs",
        policy_id="reactive_help", policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=runner, max_attempts=2, assistance_credits=1,
        advisor_transport=advisor, sleeper=lambda _: None,
    )
    assert len(runner.requests) == 2
    assert runner.requests[1].attention_input["advisor_guidance"] == "close after alignment"
    assert summary["requests"][0]["request_id"]
    events = AttentionStore(Path(summary["store"])).events()
    assert any(event["event_type"] == "response.execution_linked" for event in events)
    assert all(item["formal_runner_result"]["boundary_checked"] for item in summary["attempts"])


def test_formal_memory_use_records_grant_and_guidance(tmp_path):
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    context = {"suite": "robosuite", "task_id": "cube_lift", "perception_mode": "sim_gt",
               "scene_id": "scene:a", "object_set_id": "objects:a",
               "camera_config_id": "camera:a", "task_variant_id": "task:a",
               "camera_names": ["agentview"], "task_prompt": "lift the cube"}
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"scene_id": "scene:a", "object_set_id": "objects:a",
                                  "camera_name": "agentview"}))
    memory = MemoryRecord(
        memory_id="memory:one", version=1, source_trace_id="trace:source",
        guidance="close after alignment", candidate_repair="grasp repair",
        applicability=context, evidence_refs=("evidence:source",), created_at=1,
        status=MemoryStatus.TRUSTED,
    )

    class Gateway:
        uses = []

        def retrieve(self, queried, *, now):
            assert queried == context
            return [memory]

        def get_memory(self, memory_id):
            assert memory_id == memory.memory_id
            return memory

        def authorize_use(self, queried, *, memory_id, attempt_id, now):
            assert queried == context
            return {"grant_id": "grant:one", "memory_id": memory_id,
                    "version": 1, "attempt_id": attempt_id, "used_at": now}

        def record_use(self, use):
            self.uses.append(use)

    gateway = Gateway()
    runner = FakeFormalRunner("robosuite")
    summary = run_formal_attention(
        suite="robosuite", task_id="cube_lift", seed=101, artifact_root=tmp_path / "runs",
        policy_id="autonomous", policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config, approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=runner, max_attempts=2, assistance_credits=0,
        memory_context=context, memory_gateway=gateway,
    )
    assert len(runner.requests) == 2
    assert runner.requests[1].attention_input["memory_guidance"] == {
        "memory:one": "close after alignment"}
    assert len(gateway.uses) == 1
    assert gateway.uses[0].attempt_id == summary["attempts"][1]["attention_trace"]["attempt_id"]
    assert summary["attempts"][1]["memory_ids"] == ["memory:one"]
