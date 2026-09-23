from pathlib import Path

from benchmarks.attention_harness.attention_modes import RequestState
from benchmarks.attention_harness.core.advisor import AdvisorProxy
from benchmarks.attention_harness.core.artifacts import write_run_bundle
from benchmarks.attention_harness.core.memory import MemoryManager
from benchmarks.attention_harness.core.models import (
    AttemptStatus,
    MemoryRecord,
    MemoryUseRecord,
    RunStatus,
)
from benchmarks.attention_harness.core.policies import DecisionAction, PolicyContext, build_policy
from benchmarks.attention_harness.core.projection import AttentionProjection
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.tests.test_attention_store import records


def test_trace_request_proxy_memory_retrieval_and_restart(tmp_path: Path) -> None:
    database = tmp_path / "attention.sqlite3"
    store = AttentionStore(database)
    run, attempt, trace, request = records()
    store.create_run(run)
    store.transition_run(run.run_id, RunStatus.RUNNING, event_key="run-start")
    store.create_attempt(attempt)
    store.put_trace(trace)

    decision = build_policy("full_trace_aware_attention_planner").decide(
        PolicyContext(run.run_id, 0, 1, 2, 2, 1, True)
    )
    assert decision.action is DecisionAction.REQUEST
    proxy = AdvisorProxy(
        store,
        transport=lambda _: "approach vertically and reduce lateral offset",
        sleeper=lambda _: None,
    )
    runtime = AttentionRuntime(store, proxy, clock=lambda: 10.0)
    runtime.open_request(request)
    answered = runtime.resolve_benchmark_proxy(
        request.request_id,
        trace_packet={
            "failure": "missed grasp",
            "camera_frame_sha256": "abc",
            "agent_hypothesis": "right-biased estimate",
        },
    )
    assert answered.state is RequestState.ANSWERED

    memories = MemoryManager(store)
    memories.add_candidate(
        MemoryRecord(
            "memory-1", 1, trace.trace_id,
            "approach vertically", "reduce lateral offset", {"task_id": "cube_lift"},
            ("artifact://run-1/trace-1", "artifact://run-1/frame-1"), 11.0,
        )
    )
    memories.record_validation("memory-1", successes=4, failures=0)
    memories.promote("memory-1")
    assert len(memories.retrieve({"task_id": "cube_lift"}, now=12.0)) == 1
    memories.record_use(
        MemoryUseRecord("use-1", "memory-1", 1, run.run_id, attempt.attempt_id, 13.0, "success")
    )
    store.complete_attempt(
        attempt.attempt_id,
        AttemptStatus.SUCCEEDED,
        ended_at=14.0,
        native_success=True,
        artifact_uri="artifact://run-1/result",
        event_key="attempt-finish",
    )
    store.transition_run(run.run_id, RunStatus.COMPLETED, event_key="run-finish")

    bundle = write_run_bundle(store, run.run_id, tmp_path / "run-bundle.json")
    assert bundle.is_file()
    restarted = AttentionStore(database)
    snapshot = AttentionProjection(restarted).snapshot(run.run_id)
    assert snapshot["run_context"]["locked"] is True
    assert snapshot["live_station"]["status"] == "completed"
    assert snapshot["attention_inbox"][0]["state"] == "answered"
    assert snapshot["request_detail"]["responses"][0]["responder"] == "advisor_proxy"
    assert snapshot["request_detail"]["memories"][0]["status"] == "trusted"
