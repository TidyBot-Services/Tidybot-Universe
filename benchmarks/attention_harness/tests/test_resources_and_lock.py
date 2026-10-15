"""Resource budget (reserved/used/remaining incl. GPU), run lock and autonomous-work states."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.core.models import AssistanceBudget, RunRecord
from benchmarks.attention_harness.core.store import AttentionStore, StateConflictError
from benchmarks.attention_harness.gpu_meter import GpuMeter, nvidia_smi_utilization
from benchmarks.attention_harness.ui_server import autonomous_work, dashboard_snapshot

from test_async_requests import GUIDANCE, advisor_reply, guided_policy, run


def store_of(result) -> AttentionStore:
    return AttentionStore(Path(result["store"]))


def make_run(store, **budget):
    record = RunRecord(
        run_id="run:r", suite="robosuite", task_id="cube_lift", seed=101, policy_id="autonomous",
        developer_model="m", assistance_mode=AssistanceMode.BENCHMARK_PROXY,
        execution_target="robosuite_sim", created_at=1.0,
        budget=AssistanceBudget(**{"assistance_credits": 1, "token_limit": 1000,
                                   "execution_seconds": 100.0, **budget}))
    store.create_run(record)
    return record.run_id


# -- reservations -------------------------------------------------------------------


def test_reservations_hold_and_release_a_budget(tmp_path):
    store = AttentionStore(tmp_path / "a.sqlite3")
    run_id = make_run(store)
    assert store.resource_reservations(run_id) == {}
    store.reserve_resource(run_id, resource="execution_seconds", amount=60, reservation_id="x")
    store.reserve_resource(run_id, resource="execution_seconds", amount=60, reservation_id="x")  # idempotent
    assert store.resource_reservations(run_id) == {"execution_seconds": 60.0}
    with pytest.raises(StateConflictError, match="exhausted"):
        store.reserve_resource(run_id, resource="execution_seconds", amount=60, reservation_id="y")
    store.release_resource("x")
    store.release_resource("x")
    assert store.resource_reservations(run_id) == {}
    store.reserve_resource(run_id, resource="execution_seconds", amount=60, reservation_id="y")
    # What was already consumed counts against new reservations.
    store.consume_resources(run_id, execution_seconds=30, event_key="u")
    store.release_resource("y")
    with pytest.raises(StateConflictError):
        store.reserve_resource(run_id, resource="execution_seconds", amount=80, reservation_id="z")


def test_opening_a_store_never_creates_the_reservation_table(tmp_path):
    """Frozen Memory snapshots are opened by the same class and must stay byte-identical."""
    path = tmp_path / "frozen.sqlite3"
    store = AttentionStore(path)
    make_run(store)
    store.resource_reservations("run:r")
    store.release_resource("nothing")
    with sqlite3.connect(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "resource_reservations" not in tables


def test_gpu_time_is_recorded_unbudgeted_and_never_refused(tmp_path):
    store = AttentionStore(tmp_path / "gpu.sqlite3")
    run_id = make_run(store)
    assert store.resource_status(run_id)["gpu_seconds"] == {
        "limit": 0.0, "used": 0.0, "remaining": 0.0, "budgeted": False}
    store.consume_resources(run_id, gpu_seconds=12.5, event_key="g1")
    assert store.resource_status(run_id)["gpu_seconds"]["used"] == 12.5
    budgeted = make_run_with_gpu(store, 10.0)
    store.consume_resources(budgeted, gpu_seconds=15.0, event_key="g2")  # measured after the fact
    status = store.resource_status(budgeted)["gpu_seconds"]
    assert status["budgeted"] is True and status["used"] == 15.0 and status["remaining"] == -5.0


def make_run_with_gpu(store, limit):
    record = RunRecord(
        run_id="run:g", suite="robosuite", task_id="cube_lift", seed=101, policy_id="autonomous",
        developer_model="m", assistance_mode=AssistanceMode.BENCHMARK_PROXY,
        execution_target="robosuite_sim", created_at=1.0,
        budget=AssistanceBudget(assistance_credits=1, token_limit=10, execution_seconds=10.0,
                                gpu_seconds=limit))
    store.create_run(record)
    return record.run_id


# -- GPU meter ------------------------------------------------------------------------


def test_gpu_meter_integrates_utilization_over_wall_time():
    now = [0.0]
    readings = iter([0.5, 1.0, 1.0, 0.0])
    meter = GpuMeter(lambda: next(readings), interval=1000, monotonic=lambda: now[0])
    meter.start()                      # t=0 reading 0.5
    now[0] = 2.0
    meter._sample()                    # t=2 reading 1.0
    now[0] = 4.0
    meter._sample()                    # t=4 reading 1.0
    now[0] = 5.0
    result = meter.stop()              # t=5 reading 0.0
    # trapezoids: (0.5+1)/2*2 + (1+1)/2*2 + (1+0)/2*1 = 1.5 + 2 + 0.5
    assert result["gpu_seconds"] == pytest.approx(4.0)
    assert result["samples"] == 4 and "includes other processes" in result["coverage"]


def test_gpu_meter_reports_untracked_not_zero_without_a_gpu_reading():
    meter = GpuMeter(lambda: None, interval=1000).start()
    assert meter.stop()["gpu_seconds"] is None
    broken = GpuMeter(lambda: 1 / 0, interval=1000).start()
    assert broken.stop()["gpu_seconds"] is None


def test_nvidia_smi_probe_never_raises(monkeypatch):
    import subprocess

    def missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(subprocess, "run", missing)
    assert nvidia_smi_utilization() is None


# -- the scheduler uses them --------------------------------------------------------------


def test_attempt_execution_time_is_reserved_while_it_runs_and_released_after(tmp_path):
    seen = {}

    def policy(sdk, context):
        store = AttentionStore(Path(context["store_path"])) if "store_path" in context else None
        seen["policy_ran"] = True

    store_path = tmp_path / "attention.sqlite3"
    observed = []

    def watcher():
        store = AttentionStore(store_path)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            runs = [e["entity_id"] for e in store.events() if e["event_type"] == "run.created"]
            if runs:
                held = store.resource_reservations(runs[0]).get("execution_seconds")
                if held:
                    observed.append(held)
                    return
            time.sleep(0.01)

    def slow(sdk, context):
        time.sleep(0.4)

    thread = threading.Thread(target=watcher, daemon=True)
    thread.start()
    result = run(tmp_path, slow, store_path=store_path, max_attempts=1, assistance_credits=0,
                 attempt_budget_seconds=120.0, total_execution_seconds=300.0)
    thread.join(timeout=5)
    assert observed == [120.0]
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    assert store_of(result).resource_reservations(run_id) == {}


def test_tokens_are_reserved_while_an_advisor_request_is_open(tmp_path):
    gate = threading.Event()
    held = []
    store_path = tmp_path / "attention.sqlite3"

    def slow_advisor(request):
        store = AttentionStore(store_path)
        run_id = next(e["entity_id"] for e in store.events() if e["event_type"] == "run.created")
        held.append(store.resource_reservations(run_id).get("tokens"))
        return advisor_reply(request)

    result = run(tmp_path, guided_policy([]), store_path=store_path, advisor_transport=slow_advisor,
                 token_limit=4096)
    assert held == [1024.0]  # the Advisor's output cap, held while the call runs
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    assert store_of(result).resource_reservations(run_id) == {}


def test_gpu_usage_is_recorded_per_attempt_and_stops_the_run_when_budget_is_spent(tmp_path):
    def policy(sdk, context):
        time.sleep(0.25)

    result = run(tmp_path, policy, assistance_credits=0, max_attempts=6,
                 gpu_sampler=lambda: 1.0, gpu_sample_interval=0.02, gpu_seconds_limit=0.4)
    assert result["stopped_reason"] == "resource_blocked"
    assert 1 <= len(result["attempts"]) < 6
    assert all(item["gpu_seconds"] and item["gpu_seconds"] >= 0.2 for item in result["gpu_usage"])
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    store = store_of(result)
    gpu = store.resource_status(run_id)["gpu_seconds"]
    assert gpu["budgeted"] and gpu["used"] == pytest.approx(
        sum(item["gpu_seconds"] for item in result["gpu_usage"]))
    blocked = [item for item in store.list_work_items(run_id) if item["resource"] == "gpu_seconds"]
    assert blocked and blocked[0]["state"] == "blocked"


def test_snapshot_shows_reserved_for_every_resource_and_gpu_coverage(tmp_path):
    result = run(tmp_path, guided_policy([]), gpu_sampler=lambda: 0.5, gpu_sample_interval=0.02)
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    snapshot = dashboard_snapshot(store_of(result), run_id)
    resources = snapshot["resources"]
    for key in ("assistance", "tokens", "execution_seconds", "gpu_seconds"):
        assert resources[key]["reserved"] is not None
    assert resources["gpu_seconds"]["budgeted"] is False
    assert "includes other processes" in resources["gpu_seconds"]["coverage"]
    assert resources["execution_seconds"]["used"] > 0


# -- run lock ----------------------------------------------------------------------------


def test_lock_lists_the_fixed_conditions_and_comparability(tmp_path):
    result = run(tmp_path, guided_policy([]), gpu_seconds_limit=0.0)
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    lock = dashboard_snapshot(store_of(result), run_id)["lock"]
    assert lock["locked"] is True and lock["policy_switchable"] is False
    assert lock["ranking_eligible"] is True
    conditions = lock["conditions"]
    assert conditions["policy_id"] == "reactive_help" and conditions["request_mode"] == "async"
    assert conditions["execution_target"] == "robosuite_sim"
    assert conditions["assistance_mode"] == "benchmark_proxy"
    assert conditions["budget"]["assistance_credits"] == 1


def test_a_live_human_run_is_not_eligible_for_ranking(tmp_path):
    result = run(tmp_path, lambda sdk, context: None, max_attempts=2, assistance_credits=0,
                 assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST)
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    assert dashboard_snapshot(store_of(result), run_id)["lock"]["ranking_eligible"] is False


# -- autonomous work states ---------------------------------------------------------------


def test_autonomous_work_separates_running_blocked_and_waiting():
    attempts = [{"attempt_id": "a0", "status": "failed"}, {"attempt_id": "a1", "status": "running"}]
    requests = [
        {"request_id": "r-hint", "request_type": "hint", "state": "pending",
         "explanation": {"while_waiting": "The agent continues with safe work and applies the reply."}},
        {"request_id": "r-approval", "request_type": "approval", "state": "fallback",
         "explanation": {"while_waiting": "The agent waits for the reply (approval gates next action)."}},
        {"request_id": "r-done", "request_type": "hint", "state": "answered"},
    ]
    work = [{"work_id": "w1", "state": "blocked", "resource": "gpu_seconds"},
            {"work_id": "w2", "state": "failed", "reason": "boom"}]
    summary = autonomous_work(attempts, requests, work)
    assert [a["attempt_id"] for a in summary["running"]] == ["a1"] and summary["completed"] == 1
    assert [c["request_id"] for c in summary["continuing_while_waiting"]] == ["r-hint"]
    assert {(b["kind"], b.get("depends_on") or b.get("resource") or b.get("reason"))
            for b in summary["blocked"]} == {
        ("resource", "gpu_seconds"), ("failure", "boom"), ("dependency", "r-approval")}


def snapshot_while_open(tmp_path, *, policy, robot_policy, answer, **options):
    """Run in live-human mode; snapshot the run while its request is still unanswered."""
    store_path = tmp_path / "attention.sqlite3"
    taken = {}

    def human():
        store = AttentionStore(store_path)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            created = [e["entity_id"] for e in store.events() if e["event_type"] == "request.created"]
            if created:
                run_id = next(e["entity_id"] for e in store.events() if e["event_type"] == "run.created")
                taken["snapshot"] = dashboard_snapshot(store, run_id)
                taken["request_id"] = created[0]
                from benchmarks.attention_harness.core.runtime import AttentionRuntime
                AttentionRuntime(store, None, clock=time.time).submit_human_response(
                    created[0], response_id="human-response:w", content=answer)
                return
            time.sleep(0.01)

    thread = threading.Thread(target=human, daemon=True)
    thread.start()
    run(tmp_path, robot_policy, policy_id=policy, store_path=store_path,
        assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST, human_deadline_seconds=20.0,
        sleeper=time.sleep, **options)
    thread.join(timeout=5)
    return taken


def test_work_held_by_an_open_approval_is_reported_as_a_dependency(tmp_path):
    from test_async_requests import approval_policy

    taken = snapshot_while_open(
        tmp_path, policy="full_trace_aware_attention_planner", robot_policy=approval_policy([]),
        answer="approve", max_attempts=3, approval_rules={"soft_max_delta_m": 0.1})
    work = taken["snapshot"]["autonomous_work"]
    assert work["blocked"] == [{"kind": "dependency", "depends_on": taken["request_id"],
                                "waiting_for": "approval"}]
    assert work["continuing_while_waiting"] == []


def test_an_open_hint_lets_work_continue_and_is_not_a_block(tmp_path):
    def slow(sdk, context):
        time.sleep(0.15)
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()

    taken = snapshot_while_open(tmp_path, policy="reactive_help", robot_policy=slow,
                                answer=GUIDANCE, max_attempts=5)
    work = taken["snapshot"]["autonomous_work"]
    assert work["blocked"] == []
    assert [item["request_id"] for item in work["continuing_while_waiting"]] == [taken["request_id"]]
    assert work["completed"] >= 1


# -- launch options ------------------------------------------------------------------------


def test_launch_options_make_the_execution_target_a_separate_choice():
    from benchmarks.attention_harness.ui_launch import public_options

    options = public_options([{"id": "p", "suite": "robosuite", "task": "cube_lift", "seed": 101,
                               "execution_target": "robosuite_sim"}])
    targets = {item["id"]: item for item in options["execution_targets"]}
    assert targets["simulation"]["available"] is True and targets["simulation"]["profiles"] == ["p"]
    assert targets["real_robot"]["available"] is False and "runner" in targets["real_robot"]["reason"]
    assert [m["id"] for m in options["request_modes"]] == ["async", "blocking"]
    assert options["limits"]["gpu_seconds"] == 86400


# -- what an operator may do with a request ---------------------------------------------


def test_permitted_actions_are_decided_by_the_server():
    from benchmarks.attention_harness.ui_server import permitted_actions

    live = {"mode": "live_human_first", "state": "pending", "deadline_at": 100.0, "request_type": "hint"}
    assert permitted_actions(live, False, 50.0) == ["respond_hint", "defer", "ignore", "cancel"]
    assert permitted_actions(live, True, 50.0) == ["respond_hint", "ignore", "cancel"]
    assert permitted_actions({**live, "request_type": "approval"}, False, 50.0)[0] == "approve_or_deny"
    assert permitted_actions({**live, "request_type": "interrupt"}, False, 50.0)[0] == "confirm_interrupt"
    assert permitted_actions(live, False, 100.0) == []                      # deadline passed
    assert permitted_actions({**live, "state": "answered"}, False, 50.0) == []
    assert permitted_actions({**live, "mode": "benchmark_proxy"}, False, 50.0) == []


def test_snapshot_lists_the_permitted_actions_of_an_open_request(tmp_path):
    def slow(sdk, context):
        time.sleep(0.1)

    taken = snapshot_while_open(tmp_path, policy="reactive_help", robot_policy=slow,
                                answer=GUIDANCE, max_attempts=4)
    request = next(r for r in taken["snapshot"]["requests"] if r["request_id"] == taken["request_id"])
    assert request["permitted_actions"] == ["respond_hint", "defer", "ignore", "cancel"]
