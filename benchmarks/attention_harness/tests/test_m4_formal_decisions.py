"""M4 decisions exercised across the shared scheduler and both formal boundaries."""

import hashlib
import json
from pathlib import Path

import pytest

from benchmarks.attention_harness.core.advisor import AdvisorTransportReply
from benchmarks.attention_harness.core.policies import POLICY_IDS
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from demo_fixture import approved_demo
from test_formal_attention_chain import FakeFormalRunner


SUITES = (("robocasa", "counter_to_sink"), ("robosuite", "cube_lift"))


def run_case(tmp_path, suite, task, policy, *, runner=None, max_attempts=3,
             credits=1, tokens=8000, policy_config=None, transport=None,
             memory_context=None, memory_gateway=None, config_payload=None,
             safety_signals=None, approval_granted=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(config_payload or {}))
    runner = runner or FakeFormalRunner(suite)
    sent = []

    def reply(request):
        sent.append(request)
        if transport:
            return transport(request)
        kind = json.loads(request["messages"][1]["content"])["request_type"]
        return AdvisorTransportReply(
            content=json.dumps({"schema_version": "attentionbench.advisor-advice.v1",
                                "request_type": kind, "diagnosis": "visible failure",
                                "guidance": "retry using public trace", "caution": "respect safety",
                                "confidence": 0.5}),
            model="parcc/GLM", latency_seconds=0, attempts=1,
            usage={"total_tokens": 11},
        )

    options = {}
    if policy == "demo_first":
        demo = approved_demo(tmp_path, suite, task)
        options.update(demo_prior=demo,
                       approved_demo_sha256=hashlib.sha256(demo.encode()).hexdigest())
    summary = run_formal_attention(
        suite=suite, task_id=task, seed=101, artifact_root=tmp_path / "runs",
        policy_id=policy, policy_code_path=code,
        approved_policy_sha256=hashlib.sha256(code.read_bytes()).hexdigest(),
        config_path=config,
        approved_config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
        runner=runner, max_attempts=max_attempts, assistance_credits=credits,
        token_limit=tokens, policy_config=policy_config,
        advisor_transport=reply, sleeper=lambda _: None,
        memory_context=memory_context, memory_gateway=memory_gateway, **options,
        safety_signals=safety_signals, approval_granted=approval_granted,
    )
    return summary, runner, sent


@pytest.mark.parametrize("suite,task", SUITES)
@pytest.mark.parametrize("policy", POLICY_IDS)
def test_formal_decision_and_execution_matrix(tmp_path, suite, task, policy):
    config = ({"k": 2} if policy == "retry_k_then_ask" else
              {"target_request_count": 1, "total_failure_slots": 2, "seed": 101}
              if policy == "budget_matched_random_escalation" else None)
    summary, runner, sent = run_case(tmp_path, suite, task, policy,
                                    policy_config=config)
    assert summary["formal_eligible"] is False
    assert len(runner.requests) == 3
    assert all(row["formal_runner_result"]["boundary_checked"] for row in summary["attempts"])
    assert all(row["status"] == "completed" for row in summary["attempts"])
    assert all(row["native_success"] is False for row in summary["attempts"])
    if policy == "demo_first":
        assert summary["demo_receipt"] and summary["decisions"][0]["action"] == "use_demo"
        assert "demo_prior" in runner.requests[0].attention_input
        assert all("demo_prior" in request.attention_input for request in runner.requests)
    if policy in {"autonomous", "demo_first"}:
        assert not sent and not summary["requests"]
    else:
        assert len(sent) == len(summary["requests"]) == 1
        request = summary["requests"][0]
        slot = request["failure_slot"]
        assert runner.requests[slot + 1].attention_input["advisor_guidance"] == "retry using public trace"
        assert summary["resource_usage"] == {"assistance_credits": 1, "tokens": 11}
        events = AttentionStore(Path(summary["store"])).events()
        assert any(row["event_type"] == "response.execution_linked" for row in events)
        assert any(row["event_type"] == "response.used" for row in events)
        if policy == "reactive_help":
            assert slot == 0
        if policy == "retry_k_then_ask":
            assert slot == 1
        if policy == "budget_matched_random_escalation":
            assert summary["random_preregistration"]["selected_slots"] == [slot]
            assert summary["random_matching"]["realized_slots"] == [slot]
            prereg = Path(summary["artifact_dir"]) / "random_preregistration.json"
            assert prereg.is_file()
            assert hashlib.sha256(prereg.read_bytes()).hexdigest() == summary["random_preregistration"]["sha256"]
        if policy.startswith("trace_aware") or policy.startswith("full_trace"):
            decision = next(d for d in summary["decisions"] if d.get("after_attempt") == slot)
            assert decision["assessment"]["trace_id"]
            assert decision["assessment"]["prior_trace_ids"] if slot > 0 else True
            assert "native_success" not in json.dumps(sent[0])


@pytest.mark.parametrize("suite,task", SUITES)
def test_zero_token_budget_does_not_call_advisor(tmp_path, suite, task):
    summary, runner, sent = run_case(tmp_path, suite, task, "reactive_help",
                                    max_attempts=2, tokens=0)
    assert len(runner.requests) == 1
    assert not sent and not summary["requests"]
    assert summary["stopped_reason"] == "advisor_token_budget_exhausted"


@pytest.mark.parametrize("suite,task", SUITES)
def test_hint_only_never_probes_memory_service(tmp_path, suite, task):
    class NoMemory:
        def retrieve(self, *args, **kwargs):
            raise AssertionError("hint_only queried Memory")

    context = {"suite": suite, "task_id": task, "perception_mode": "sim_gt",
               "scene_id": "a", "object_set_id": "b", "camera_config_id": "c",
               "task_variant_id": "d", "camera_names": ["agentview"], "task_prompt": "test"}
    # Matching approved simulator fields are required before the attempt.
    summary, runner, _ = run_case(tmp_path, suite, task, "trace_aware_hint_only",
                                  memory_context=context, memory_gateway=NoMemory(),
                                  config_payload={"scene_id": "a", "object_set_id": "b",
                                                  "camera_names": ["agentview"], "task_prompt": "test",
                                                  "camera_name": "agentview"},
                                  max_attempts=1)
    assert len(runner.requests) == 1
    assert summary["attempts"][0]["memory_ids"] == []


@pytest.mark.parametrize("suite,task", SUITES)
def test_full_approval_is_explicit_and_enters_next_attempt(tmp_path, suite, task):
    summary, runner, sent = run_case(
        tmp_path, suite, task, "full_trace_aware_attention_planner",
        max_attempts=2, safety_signals=lambda _: {"approval_required": True},
        approval_granted=lambda _: True,
    )
    assert len(sent) == 1
    assert summary["decisions"][0]["request_type"] == "approval"
    assert summary["requests"][0]["responder"] == "advisor_proxy"
    assert runner.requests[1].attention_input["approval_granted"] is True
    assert summary["formal_eligible"] is False


@pytest.mark.parametrize("suite,task", SUITES)
def test_independent_unsafe_stops_without_model(tmp_path, suite, task):
    summary, runner, sent = run_case(
        tmp_path, suite, task, "full_trace_aware_attention_planner",
        safety_signals=lambda _: {"unsafe": True},
    )
    assert len(runner.requests) == 1
    assert not sent and not summary["requests"]
    assert summary["decisions"][0]["reason"] == "independent_safety_stop"
    assert summary["stopped_reason"] == "independent_safety_stop"


@pytest.mark.parametrize("suite,task", SUITES)
def test_incomplete_attempt_still_records_independent_unsafe(tmp_path, suite, task):
    class IncompleteUnsafeRunner(FakeFormalRunner):
        def execute(self, request):
            result = super().execute(request)
            result["status"] = "failed"
            result["native_evaluator"]["evaluated"] = False
            for name in ("trace", "native_result"):
                path = Path(result["artifacts"][name]["uri"])
                payload = json.loads(path.read_text())
                payload["status"] = "failed"
                if name == "native_result":
                    payload["evaluated"] = False
                path.write_text(json.dumps(payload))
                result["artifacts"][name]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            safety_path = Path(result["artifacts"]["safety"]["uri"])
            safety = json.loads(safety_path.read_text())
            safety["unsafe_attempts"] = 1
            safety_path.write_text(json.dumps(safety))
            result["artifacts"]["safety"]["sha256"] = hashlib.sha256(safety_path.read_bytes()).hexdigest()
            return result

    summary, runner, sent = run_case(
        tmp_path, suite, task, "reactive_help", runner=IncompleteUnsafeRunner(suite),
        max_attempts=2,
    )
    assert len(runner.requests) == 1 and not sent
    assert summary["stopped_reason"] == "independent_safety_stop"
    assert summary["decisions"][0]["reason"] == "independent_safety_stop"


@pytest.mark.parametrize("suite,task", SUITES)
def test_wrong_scope_memory_is_not_selected(tmp_path, suite, task):
    from benchmarks.attention_harness.core.models import MemoryRecord, MemoryStatus

    context = {"suite": suite, "task_id": task, "perception_mode": "sim_gt",
               "scene_id": "scene:approved", "object_set_id": "objects:approved",
               "camera_config_id": "camera:a", "task_variant_id": "task:a",
               "camera_names": ["agentview"], "task_prompt": "test"}
    wrong = {**context, "scene_id": "scene:wrong"}
    memory = MemoryRecord(
        memory_id="memory:wrong", version=1, source_trace_id="trace:source",
        guidance="never use this", candidate_repair="wrong scene",
        applicability=wrong, evidence_refs=("evidence:source",), created_at=1,
        status=MemoryStatus.TRUSTED,
    )

    class Gateway:
        def retrieve(self, *_args, **_kwargs):
            return [memory]

        def get_memory(self, memory_id):
            assert memory_id == memory.memory_id
            return memory

        def authorize_use(self, *_args, **_kwargs):
            raise AssertionError("wrong-scope Memory authorized")

    summary, runner, _ = run_case(
        tmp_path, suite, task, "full_trace_aware_attention_planner",
        memory_context=context, memory_gateway=Gateway(), max_attempts=2,
        config_payload={"scene_id": "scene:approved", "object_set_id": "objects:approved",
                        "camera_names": ["agentview"], "task_prompt": "test",
                        "camera_name": "agentview"},
    )
    assert len(runner.requests) == 2
    assert not any("memory_ids_to_use" in request.attention_input for request in runner.requests)
    assert all(not row.get("trusted_memory_ids") for row in summary["decisions"])


@pytest.mark.parametrize("suite,task", SUITES)
def test_full_uses_only_granted_applicable_memory(tmp_path, suite, task):
    from benchmarks.attention_harness.core.models import MemoryRecord, MemoryStatus

    context = {"suite": suite, "task_id": task, "perception_mode": "sim_gt",
               "scene_id": "scene:approved", "object_set_id": "objects:approved",
               "camera_config_id": "camera:a", "task_variant_id": "task:a",
               "camera_names": ["agentview"], "task_prompt": "test"}
    memory = MemoryRecord(
        memory_id="memory:approved", version=2, source_trace_id="trace:source",
        guidance="use the visible grasp correction", candidate_repair="grasp",
        applicability={key: context[key] for key in ("suite", "task_id", "perception_mode")},
        evidence_refs=("evidence:source",), created_at=1,
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
            assert queried == context and memory_id == memory.memory_id
            return {"grant_id": "grant:approved", "memory_id": memory_id,
                    "version": 2, "attempt_id": attempt_id, "used_at": now}

        def record_use(self, record):
            self.uses.append(record)

    gateway = Gateway()

    class ConcreteFailureRunner(FakeFormalRunner):
        def execute(self, request):
            result = super().execute(request)
            trace_path = Path(result["artifacts"]["trace"]["uri"])
            trace = json.loads(trace_path.read_text())
            trace["sdk_events"][0].update(
                status="failed", error={"type": "GraspFailure", "message": "visible grasp failed"})
            trace_path.write_text(json.dumps(trace))
            result["artifacts"]["trace"]["sha256"] = hashlib.sha256(trace_path.read_bytes()).hexdigest()
            return result

    summary, runner, sent = run_case(
        tmp_path, suite, task, "full_trace_aware_attention_planner",
        runner=ConcreteFailureRunner(suite), memory_context=context,
        memory_gateway=gateway, max_attempts=2,
        config_payload={"scene_id": "scene:approved", "object_set_id": "objects:approved",
                        "camera_names": ["agentview"], "task_prompt": "test",
                        "camera_name": "agentview"},
    )
    assert len(runner.requests) == 2 and not sent
    assert summary["decisions"][0]["action"] == "retrieve_memory"
    assert runner.requests[1].attention_input["memory_ids_to_use"] == [memory.memory_id]
    assert runner.requests[1].attention_input["memory_guidance"][memory.memory_id] == memory.guidance
    assert summary["attempts"][1]["memory_ids"] == [memory.memory_id]
    assert len(gateway.uses) == 1
    assert gateway.uses[0].attempt_id == summary["attempts"][1]["attention_trace"]["attempt_id"]


@pytest.mark.parametrize("suite,task", SUITES)
def test_oracle_event_cannot_steer_formal_trace_decision(tmp_path, suite, task):
    class OracleRunner(FakeFormalRunner):
        def execute(self, request):
            result = super().execute(request)
            trace_path = Path(result["artifacts"]["trace"]["uri"])
            trace = json.loads(trace_path.read_text())
            trace["sdk_events"].append({
                "source": "evaluator", "event_type": "oracle.grasp_failure",
                "operation": "private_state", "status": "failed", "timestamp": 2,
                "oracle_state": {"hidden_success": False},
            })
            trace_path.write_text(json.dumps(trace))
            result["artifacts"]["trace"]["sha256"] = hashlib.sha256(trace_path.read_bytes()).hexdigest()
            return result

    clean, _, _ = run_case(tmp_path / "clean", suite, task, "trace_aware_hint_only",
                           max_attempts=2, credits=0)
    injected, _, _ = run_case(tmp_path / "injected", suite, task, "trace_aware_hint_only",
                              runner=OracleRunner(suite), max_attempts=2, credits=0)
    assert [(d["action"], d["reason"]) for d in clean["decisions"]] == [
        (d["action"], d["reason"]) for d in injected["decisions"]]
    assert "oracle" not in json.dumps(injected["decisions"])
