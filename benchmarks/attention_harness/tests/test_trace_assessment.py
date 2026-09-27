from __future__ import annotations

from copy import deepcopy

import pytest

from benchmarks.attention_harness.core.assessment import assess_trace
from benchmarks.attention_harness.core.policies import DecisionAction, PolicyContext, build_policy
from benchmarks.attention_harness.core.models import RequestType


def packet(kind: str, *, trace_id: str = "trace-1", code: str = "a") -> dict:
    if kind == "grasp":
        event = dict(event_id="sdk-0", sequence=1, source="robot_sdk.gripper",
                     event_type="sdk.gripper_command", operation="gripper.close",
                     status="failed", visibility=["advisor"],
                     error={"type": "GraspError", "message": "close failed"},
                     evidence_refs=["frame-1"])
    else:
        event = dict(event_id="execution-finished", sequence=1,
                     source="attention_harness", event_type="execution.finished",
                     operation="run_policy", status="failed", visibility=["advisor"],
                     error={"type": "TaskOutcomeFailure", "message": "goal not verified"},
                     evidence_refs=["frame-1"])
    return dict(trace_id=trace_id, events=[event], evidence=[{"evidence_id": "frame-1"}],
                code={"sha256": code * 64}, hypothesis="the grasp seems off")


def decision(assessment, *, policy="trace_aware_hint_only", failures=1, memory=0):
    return build_policy(policy).decide(PolicyContext(
        run_id="r", attempt_index=failures - 1, failure_index=failures - 1,
        consecutive_failures=failures, assistance_remaining=2, evidence_count=1,
        has_hypothesis=True, matching_memory_count=memory, assessment=assessment,
    ))


def test_equal_evidence_count_different_failure_content_changes_decision():
    grasp = assess_trace(packet("grasp"))
    unknown = assess_trace(packet("unknown"))
    assert grasp.evidence_sufficient and grasp.locally_repairable
    assert not unknown.evidence_sufficient
    assert decision(grasp).action is DecisionAction.RETRY
    assert decision(unknown).action is DecisionAction.INSPECT_TRACE
    assert decision(grasp).event_ids == ("sdk-0",)
    assert decision(grasp).evidence_ids == ("frame-1",)


def test_oracle_fields_and_hypothesis_do_not_change_assessment_or_decision():
    visible = packet("grasp")
    injected = deepcopy(visible)
    injected.update(native_success=True, oracle_pose=[1, 2, 3], evaluator_debug="hidden")
    injected["hypothesis"] = "opposite, unsupported claim"
    injected["events"].append(dict(event_id="oracle", sequence=2, source="native_evaluator",
                                   event_type="evaluator.result", operation="native_success",
                                   status="failed", visibility=["advisor"],
                                   error={"type": "CollisionError"}))
    injected["events"][0]["result"] = {"object_pose": [1, 2, 3], "native_success": True}
    first, second = assess_trace(visible), assess_trace(injected)
    assert first == second
    assert decision(first) == decision(second)


def test_repetition_code_change_memory_and_budget():
    first = packet("grasp")
    same = packet("grasp", trace_id="trace-2")
    assessment = assess_trace(same, [first])
    assert assessment.repeated_failure and assessment.code_changed is False
    assert assessment.prior_trace_ids == ("trace-1",)
    assert decision(assessment, failures=2).request_type.value == "hint"
    changed = assess_trace(packet("grasp", trace_id="trace-3", code="b"), [first])
    assert changed.code_changed is True
    assert decision(changed, failures=2).reason == "same_failure_after_code_change"
    assert decision(assessment, policy="full_trace_aware_attention_planner",
                    failures=2, memory=1).action is DecisionAction.RETRIEVE_MEMORY
    assert decision(assessment, failures=2, memory=1).request_type.value == "hint"


@pytest.mark.parametrize("policy", ["trace_aware_hint_only", "full_trace_aware_attention_planner"])
def test_both_trace_policies_share_assessment_and_never_trust_hypothesis(policy):
    unknown = assess_trace(packet("unknown"))
    assert decision(unknown, policy=policy).action is DecisionAction.INSPECT_TRACE
    assert decision(unknown, policy=policy, failures=2).request_type.value == "hint"


def test_full_trace_safety_interrupt_is_local_and_evidence_bound():
    visible = packet("grasp")
    visible["events"][0]["source"] = "safety_monitor"
    visible["events"][0]["error"]["type"] = "CollisionRisk"
    assessment = assess_trace(visible)
    chosen = decision(assessment, policy="full_trace_aware_attention_planner")
    assert (chosen.action, chosen.request_type, chosen.reason) == (
        DecisionAction.STOP, RequestType.INTERRUPT, "trace_safety_interrupt")
    assert chosen.event_ids == ("sdk-0",)
    assert chosen.evidence_ids == ("frame-1",)
    assert decision(assessment).action is DecisionAction.STOP
