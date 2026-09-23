from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.attention_harness.attention_modes import AssistanceMode, RequestState
from benchmarks.attention_harness.core.advisor import AdvisorProxy
from benchmarks.attention_harness.core.models import (
    AttentionRequestRecord,
    RequestPriority,
    RequestType,
)
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.tests.test_attention_store import populated_store


def runtime(path: Path):
    store, base_request = populated_store(path)
    calls = []
    sleeps = []

    def transport(request):
        calls.append(request)
        return "move 2 cm left and retry the grasp"

    proxy = AdvisorProxy(store, transport=transport, sleeper=sleeps.append)
    core = AttentionRuntime(store, proxy, clock=lambda: 100.0)
    return core, store, base_request, calls, sleeps


def trace_packet():
    return {
        "failure": "missed grasp",
        "camera_frame_sha256": "abc",
        "agent_hypothesis": "target estimate was right-biased",
    }


def test_benchmark_proxy_is_cached_but_keeps_fixed_latency(tmp_path: Path) -> None:
    core, store, base, calls, sleeps = runtime(tmp_path / "attention.sqlite3")
    core.open_request(base)
    answered = core.resolve_benchmark_proxy(base.request_id, trace_packet=trace_packet())
    assert answered.state is RequestState.ANSWERED
    assert store.budget_status(base.run_id)["used"] == 1

    second = replace(base, request_id="request-2")
    core.open_request(second)
    core.resolve_benchmark_proxy(second.request_id, trace_packet=trace_packet())
    assert len(calls) == 1
    assert sleeps == [2.0, 2.0]
    response = store.get_response("proxy-response:request-2")
    assert response["cached"] is True


def test_live_human_first_and_fallback_paths(tmp_path: Path) -> None:
    core, store, base, calls, _ = runtime(tmp_path / "attention.sqlite3")
    live = replace(
        base,
        request_id="live-1",
        mode=AssistanceMode.LIVE_HUMAN_FIRST,
        deadline_at=160.0,
    )
    core.open_request(live)
    answered = core.submit_human_response(
        live.request_id, response_id="human-response-1", content="retry from above"
    )
    assert answered.state is RequestState.ANSWERED
    assert not calls

    live_fallback = replace(live, request_id="live-2", deadline_at=60.0)
    core.open_request(live_fallback)
    fallback_answered = core.handle_deadline(
        live_fallback.request_id, trace_packet=trace_packet()
    )
    assert fallback_answered.state is RequestState.ANSWERED
    events = [event["event_type"] for event in store.events()]
    assert "request.fallback" in events
    assert len(calls) == 1


def test_late_human_is_rejected_and_failed_fallback_can_retry(tmp_path: Path) -> None:
    store, base = populated_store(tmp_path / "attention.sqlite3")
    attempts = []

    def flaky(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary advisor failure")
        return "safe fallback response"

    proxy = AdvisorProxy(store, transport=flaky, sleeper=lambda _: None)
    core = AttentionRuntime(store, proxy, clock=lambda: 100.0)
    live = replace(
        base,
        request_id="live-retry",
        mode=AssistanceMode.LIVE_HUMAN_FIRST,
        deadline_at=60.0,
    )
    core.open_request(live)
    with pytest.raises(StateConflictError, match="after the request deadline"):
        core.submit_human_response(live.request_id, response_id="late", content="too late")
    with pytest.raises(RuntimeError, match="temporary"):
        core.handle_deadline(live.request_id, trace_packet=trace_packet())
    assert store.get_request(live.request_id).state is RequestState.FALLBACK
    assert core.handle_deadline(live.request_id, trace_packet=trace_packet()).state is RequestState.ANSWERED
    assert len(attempts) == 2


def test_mode_boundary_and_budget_exhaustion(tmp_path: Path) -> None:
    core, store, base, _, _ = runtime(tmp_path / "attention.sqlite3")
    core.open_request(base)
    with pytest.raises(StateConflictError, match="cannot accept human"):
        core.submit_human_response(
            base.request_id, response_id="bad", content="human response"
        )
    core.cancel(base.request_id)
    assert store.budget_status(base.run_id)["remaining"] == 2


def test_oracle_trace_is_rejected_without_spending_credit(tmp_path: Path) -> None:
    core, store, base, _, _ = runtime(tmp_path / "attention.sqlite3")
    core.open_request(base)
    with pytest.raises(ValueError, match="privileged"):
        core.resolve_benchmark_proxy(
            base.request_id, trace_packet={"native_success": False}
        )
    assert store.budget_status(base.run_id) == {
        "limit": 2,
        "used": 0,
        "reserved": 1,
        "remaining": 1,
    }
