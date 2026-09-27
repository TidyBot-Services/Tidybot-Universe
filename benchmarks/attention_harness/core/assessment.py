"""Deterministic diagnoses from advisor-visible traces only.

This module does not inspect images or infer a cause from an agent hypothesis.
Every positive observation carries the visible event/evidence IDs that support it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

from .trace import _forbidden_key


ASSESSMENT_VERSION = "attentionbench.trace-assessment.v1"
_FAILED = {"failed", "error", "timeout", "timed_out"}
_PASSED = {"ok", "success", "succeeded", "completed"}
_LOCAL = {"code_validation", "sandbox_runtime", "perception", "frame_transform",
          "grasp_planning", "ik_motion_planning", "controller_actuation", "grasp",
          "placement", "transport"}


@dataclass(frozen=True)
class TraceAssessment:
    trace_id: str
    failure_type: str
    repeated_failure: bool
    code_changed: bool | None
    progress: bool
    evidence_sufficient: bool
    locally_repairable: bool
    risk: str
    event_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    prior_trace_ids: tuple[str, ...] = ()
    prior_event_ids: tuple[str, ...] = ()
    code_sha256: str | None = None
    prior_code_sha256: str | None = None
    hypothesis_unverified: bool = False
    schema_version: str = ASSESSMENT_VERSION

    def artifact(self) -> dict[str, Any]:
        return asdict(self)


def assess_trace(
    packet: Mapping[str, Any], previous: Sequence[Mapping[str, Any]] = (),
) -> TraceAssessment:
    """Assess projected packets; ignore all fields outside the public allowlist.

    A prior attempt is compared using its own projected events and code hash.
    Unknown or summary-only failures cannot be promoted to a proven robot cause.
    """
    trace_id = str(packet.get("trace_id") or "")
    if not trace_id:
        raise ValueError("assessment requires a projected trace_id")
    events = _visible_events(packet)
    evidence = {str(item["evidence_id"]) for item in packet.get("evidence", ())
                if isinstance(item, Mapping) and item.get("evidence_id")}
    failed = next((event for event in events if _failed(event)), None)
    classified = _classify(failed)
    stage = classified[0]
    event_ids = (str(failed["event_id"]),) if failed else ()
    evidence_ids = tuple(sorted(evidence.intersection(
        str(ref) for ref in failed.get("evidence_refs", ())
    ))) if failed else ()
    # A concrete SDK error is usable even without an image. A generic
    # execution-finished/TaskOutcomeFailure says only that the task was not
    # verified; it gives no grounded local cause.
    concrete = bool(failed and failed.get("event_type") != "execution.finished"
                    and (failed.get("error") or failed.get("status") in _FAILED))
    sufficient = concrete or bool(failed and stage != "unknown" and evidence_ids)
    prior = previous[-1] if previous else None
    prior_failed = next((event for event in _visible_events(prior) if _failed(event)), None) if prior else None
    repeated = bool(prior_failed and _signature(failed) == _signature(prior_failed))
    current_code = _code_hash(packet)
    prior_code = _code_hash(prior) if prior else None
    code_changed = (current_code != prior_code) if current_code and prior_code else None
    passed_events = [event for event in events[:events.index(failed)]
                     if str(event.get("status", "")).lower() in _PASSED and
                     event.get("event_type") not in {"execution.started", "execution.finished"}] if failed else []
    passed_before = len(passed_events)
    prior_events = _visible_events(prior) if prior else []
    prior_passed = sum(str(event.get("status", "")).lower() in _PASSED and
                       event.get("event_type") not in {"execution.started", "execution.finished"}
                       for event in prior_events[:prior_events.index(prior_failed)]) if prior_failed else 0
    progress = bool(prior and passed_before > prior_passed)
    if progress:
        event_ids += tuple(str(event["event_id"]) for event in passed_events)
    return TraceAssessment(
        trace_id=trace_id, failure_type=stage, repeated_failure=repeated,
        code_changed=code_changed, progress=progress,
        evidence_sufficient=sufficient, locally_repairable=sufficient and stage in _LOCAL,
        risk="safety" if stage == "safety_interrupt" else "normal",
        event_ids=event_ids, evidence_ids=evidence_ids,
        prior_trace_ids=(str(prior.get("trace_id")),) if prior else (),
        prior_event_ids=(str(prior_failed["event_id"]),) if prior_failed else (),
        code_sha256=current_code, prior_code_sha256=prior_code,
        hypothesis_unverified=bool(packet.get("hypothesis")),
    )


def _visible_events(packet: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not packet:
        return []
    result = []
    for event in packet.get("events", ()):
        if not isinstance(event, Mapping) or not event.get("event_id"):
            continue
        identity = " ".join(str(event.get(key, "")) for key in ("source", "event_type", "operation")).lower()
        if any(marker in identity for marker in ("evaluator", "oracle", "privileged")):
            continue
        visibility = event.get("visibility", ())
        if visibility and not any(str(item) in {"advisor", "public"} for item in visibility):
            continue
        result.append(event)
    return sorted(result, key=lambda event: int(event.get("sequence", 0)))


def _failed(event: Mapping[str, Any]) -> bool:
    return str(event.get("status", "")).lower() in _FAILED or bool(_safe_error(event))


def _safe_error(event: Mapping[str, Any]) -> Mapping[str, Any]:
    error = event.get("error")
    return {str(key): value for key, value in error.items() if not _forbidden_key(str(key).lower())} if isinstance(error, Mapping) else {}


def _classify(event: Mapping[str, Any] | None) -> tuple[str, str]:
    if event is None:
        return "unknown", ""
    error = _safe_error(event)
    kind = str(error.get("type", "")).lower()
    identity = " ".join(str(event.get(key, "")) for key in ("source", "event_type", "operation")) .lower()
    if "taskoutcomefailure" in kind:
        return "unknown", kind
    rules = (("safety_interrupt", ("safety", "collision", "emergency")),
             ("timeout", ("timeout", "timed_out")),
             ("service_connection", ("service", "connection", "http")),
             ("code_validation", ("syntax", "validate_policy")),
             ("perception", ("perception", "detect", "segment", "find_objects")),
             ("frame_transform", ("frame_transform", "pixel_to_world", "calibration")),
             ("grasp_planning", ("grasp_plan", "plan_grasp")),
             ("ik_motion_planning", ("inverse_kinematics", "solve_ik", "motion_plan")),
             ("controller_actuation", ("controller", "actuation", "move_to_position")),
             ("grasp", ("gripper", "grasp")),
             ("transport", ("transport",)),
             ("placement", ("place", "placement")),
             ("sandbox_runtime", ("sandbox", "runtime", "exception")))
    text = identity + " " + kind
    for stage, needles in rules:
        if any(needle in text for needle in needles):
            return stage, kind
    return "unknown", kind


def _signature(event: Mapping[str, Any] | None) -> tuple[str, str, str] | None:
    if event is None:
        return None
    stage, kind = _classify(event)
    return stage, str(event.get("operation", "")), kind


def _code_hash(packet: Mapping[str, Any] | None) -> str | None:
    code = packet.get("code", {}) if packet else {}
    return str(code["sha256"]) if isinstance(code, Mapping) and code.get("sha256") else None
