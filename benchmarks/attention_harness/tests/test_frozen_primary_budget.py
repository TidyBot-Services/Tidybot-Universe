"""Aggregate SDK caps and auditable provider/cache accounting, without simulators."""
import json
import time
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.core.advisor import AdvisorTransportReply
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.robosuite_memory.formal_sandbox import execute_formal_policy
from benchmarks.attention_harness.v2_advisor import SimGTAdvisorProxy, SharedAdvisorCache
from benchmarks.attention_harness.v2_advisor import SimGTGLMAdvisorTransport
from benchmarks.attention_harness.parcc_client import ParccClient
from benchmarks.attention_harness.advisor_proxy import build_advisor_request
from benchmarks.attention_harness.sim_gt_attention_run import run_robosuite_attention
from benchmarks.attention_harness.tests.test_sim_gt_attention_run import advisor_reply
from benchmarks.attention_harness.tests.test_robosuite_memory import FakeClient, _adapter
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.tests.test_m1_entry import entry, digest
from benchmarks.attention_harness.tests.test_formal_memory_contract import contract
from benchmarks.attention_harness.tests.test_formal_attention_chain import FakeFormalRunner


def packet():
    return {"trace_id": "trace:fixed", "task_id": "cube_lift", "hypothesis": "test",
            "failure": {"reason": "failure"}, "actions": [], "evidence": [],
            "constraints": [], "history": []}


def test_sandbox_never_dispatches_more_than_remaining_calls(tmp_path):
    dispatched = []
    sdk = SimpleNamespace(sensors=SimpleNamespace(
        get_observation=lambda: dispatched.append("read") or {}))
    outcome = execute_formal_policy(code="from robot_sdk import sensors\nfor _ in range(3):\n    sensors.get_observation()\n",
        sdk=sdk, context={}, deadline=time.monotonic() + 10,
        stderr_path=tmp_path / "worker.stderr", max_sdk_calls=2)
    assert len(dispatched) == 2 and outcome.call_count == 3
    assert outcome.status == "failed" and outcome.error == "policy exceeded SDK call limit"


@pytest.mark.parametrize("usage", [{}, {"total_tokens": 0},
    {"prompt_tokens": True, "completion_tokens": 3, "total_tokens": 4},
    {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 6},
    {"prompt_tokens": 4096, "completion_tokens": 1, "total_tokens": 4097}])
def test_strict_usage_rejects_unknown_inconsistent_or_overbudget_and_keeps_reply(tmp_path, usage):
    rejected = tmp_path / "rejected.json"
    proxy = SimGTAdvisorProxy(AttentionStore(tmp_path / "store.sqlite3"),
        transport=lambda _: AdvisorTransportReply("response retained", "parcc/GLM", 0, 1, usage),
        sleeper=lambda _: None, cache_path=tmp_path / "cache.sqlite3",
        strict_token_budget_remaining=4096, usage_rejection_path=rejected)
    with pytest.raises(ValueError):
        proxy.answer(request_type="hint", trace_packet=packet())
    artifact = json.loads(rejected.read_bytes())
    assert artifact["response"]["content"] == "response retained"
    assert artifact["response"]["usage"] == usage
    assert SharedAdvisorCache(tmp_path / "cache.sqlite3").get(artifact["cache_key"]) == artifact["response"]


def test_cached_reply_retains_credit_semantics_and_zero_new_provider_tokens(tmp_path):
    calls = []
    def transport(_):
        calls.append(1)
        return AdvisorTransportReply("reply", "parcc/GLM", 0, 1,
                                     {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})
    proxy = SimGTAdvisorProxy(AttentionStore(tmp_path / "store.sqlite3"), transport=transport,
        sleeper=lambda _: None, cache_path=tmp_path / "cache.sqlite3", strict_token_budget_remaining=4096)
    first = proxy.answer(request_type="hint", trace_packet=packet())
    second = proxy.answer(request_type="hint", trace_packet=packet())
    assert first.total_tokens == 5 and not first.cached
    assert second.total_tokens == 0 and second.cached and second.provider_attempts == 0
    assert len(calls) == 1 and first.logical_latency_seconds == second.logical_latency_seconds == 2


def test_two_frozen_runs_do_not_share_advisor_cache(tmp_path):
    calls = []
    def transport(request):
        calls.append(1)
        return AdvisorTransportReply(advisor_reply(request), "parcc/GLM", 0, 1,
                                     {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})
    results = [run_robosuite_attention(task_id="cube_lift", seed=101, artifact_root=tmp_path,
        policy_id="reactive_help", robot_policy=lambda *_: None,
        adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        max_attempts=2, assistance_credits=1, token_limit=4096, advisor_transport=transport,
        sleeper=lambda _: None, fresh_advisor_cache=True, strict_admission_budget=True)
        for _ in range(2)]
    assert len(calls) == 2
    assert all(r["resource_usage"] == {"assistance_credits": 1, "tokens": 5} for r in results)
    assert all(r["requests"][0]["cached"] is False for r in results)
    assert results[0]["advisor_cache"]["path"] != results[1]["advisor_cache"]["path"]


def test_sdk_budget_is_shared_by_attempts_and_stops_before_new_dispatch(tmp_path):
    args = entry(tmp_path, "robosuite")
    args.update(max_attempts=4, assistance_credits=0, token_limit=4096,
                overall_deadline_seconds=300)
    value = contract(tmp_path, args)
    lock, _ = inspect_formal_entry(**args)
    class BudgetRunner(FakeFormalRunner):
        def execute(self, request):
            result = super().execute(request)
            result["entry_lock"] = request.entry_lock
            for name in ("trace", "sandbox_receipt"):
                ref = result["artifacts"][name]
                from pathlib import Path
                path = Path(ref["uri"])
                data = json.loads(path.read_bytes())
                if name == "trace":
                    data["entry_lock"] = request.entry_lock
                else:
                    count = min(120, request.sdk_call_limit)
                    data["sdk_call_budget"] = {"limit": request.sdk_call_limit,
                                              "requested": count, "dispatched": count, "rejected": 0}
                path.write_text(json.dumps(data))
                ref["sha256"] = digest(path)
            return result
    runner = BudgetRunner("robosuite")
    result = run_formal_attention(suite=args["suite"], task_id=args["task_id"], seed=101,
        artifact_root=tmp_path / "run", policy_id=args["policy_id"],
        policy_code_path=args["code"], approved_policy_sha256=args["approved_policy_sha256"],
        config_path=args["config"], approved_config_sha256=args["approved_config_sha256"],
        runner=runner, max_attempts=4, assistance_credits=0, token_limit=4096,
        assistance_mode="benchmark_proxy", human_deadline_seconds=30,
        overall_deadline_seconds=300, formal_attempt_deadline_seconds=120,
        formal_run_wall_seconds=300, entry_lock=lock, memory_contract=value,
        approved_memory_contract_sha256=args["approved_memory_contract_sha256"])
    assert [r.sdk_call_limit for r in runner.requests] == [200, 80]
    assert result["sdk_call_budget"] == {"limit": 200, "dispatched": 200, "remaining": 0}
    assert result["stopped_reason"] == "sdk_call_budget_exhausted"


@pytest.mark.parametrize("body", [
    {"choices": [{"message": {"content": "bad JSON"}}],
     "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}},
    {"choices": [{"message": {"content": "bad JSON"}}], "usage": {}},
    {"choices": [{"message": {}}],
     "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}},
])
def test_raw_provider_failure_is_retained_without_retry_or_zero_cost(tmp_path, body):
    raw = json.dumps(body).encode()
    calls = []
    def http(_url, _headers, _payload, timeout):
        calls.append(timeout)
        return 200, {"x-request-id": "fixed-test-response"}, raw
    client = ParccClient(api_key="fixture-key", transport=http, max_attempts=1, timeout_seconds=90)
    transport = SimGTGLMAdvisorTransport(client, format_attempts=1, strict_usage=True,
                                         response_log_dir=tmp_path / "raw", token_budget_limit=4096)
    request = build_advisor_request(request_type="hint", trace_packet=packet())
    request["provider_timeout_seconds"] = 2.5
    with pytest.raises(Exception):
        transport(request)
    assert calls == [2.5]
    bodies = list((tmp_path / "raw").glob("*.http-body"))
    assert len(bodies) == 1 and bodies[0].read_bytes() == raw
    records = [json.loads(p.read_bytes()) for p in (tmp_path / "raw").glob("*.json") if not p.name.endswith(".http.json")]
    assert len(records) == 1 and records[0]["status"].startswith("invalid_")
    assert records[0]["usage"] == body.get("usage") or records[0]["usage"] is None


def test_strict_transport_forbids_retrying_provider_or_format(tmp_path):
    client = ParccClient(api_key="fixture-key", transport=lambda *_: pytest.fail("provider must not run"))
    with pytest.raises(ValueError, match="one provider call"):
        SimGTGLMAdvisorTransport(client, format_attempts=1, strict_usage=True)
    client.max_attempts = 1
    with pytest.raises(ValueError, match="one provider call"):
        SimGTGLMAdvisorTransport(client, format_attempts=2, strict_usage=True)


def test_overbudget_provider_cost_is_known_and_retained(tmp_path):
    body = {"choices": [{"message": {"content": "not parsed after budget rejection"}}],
            "usage": {"prompt_tokens": 4096, "completion_tokens": 1, "total_tokens": 4097}}
    client = ParccClient(api_key="fixture-key", max_attempts=1,
        transport=lambda *_: (200, {}, json.dumps(body).encode()))
    transport = SimGTGLMAdvisorTransport(client, format_attempts=1, strict_usage=True,
                                         response_log_dir=tmp_path / "raw", token_budget_limit=4096)
    with pytest.raises(ValueError, match="over-budget"):
        transport(build_advisor_request(request_type="hint", trace_packet=packet()))
    records = [json.loads(p.read_bytes()) for p in (tmp_path / "raw").glob("*.json")
               if not p.name.endswith(".http.json")]
    assert records[0]["provider_cost_known"] is True
    assert records[0]["usage"]["total_tokens"] == 4097


def test_frozen_runtime_cannot_omit_attempt_and_whole_run_guards(tmp_path):
    args = entry(tmp_path, "robosuite")
    value = contract(tmp_path, args)
    lock, _ = inspect_formal_entry(**args)
    with pytest.raises(ValueError, match="explicit 120/300-second"):
        run_formal_attention(suite=args["suite"], task_id=args["task_id"], seed=101,
            artifact_root=tmp_path / "run", policy_id=args["policy_id"],
            policy_code_path=args["code"], approved_policy_sha256=args["approved_policy_sha256"],
            config_path=args["config"], approved_config_sha256=args["approved_config_sha256"],
            runner=FakeFormalRunner("robosuite"), max_attempts=2, assistance_credits=1,
            token_limit=100, assistance_mode="benchmark_proxy", human_deadline_seconds=30,
            overall_deadline_seconds=90, entry_lock=lock, memory_contract=value,
            approved_memory_contract_sha256=args["approved_memory_contract_sha256"])
    assert not (tmp_path / "run").exists()
