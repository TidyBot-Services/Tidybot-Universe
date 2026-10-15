"""Asynchronous request mechanism: waiting work, dispositions, approval gating."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.robocasa_native.safety_monitor import (
    ApprovalRequired, SafetyMonitorBackend,
)
from benchmarks.attention_harness.sim_gt_attention_run import run_robosuite_attention

from test_robosuite_memory import FakeClient, _adapter

GUIDANCE = "close the gripper after aligning with the object"


def advisor_reply(request):
    request_type = json.loads(request["messages"][1]["content"])["request_type"]
    return json.dumps({
        "schema_version": "attentionbench.advisor-advice.v1",
        "request_type": request_type, "diagnosis": "grasp was not completed",
        "guidance": GUIDANCE, "caution": "keep within the workspace", "confidence": 0.8,
    })


def run(tmp_path, robot_policy, **overrides):
    options = dict(
        task_id="cube_lift", seed=101, artifact_root=tmp_path, policy_id="reactive_help",
        robot_policy=robot_policy,
        adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        max_attempts=4, assistance_credits=1, advisor_transport=advisor_reply,
        sleeper=lambda _: None,
    )
    options.update(overrides)
    return run_robosuite_attention(**options)


def guided_policy(contexts):
    def policy(sdk, context):
        contexts.append(context)
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()
    return policy


def store_of(result) -> AttentionStore:
    return AttentionStore(Path(result["store"]))


def test_async_hint_runs_an_autonomous_attempt_then_applies_the_reply(tmp_path):
    contexts: list = []
    result = run(tmp_path, guided_policy(contexts))
    guidance = [c["attention_input"].get("advisor_guidance") for c in contexts]
    # attempt 0 fails -> request -> attempt 1 runs while the reply is pending -> attempt 2 is guided
    assert guidance == [None, None, GUIDANCE]
    assert result["native_success"] is True and result["request_mode"] == "async"
    row = result["requests"][0]
    assert row["request_mode"] == "async" and row["blocking_reason"] is None
    assert row["waiting_activities"] == 1
    store = store_of(result)
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    activities = store.waiting_activities(run_id)
    assert [(a["activity"], a["robot_motion"], a["attempt_index"]) for a in activities] == [
        ("autonomous_retry", True, 1)]
    # The planner chooses the waiting work twice: when the request is raised and
    # again after the waiting attempt fails, both times without asking again.
    waiting_decisions = [d for d in result["decisions"] if d["waiting_for_request"]]
    # With a single credit the reserved credit leaves nothing to ask with.
    assert [(d["action"], d["reason"]) for d in waiting_decisions] == [
        ("retry", "assistance_budget_exhausted")] * 2
    assert store.request_disposition(row["request_id"])["disposition"] == "accepted"
    linked = [e["payload"] for e in store.events() if e["event_type"] == "response.execution_linked"]
    assert linked[0]["execution_id"] == result["attempts"][2]["attention_trace"]["execution_id"]
    window = store.waiting_windows(run_id)[row["request_id"]]
    assert window["ended_at"] is not None and window["attempt_dispatches"] == 1
    assert result["resource_usage"]["assistance_credits"] == 1
    assert result["request_dispositions"][row["request_id"]]["disposition"] == "accepted"


def test_request_explains_why_what_and_which_evidence(tmp_path):
    result = run(tmp_path, guided_policy([]))
    explanation = result["requests"][0]["explanation"]
    assert explanation["reason_code"] == "request_after_each_failure"
    assert explanation["why"] and explanation["assistance_requested"]
    assert explanation["evidence_shown"]["trace_id"]
    assert explanation["evidence_shown"]["projection_sha256"]
    assert "continues with safe work" in explanation["while_waiting"]
    stored = store_of(result).get_request(result["requests"][0]["request_id"])
    assert stored.explanation == explanation


def test_reply_is_ignored_when_autonomous_retry_succeeds_first(tmp_path):
    contexts: list = []

    def policy(sdk, context):
        contexts.append(context)
        if len(contexts) == 2:
            sdk.gripper.close()

    result = run(tmp_path, policy)
    assert result["native_success"] is True and len(contexts) == 2
    row = result["requests"][0]
    assert row["disposition"] == "ignored" and row["ignored_reason"] == "native_success_first"
    store = store_of(result)
    assert store.request_disposition(row["request_id"])["disposition"] == "ignored"
    assert any(e["event_type"] == "response.ignored" for e in store.events())
    # The credit was spent because the Advisor did answer; the accounting is not hidden.
    assert result["resource_usage"]["assistance_credits"] == 1
    assert store.budget_status(result["attempts"][0]["attention_trace"]["run_id"])["used"] == 1
    with pytest.raises(StateConflictError):
        store.record_response_use(row["request_id"], response_id=row["response_id"], use="guidance")


def test_blocking_mode_is_the_ablation_baseline(tmp_path):
    contexts: list = []
    result = run(tmp_path, guided_policy(contexts), request_mode="blocking")
    assert [c["attention_input"].get("advisor_guidance") for c in contexts] == [None, GUIDANCE]
    row = result["requests"][0]
    assert row["request_mode"] == "blocking" and row["blocking_reason"] == "request_mode_blocking"
    assert row["waiting_activities"] == 0
    assert store_of(result).waiting_activities(
        result["attempts"][0]["attention_trace"]["run_id"]) == []


def test_last_remaining_attempt_waits_for_the_reply(tmp_path):
    contexts: list = []
    result = run(tmp_path, guided_policy(contexts), max_attempts=2)
    assert [c["attention_input"].get("advisor_guidance") for c in contexts] == [None, GUIDANCE]
    assert result["requests"][0]["blocking_reason"] == "last_attempt_remaining"


def test_invalid_request_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="request_mode"):
        run(tmp_path, guided_policy([]), request_mode="eventually")


def test_only_one_request_is_outstanding(tmp_path):
    contexts: list = []
    result = run(tmp_path, guided_policy(contexts), assistance_credits=3, max_attempts=5)
    # Reactive help would ask after every failure, but a pending request turns asks into retries.
    assert len(result["requests"]) == 1
    assert any(d["reason"] == "request_already_pending" for d in result["decisions"])


def test_live_human_async_fallback_applies_after_the_deadline(tmp_path):
    contexts: list = []

    def policy(sdk, context):
        contexts.append(context)
        time.sleep(0.2)
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()

    result = run(tmp_path, policy, max_attempts=6, assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,
                 human_deadline_seconds=0.5, sleeper=time.sleep)
    assert result["native_success"] is True
    row = result["requests"][0]
    assert row["responder"] == "advisor_proxy" and row["request_mode"] == "async"
    # The robot kept working while no person answered.
    assert row["waiting_activities"] >= 2
    store = store_of(result)
    events = {e["event_type"] for e in store.events()}
    assert {"request.timeout_observed", "request.fallback"} <= events
    first_guided = next(i for i, c in enumerate(contexts) if c["attention_input"].get("advisor_guidance"))
    assert first_guided >= 3


def test_live_human_reply_during_waiting_attempt_is_used_next(tmp_path):
    contexts: list = []
    store_path = tmp_path / "attention.sqlite3"

    def policy(sdk, context):
        contexts.append(context)
        time.sleep(0.3)
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()

    def human():
        store = AttentionStore(store_path)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            created = [e["entity_id"] for e in store.events() if e["event_type"] == "request.created"]
            if created:
                AttentionRuntime(store, None, clock=time.time).submit_human_response(
                    created[0], response_id="human-response:test", content=GUIDANCE)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=human, daemon=True)
    thread.start()
    result = run(tmp_path, policy, store_path=store_path, max_attempts=5,
                 assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST, human_deadline_seconds=20.0,
                 sleeper=time.sleep)
    thread.join(timeout=5)
    assert result["native_success"] is True
    row = result["requests"][0]
    assert row["responder"] == "human"
    assert [c["attention_input"].get("advisor_guidance") is not None for c in contexts][:3] == [
        False, False, True]
    events = {e["event_type"] for e in store_of(result).events()}
    assert "request.fallback" not in events


def test_operator_ignoring_a_pending_request_routes_it_to_the_advisor(tmp_path):
    contexts: list = []
    store_path = tmp_path / "attention.sqlite3"

    def policy(sdk, context):
        contexts.append(context)
        time.sleep(0.2)
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()

    def operator():
        store = AttentionStore(store_path)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            created = [e["entity_id"] for e in store.events() if e["event_type"] == "request.created"]
            if created:
                AttentionRuntime(store, None, clock=time.time).decline(created[0], actor="tester")
                return
            time.sleep(0.02)

    thread = threading.Thread(target=operator, daemon=True)
    thread.start()
    result = run(tmp_path, policy, store_path=store_path, max_attempts=5,
                 assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST, human_deadline_seconds=30.0,
                 sleeper=time.sleep)
    thread.join(timeout=5)
    assert result["native_success"] is True
    assert result["requests"][0]["responder"] == "advisor_proxy"
    events = store_of(result).events()
    ignored = [e["payload"] for e in events if e["event_type"] == "request.ignored"]
    assert ignored and ignored[0]["actor"] == "tester" and ignored[0]["reason"] == "operator_declined"
    assert not any(e["event_type"] == "request.timeout_observed" for e in events)


def test_unanswered_request_is_cancelled_and_its_credit_returned(tmp_path):
    def policy(sdk, context):
        time.sleep(0.1)

    result = run(tmp_path, policy, max_attempts=3, assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,
                 human_deadline_seconds=60.0, sleeper=time.sleep)
    assert result["native_success"] is False
    store = store_of(result)
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    request_id = f"attention-request:{Path(result['artifact_dir']).name}:0"
    assert store.get_request(request_id).state.value == "cancelled"
    assert store.budget_status(run_id) == {"limit": 1, "used": 0, "reserved": 0, "remaining": 1}
    assert result["resource_usage"]["assistance_credits"] == 0


def test_operator_cannot_ignore_a_proxy_request_or_a_decided_one(tmp_path):
    result = run(tmp_path, guided_policy([]))
    store = store_of(result)
    request_id = result["requests"][0]["request_id"]
    runtime = AttentionRuntime(store, None, clock=time.time)
    with pytest.raises(StateConflictError):
        runtime.decline(request_id)


# -- approval gating ------------------------------------------------------------


class RecordingBackend:
    control_frame = "robosuite_world"

    def __init__(self):
        self.calls = []

    def observe(self):
        import numpy as np
        return {"robot0_eef_pos": np.zeros(3), "robot0_joint_pos": np.zeros(7)}

    def move_arm_delta(self, dx, dy, dz, rotation_delta):
        self.calls.append(("arm", dx, dy, dz))

    def set_gripper(self, command, *, settle_steps):
        self.calls.append(("gripper", command))

    def move_base_delta(self, dx, dy, dtheta=0.0, *, frame="local"):
        self.calls.append(("base", dx, dy))


def test_soft_limit_withholds_the_action_until_approved():
    backend = RecordingBackend()
    monitor = SafetyMonitorBackend(backend, max_delta_m=0.25,
                                   approval_rules={"soft_max_delta_m": 0.1})
    monitor.move_arm_delta(0.05, 0, 0, (0, 0, 0))
    with pytest.raises(ApprovalRequired):
        monitor.move_arm_delta(0.2, 0, 0, (0, 0, 0))
    assert backend.calls == [("arm", 0.05, 0, 0)]  # the large move never ran
    assert monitor.approvals_required[0]["rule"] == "soft_max_delta_m"
    assert monitor.violations == []  # approval is not an unsafe violation
    with pytest.raises(Exception, match="delta_exceeds_limit"):
        monitor.move_arm_delta(0.3, 0, 0, (0, 0, 0))  # the hard limit is still unsafe
    approved = SafetyMonitorBackend(backend, max_delta_m=0.25,
                                    approval_rules={"soft_max_delta_m": 0.1}, approval_granted=True)
    approved.move_arm_delta(0.2, 0, 0, (0, 0, 0))
    assert backend.calls[-1] == ("arm", 0.2, 0, 0)
    assert any(e["kind"] == "approval_used" for e in approved.events)


def test_release_after_grasp_and_base_rules():
    backend = RecordingBackend()
    monitor = SafetyMonitorBackend(backend, approval_rules={
        "gripper_release_after_grasp": True, "soft_max_base_delta_m": 0.1})
    monitor.set_gripper(-1.0, settle_steps=1)  # opening before any grasp is fine
    monitor.set_gripper(1.0, settle_steps=1)
    with pytest.raises(ApprovalRequired):
        monitor.set_gripper(-1.0, settle_steps=1)
    with pytest.raises(ApprovalRequired):
        monitor.move_base_delta(0.2, 0.0)
    assert [c[0] for c in backend.calls] == ["gripper", "gripper"]


@pytest.mark.parametrize("rules", [{"unknown": 1}, {"soft_max_delta_m": 0.5},
                                   {"soft_max_delta_m": -1}, {"gripper_release_after_grasp": False}])
def test_invalid_approval_rules_are_rejected(rules):
    with pytest.raises(ValueError):
        SafetyMonitorBackend(RecordingBackend(), approval_rules=rules)


def approval_policy(contexts):
    def policy(sdk, context):
        contexts.append(context)
        sdk.arm.move_delta(0.2, 0.0, 0.0)
        sdk.gripper.close()
    return policy


def test_approval_flows_from_the_monitor_to_the_next_attempt(tmp_path):
    contexts: list = []
    result = run(tmp_path, approval_policy(contexts), policy_id="full_trace_aware_attention_planner",
                 max_attempts=3, approval_rules={"soft_max_delta_m": 0.1},
                 approval_granted=lambda row: True)
    assert result["native_success"] is True
    first = result["attempts"][0]
    assert first["native_success"] is False
    assert result["safety_signals"][0] == {"approval_required": True}
    request = result["requests"][0]
    assert request["request_type"] == "approval" and request["request_mode"] == "blocking"
    assert request["blocking_reason"] == "approval_gates_next_action"
    withheld = request["explanation"]["withheld_actions"][0]
    assert withheld["rule"] == "soft_max_delta_m" and withheld["action"] == "move_arm_delta"
    assert contexts[1]["attention_input"]["approval_granted"] is True
    run_id = first["attention_trace"]["run_id"]
    activities = store_of(result).waiting_activities(run_id)
    assert [(a["activity"], a["robot_motion"]) for a in activities] == [("approval_gate_hold", False)]
    safety = json.loads(Path(first["safety_artifact"]).read_text())
    assert safety["approvals_required"][0]["rule"] == "soft_max_delta_m"


def test_denied_approval_stops_fail_closed(tmp_path):
    contexts: list = []
    result = run(tmp_path, approval_policy(contexts), policy_id="full_trace_aware_attention_planner",
                 max_attempts=3, approval_rules={"soft_max_delta_m": 0.1},
                 approval_granted=lambda row: False)
    assert result["native_success"] is False
    assert result["stopped_reason"] == "approval_not_granted"
    assert len(contexts) == 1


def test_policies_without_approval_cannot_proceed_past_a_gate(tmp_path):
    contexts: list = []
    result = run(tmp_path, approval_policy(contexts), policy_id="reactive_help",
                 max_attempts=3, approval_rules={"soft_max_delta_m": 0.1})
    assert result["stopped_reason"] == "approval_required_policy_cannot_proceed"
    assert result["requests"] == [] and len(contexts) == 1


# -- UI server read model and controls -------------------------------------------


def test_dashboard_snapshot_exposes_explanation_disposition_and_waiting_work(tmp_path):
    from benchmarks.attention_harness.ui_server import dashboard_snapshot

    result = run(tmp_path, guided_policy([]))
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    snapshot = dashboard_snapshot(store_of(result), run_id)
    request = snapshot["requests"][0]
    assert request["explanation"]["reason_code"] == "request_after_each_failure"
    assert snapshot["request_dispositions"][request["request_id"]]["disposition"] == "accepted"
    assert [a["activity"] for a in snapshot["waiting_activity"]] == ["autonomous_retry"]
    assert snapshot["scheduler_options"] == {
        "request_mode": "async", "approval_rules": {}, "assistance_mode": "benchmark_proxy"}


def test_ignore_endpoint_routes_a_pending_human_request_to_the_advisor(tmp_path):
    import re
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    from benchmarks.attention_harness.ui_server import create_server

    store_path = tmp_path / "attention.sqlite3"
    store = AttentionStore(store_path)
    server = create_server(store=store, port=0)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    base = f"http://127.0.0.1:{server.server_port}"
    with urlopen(base + "/ui/") as response:
        token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)

    def post(path, body=b"{}"):
        return urlopen(Request(base + path, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-Attention-Token": token}))

    def policy(sdk, context):
        time.sleep(0.2)
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()

    def operator():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            created = [e["entity_id"] for e in store.events() if e["event_type"] == "request.created"]
            if created:
                with post(f"/api/requests/{created[0]}/ignore") as reply:
                    assert json.load(reply)["state"] == "fallback"
                return
            time.sleep(0.02)

    thread = threading.Thread(target=operator, daemon=True)
    thread.start()
    try:
        result = run(tmp_path, policy, store_path=store_path, max_attempts=5,
                     assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST, human_deadline_seconds=30.0,
                     sleeper=time.sleep)
        thread.join(timeout=5)
        assert result["requests"][0]["responder"] == "advisor_proxy"
        request_id = result["requests"][0]["request_id"]
        with post(f"/api/requests/{request_id}/ignore") as repeated:
            assert repeated.status == 200  # idempotent: the earlier decision stands
        proxy = run(tmp_path, guided_policy([]), store_path=store_path)
        with pytest.raises(HTTPError) as conflict:
            post(f"/api/requests/{proxy['requests'][0]['request_id']}/ignore")
        assert conflict.value.code == 409  # a benchmark-proxy request has no human to ignore it
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


# -- the planner chooses the safe work while a request is pending -----------------


def test_full_planner_inspects_the_trace_while_its_request_is_pending(tmp_path, monkeypatch):
    import benchmarks.attention_harness.sim_gt_attention_run as scheduler
    from benchmarks.attention_harness.core.assessment import TraceAssessment

    def assessment(trace, prior):
        return TraceAssessment(
            trace_id=trace["trace_id"], failure_type="unknown", repeated_failure=False,
            code_changed=None, progress=False, evidence_sufficient=False,
            locally_repairable=False, risk="normal", event_ids=(), evidence_ids=())

    monkeypatch.setattr(scheduler, "assess_trace", assessment)
    contexts: list = []
    result = run(tmp_path, guided_policy(contexts), policy_id="full_trace_aware_attention_planner",
                 max_attempts=5)
    actions = [(d["after_attempt"], d["action"], d["reason"], d["waiting_for_request"] is not None)
               for d in result["decisions"]]
    # attempt 0: not enough visible evidence -> inspect; attempt 1: still unknown -> ask,
    # then keep inspecting the trace (not retrying blind) while the reply is pending.
    assert actions[0][:3] == (0, "inspect_trace", "insufficient_visible_evidence")
    assert actions[1][:3] == (1, "request", "cause_unknown_after_inspection")
    waiting = [a for a in actions if a[3]]
    assert waiting and waiting[0][1] == "inspect_trace"
    assert contexts[2]["attention_input"].get("inspected_trace")
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    kinds = [a["activity"] for a in store_of(result).waiting_activities(run_id)]
    assert kinds[0] == "trace_inspection"
