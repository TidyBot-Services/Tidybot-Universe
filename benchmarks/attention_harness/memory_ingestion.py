"""Event-driven Memory candidate ingestion.

A hint becomes a candidate Memory only after its guidance was *adopted* and the
execution that used it has finished, so the candidate carries what happened:

    request answered -> response.used(guidance) -> response.execution_linked
        -> the guided execution has an evaluator-recorded outcome -> candidate

Both Advisor and human replies qualify. A human reply is charged the time the
person took to answer as its attention cost. Every request is considered once:
the outcome (ingested or skipped, and why) is persisted, so a restart or a
second sweep never creates a duplicate and a skip is never silent.

Ingestion never promotes. Validation and promotion stay with the Memory Service.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .core.models import RequestType
from .core.store import AttentionStore
from .attention_modes import RequestState
from .memory_agent import MemoryAgent

# Failures that mean "this source cannot become a candidate", as opposed to a bug.
_REFUSALS = (RuntimeError, ValueError, PermissionError, LookupError, OSError)


class MemoryIngestion:
    def __init__(self, store: AttentionStore, gateway: Any, *,
                 clock: Callable[[], float] = time.time) -> None:
        self.store = store
        self.gateway = gateway
        self.clock = clock

    # -- queries -----------------------------------------------------------------

    def _event(self, key: str) -> dict[str, Any] | None:
        with self.store._connect() as connection:
            row = connection.execute("SELECT payload FROM events WHERE event_key = ?",
                                     (key,)).fetchone()
        if row is None:
            return None
        import json
        return json.loads(row[0])

    def outcome(self, request_id: str) -> dict[str, Any] | None:
        """The persisted ingestion decision for a request, if one was made."""
        return self._event(f"memory-ingest:{request_id}")

    def due(self, run_id: str) -> list[str]:
        """Adopted, executed hint requests of a run that have no ingestion decision yet."""
        due = []
        for event in self.store.events_for_run(run_id):
            if event["event_type"] != "response.execution_linked":
                continue
            request_id = event["entity_id"]
            if request_id not in due and self.outcome(request_id) is None:
                due.append(request_id)
        return due

    # -- ingestion ---------------------------------------------------------------

    def sweep(self, run_id: str) -> list[dict[str, Any]]:
        return [self.ingest(request_id) for request_id in self.due(run_id)]

    def ingest(self, request_id: str) -> dict[str, Any]:
        existing = self.outcome(request_id)
        if existing is not None:
            return existing
        request = self.store.get_request(request_id)
        if (request is None or request.request_type is not RequestType.HINT
                or request.state is not RequestState.ANSWERED or not request.response_id):
            return self._record(request_id, None, skipped="not an answered hint request")
        disposition = self.store.request_disposition(request_id)
        if not disposition or disposition["disposition"] != "accepted" or disposition.get("use") != "guidance":
            return self._record(request_id, request, skipped="guidance was not adopted")
        if not disposition.get("execution_id"):
            linked = self._event(f"response-execution:{request_id}")
            if linked is None:
                return self._record(request_id, request, skipped="guided execution not recorded yet",
                                    final=False)
            disposition = {**disposition, "execution_id": linked["execution_id"]}
        raw = self.store.get_raw_trace_by_execution(disposition["execution_id"])
        if raw is None:
            return self._record(request_id, request, skipped="guided execution has no trace yet",
                                final=False)
        outcome = raw.get("outcome") or {}
        guided = ("timeout" if outcome.get("timed_out")
                  else "cancelled" if outcome.get("status") == "cancelled"
                  else "evaluator_unavailable" if outcome.get("evaluator_authoritative") is not True
                  else "native_success" if outcome.get("native_success") else "failed")
        response = self.store.get_response(request.response_id)
        attention = 0.0
        if response["responder"] != "advisor_proxy":
            # Time the person took; an upper bound on attention, never zero.
            attention = round(max(response["created_at"] - request.created_at, 0.001), 3)
        try:
            candidate = MemoryAgent(self.gateway).ingest_answered_hint(
                request_id, memory_id=f"candidate:{request.attempt_id}",
                human_attention_seconds=attention)
        except _REFUSALS as exc:
            return self._record(request_id, request, skipped=f"{type(exc).__name__}: {exc}"[:300],
                                guided_execution_id=disposition["execution_id"], guided_outcome=guided)
        return self._record(
            request_id, request, memory_id=candidate.memory_id, responder=response["responder"],
            guided_execution_id=disposition["execution_id"], guided_outcome=guided,
            effective=guided == "native_success", human_attention_seconds=attention)

    def _record(self, request_id: str, request, *, skipped: str | None = None,
                final: bool = True, **fields: Any) -> dict[str, Any]:
        import json
        payload = {"request_id": request_id,
                   "run_id": None if request is None else request.run_id,
                   "decision": "skipped" if skipped else "ingested", "recorded_at": self.clock()}
        if skipped:
            payload["reason"] = skipped
        payload.update({key: value for key, value in fields.items()})
        if not final:
            return payload  # try again once the guided execution has finished
        key = f"memory-ingest:{request_id}"
        with self.store._transaction() as connection:
            row = connection.execute("SELECT payload FROM events WHERE event_key = ?", (key,)).fetchone()
            if row is not None:
                return json.loads(row[0])
            connection.execute(
                "INSERT INTO events(event_key, entity_type, entity_id, event_type, payload) "
                "VALUES (?, 'memory_ingestion', ?, ?, ?)",
                (key, request_id, f"memory.candidate_{payload['decision']}",
                 json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)))
        return payload
