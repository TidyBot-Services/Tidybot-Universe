from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.attention_harness.attention_modes import AssistanceMode, RequestState
from benchmarks.attention_harness.core.models import (
    AssistanceBudget,
    AttentionRequestRecord,
    AttentionResponseRecord,
    AttemptRecord,
    FailureSummary,
    RequestPriority,
    RequestType,
    RunRecord,
    RunStatus,
    AttemptStatus,
    TracePacket,
)
from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError


def records():
    run = RunRecord(
        run_id="run-1",
        suite="robosuite",
        task_id="cube_lift",
        seed=101,
        policy_id="reactive_help",
        developer_model="parcc/GLM",
        assistance_mode=AssistanceMode.BENCHMARK_PROXY,
        execution_target="simulation",
        budget=AssistanceBudget(assistance_credits=2, token_limit=1000, execution_seconds=90),
        created_at=1.0,
    )
    attempt = AttemptRecord("attempt-1", run.run_id, 0, 2.0)
    trace = TracePacket(
        trace_id="trace-1",
        run_id=run.run_id,
        attempt_id=attempt.attempt_id,
        created_at=3.0,
        agent_state="blocked_after_failure",
        failure=FailureSummary("grasp", "miss", "cube slipped", 1),
        evidence=({"kind": "image", "sha256": "abc"},),
        hypothesis="grasp was off-center",
    )
    request = AttentionRequestRecord(
        request_id="request-1",
        run_id=run.run_id,
        attempt_id=attempt.attempt_id,
        trace_id=trace.trace_id,
        request_type=RequestType.HINT,
        reason="repeated grasp miss",
        priority=RequestPriority.NORMAL,
        created_at=4.0,
        deadline_at=None,
        mode=AssistanceMode.BENCHMARK_PROXY,
    )
    return run, attempt, trace, request


def populated_store(path: Path) -> tuple[AttentionStore, AttentionRequestRecord]:
    store = AttentionStore(path)
    run, attempt, trace, request = records()
    store.create_run(run)
    store.create_attempt(attempt)
    store.put_trace(trace)
    store.create_request(request)
    return store, request


def test_records_are_idempotent_and_survive_restart(tmp_path: Path) -> None:
    path = tmp_path / "attention.sqlite3"
    store, request = populated_store(path)
    store.create_request(request)
    assert len(store.events()) == 4

    restarted = AttentionStore(path)
    recovered = restarted.get_request(request.request_id)
    assert recovered == request
    assert restarted.get_trace("trace-1")["hypothesis"] == "grasp was off-center"


def test_conflicting_id_and_illegal_transition_are_rejected(tmp_path: Path) -> None:
    store, request = populated_store(tmp_path / "attention.sqlite3")
    altered = AttentionRequestRecord(
        **{**request.__dict__, "reason": "different reason"}
    )
    with pytest.raises(StateConflictError, match="different content"):
        store.create_request(altered)

    response = AttentionResponseRecord(
        "response-1", request.request_id, "advisor_proxy", "move left", 5.0
    )
    answered = store.transition_request(
        request.request_id,
        RequestState.ANSWERED,
        response=response,
        event_key="answer:request-1",
    )
    assert answered.response_id == response.response_id
    with pytest.raises(StateConflictError, match="illegal"):
        store.transition_request(
            request.request_id,
            RequestState.CANCELLED,
            event_key="cancel:request-1",
        )


def test_transition_event_key_is_idempotent(tmp_path: Path) -> None:
    store, request = populated_store(tmp_path / "attention.sqlite3")
    first = store.transition_request(
        request.request_id,
        RequestState.FALLBACK,
        event_key="deadline:request-1",
    )
    second = store.transition_request(
        request.request_id,
        RequestState.FALLBACK,
        event_key="deadline:request-1",
    )
    assert first == second
    assert len([event for event in store.events() if event["entity_id"] == request.request_id]) == 2


def test_budget_reservation_commit_release_and_exhaustion(tmp_path: Path) -> None:
    store, _ = populated_store(tmp_path / "attention.sqlite3")
    assert store.reserve_assistance("run-1", credits=1, reservation_id="r1") == {
        "limit": 2,
        "used": 0,
        "reserved": 1,
        "remaining": 1,
    }
    store.settle_assistance("r1", commit=True)
    assert store.reserve_assistance("run-1", credits=1, reservation_id="r2")[
        "remaining"
    ] == 0
    with pytest.raises(StateConflictError, match="exhausted"):
        store.reserve_assistance("run-1", credits=1, reservation_id="r3")
    store.settle_assistance("r2", commit=False)
    assert store.budget_status("run-1") == {
        "limit": 2,
        "used": 1,
        "reserved": 0,
        "remaining": 1,
    }


def test_run_and_attempt_lifecycle_are_idempotent(tmp_path: Path) -> None:
    store, _ = populated_store(tmp_path / "attention.sqlite3")
    started = store.transition_run("run-1", RunStatus.RUNNING, event_key="start-run-1")
    assert started.status is RunStatus.RUNNING
    assert store.transition_run("run-1", RunStatus.RUNNING, event_key="start-run-1") == started
    finished = store.complete_attempt(
        "attempt-1",
        AttemptStatus.FAILED,
        ended_at=10.0,
        native_success=False,
        artifact_uri="artifact://run-1/result",
        event_key="finish-attempt-1",
    )
    assert finished.status is AttemptStatus.FAILED
    assert store.complete_attempt(
        "attempt-1",
        AttemptStatus.FAILED,
        ended_at=10.0,
        native_success=False,
        artifact_uri="artifact://run-1/result",
        event_key="finish-attempt-1",
    ) == finished


def test_resource_consumption_is_idempotent_and_enforced(tmp_path: Path) -> None:
    store, _ = populated_store(tmp_path / "attention.sqlite3")
    first = store.consume_resources(
        "run-1", tokens=250, execution_seconds=12.5, event_key="usage-1"
    )
    assert first["tokens"] == {"limit": 1000, "used": 250, "remaining": 750}
    assert store.consume_resources(
        "run-1", tokens=250, execution_seconds=12.5, event_key="usage-1"
    ) == first
    with pytest.raises(StateConflictError, match="token budget"):
        store.consume_resources("run-1", tokens=751, event_key="usage-2")
    with pytest.raises(StateConflictError, match="execution-time"):
        store.consume_resources("run-1", execution_seconds=78.0, event_key="usage-3")


def test_cross_linked_trace_and_request_are_rejected(tmp_path: Path) -> None:
    store, request = populated_store(tmp_path / "attention.sqlite3")
    _, _, trace, _ = records()
    with pytest.raises(StateConflictError, match="trace run"):
        store.put_trace(replace(trace, trace_id="bad-trace", run_id="other-run"))
    with pytest.raises(StateConflictError, match="request links"):
        store.create_request(replace(request, request_id="bad-request", run_id="other-run"))
