import pytest

from benchmarks.attention_harness.core.models import RequestType
from benchmarks.attention_harness.core.policies import (
    POLICY_IDS,
    BudgetMatchedRandomEscalationPolicy,
    DecisionAction,
    PolicyContext,
    build_policy,
)


def context(**changes):
    values = dict(
        run_id="run-1",
        attempt_index=1,
        failure_index=0,
        consecutive_failures=1,
        assistance_remaining=3,
        evidence_count=1,
        has_hypothesis=True,
    )
    values.update(changes)
    return PolicyContext(**values)


def test_all_seven_policies_construct_and_share_contract() -> None:
    assert len(POLICY_IDS) == 7
    for policy_id in POLICY_IDS:
        config = (
            {"target_request_count": 2, "total_failure_slots": 5, "seed": 17}
            if policy_id == "budget_matched_random_escalation"
            else {}
        )
        decision = build_policy(policy_id, **config).decide(context())
        assert isinstance(decision.action, DecisionAction)


def test_autonomous_and_demo_first_never_request_online() -> None:
    assert build_policy("autonomous").decide(context(evidence_count=0)).action is DecisionAction.INSPECT_TRACE
    assert build_policy("autonomous").decide(context(matching_memory_count=1)).action is DecisionAction.RETRIEVE_MEMORY
    demo = build_policy("demo_first").decide(context(attempt_index=0, demo_available=True))
    assert demo.action is DecisionAction.USE_DEMO
    assert build_policy("demo_first").decide(context()).action is DecisionAction.RETRY


def test_reactive_retry_k_and_budget_boundary() -> None:
    assert build_policy("reactive_help").decide(context()).request_type is RequestType.HINT
    retry = build_policy("retry_k_then_ask", k=2)
    assert retry.decide(context(consecutive_failures=1)).action is DecisionAction.RETRY
    assert retry.decide(context(consecutive_failures=2)).action is DecisionAction.REQUEST
    assert retry.decide(context(consecutive_failures=3, assistance_remaining=0)).action is DecisionAction.RETRY


def test_budget_matched_random_has_exact_deterministic_count() -> None:
    first = BudgetMatchedRandomEscalationPolicy(target_request_count=3, total_failure_slots=10, seed=9)
    second = BudgetMatchedRandomEscalationPolicy(target_request_count=3, total_failure_slots=10, seed=9)
    actions = [first.decide(context(failure_index=index)).action for index in range(10)]
    assert actions.count(DecisionAction.REQUEST) == 3
    assert first.selected_slots == second.selected_slots


def test_trace_gate_is_hint_only_and_full_planner_routes_all_actions() -> None:
    gate = build_policy("trace_aware_hint_only")
    assert gate.decide(context(consecutive_failures=2)).request_type is RequestType.HINT
    assert gate.decide(context(evidence_count=0)).action is DecisionAction.INSPECT_TRACE

    full = build_policy("full_trace_aware_attention_planner")
    assert full.decide(context(unsafe=True)).request_type is RequestType.INTERRUPT
    assert full.decide(context(approval_required=True)).request_type is RequestType.APPROVAL
    assert full.decide(context(matching_memory_count=1)).action is DecisionAction.RETRIEVE_MEMORY
    assert full.decide(context(consecutive_failures=2)).request_type is RequestType.HINT


def test_unknown_policy_is_rejected() -> None:
    with pytest.raises(KeyError):
        build_policy("not-a-policy")
