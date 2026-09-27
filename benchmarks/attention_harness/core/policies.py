"""Seven deterministic policies sharing one decision and request contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .assessment import TraceAssessment
from .models import RequestPriority, RequestType


class DecisionAction(str, Enum):
    CONTINUE = "continue"
    RETRY = "retry"
    USE_DEMO = "use_demo"
    INSPECT_TRACE = "inspect_trace"
    RETRIEVE_MEMORY = "retrieve_memory"
    REQUEST = "request"
    STOP = "stop"


@dataclass(frozen=True)
class PolicyContext:
    run_id: str
    attempt_index: int
    failure_index: int
    consecutive_failures: int
    assistance_remaining: int
    evidence_count: int
    has_hypothesis: bool
    matching_memory_count: int = 0
    unsafe: bool = False
    approval_required: bool = False
    demo_available: bool = False
    assessment: TraceAssessment | None = None


@dataclass(frozen=True)
class PolicyDecision:
    action: DecisionAction
    reason: str
    request_type: RequestType | None = None
    priority: RequestPriority | None = None
    event_ids: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    prior_trace_ids: tuple[str, ...] = ()
    prior_event_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        is_request = self.action is DecisionAction.REQUEST
        local_interrupt = (self.action is DecisionAction.STOP and
                           self.request_type is RequestType.INTERRUPT and
                           self.priority is RequestPriority.CRITICAL)
        if not local_interrupt and is_request != (self.request_type is not None):
            raise ValueError("request decisions require exactly one request type")
        if not local_interrupt and is_request != (self.priority is not None):
            raise ValueError("request decisions require exactly one priority")


class AttentionPolicy(Protocol):
    policy_id: str

    def decide(self, context: PolicyContext) -> PolicyDecision: ...


def _request(
    context: PolicyContext,
    request_type: RequestType,
    reason: str,
    priority: RequestPriority = RequestPriority.NORMAL,
) -> PolicyDecision:
    if context.assistance_remaining <= 0:
        return PolicyDecision(DecisionAction.RETRY, "assistance_budget_exhausted")
    return PolicyDecision(DecisionAction.REQUEST, reason, request_type, priority)


class AutonomousPolicy:
    policy_id = "autonomous"

    def decide(self, context: PolicyContext) -> PolicyDecision:
        if context.matching_memory_count:
            return PolicyDecision(DecisionAction.RETRIEVE_MEMORY, "trusted_memory_matches")
        if context.evidence_count == 0 or not context.has_hypothesis:
            return PolicyDecision(DecisionAction.INSPECT_TRACE, "trace_needs_inspection")
        return PolicyDecision(DecisionAction.RETRY, "autonomous_retry")


class DemoFirstPolicy:
    policy_id = "demo_first"

    def decide(self, context: PolicyContext) -> PolicyDecision:
        if context.attempt_index == 0 and context.demo_available:
            return PolicyDecision(DecisionAction.USE_DEMO, "fixed_pre_task_demo")
        if context.matching_memory_count:
            return PolicyDecision(DecisionAction.RETRIEVE_MEMORY, "trusted_memory_matches")
        return PolicyDecision(DecisionAction.RETRY, "no_online_assistance")


class ReactiveHelpPolicy:
    policy_id = "reactive_help"

    def decide(self, context: PolicyContext) -> PolicyDecision:
        return _request(context, RequestType.HINT, "request_after_each_failure")


class RetryKThenAskPolicy:
    policy_id = "retry_k_then_ask"

    def __init__(self, *, k: int = 2) -> None:
        if k < 1:
            raise ValueError("k must be positive")
        self.k = k

    def decide(self, context: PolicyContext) -> PolicyDecision:
        if context.consecutive_failures < self.k:
            return PolicyDecision(DecisionAction.RETRY, f"retry_before_threshold_{self.k}")
        return _request(context, RequestType.HINT, f"retry_threshold_{self.k}_reached")


class BudgetMatchedRandomEscalationPolicy:
    policy_id = "budget_matched_random_escalation"

    def __init__(self, *, target_request_count: int, total_failure_slots: int, seed: int) -> None:
        if isinstance(total_failure_slots, bool) or not isinstance(total_failure_slots, int) or total_failure_slots < 0:
            raise ValueError("total_failure_slots must be non-negative")
        if (isinstance(target_request_count, bool) or not isinstance(target_request_count, int)
                or not 0 <= target_request_count <= total_failure_slots):
            raise ValueError("target count must fit within failure slots")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("random escalation seed must be an integer")
        self.target_request_count = target_request_count
        self.total_failure_slots = total_failure_slots
        self.seed = seed
        ranked = sorted(
            range(total_failure_slots),
            key=lambda slot: hashlib.sha256(f"{seed}:{slot}".encode()).digest(),
        )
        self.selected_slots = frozenset(ranked[:target_request_count])

    def decide(self, context: PolicyContext) -> PolicyDecision:
        if context.failure_index in self.selected_slots:
            return _request(context, RequestType.HINT, "budget_matched_random_slot")
        return PolicyDecision(DecisionAction.RETRY, "random_slot_not_selected")


class TraceAwareHintOnlyPolicy:
    policy_id = "trace_aware_hint_only"

    def decide(self, context: PolicyContext) -> PolicyDecision:
        return _trace_decision(context, full=False)


class FullTraceAwareAttentionPlanner:
    policy_id = "full_trace_aware_attention_planner"

    def decide(self, context: PolicyContext) -> PolicyDecision:
        return _trace_decision(context, full=True)


def _trace_decision(context: PolicyContext, *, full: bool) -> PolicyDecision:
    """Shared trace gate; the full method alone can choose Memory or approval."""
    a = context.assessment
    refs = dict(event_ids=a.event_ids, evidence_ids=a.evidence_ids,
                prior_trace_ids=a.prior_trace_ids,
                prior_event_ids=a.prior_event_ids) if a else {}
    if context.unsafe:
        return PolicyDecision(DecisionAction.STOP, "independent_safety_stop", **refs)
    if a and a.risk == "safety":
        if full:
            return PolicyDecision(DecisionAction.STOP, "trace_safety_interrupt",
                                  RequestType.INTERRUPT, RequestPriority.CRITICAL, **refs)
        return PolicyDecision(DecisionAction.STOP, "trace_safety_stop", **refs)
    if context.approval_required:
        if full:
            decision = _request(context, RequestType.APPROVAL,
                                "irreversible_action_requires_approval", RequestPriority.HIGH)
            return PolicyDecision(decision.action, decision.reason, decision.request_type,
                                  decision.priority, **refs)
        return PolicyDecision(DecisionAction.STOP, "approval_required_policy_cannot_proceed", **refs)
    if a is None:
        # Compatibility for callers that have not supplied a projected packet.
        if full and context.matching_memory_count:
            return PolicyDecision(DecisionAction.RETRIEVE_MEMORY, "trusted_memory_matches")
        if context.evidence_count == 0 or not context.has_hypothesis:
            return PolicyDecision(DecisionAction.INSPECT_TRACE, "trace_needs_inspection")
        if context.consecutive_failures >= 2:
            return _request(context, RequestType.HINT, "repeated_grounded_failure")
        return PolicyDecision(DecisionAction.RETRY, "safe_grounded_retry")
    if not a.evidence_sufficient:
        if context.consecutive_failures >= 2:
            decision = _trace_request(context, "cause_unknown_after_inspection")
        else:
            decision = PolicyDecision(DecisionAction.INSPECT_TRACE, "insufficient_visible_evidence")
    elif full and context.matching_memory_count:
        decision = PolicyDecision(DecisionAction.RETRIEVE_MEMORY, "trusted_memory_matches")
    elif a.repeated_failure and not a.progress:
        decision = _trace_request(context,
                            "same_failure_after_code_change" if a.code_changed else
                            "unchanged_repeated_failure" if a.code_changed is False else
                            "repeated_failure_code_unknown")
    elif a.locally_repairable:
        decision = PolicyDecision(DecisionAction.RETRY, "visible_local_repair_candidate")
    elif context.consecutive_failures >= 2:
        decision = _trace_request(context, "cause_unresolved")
    else:
        decision = PolicyDecision(DecisionAction.INSPECT_TRACE, "cause_needs_inspection")
    return PolicyDecision(decision.action, decision.reason, decision.request_type,
                          decision.priority, **refs)


def _trace_request(context: PolicyContext, reason: str) -> PolicyDecision:
    if context.assistance_remaining <= 0:
        return PolicyDecision(DecisionAction.STOP, "unresolved_failure_no_assistance_budget")
    return _request(context, RequestType.HINT, reason)


POLICY_IDS = (
    "autonomous",
    "demo_first",
    "reactive_help",
    "retry_k_then_ask",
    "budget_matched_random_escalation",
    "trace_aware_hint_only",
    "full_trace_aware_attention_planner",
)


def build_policy(policy_id: str, **config: int) -> AttentionPolicy:
    if policy_id == "autonomous":
        return AutonomousPolicy()
    if policy_id == "demo_first":
        return DemoFirstPolicy()
    if policy_id == "reactive_help":
        return ReactiveHelpPolicy()
    if policy_id == "retry_k_then_ask":
        return RetryKThenAskPolicy(k=config.get("k", 2))
    if policy_id == "budget_matched_random_escalation":
        return BudgetMatchedRandomEscalationPolicy(
            target_request_count=config["target_request_count"],
            total_failure_slots=config["total_failure_slots"],
            seed=config.get("seed", 0),
        )
    if policy_id == "trace_aware_hint_only":
        return TraceAwareHintOnlyPolicy()
    if policy_id == "full_trace_aware_attention_planner":
        return FullTraceAwareAttentionPlanner()
    raise KeyError(f"unknown policy: {policy_id}")
