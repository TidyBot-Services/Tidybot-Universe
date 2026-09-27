from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.robocasa_native.generated_policy_runner import (
    run_robocasa_generated_policy_episode, run_robocasa_generated_policy_sequence,
)
from benchmarks.attention_harness.robocasa_native.policy_sandbox import (
    GeneratedPolicyError, GeneratedPolicyTimeout, execute_generated_policy,
    validate_generated_policy,
)
from benchmarks.attention_harness.tests.test_memory_v2 import _case, _trusted_for_revocation_test
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.robocasa_native.agent_actions import AgentServerActionBackend
from benchmarks.attention_harness.robocasa_native.safety_monitor import SafetyMonitorBackend
from benchmarks.attention_harness.robocasa_native.sim_gt_runner import _public_attempt_history
from benchmarks.attention_harness.tests.test_agent_actions_v2 import AgentTransport
from benchmarks.attention_harness.tests.test_sim_gt_runner import FakeActionBackend, FakeWorld
from benchmarks.attention_harness.v2_advisor import (
    SimGTAdvisorProxy, SimGTGLMAdvisorTransport, semantic_advisor_cache_key,
)
from benchmarks.attention_harness.parcc_client import ParccResponse


class FakeSDK:
    class Gripper:
        def __init__(self):
            self.calls = 0

        def close(self):
            self.calls += 1

    def __init__(self):
        self.gripper = self.Gripper()


def test_generated_policy_worker_rpc_and_timeout():
    sdk = FakeSDK()
    result = execute_generated_policy(
        code="from robot_sdk import gripper\ngripper.close()\n",
        sdk=sdk, context={"task_id": "counter_to_sink"}, timeout_seconds=5,
    )
    assert result.status == "completed"
    assert result.call_count == 1
    assert sdk.gripper.calls == 1
    with pytest.raises(GeneratedPolicyError):
        validate_generated_policy("import os\nos.system('true')")
    with pytest.raises(GeneratedPolicyTimeout):
        execute_generated_policy(
            code="from robot_sdk import gripper\nwhile True: pass\n",
            sdk=sdk, context={}, timeout_seconds=0.2,
        )
    retrieved = []
    result = execute_generated_policy(
        code=("from robot_sdk import gripper\n"
              "item = context['retrieve_memory']('known')\n"
              "if item['guidance'] == 'close': gripper.close()\n"),
        sdk=sdk,
        context={"retrieve_memory": lambda memory_id: (
            retrieved.append(memory_id) or {"guidance": "close"})},
        timeout_seconds=5,
    )
    assert result.call_count == 2
    assert retrieved == ["known"]


def test_generated_policy_uses_trusted_memory_with_service_grant(tmp_path: Path):
    shared, service, memory_id = _trusted_for_revocation_test(tmp_path)
    code = tmp_path / "memory_policy.py"
    code.write_text(
        "from robot_sdk import gripper\n"
        f"item = context['retrieve_memory']({memory_id!r})\n"
        "if item['version'] == 1: gripper.close()\n",
        encoding="utf-8",
    )
    world = FakeWorld()
    result = run_robocasa_generated_policy_episode(
        task_id="counter_to_sink", seed=107, artifact_root=tmp_path,
        action_backend=FakeActionBackend(world), policy_code_path=code,
        policy_id="generated-memory-use", perception_mode="sim_gt",
        client=RobocasaSimClient("counter_to_sink", transport=world.transport),
        store_path=shared, memory_gateway=service, timeout_seconds=10.0,
        runtime_variation={key: value for key, value in _case(102).items() if key != "seed"},
    )
    assert result["native_success"] is True
    assert result["memory_ids"] == [memory_id]
    use = service.store.list_memory_uses(memory_id)
    assert len(use) == 1
    assert use[0]["memory_version"] == 1 and use[0]["outcome"] == "native_success"
    raw = service.store.get_raw_trace(result["attention_trace"]["raw_trace_id"])
    event = next(item for item in raw["events"] if item["event_type"] == "attention.memory_retrieval")
    grant = service.v2.get_use_grant(event["result"]["grant_id"])
    assert grant["attempt_id"] == result["attention_trace"]["attempt_id"]
    assert grant["used_at"] == use[0]["used_at"]
    assert grant["context"] == raw["metadata"]["runtime"]["memory_context"]


def test_generated_robocasa_trace_contains_code_hypothesis_history(tmp_path: Path):
    world = FakeWorld()
    code = tmp_path / "policy_source.py"
    code.write_text("from robot_sdk import gripper\ngripper.close()\n", encoding="utf-8")
    result = run_robocasa_generated_policy_episode(
        task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
        action_backend=FakeActionBackend(world), policy_code_path=code,
        policy_id="generated-test", perception_mode="sim_gt",
        client=RobocasaSimClient("counter_to_sink", transport=world.transport),
        hypothesis="Closing the gripper should finish the task.",
        prior_attempts=[{"attempt_index": 0, "failure_stage": "grasp",
                         "observed_symptom": "The object was not held."}],
    )
    assert result["native_success"] is True
    assert result["formal_eligible"] is False
    assert result["trusted_policy_callback"] is False
    store = AttentionStore(Path(result["attention_trace"]["store"]))
    raw = store.get_raw_trace(result["attention_trace"]["raw_trace_id"])
    assert raw["hypothesis"].startswith("Closing the gripper")
    assert "gripper.close()" in raw["code"]["content"]
    assert any(event["event_type"] == "attention.prior_attempts" for event in raw["events"])
    assert json.loads((Path(result["artifact_dir"]) / "generated_policy_execution.json").read_text())["call_count"] == 1


def test_invalid_generated_policy_rejected_before_simulator_reset(tmp_path: Path):
    world = FakeWorld()
    code = tmp_path / "invalid.py"
    code.write_text("import os\n", encoding="utf-8")
    with pytest.raises(GeneratedPolicyError):
        run_robocasa_generated_policy_episode(
            task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
            action_backend=FakeActionBackend(world), policy_code_path=code,
            policy_id="invalid", perception_mode="sim_gt",
            client=RobocasaSimClient("counter_to_sink", transport=world.transport),
        )
    assert world.calls == []


def test_backend_deadline_cannot_exceed_policy_deadline(tmp_path: Path):
    world = FakeWorld()
    backend = FakeActionBackend(world)
    backend.timeout_seconds = 90.0
    code = tmp_path / "move.py"
    code.write_text("from robot_sdk import arm\narm.move_delta(0.01, 0, 0)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="backend timeout"):
        run_robocasa_generated_policy_episode(
            task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
            action_backend=backend, policy_code_path=code,
            policy_id="deadline-test", perception_mode="sim_gt",
            client=RobocasaSimClient("counter_to_sink", transport=world.transport),
            timeout_seconds=0.6,
        )
    assert world.calls == []


def test_safety_wrapped_backend_keeps_cancellation_preflight(tmp_path: Path):
    world = FakeWorld()
    code = tmp_path / "no_action.py"
    code.write_text("from robot_sdk import gripper\n", encoding="utf-8")
    transport = AgentTransport()
    backend = AgentServerActionBackend(
        simulator_attested=True, transport=transport, timeout_seconds=0.5,
    )
    wrapped = SafetyMonitorBackend(backend)
    result = run_robocasa_generated_policy_episode(
        task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
        action_backend=wrapped, policy_code_path=code,
        policy_id="wrapped-preflight", perception_mode="sim_gt",
        client=RobocasaSimClient("counter_to_sink", transport=world.transport),
        timeout_seconds=2.0,
    )
    assert result["generated_policy"]["service_cancellation_capability_verified"] is True
    assert backend._episode_deadline is not None
    assert any(path == "/code/capabilities" for _, path, _ in transport.calls)

    too_slow = AgentServerActionBackend(
        simulator_attested=True, transport=AgentTransport(), timeout_seconds=90.0,
    )
    with pytest.raises(ValueError, match="backend timeout"):
        run_robocasa_generated_policy_episode(
            task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
            action_backend=SafetyMonitorBackend(too_slow), policy_code_path=code,
            policy_id="wrapped-timeout", perception_mode="sim_gt",
            client=RobocasaSimClient("counter_to_sink", transport=world.transport),
            timeout_seconds=2.0,
        )


def test_semantic_cache_ignores_run_ids_but_not_evidence_or_history():
    def request(run_id, timestamp, symptom):
        packet = {
            "run_id": run_id, "trace_id": f"trace:{run_id}",
            "created_at": timestamp, "events": [
                {"timestamp": timestamp, "event_type": "attention.prior_attempts",
                 "result": {"attempts": [{"observed_symptom": symptom}]}}
            ],
            "code": {"sha256": "a" * 64, "artifact_uri": f"artifact://{run_id}/policy.py"},
        }
        return {"model": "parcc/GLM", "messages": [
            {"role": "system", "content": "fixed prompt"},
            {"role": "user", "content": json.dumps({"request_type": "hint", "trace_packet": packet})},
        ]}

    assert semantic_advisor_cache_key(request("one", 1, "missed")) == semantic_advisor_cache_key(request("two", 2, "missed"))
    assert semantic_advisor_cache_key(request("one", 1, "missed")) != semantic_advisor_cache_key(request("two", 2, "slipped"))


def test_sim_gt_proxy_reuses_equivalent_cross_run_request(tmp_path: Path):
    calls = []
    proxy = SimGTAdvisorProxy(
        AttentionStore(tmp_path / "cache.sqlite3"),
        transport=lambda request: calls.append(request) or '{"guidance":"retry"}',
        sleeper=lambda _: None,
    )
    base = {"hypothesis": "grasp", "code": {"sha256": "a" * 64},
            "evidence": [{"sha256": "b" * 64, "uri": "artifact://one/camera"}]}
    first = proxy.answer(request_type="hint", trace_packet={**base, "run_id": "one", "created_at": 1})
    second = proxy.answer(request_type="hint", trace_packet={**base, "run_id": "two", "created_at": 2})
    assert first.cached is False and second.cached is True
    assert len(calls) == 1


def test_prior_history_rejects_evaluator_fields():
    with pytest.raises(ValueError):
        _public_attempt_history([{"attempt_index": 0, "native_success": False}])


def test_v2_glm_format_retry_accounts_for_all_tokens(tmp_path: Path):
    class Client:
        calls = 0

        def chat(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                content = '{"schema_version":'
                tokens = 512
            else:
                content = json.dumps({
                    "schema_version": "attentionbench.advisor-advice.v1",
                    "request_type": "hint", "diagnosis": "No action occurred.",
                    "guidance": "Attempt a grasp.", "caution": "Check reachability.",
                    "confidence": 0.7,
                })
                tokens = 100
            return ParccResponse(
                model=kwargs["model"], content=content, reasoning=None,
                latency_seconds=0.2, attempts=1,
                usage={"prompt_tokens": 20, "completion_tokens": tokens,
                       "total_tokens": 20 + tokens},
            )

    client = Client()
    proxy = SimGTAdvisorProxy(
        AttentionStore(tmp_path / "format-retry.sqlite3"),
        transport=SimGTGLMAdvisorTransport(client=client),
        sleeper=lambda _: None,
    )
    reply = proxy.answer(request_type="hint", trace_packet={"hypothesis": "grasp"})
    assert reply.provider_attempts == 2
    assert reply.usage["completion_tokens"] == 612
    assert reply.usage["total_tokens"] == 652
    assert client.calls == 2


def test_sequence_carries_projected_failure_into_next_attempt(tmp_path: Path):
    world = FakeWorld()
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    third = tmp_path / "third.py"
    first.write_text("from robot_sdk import gripper\n", encoding="utf-8")
    second.write_text("from robot_sdk import gripper\n", encoding="utf-8")
    third.write_text("from robot_sdk import gripper\ngripper.close()\n", encoding="utf-8")
    results = run_robocasa_generated_policy_sequence(
        attempts=[(first, "Maybe no action is needed"),
                  (second, "Maybe wait another attempt"), (third, "Try closing")],
        task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
        action_backend=FakeActionBackend(world), policy_id="generated-sequence",
        perception_mode="sim_gt",
        client=RobocasaSimClient("counter_to_sink", transport=world.transport),
    )
    assert [item["native_success"] for item in results] == [False, False, True]
    assert results[1]["prior_attempt_count"] == 1
    store = AttentionStore(Path(results[1]["attention_trace"]["store"]))
    raw = store.get_raw_trace(results[1]["attention_trace"]["raw_trace_id"])
    history_event = next(event for event in raw["events"] if event["event_type"] == "attention.prior_attempts")
    assert history_event["result"]["attempts"][0]["hypothesis"] == "Maybe no action is needed"
    packet = store.get_trace(results[1]["attention_trace"]["advisor_trace_id"])
    assert "Maybe no action is needed" in json.dumps(packet["events"])
    assert packet["hypothesis"] == "Maybe wait another attempt"
    assert "from robot_sdk import gripper" in packet["code"]["content"]
