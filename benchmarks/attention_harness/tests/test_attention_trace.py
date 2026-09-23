from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from benchmarks.attention_harness.advisor_proxy import build_advisor_request
from benchmarks.attention_harness.core.advisor import AdvisorProxy
from benchmarks.attention_harness.core.models import (
    AttentionRequestRecord,
    EvidenceRef,
    ExecutionOutcome,
    FailureSummary,
    RawExecutionTrace,
    TraceEvent,
    TraceVisibility,
)
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.core.trace import (
    TracePipeline,
    TraceProjectionError,
    VisibilityProjector,
    summarize_failure,
)
from benchmarks.attention_harness.tests.test_attention_store import records


PUBLIC = (TraceVisibility.ADVISOR,)
INTERNAL = (TraceVisibility.INTERNAL,)


def _digest(character: str) -> str:
    return character * 64


def raw_trace() -> RawExecutionTrace:
    return RawExecutionTrace(
        raw_trace_id="raw-1",
        run_id="run-1",
        attempt_id="attempt-1",
        execution_id="execution-1",
        created_at=3.0,
        agent_state="blocked_after_failure",
        events=(
            TraceEvent(
                event_id="event-0",
                sequence=0,
                timestamp=3.1,
                source="robot_sdk.sensors",
                event_type="sdk.sensor_read",
                operation="get_observation",
                status="completed",
                visibility=PUBLIC,
                result={"camera": "agentview", "reward": 0.25},
                evidence_refs=("frame-1", "oracle-state"),
            ),
            TraceEvent(
                event_id="event-1",
                sequence=1,
                timestamp=3.2,
                source="robot_sdk.gripper",
                event_type="sdk.gripper_command",
                operation="gripper.close",
                status="failed",
                visibility=PUBLIC,
                duration_ms=25.0,
                arguments={
                    "settle_steps": 10,
                    "object_pose": [0.1, 0.2, 0.3],
                },
                error={"type": "GraspError", "message": "gripper failed to close"},
                evidence_refs=("frame-1",),
            ),
            TraceEvent(
                event_id="event-2",
                sequence=2,
                timestamp=3.3,
                source="native_evaluator",
                event_type="evaluator.result",
                operation="check_success",
                status="completed",
                # A producer label cannot override the hard evaluator boundary.
                visibility=PUBLIC,
                result={"native_success": False, "cubeA_pos": [0.0, 0.0, 0.0]},
            ),
        ),
        evidence=(
            EvidenceRef(
                evidence_id="frame-1",
                kind="rgb",
                uri="artifact://run-1/frame-1.jpg",
                sha256=_digest("a"),
                created_at=3.1,
                visibility=PUBLIC,
                source_event_id="event-0",
                mime_type="image/jpeg",
                metadata={
                    "camera": "agentview",
                    "simulator_state": {"object_pose": [0.1, 0.2, 0.3]},
                    "api_key": "must-never-cross-boundary",
                },
            ),
            EvidenceRef(
                evidence_id="oracle-state",
                kind="simulator_state",
                uri="artifact://run-1/oracle.json",
                sha256=_digest("b"),
                created_at=3.2,
                # Deliberately mislabelled public: the projector must still
                # reject intrinsically privileged evidence as defense in depth.
                visibility=PUBLIC,
                metadata={"native_success": False},
            ),
        ),
        hypothesis="the grasp may have been off-center",
        code={
            "artifact_uri": "artifact://run-1/generated_policy.py",
            "sha256": _digest("c"),
            "api_key_name": "must-be-redacted",
        },
        outcome=ExecutionOutcome(
            status="failed",
            exit_code=1,
            elapsed_seconds=0.3,
            native_success=False,
            evaluator_verdict="failed",
            evaluator_authoritative=True,
        ),
        metadata={"suite": "robosuite", "oracle_state": {"hidden": True}},
    )


def _populated_store(path: Path) -> AttentionStore:
    store = AttentionStore(path)
    run, attempt, _, _ = records()
    store.create_run(run)
    store.create_attempt(attempt)
    return store


def test_raw_trace_projects_to_deterministic_oracle_safe_packet() -> None:
    raw = replace(
        raw_trace(),
        failure=FailureSummary(
            "native_evaluation",
            "oracle_failure",
            "hidden cube pose proves the task failed",
            3,
        ),
    )
    projector = VisibilityProjector()
    first = projector.project(raw, trace_id="trace-1")
    second = projector.project(raw, trace_id="trace-1")

    assert first == second
    assert first.raw_trace_id == raw.raw_trace_id
    assert first.execution_id == raw.execution_id
    assert [item["evidence_id"] for item in first.evidence] == ["frame-1"]
    assert [event["event_id"] for event in first.events] == ["event-0", "event-1"]
    assert first.events[0]["evidence_refs"] == ["frame-1"]
    assert "reward" not in first.events[0]["result"]
    assert "object_pose" not in first.events[1]["arguments"]
    assert first.evidence[0]["metadata"] == {"camera": "agentview"}
    assert first.outcome == {
        "elapsed_seconds": 0.3,
        "exit_code": 1,
        "status": "failed",
        "stop_reason": None,
        "timed_out": False,
    }
    assert first.failure.stage == "grasp"
    assert first.failure.last_successful_event_id == "event-0"
    assert first.failure.first_failed_event_id == "event-1"
    assert first.failure.consecutive_failures == 3
    assert "hidden cube pose" not in str(first.artifact()).lower()
    assert first.projection["omitted_event_count"] == 1
    assert first.projection["omitted_evidence_count"] == 1
    assert first.projection["redacted_field_count"] == 8

    # The existing AdvisorProxy guard accepts the projected packet as a second
    # independent boundary check.
    request = build_advisor_request(
        request_type="hint", trace_packet=first.artifact()
    )
    assert request["model"]


def test_pipeline_persists_raw_and_projected_trace_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "attention.sqlite3"
    store = _populated_store(path)
    packet = TracePipeline(store).persist(raw_trace(), trace_id="trace-1")

    restarted = AttentionStore(path)
    assert restarted.get_raw_trace("raw-1")["execution_id"] == "execution-1"
    recovered = restarted.get_trace("trace-1")
    assert recovered["raw_trace_id"] == "raw-1"
    assert recovered == packet.artifact()
    event_types = [event["event_type"] for event in restarted.events()]
    assert "raw_trace.created" in event_types
    assert "trace.created" in event_types


def test_runtime_uses_persisted_advisor_projection_by_default(tmp_path: Path) -> None:
    store = _populated_store(tmp_path / "attention.sqlite3")
    packet = TracePipeline(store).persist(raw_trace(), trace_id="trace-1")
    _, _, _, base_request = records()
    request = replace(base_request, trace_id=packet.trace_id)
    captured = []
    proxy = AdvisorProxy(
        store,
        transport=lambda payload: captured.append(payload) or "retry from above",
        sleeper=lambda _: None,
    )
    runtime = AttentionRuntime(store, proxy, clock=lambda: 10.0)
    runtime.open_request(request)

    runtime.resolve_benchmark_proxy(request.request_id)

    assert len(captured) == 1
    serialized = str(captured[0]).lower()
    assert "native_success" not in serialized
    assert "object_pose" not in serialized
    assert "must-never-cross-boundary" not in serialized


def test_projection_requires_explicitly_visible_evidence() -> None:
    raw = raw_trace()
    internal = replace(
        raw,
        evidence=tuple(replace(item, visibility=INTERNAL) for item in raw.evidence),
    )
    with pytest.raises(TraceProjectionError, match="no evidence"):
        VisibilityProjector().project(internal)


def test_projected_trace_cannot_link_to_a_different_raw_trace(tmp_path: Path) -> None:
    store = _populated_store(tmp_path / "attention.sqlite3")
    raw = raw_trace()
    store.put_raw_trace(raw)
    packet = VisibilityProjector().project(raw, trace_id="trace-1")
    with pytest.raises(StateConflictError, match="links do not match"):
        store.put_trace(replace(packet, execution_id="different-execution"))


def test_wrapped_timeout_is_classified_from_error_message() -> None:
    failure = summarize_failure(
        [
            TraceEvent(
                event_id="finished",
                sequence=0,
                timestamp=1.0,
                source="attention_harness",
                event_type="execution.finished",
                operation="run_policy",
                status="failed",
                visibility=PUBLIC,
                error={
                    "type": "RuntimeError",
                    "message": "policy exceeded 1.0s timeout",
                },
            )
        ]
    )
    assert failure.stage == "timeout"
    assert failure.termination_reason == "timeout"
