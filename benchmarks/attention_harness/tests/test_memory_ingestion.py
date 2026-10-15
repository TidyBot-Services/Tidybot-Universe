"""Candidate Memory is created from adopted, executed hints - once, with the outcome."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from attention_memory_service import MemoryService

from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.core.runtime import AttentionRuntime
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_ingestion import MemoryIngestion

from test_async_requests import GUIDANCE, guided_policy, run


def store_of(result) -> AttentionStore:
    return AttentionStore(Path(result["store"]))


def test_adopted_advisor_hint_becomes_one_candidate_with_the_guided_outcome(tmp_path):
    result = run(tmp_path, guided_policy([]))
    row = result["requests"][0]
    ingestion = row["memory_ingestion"]
    assert ingestion["decision"] == "ingested" and row["candidate_memory_id"] == ingestion["memory_id"]
    assert ingestion["guided_outcome"] == "native_success" and ingestion["effective"] is True
    assert ingestion["guided_execution_id"] == result["attempts"][2]["attention_trace"]["execution_id"]
    store = store_of(result)
    service = MemoryService(store.path)
    memory = service.get_memory(ingestion["memory_id"])
    assert memory.status.value == "candidate" and memory.guidance == GUIDANCE
    assert service.provenance(memory.memory_id)["source_kind"] == "advisor_proxy"
    # Restart safety: the decision is persisted, so nothing is created twice.
    again = MemoryIngestion(store, service)
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    assert again.due(run_id) == [] and again.sweep(run_id) == []
    assert again.ingest(row["request_id"]) == again.outcome(row["request_id"])
    assert len([m for m in store.list_memories()]) == 1


def test_guided_attempt_that_still_fails_is_a_candidate_marked_not_effective(tmp_path):
    result = run(tmp_path, lambda sdk, context: None)
    ingestion = result["requests"][0]["memory_ingestion"]
    assert ingestion["decision"] == "ingested"
    assert ingestion["guided_outcome"] == "failed" and ingestion["effective"] is False


def test_human_reply_becomes_a_candidate_charged_with_the_time_the_person_took(tmp_path):
    store_path = tmp_path / "attention.sqlite3"

    def policy(sdk, context):
        time.sleep(0.15)
        if context["attention_input"].get("advisor_guidance"):
            sdk.gripper.close()

    def human():
        store = AttentionStore(store_path)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            created = [e["entity_id"] for e in store.events() if e["event_type"] == "request.created"]
            if created:
                time.sleep(0.3)
                AttentionRuntime(store, None, clock=time.time).submit_human_response(
                    created[0], response_id="human-response:ingest", content=GUIDANCE)
                return
            time.sleep(0.02)

    thread = threading.Thread(target=human, daemon=True)
    thread.start()
    result = run(tmp_path, policy, store_path=store_path, max_attempts=5,
                 assistance_mode=AssistanceMode.LIVE_HUMAN_FIRST, human_deadline_seconds=20.0,
                 sleeper=time.sleep)
    thread.join(timeout=5)
    row = result["requests"][0]
    assert row["responder"] == "human"
    ingestion = row["memory_ingestion"]
    assert ingestion["decision"] == "ingested" and ingestion["responder"] == "human"
    assert 0.2 < ingestion["human_attention_seconds"] < 10
    provenance = MemoryService(store_path).provenance(ingestion["memory_id"])
    assert provenance["source_kind"] == "human"
    assert provenance["human_attention_seconds"] == ingestion["human_attention_seconds"]


def test_an_ignored_reply_never_becomes_a_candidate(tmp_path):
    contexts: list = []

    def policy(sdk, context):
        contexts.append(context)
        if len(contexts) == 2:
            sdk.gripper.close()

    result = run(tmp_path, policy)
    row = result["requests"][0]
    assert row["disposition"] == "ignored" and "memory_ingestion" not in row
    assert store_of(result).list_memories() == []


def test_a_refused_source_is_recorded_as_skipped_with_the_reason(tmp_path):
    class Refusing:
        def retrieve(self, context, *, now):
            return []

        def get_memory(self, memory_id):
            raise AssertionError("no memory expected")

        def source_for_agent(self, request_id):
            raise RuntimeError("memory source must be an evaluator-confirmed failed attempt")

    result = run(tmp_path, guided_policy([]), memory_gateway=Refusing())
    row = result["requests"][0]
    assert result["native_success"] is True  # a refused candidate never fails the run
    assert row["memory_ingestion"]["decision"] == "skipped"
    assert "evaluator-confirmed" in row["memory_ingestion"]["reason"]
    assert "candidate_memory_id" not in row
    store = store_of(result)
    assert any(e["event_type"] == "memory.candidate_skipped" and e["entity_id"] == row["request_id"]
               for e in store.events())
