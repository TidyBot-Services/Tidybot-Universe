from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from benchmarks.attention_harness.core.policies import POLICY_IDS
from benchmarks.attention_harness.core.models import MemoryRecord, MemoryStatus
from benchmarks.attention_harness.core.assessment import TraceAssessment
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.ui_server import create_server, dashboard_snapshot
from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.sim_gt_attention_run import (
    run_robocasa_attention, run_robosuite_attention, run_sim_gt_attention,
)

from test_sim_gt_runner import FakeActionBackend, FakeWorld
from test_robosuite_memory import FakeClient, _adapter
from demo_fixture import approved_demo


def advisor_reply(request):
    request_type = json.loads(request["messages"][1]["content"])["request_type"]
    return json.dumps({
        "schema_version": "attentionbench.advisor-advice.v1",
        "request_type": request_type,
        "diagnosis": "grasp was not completed",
        "guidance": "close the gripper after aligning with the object",
        "caution": "keep within the workspace",
        "confidence": 0.8,
    })


def test_live_human_hint_reaches_next_attempt_and_records_execution(tmp_path):
    store_path = tmp_path / "attention.sqlite3"
    store = AttentionStore(store_path)
    server = create_server(store=store, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    with urlopen(base + "/ui/") as response:
        token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)
    contexts = []
    outcome = {}

    def robot_policy(sdk, context):
        contexts.append(context)
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()

    def run():
        outcome["result"] = run_robosuite_attention(
            task_id="cube_lift", seed=101, artifact_root=tmp_path,
            policy_id="reactive_help", robot_policy=robot_policy,
            adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
            store_path=store_path, max_attempts=2, assistance_credits=1,
            assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,
            human_deadline_seconds=5.0, sleeper=time.sleep,
            advisor_transport=advisor_reply,
        )

    run_thread = threading.Thread(target=run, daemon=True)
    run_thread.start()
    try:
        deadline = time.monotonic() + 10
        request_id = None
        while time.monotonic() < deadline:
            pending = [event["entity_id"] for event in store.events()
                       if event["event_type"] == "request.created"]
            if pending:
                request_id = pending[-1]
                break
            time.sleep(0.05)
        assert request_id is not None
        body = json.dumps({"content": "close the gripper after alignment"}).encode()
        with urlopen(Request(base + "/api/requests/" + request_id + "/respond",
                             data=body, method="POST", headers={
                                 "Content-Type": "application/json",
                                 "X-Attention-Token": token,
                             })) as response:
            assert json.load(response)["state"] == "answered"
        run_thread.join(timeout=15)
        assert not run_thread.is_alive()
        result = outcome["result"]
        assert result["native_success"] is True
        assert contexts[1]["attention_input"]["advisor_guidance"] == "close the gripper after alignment"
        assert result["requests"][0]["responder"] == "human"
        uses = [event["payload"] for event in store.events()
                if event["event_type"] == "response.execution_linked"]
        assert uses[0]["execution_id"] == result["attempts"][1]["attention_trace"]["execution_id"]
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_live_human_timeout_uses_proxy_fallback(tmp_path):
    store_path = tmp_path / "attention.sqlite3"

    def robot_policy(sdk, context):
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()

    result = run_robosuite_attention(
        task_id="cube_lift", seed=101, artifact_root=tmp_path,
        policy_id="reactive_help", robot_policy=robot_policy,
        adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        store_path=store_path, max_attempts=2, assistance_credits=1,
        assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,
        human_deadline_seconds=0.05, sleeper=time.sleep,
        advisor_transport=advisor_reply,
    )
    events = AttentionStore(store_path).events()
    assert result["native_success"] is True
    assert result["requests"][0]["responder"] == "advisor_proxy"
    assert any(event["event_type"] == "request.fallback" for event in events)
    assert any(event["event_type"] == "request.timeout_observed" for event in events)
    waiting = [event["payload"] for event in events if event["event_type"] == "work.waiting_completed"]
    assert len(waiting) == 1 and waiting[0]["robot_actions"] == 0
    assert any(event["event_type"] == "response.execution_linked" for event in events)


def test_live_human_wait_cancels_if_wall_clock_stalls(tmp_path):
    ticks = iter(range(100))
    result = run_robosuite_attention(
        task_id="cube_lift", seed=101, artifact_root=tmp_path,
        policy_id="reactive_help", robot_policy=lambda sdk, context: None,
        adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        max_attempts=2, assistance_credits=1,
        assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,
        human_deadline_seconds=0.05, clock=lambda: 100.0,
        monotonic=lambda: float(next(ticks)), sleeper=lambda _: None,
        advisor_transport=advisor_reply,
    )
    assert result["stopped_reason"] == "human_deadline_clock_stalled"
    assert len(result["attempts"]) == 1
    store = AttentionStore(Path(result["store"]))
    assert any(event["event_type"] == "request.cancelled" for event in store.events())


def test_live_human_approval_is_used_by_next_attempt(tmp_path):
    store_path = tmp_path / "attention.sqlite3"
    store = AttentionStore(store_path)
    server = create_server(store=store, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    with urlopen(base + "/ui/") as response:
        token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)
    contexts = []
    outcome = {}

    def policy(sdk, context):
        contexts.append(context)
        if context["attention_input"].get("approval_granted"):
            sdk.gripper.close()

    def run():
        outcome["result"] = run_robosuite_attention(
            task_id="cube_lift", seed=101, artifact_root=tmp_path,
            policy_id="full_trace_aware_attention_planner", robot_policy=policy,
            adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
            store_path=store_path, max_attempts=2, assistance_credits=1,
            assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,
            human_deadline_seconds=5.0, sleeper=time.sleep,
            advisor_transport=advisor_reply,
            safety_signals=lambda result: {"approval_required": not result["native_success"]},
        )

    run_thread = threading.Thread(target=run, daemon=True)
    run_thread.start()
    try:
        deadline = time.monotonic() + 10
        request_id = None
        while time.monotonic() < deadline:
            pending = [event["entity_id"] for event in store.events()
                       if event["event_type"] == "request.created"]
            if pending:
                request_id = pending[-1]
                break
            time.sleep(0.05)
        assert request_id is not None
        with urlopen(Request(base + "/api/requests/" + request_id + "/respond",
                             data=b'{"decision":"approve"}', method="POST", headers={
                                 "Content-Type": "application/json",
                                 "X-Attention-Token": token,
                             })) as response:
            assert json.load(response)["state"] == "answered"
        run_thread.join(timeout=15)
        assert not run_thread.is_alive()
        result = outcome["result"]
        assert result["native_success"] is True
        assert contexts[1]["attention_input"]["approval_granted"] is True
        assert len({item["attention_trace"]["run_id"] for item in result["attempts"]}) == 1
        run_id = result["attempts"][0]["attention_trace"]["run_id"]
        assert len(dashboard_snapshot(store, run_id)["attempts"]) == 2
        assert store.get_run(run_id)["status"] == "completed"
    finally:
        server.shutdown(); server.server_close(); server_thread.join(timeout=2)


def test_emergency_interrupt_cancels_live_simulator_run(tmp_path):
    store_path = tmp_path / "attention.sqlite3"
    store = AttentionStore(store_path)
    server = create_server(store=store, port=0)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    with urlopen(base + "/ui/") as response:
        token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)
    entered = threading.Event()
    release = threading.Event()
    outcome = {}

    def policy(_sdk, _context):
        entered.set()
        release.wait(timeout=5)

    def run():
        outcome["result"] = run_robosuite_attention(
            task_id="cube_lift", seed=101, artifact_root=tmp_path,
            policy_id="reactive_help", robot_policy=policy,
            adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
            store_path=store_path, max_attempts=2, assistance_credits=1,
            assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST,
            human_deadline_seconds=5.0, advisor_transport=advisor_reply,
        )

    run_thread = threading.Thread(target=run, daemon=True)
    run_thread.start()
    try:
        assert entered.wait(timeout=5)
        run_id = next(event["entity_id"] for event in store.events()
                      if event["event_type"] == "run.created")
        with urlopen(Request(base + "/api/runs/" + run_id + "/interrupt",
                             method="POST", data=b"", headers={
                                 "X-Attention-Token": token,
                             })) as response:
            assert json.load(response)["state"] == "requested"
        release.set()
        run_thread.join(timeout=15)
        assert not run_thread.is_alive()
        assert outcome["result"]["stopped_reason"] == "emergency_interrupt"
        assert store.get_run(run_id)["status"] == "cancelled"
        assert store.interrupt_status(run_id)["state"] == "stopped"
    finally:
        release.set()
        server.shutdown(); server.server_close(); server_thread.join(timeout=2)


def test_resource_exhausted_trial_is_persisted_as_blocked_work(tmp_path, monkeypatch):
    store_path = tmp_path / "attention.sqlite3"
    with monkeypatch.context() as patch:
        patch.setattr(AttentionStore, "resource_status", lambda self, run_id: {
            "execution_seconds": {"remaining": 0.0},
        })
        result = run_sim_gt_attention(
            suite="robosuite", task_id="cube_lift", seed=101,
            artifact_root=tmp_path, policy_id="autonomous",
            attempt_executor=lambda **kwargs: pytest.fail("blocked attempt was launched"),
            max_attempts=1, assistance_credits=0, store_path=store_path,
        )
    assert result["stopped_reason"] == "resource_blocked"
    store = AttentionStore(store_path)
    run_id = next(event["entity_id"] for event in store.events()
                  if event["event_type"] == "run.created")
    assert store.get_run(run_id)["status"] == "failed"
    snapshot = dashboard_snapshot(store, run_id)
    work = snapshot["work_items"]
    assert work[0]["state"] == "blocked"
    assert work[0]["resource"] == "execution_seconds"
    assert work[0]["required"] == 300.0
    assert snapshot["comparison"]["policies"] == []


def test_attempt_executor_failure_is_visible_without_stale_running_run(tmp_path):
    store_path = tmp_path / "attention.sqlite3"

    def failing_executor(**_kwargs):
        raise ConnectionError("simulator unavailable")

    result = run_sim_gt_attention(
        suite="robosuite", task_id="cube_lift", seed=101,
        artifact_root=tmp_path, policy_id="autonomous",
        attempt_executor=failing_executor, max_attempts=1,
        assistance_credits=0, store_path=store_path,
    )
    assert result["stopped_reason"] == "attempt_executor_failed"
    store = AttentionStore(store_path)
    run_id = next(event["entity_id"] for event in store.events()
                  if event["event_type"] == "run.created")
    snapshot = dashboard_snapshot(store, run_id)
    assert snapshot["run"]["status"] == "failed"
    assert snapshot["work_items"][0]["state"] == "failed"
    assert "simulator unavailable" in snapshot["work_items"][0]["reason"]
    assert snapshot["comparison"]["policies"] == []


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
@pytest.mark.parametrize("policy_id", POLICY_IDS)
def test_all_seven_policies_use_both_native_gt_runners(tmp_path, suite, policy_id):
    contexts = []
    if suite == "robocasa":
        world = FakeWorld()

        def robot_policy(sdk, context):
            contexts.append(context)
            if len(contexts) >= 3 or context["attention_input"].get("advisor_guidance"):
                sdk.gripper.close()

        run = lambda: run_robocasa_attention(
            task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
            policy_id=policy_id, robot_policy=robot_policy,
            backend_factory=lambda: FakeActionBackend(world),
            client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
            hypothesis="gripper never closed",
            max_attempts=3, assistance_credits=2, advisor_transport=advisor_reply,
            sleeper=lambda _: None,
            demo_prior=(demo := approved_demo(tmp_path, "robocasa", "counter_to_sink")) if policy_id == "demo_first" else None,
            approved_demo_sha256=hashlib.sha256(demo.encode()).hexdigest() if policy_id == "demo_first" else None,
            policy_config={"target_request_count": 2} if policy_id == "budget_matched_random_escalation" else None,
        )
    else:
        def robot_policy(sdk, context):
            contexts.append(context)
            if len(contexts) >= 3 or context["attention_input"].get("advisor_guidance"):
                sdk.gripper.close()

        run = lambda: run_robosuite_attention(
            task_id="cube_lift", seed=101, artifact_root=tmp_path,
            policy_id=policy_id, robot_policy=robot_policy,
            adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
            hypothesis="gripper never closed",
            max_attempts=3, assistance_credits=2, advisor_transport=advisor_reply,
            sleeper=lambda _: None,
            demo_prior=(demo := approved_demo(tmp_path, "robosuite", "cube_lift")) if policy_id == "demo_first" else None,
            approved_demo_sha256=hashlib.sha256(demo.encode()).hexdigest() if policy_id == "demo_first" else None,
            policy_config={"target_request_count": 2} if policy_id == "budget_matched_random_escalation" else None,
        )
    result = run()
    assert result["suite"] == suite
    assert result["policy_id"] == policy_id
    assert result["native_success"] is True
    assert result["formal_eligible"] is False
    assert len(result["attempts"]) in {2, 3}
    assert all(row["attention_trace"]["raw_trace_id"] for row in result["attempts"])
    assert all(row["safety_artifact"] and Path(row["safety_artifact"]).is_file()
               for row in result["attempts"])
    assert result["resource_usage"]["assistance_credits"] <= 2
    assert (tmp_path / result["artifact_dir"].split("/")[-1] / "attention_run.json").is_file()
    if policy_id == "demo_first":
        assert contexts[0]["attention_input"]["demo_prior"]
        assert result["decisions"][0]["action"] == "use_demo"
        assert result["requests"] == []
        assert result["demo_receipt"]["approval"]["id"] == "fixture-approval"
        assert "oracle" not in contexts[0]["attention_input"]["demo_prior"]
        public = json.loads(contexts[0]["attention_input"]["demo_prior"])
        assert all(Path(item["path"]).is_relative_to(result["artifact_dir"])
                   for item in public["assets"])
        assert all(hashlib.sha256(Path(item["path"]).read_bytes()).hexdigest() == item["sha256"]
                   for item in public["assets"])
    if policy_id == "budget_matched_random_escalation":
        assert result["random_preregistration"]["target_request_count"] == 2
        assert result["random_matching"]["deviation"] >= 0
        assert all(row["assessment"] is None for row in result["decisions"])
    if policy_id in {"reactive_help", "retry_k_then_ask", "budget_matched_random_escalation",
                     "trace_aware_hint_only", "full_trace_aware_attention_planner"}:
        assert any(row["request_type"] == "hint" for row in result["decisions"])
        assert result["requests"]
    if policy_id == "full_trace_aware_attention_planner":
        hint = next(row for row in result["decisions"] if row["request_type"] == "hint")
        assert hint["decision_source"] == "projected_public_trace"
        assert hint["event_ids"] and hint["evidence_ids"]
    if policy_id == "autonomous":
        assert result["decisions"][0]["action"] == "retry"


def test_demo_first_requires_pretask_demo(tmp_path):
    with pytest.raises(ValueError, match="pre-task demo"):
        run_robosuite_attention(
            task_id="cube_lift", seed=101, artifact_root=tmp_path,
            policy_id="demo_first", robot_policy=lambda sdk, context: None,
            adapter_factory=lambda task, camera: _adapter(task, camera),
        )


def test_demo_rejects_unapproved_or_private_trajectory_before_attempt(tmp_path):
    raw = approved_demo(tmp_path, "robosuite", "cube_lift")
    called = []
    base = dict(task_id="cube_lift", seed=101, artifact_root=tmp_path / "runs",
                policy_id="demo_first", robot_policy=lambda sdk, context: called.append(1),
                adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
                demo_prior=raw)
    with pytest.raises(ValueError, match="approved SHA-256"):
        run_robosuite_attention(**base, approved_demo_sha256="0" * 64)
    manifest = json.loads(raw)
    trajectory = Path(manifest["assets"][1]["path"])
    trajectory.write_text(json.dumps({"source": "public_sdk", "steps": [
        {"operation": "sdk.gripper.close", "arguments": {"oracle_pose": [1, 2, 3]}}
    ]}))
    with pytest.raises(ValueError, match="asset differs from approved SHA-256"):
        run_robosuite_attention(**base,
                                approved_demo_sha256=hashlib.sha256(raw.encode()).hexdigest())
    manifest["assets"][1]["sha256"] = hashlib.sha256(trajectory.read_bytes()).hexdigest()
    changed = json.dumps(manifest)
    with pytest.raises(ValueError, match="non-public SDK"):
        run_robosuite_attention(**{**base, "demo_prior": changed},
                                approved_demo_sha256=hashlib.sha256(changed.encode()).hexdigest())
    assert called == []


def test_random_requires_registered_target_within_budget(tmp_path):
    base = dict(task_id="cube_lift", seed=101, artifact_root=tmp_path,
                policy_id="budget_matched_random_escalation",
                robot_policy=lambda sdk, context: None,
                adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
                max_attempts=3, assistance_credits=1)
    with pytest.raises(ValueError, match="pre-registered target"):
        run_robosuite_attention(**base)
    with pytest.raises(ValueError, match="exceeds assistance budget"):
        run_robosuite_attention(**base, policy_config={"target_request_count": 2})


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
def test_random_prereg_early_success_records_unmet_target_without_trace_assessment(
    tmp_path, monkeypatch, suite,
):
    import benchmarks.attention_harness.sim_gt_attention_run as scheduler

    monkeypatch.setattr(scheduler, "assess_trace", lambda *args: pytest.fail("random read trace assessment"))
    common = dict(task_id="counter_to_sink" if suite == "robocasa" else "cube_lift",
                  seed=101, artifact_root=tmp_path, policy_id="budget_matched_random_escalation",
                  robot_policy=lambda sdk, context: sdk.gripper.close(),
                  max_attempts=3, assistance_credits=2,
                  policy_config={"target_request_count": 2},
                  advisor_transport=lambda _: pytest.fail("early success called Advisor"))
    if suite == "robocasa":
        world = FakeWorld()
        result = run_robocasa_attention(
            **common, backend_factory=lambda: FakeActionBackend(world),
            client_factory=lambda task: RobocasaSimClient(task, transport=world.transport))
    else:
        result = run_robosuite_attention(
            **common, adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()))
    matching = result["random_matching"]
    assert result["native_success"] is True
    assert matching["target_request_count"] == 2
    assert matching["realized_request_count"] == 0
    assert matching["deviation"] == 2
    assert matching["deviation_reason"] == "early_native_success"
    plan = Path(result["artifact_dir"]) / "random_preregistration.json"
    assert hashlib.sha256(plan.read_bytes()).hexdigest() == result["random_preregistration"]["sha256"]
    assert result["formal_eligible"] is False


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
def test_full_trace_interrupt_records_evidence_without_advisor_wait(tmp_path, monkeypatch, suite):
    import benchmarks.attention_harness.sim_gt_attention_run as scheduler

    def assessment(trace, prior):
        return TraceAssessment(trace_id=trace["trace_id"], failure_type="safety_interrupt",
                               repeated_failure=False, code_changed=None, progress=False,
                               evidence_sufficient=True, locally_repairable=False, risk="safety",
                               event_ids=("visible-safety-event",), evidence_ids=("public-frame",))

    monkeypatch.setattr(scheduler, "assess_trace", assessment)
    common = dict(task_id="counter_to_sink" if suite == "robocasa" else "cube_lift",
                  seed=101, artifact_root=tmp_path,
                  policy_id="full_trace_aware_attention_planner",
                  robot_policy=lambda sdk, context: None, max_attempts=2,
                  advisor_transport=lambda _: pytest.fail("safety interrupt called Advisor"))
    if suite == "robocasa":
        world = FakeWorld()
        result = run_robocasa_attention(
            **common, backend_factory=lambda: FakeActionBackend(world),
            client_factory=lambda task: RobocasaSimClient(task, transport=world.transport))
    else:
        result = run_robosuite_attention(
            **common, adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()))
    assert result["stopped_reason"] == "trace_safety_interrupt"
    assert result["requests"] == []
    assert result["decisions"][0]["request_type"] == "interrupt"
    assert result["decisions"][0]["priority"] == "critical"
    assert result["decisions"][0]["decision_source"] == "projected_public_trace"
    assert result["decisions"][0]["event_ids"] == ["visible-safety-event"]
    assert result["decisions"][0]["evidence_ids"] == ["public-frame"]
    assert len(result["attempts"]) == 1


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
@pytest.mark.parametrize("signal,expected_request,expected_stop", [
    ({"unsafe": True}, None, "independent_safety_stop"),
    ({"approval_required": True}, "approval", "approval_not_granted"),
])
def test_full_planner_safety_and_approval_fail_closed(
    tmp_path, suite, signal, expected_request, expected_stop,
):
    common = dict(
        task_id="counter_to_sink" if suite == "robocasa" else "cube_lift",
        seed=101, artifact_root=tmp_path,
        policy_id="full_trace_aware_attention_planner",
        robot_policy=lambda sdk, context: None,
        max_attempts=3, assistance_credits=1,
        advisor_transport=(lambda _: pytest.fail("independent safety waited for Advisor"))
            if signal.get("unsafe") else advisor_reply,
        sleeper=lambda _: None,
        safety_signals=lambda result: signal,
    )
    if suite == "robocasa":
        world = FakeWorld()
        result = run_robocasa_attention(
            **common, backend_factory=lambda: FakeActionBackend(world),
            client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
        )
    else:
        result = run_robosuite_attention(
            **common, adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        )
    assert result["native_success"] is False
    assert result["decisions"][0]["request_type"] == expected_request
    if expected_request is None:
        assert result["requests"] == []
        assert result["decisions"][0]["monitor_signals"]["unsafe"] is True
        assert Path(result["decisions"][0]["safety_artifact"]).is_file()
    else:
        assert result["requests"][0]["advice"]["request_type"] == expected_request
        assert result["decisions"][0]["event_ids"]
        assert result["decisions"][0]["evidence_ids"]
        assert result["decisions"][0]["decision_source"] == "independent_monitor"
        assert result["decisions"][0]["priority"] == "high"
    assert result["stopped_reason"] == expected_stop
    assert len(result["attempts"]) == 1


def test_independent_safety_stops_non_safety_policy(tmp_path):
    world = FakeWorld()
    result = run_robocasa_attention(
        task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
        policy_id="autonomous", robot_policy=lambda sdk, context: None,
        backend_factory=lambda: FakeActionBackend(world),
        client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
        max_attempts=3, safety_signals=lambda result: {"unsafe": True},
    )
    assert result["stopped_reason"] == "independent_safety_stop"
    assert len(result["attempts"]) == 1


def test_explicit_approval_reaches_next_attempt(tmp_path):
    world = FakeWorld()
    seen = []

    def robot_policy(sdk, context):
        seen.append(context["attention_input"])
        if context["attention_input"].get("approval_granted") is True:
            sdk.gripper.close()

    result = run_robocasa_attention(
        task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
        policy_id="full_trace_aware_attention_planner", robot_policy=robot_policy,
        backend_factory=lambda: FakeActionBackend(world),
        client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
        max_attempts=2, assistance_credits=1,
        advisor_transport=advisor_reply, sleeper=lambda _: None,
        safety_signals=lambda result: {"approval_required": not result["native_success"]},
        approval_granted=lambda request: True,
    )
    assert result["native_success"] is True
    assert seen[1]["approval_granted"] is True


def test_independent_safety_invalidates_native_success(tmp_path):
    world = FakeWorld()
    result = run_robocasa_attention(
        task_id="counter_to_sink", seed=101, artifact_root=tmp_path,
        policy_id="autonomous", robot_policy=lambda sdk, context: sdk.gripper.close(),
        backend_factory=lambda: FakeActionBackend(world),
        client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
        max_attempts=1, safety_signals=lambda result: {"unsafe": True},
    )
    assert result["attempts"][0]["native_success"] is True
    assert result["native_success"] is False
    assert result["stopped_reason"] == "independent_safety_stop"


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
def test_trace_aware_replay_records_same_visible_unknown_failure_rule(tmp_path, suite):
    common = dict(seed=101, artifact_root=tmp_path,
                  policy_id="trace_aware_hint_only", robot_policy=lambda sdk, context: None,
                  max_attempts=2, assistance_credits=1, advisor_transport=advisor_reply,
                  sleeper=lambda _: None)
    if suite == "robocasa":
        world = FakeWorld()
        result = run_robocasa_attention(
            task_id="counter_to_sink", backend_factory=lambda: FakeActionBackend(world),
            client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
            **common,
        )
    else:
        result = run_robosuite_attention(
            task_id="cube_lift",
            adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
            **common,
        )
    row = result["decisions"][0]
    assert (row["action"], row["reason"]) == ("inspect_trace", "insufficient_visible_evidence")
    assert row["assessment"]["failure_type"] == "unknown"
    assert len(row["assessment"]["code_sha256"]) == 64
    assert row["event_ids"] == ["execution-finished"]
    assert row["evidence_ids"]
    assert result["requests"] == []


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
def test_hint_only_replay_has_no_memory_catalog_or_retrieval(tmp_path, suite):
    class ForbiddenGateway:
        def retrieve(self, *args, **kwargs):
            pytest.fail("hint-only arm accessed Memory Service")

    def policy(_sdk, context):
        assert context["memory_catalog"] == []

    common = dict(seed=101, artifact_root=tmp_path,
                  policy_id="trace_aware_hint_only", robot_policy=policy,
                  max_attempts=1, memory_gateway=ForbiddenGateway())
    if suite == "robocasa":
        world = FakeWorld()
        result = run_robocasa_attention(
            task_id="counter_to_sink", backend_factory=lambda: FakeActionBackend(world),
            client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
            **common,
        )
    else:
        result = run_robosuite_attention(
            task_id="cube_lift",
            adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
            **common,
        )
    assert result["attempts"][0]["status"] == "completed"


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
def test_autonomous_policy_retrieves_matching_trusted_memory(tmp_path, suite):
    task = "counter_to_sink" if suite == "robocasa" else "cube_lift"
    memory = MemoryRecord(
        memory_id="trusted-test", version=1, source_trace_id="prior-trace",
        guidance="close gripper", candidate_repair="close gripper",
        applicability={"suite": suite, "task_id": task, "perception_mode": "sim_gt"},
        evidence_refs=("prior-evidence",), created_at=1.0, status=MemoryStatus.TRUSTED,
    )

    class Gateway:
        def __init__(self):
            self.uses = []

        def retrieve(self, context, *, now):
            return [memory] if all(context.get(k) == v for k, v in memory.applicability.items()) else []

        def provenance(self, memory_id):
            return {"artifact": {"kind": "text_hint"}, "source_kind": "advisor_proxy"}

        def get_memory(self, memory_id):
            assert memory_id == memory.memory_id
            return memory

        def authorize_use(self, context, *, memory_id, attempt_id, now):
            return {"version": 1, "used_at": now, "grant_id": "test-grant"}

        def record_use(self, use):
            self.uses.append(use)

    gateway = Gateway()

    def robot_policy(sdk, context):
        if context["attention_input"].get("selected_memories"):
            assert context["attention_input"]["selected_memories"][0]["guidance"] == "close gripper"
            sdk.gripper.close()

    common = dict(
        task_id=task, seed=101, artifact_root=tmp_path,
        policy_id="autonomous", robot_policy=robot_policy,
        max_attempts=2, memory_gateway=gateway,
    )
    if suite == "robocasa":
        world = FakeWorld()
        result = run_robocasa_attention(
            **common, backend_factory=lambda: FakeActionBackend(world),
            client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
        )
    else:
        result = run_robosuite_attention(
            **common, adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        )
    assert result["native_success"] is True
    assert result["decisions"][0]["action"] == "retrieve_memory"
    assert len(gateway.uses) == 1
    assert gateway.uses[0].memory_id == "trusted-test"


@pytest.mark.parametrize("suite", ["robocasa", "robosuite"])
def test_trace_inspection_is_passed_to_next_attempt(tmp_path, suite):
    def robot_policy(sdk, context):
        if context["attention_input"].get("inspected_trace"):
            sdk.gripper.close()

    common = dict(
        task_id="counter_to_sink" if suite == "robocasa" else "cube_lift",
        seed=101, artifact_root=tmp_path, policy_id="autonomous",
        robot_policy=robot_policy, max_attempts=2,
    )
    if suite == "robocasa":
        world = FakeWorld()
        result = run_robocasa_attention(
            **common, backend_factory=lambda: FakeActionBackend(world),
            client_factory=lambda task: RobocasaSimClient(task, transport=world.transport),
        )
    else:
        result = run_robosuite_attention(
            **common, adapter_factory=lambda task, camera: _adapter(task, camera, client=FakeClient()),
        )
    assert result["native_success"] is True
    assert result["decisions"][0]["action"] == "inspect_trace"
