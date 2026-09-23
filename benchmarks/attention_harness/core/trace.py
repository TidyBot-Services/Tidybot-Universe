"""Raw execution trace collection and deterministic advisor-safe projection."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, TYPE_CHECKING

from .models import (
    AdvisorTracePacket,
    EvidenceRef,
    FailureSummary,
    RawExecutionTrace,
    TraceEvent,
    TraceVisibility,
)

if TYPE_CHECKING:
    from .store import AttentionStore


PROJECTION_POLICY_VERSION = "attentionbench.advisor-visibility.v1"

# These values are allowed in RawExecutionTrace but cannot cross the Advisor
# boundary. Matching is performed after lower-casing and normalizing hyphens.
FORBIDDEN_ADVISOR_KEYS = {
    "cube_pos",
    "cubea_pos",
    "cubeb_pos",
    "done",
    "evaluator_authoritative",
    "evaluator_verdict",
    "is_success",
    "native_success",
    "object_pose",
    "object_poses",
    "oracle_state",
    "reward",
    "segmentation_id",
    "simulator_state",
    "success",
    "task_completed",
}
FORBIDDEN_ADVISOR_MARKERS = (
    "api_key",
    "credential",
    "oracle",
    "password",
    "privileged",
    "secret",
    "token",
)


class TraceProjectionError(ValueError):
    pass


class VisibilityProjector:
    """Create the only trace representation that an advisor may receive."""

    def __init__(self, *, policy_version: str = PROJECTION_POLICY_VERSION) -> None:
        self.policy_version = policy_version

    def project(
        self,
        raw: RawExecutionTrace,
        *,
        trace_id: str | None = None,
    ) -> AdvisorTracePacket:
        redacted_paths: list[str] = []
        visible_evidence = [
            item
            for item in raw.evidence
            if _advisor_visible(item.visibility)
            and not _intrinsically_privileged_evidence(item)
        ]
        if not visible_evidence:
            raise TraceProjectionError(
                "raw trace has no evidence explicitly visible to the advisor"
            )
        visible_ids = {item.evidence_id for item in visible_evidence}

        evidence = tuple(
            self._evidence(item, redacted_paths)
            for item in sorted(visible_evidence, key=lambda value: value.evidence_id)
        )
        visible_events = [
            event
            for event in raw.events
            if _advisor_visible(event.visibility)
            and not _intrinsically_privileged_event(event)
        ]
        events = tuple(
            self._event(event, visible_ids, redacted_paths)
            for event in sorted(visible_events, key=lambda value: value.sequence)
        )
        # Rebuild the advisor-facing failure from advisor-visible events. A raw
        # failure summary may have been produced with evaluator/oracle context
        # and is therefore never copied across the boundary verbatim.
        failure = summarize_failure(
            visible_events,
            consecutive_failures=(
                raw.failure.consecutive_failures if raw.failure is not None else 1
            ),
        )
        code = _sanitize(raw.code, "code", redacted_paths)
        outcome = _public_outcome(raw, redacted_paths)

        packet_id = trace_id or f"advisor:{raw.raw_trace_id}"
        source_sha256 = _digest(raw.artifact())
        public_body = {
            "trace_id": packet_id,
            "raw_trace_id": raw.raw_trace_id,
            "run_id": raw.run_id,
            "attempt_id": raw.attempt_id,
            "execution_id": raw.execution_id,
            "created_at": raw.created_at,
            "agent_state": raw.agent_state,
            "failure": failure.artifact(),
            "evidence": evidence,
            "hypothesis": raw.hypothesis,
            "memory_refs": raw.memory_refs,
            "code": code,
            "events": events,
            "outcome": outcome,
        }
        projection_sha256 = _digest(public_body)
        projection = {
            "policy_version": self.policy_version,
            "source_sha256": source_sha256,
            "projection_sha256": projection_sha256,
            "included_event_count": len(events),
            "omitted_event_count": len(raw.events) - len(events),
            "included_evidence_count": len(evidence),
            "omitted_evidence_count": len(raw.evidence) - len(evidence),
            # Do not disclose redacted field names: even a path such as
            # ``outcome.native_success`` reveals that privileged state exists.
            "redacted_field_count": len(set(redacted_paths)),
            "source_complete": raw.complete,
        }
        return AdvisorTracePacket(
            trace_id=packet_id,
            run_id=raw.run_id,
            attempt_id=raw.attempt_id,
            created_at=raw.created_at,
            agent_state=raw.agent_state,
            failure=failure,
            evidence=evidence,
            hypothesis=raw.hypothesis,
            memory_refs=raw.memory_refs,
            raw_trace_id=raw.raw_trace_id,
            execution_id=raw.execution_id,
            code=code,
            events=events,
            outcome=outcome,
            projection=projection,
        )

    @staticmethod
    def _evidence(item: EvidenceRef, redacted_paths: list[str]) -> dict[str, Any]:
        value = item.artifact()
        value["visibility"] = [TraceVisibility.ADVISOR.value]
        value["metadata"] = _sanitize(
            value.get("metadata", {}),
            f"evidence.{item.evidence_id}.metadata",
            redacted_paths,
        )
        return value

    @staticmethod
    def _event(
        event: TraceEvent,
        visible_evidence_ids: set[str],
        redacted_paths: list[str],
    ) -> dict[str, Any]:
        value = event.artifact()
        value["visibility"] = [TraceVisibility.ADVISOR.value]
        value["arguments"] = _sanitize(
            value.get("arguments", {}),
            f"events.{event.event_id}.arguments",
            redacted_paths,
        )
        value["result"] = _sanitize(
            value.get("result", {}),
            f"events.{event.event_id}.result",
            redacted_paths,
        )
        value["error"] = _sanitize(
            value.get("error"),
            f"events.{event.event_id}.error",
            redacted_paths,
        )
        value["evidence_refs"] = [
            ref for ref in event.evidence_refs if ref in visible_evidence_ids
        ]
        return value


class TracePipeline:
    """Persist the internal ledger, then its immutable advisor projection."""

    def __init__(
        self,
        store: AttentionStore,
        projector: VisibilityProjector | None = None,
    ) -> None:
        self.store = store
        self.projector = projector or VisibilityProjector()

    def persist(
        self,
        raw: RawExecutionTrace,
        *,
        trace_id: str | None = None,
    ) -> AdvisorTracePacket:
        self.store.put_raw_trace(raw)
        packet = self.projector.project(raw, trace_id=trace_id)
        self.store.put_trace(packet)
        return packet


def summarize_failure(
    events: Iterable[TraceEvent],
    *,
    consecutive_failures: int = 1,
) -> FailureSummary:
    """Build a deterministic symptom summary without claiming causal truth."""

    ordered = sorted(events, key=lambda value: value.sequence)
    failure_index = next(
        (
            index
            for index, event in enumerate(ordered)
            if event.status.lower() in {"error", "failed", "timeout", "timed_out"}
            or event.error is not None
        ),
        None,
    )
    if failure_index is None:
        raise TraceProjectionError("raw trace contains no failed event to summarize")
    failed = ordered[failure_index]
    previous = next(
        (
            event
            for event in reversed(ordered[:failure_index])
            if event.status.lower() in {"ok", "success", "succeeded", "completed"}
        ),
        None,
    )
    stage, confidence = _failure_stage(failed)
    error = failed.error or {}
    error_type = str(error.get("type") or failed.status)
    message = str(error.get("message") or f"{failed.operation} {failed.status}")
    return FailureSummary(
        stage=stage,
        error_type=error_type,
        message=message,
        consecutive_failures=consecutive_failures,
        first_failed_event_id=failed.event_id,
        last_successful_event_id=previous.event_id if previous else None,
        observed_symptom=message,
        inferred_cause=None,
        termination_reason="timeout" if "timeout" in failed.status.lower() else None,
        retryable=None,
        safety_relevant="safety" in failed.event_type.lower(),
        classification_source="deterministic_event_rules",
        confidence=confidence,
    )


def _failure_stage(event: TraceEvent) -> tuple[str, float]:
    text = " ".join(
        (event.source, event.event_type, event.operation, event.status)
    ).lower()
    rules = (
        (("code_generation", "generate_code"), "code_generation"),
        (("code_validation", "validate_policy", "syntax"), "code_validation"),
        (("timeout", "timed_out"), "timeout"),
        (("safety", "emergency", "collision"), "safety_interrupt"),
        (("service", "connection", "http"), "service_connection"),
        (("perception", "detect", "segment", "find_objects"), "perception"),
        (("frame_transform", "pixel_to_world", "calibration"), "frame_transform"),
        (("grasp_plan", "plan_grasp"), "grasp_planning"),
        (("inverse_kinematics", "solve_ik", "motion_plan"), "ik_motion_planning"),
        (("controller", "actuation", "move_to_position"), "controller_actuation"),
        (("gripper", "grasp"), "grasp"),
        (("transport",), "transport"),
        (("place", "placement"), "placement"),
        (("native_evaluator", "evaluator"), "native_evaluation"),
        (("sandbox", "runtime", "exception"), "sandbox_runtime"),
    )
    for needles, stage in rules:
        if any(needle in text for needle in needles):
            return stage, 1.0
    return "unknown", 0.5


def _advisor_visible(visibility: tuple[TraceVisibility, ...]) -> bool:
    return TraceVisibility.ADVISOR in visibility or TraceVisibility.PUBLIC in visibility


def _intrinsically_privileged_event(event: TraceEvent) -> bool:
    identity = " ".join((event.source, event.event_type, event.operation)).lower()
    return any(
        marker in identity
        for marker in ("native_evaluator", "evaluator.result", "oracle", "privileged")
    )


def _intrinsically_privileged_evidence(evidence: EvidenceRef) -> bool:
    identity = " ".join((evidence.kind, evidence.uri)).lower()
    return any(
        marker in identity
        for marker in ("simulator_state", "oracle", "privileged", "native_evaluator")
    )


def _public_outcome(
    raw: RawExecutionTrace, redacted_paths: list[str]
) -> dict[str, Any]:
    if raw.outcome is None:
        return {}
    value = raw.outcome.artifact()
    return _sanitize(value, "outcome", redacted_paths)


def _sanitize(value: Any, path: str, redacted_paths: list[str]) -> Any:
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key in sorted(value, key=lambda item: str(item)):
            normalized = str(key).lower().replace("-", "_")
            child_path = f"{path}.{key}"
            if _forbidden_key(normalized):
                redacted_paths.append(child_path)
                continue
            sanitized[str(key)] = _sanitize(value[key], child_path, redacted_paths)
        return sanitized
    if isinstance(value, (tuple, list)):
        return [
            _sanitize(item, f"{path}[{index}]", redacted_paths)
            for index, item in enumerate(value)
        ]
    return value


def _forbidden_key(normalized: str) -> bool:
    return normalized in FORBIDDEN_ADVISOR_KEYS or any(
        marker in normalized for marker in FORBIDDEN_ADVISOR_MARKERS
    )


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
