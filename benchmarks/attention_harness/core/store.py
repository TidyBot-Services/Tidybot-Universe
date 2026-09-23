"""SQLite event/snapshot store with idempotent writes and restart recovery."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from ..attention_modes import RequestState
from .models import (
    AttentionRequestRecord,
    AttentionResponseRecord,
    AttemptRecord,
    AttemptStatus,
    MemoryRecord,
    MemoryStatus,
    MemoryUseRecord,
    RawExecutionTrace,
    RunRecord,
    RunStatus,
    TracePacket,
)


class StateConflictError(RuntimeError):
    pass


REQUEST_TRANSITIONS = {
    RequestState.PENDING: {
        RequestState.ANSWERED,
        RequestState.TIMEOUT,
        RequestState.CANCELLED,
        RequestState.FALLBACK,
    },
    RequestState.FALLBACK: {
        RequestState.ANSWERED,
        RequestState.TIMEOUT,
        RequestState.CANCELLED,
    },
    RequestState.ANSWERED: set(),
    RequestState.TIMEOUT: set(),
    RequestState.CANCELLED: set(),
}

MEMORY_TRANSITIONS = {
    MemoryStatus.CANDIDATE: {MemoryStatus.VALIDATED, MemoryStatus.REJECTED},
    MemoryStatus.VALIDATED: {MemoryStatus.TRUSTED, MemoryStatus.REJECTED},
    MemoryStatus.TRUSTED: {
        MemoryStatus.DISABLED,
        MemoryStatus.EXPIRED,
        MemoryStatus.ROLLED_BACK,
    },
    MemoryStatus.DISABLED: {MemoryStatus.TRUSTED, MemoryStatus.ROLLED_BACK},
    MemoryStatus.REJECTED: set(),
    MemoryStatus.EXPIRED: set(),
    MemoryStatus.ROLLED_BACK: set(),
}
RUN_TRANSITIONS = {
    RunStatus.CREATED: {RunStatus.RUNNING, RunStatus.CANCELLED},
    RunStatus.RUNNING: {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED},
    RunStatus.COMPLETED: set(),
    RunStatus.FAILED: set(),
    RunStatus.CANCELLED: set(),
}
ATTEMPT_TERMINAL_STATES = {
    AttemptStatus.SUCCEEDED,
    AttemptStatus.FAILED,
    AttemptStatus.TIMED_OUT,
    AttemptStatus.CANCELLED,
}


class AttentionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def close(self) -> None:
        """Compatibility no-op: connections are intentionally short-lived."""

    def create_run(self, run: RunRecord) -> RunRecord:
        self._insert_immutable("runs", run.run_id, run.artifact(), "run.created")
        return run

    def create_attempt(self, attempt: AttemptRecord) -> AttemptRecord:
        if self.get_run(attempt.run_id) is None:
            raise StateConflictError(f"run {attempt.run_id!r} does not exist")
        self._insert_immutable(
            "attempts", attempt.attempt_id, attempt.artifact(), "attempt.created"
        )
        return attempt

    def transition_run(
        self, run_id: str, status: RunStatus, *, event_key: str
    ) -> RunRecord:
        with self._transaction() as connection:
            existing_event = connection.execute(
                "SELECT payload FROM events WHERE event_key = ?", (event_key,)
            ).fetchone()
            if existing_event is not None:
                return self._decode_run(json.loads(existing_event[0])["run"])
            row = connection.execute(
                "SELECT payload FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise StateConflictError(f"run {run_id!r} does not exist")
            current = self._decode_run(json.loads(row[0]))
            if status not in RUN_TRANSITIONS[current.status]:
                raise StateConflictError(
                    f"illegal run transition {current.status.value} -> {status.value}"
                )
            updated = replace(current, status=status)
            self._update_with_event(
                connection, "runs", run_id, updated.artifact(),
                event_key, f"run.{status.value}", "run"
            )
            return updated

    def complete_attempt(
        self,
        attempt_id: str,
        status: AttemptStatus,
        *,
        ended_at: float,
        native_success: bool,
        artifact_uri: str,
        event_key: str,
    ) -> AttemptRecord:
        if status not in ATTEMPT_TERMINAL_STATES:
            raise StateConflictError("attempt completion requires a terminal state")
        if status is AttemptStatus.SUCCEEDED and not native_success:
            raise StateConflictError("succeeded attempt requires native success")
        with self._transaction() as connection:
            existing_event = connection.execute(
                "SELECT payload FROM events WHERE event_key = ?", (event_key,)
            ).fetchone()
            if existing_event is not None:
                return self._decode_attempt(json.loads(existing_event[0])["attempt"])
            row = connection.execute(
                "SELECT payload FROM attempts WHERE id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise StateConflictError(f"attempt {attempt_id!r} does not exist")
            current = self._decode_attempt(json.loads(row[0]))
            if current.status is not AttemptStatus.RUNNING:
                raise StateConflictError("attempt is already terminal")
            if ended_at < current.started_at:
                raise StateConflictError("attempt end precedes start")
            updated = replace(
                current,
                status=status,
                ended_at=ended_at,
                native_success=native_success,
                artifact_uri=artifact_uri,
            )
            self._update_with_event(
                connection, "attempts", attempt_id, updated.artifact(),
                event_key, f"attempt.{status.value}", "attempt"
            )
            return updated

    def put_trace(self, trace: TracePacket) -> TracePacket:
        attempt = self.get_attempt(trace.attempt_id)
        if attempt is None:
            raise StateConflictError(f"attempt {trace.attempt_id!r} does not exist")
        if attempt["run_id"] != trace.run_id:
            raise StateConflictError("trace run does not match its attempt")
        if trace.raw_trace_id is not None:
            raw = self.get_raw_trace(trace.raw_trace_id)
            if raw is None:
                raise StateConflictError(
                    f"raw trace {trace.raw_trace_id!r} does not exist"
                )
            if (
                raw["run_id"] != trace.run_id
                or raw["attempt_id"] != trace.attempt_id
                or raw["execution_id"] != trace.execution_id
            ):
                raise StateConflictError("advisor trace links do not match its raw trace")
        self._insert_immutable("traces", trace.trace_id, trace.artifact(), "trace.created")
        return trace

    def put_raw_trace(self, trace: RawExecutionTrace) -> RawExecutionTrace:
        attempt = self.get_attempt(trace.attempt_id)
        if attempt is None:
            raise StateConflictError(f"attempt {trace.attempt_id!r} does not exist")
        if attempt["run_id"] != trace.run_id:
            raise StateConflictError("raw trace run does not match its attempt")
        self._insert_immutable(
            "raw_traces",
            trace.raw_trace_id,
            trace.artifact(),
            "raw_trace.created",
        )
        return trace

    def create_request(self, request: AttentionRequestRecord) -> AttentionRequestRecord:
        trace = self.get_trace(request.trace_id)
        if trace is None:
            raise StateConflictError(f"trace {request.trace_id!r} does not exist")
        if trace["run_id"] != request.run_id or trace["attempt_id"] != request.attempt_id:
            raise StateConflictError("request links do not match its trace")
        self._insert_immutable(
            "requests", request.request_id, request.artifact(), "request.created"
        )
        return request

    def transition_request(
        self,
        request_id: str,
        state: RequestState,
        *,
        response: AttentionResponseRecord | None = None,
        event_key: str,
    ) -> AttentionRequestRecord:
        with self._transaction() as connection:
            existing_event = connection.execute(
                "SELECT payload FROM events WHERE event_key = ?", (event_key,)
            ).fetchone()
            if existing_event is not None:
                return self._decode_request(json.loads(existing_event[0])["request"])
            row = connection.execute(
                "SELECT payload FROM requests WHERE id = ?", (request_id,)
            ).fetchone()
            if row is None:
                raise StateConflictError(f"request {request_id!r} does not exist")
            current = self._decode_request(json.loads(row[0]))
            if state not in REQUEST_TRANSITIONS[current.state]:
                raise StateConflictError(
                    f"illegal request transition {current.state.value} -> {state.value}"
                )
            if state is RequestState.ANSWERED and response is None:
                raise StateConflictError("answered transition requires a response")
            if response is not None:
                if response.request_id != request_id:
                    raise StateConflictError("response belongs to a different request")
                self._insert_immutable_tx(
                    connection,
                    "responses",
                    response.response_id,
                    response.artifact(),
                )
            updated = replace(
                current,
                state=state,
                response_id=response.response_id if response else current.response_id,
            )
            connection.execute(
                "UPDATE requests SET payload = ? WHERE id = ?",
                (_json(updated.artifact()), request_id),
            )
            payload = {
                "request": updated.artifact(),
                "response": response.artifact() if response else None,
            }
            connection.execute(
                "INSERT INTO events(event_key, entity_type, entity_id, event_type, payload) "
                "VALUES (?, 'request', ?, ?, ?)",
                (event_key, request_id, f"request.{state.value}", _json(payload)),
            )
            return updated

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._get_payload("runs", run_id)

    def get_attempt(self, attempt_id: str) -> dict[str, Any] | None:
        return self._get_payload("attempts", attempt_id)

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        return self._get_payload("traces", trace_id)

    def get_raw_trace(self, raw_trace_id: str) -> dict[str, Any] | None:
        return self._get_payload("raw_traces", raw_trace_id)

    def get_request(self, request_id: str) -> AttentionRequestRecord | None:
        payload = self._get_payload("requests", request_id)
        return None if payload is None else self._decode_request(payload)

    def get_response(self, response_id: str) -> dict[str, Any] | None:
        return self._get_payload("responses", response_id)

    def create_memory(self, memory: MemoryRecord) -> MemoryRecord:
        if self.get_trace(memory.source_trace_id) is None:
            raise StateConflictError(
                f"trace {memory.source_trace_id!r} does not exist"
            )
        self._insert_immutable(
            "memories", memory.memory_id, memory.artifact(), "memory.created"
        )
        return memory

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        payload = self._get_payload("memories", memory_id)
        return None if payload is None else self._decode_memory(payload)

    def transition_memory(
        self,
        memory_id: str,
        status: MemoryStatus,
        *,
        event_key: str,
        confidence: float | None = None,
        validation_successes: int | None = None,
        validation_failures: int | None = None,
        reason: str | None = None,
    ) -> MemoryRecord:
        with self._transaction() as connection:
            existing_event = connection.execute(
                "SELECT payload FROM events WHERE event_key = ?", (event_key,)
            ).fetchone()
            if existing_event is not None:
                return self._decode_memory(json.loads(existing_event[0])["memory"])
            row = connection.execute(
                "SELECT payload FROM memories WHERE id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                raise StateConflictError(f"memory {memory_id!r} does not exist")
            current = self._decode_memory(json.loads(row[0]))
            if status not in MEMORY_TRANSITIONS[current.status]:
                raise StateConflictError(
                    f"illegal memory transition {current.status.value} -> {status.value}"
                )
            updated = replace(
                current,
                status=status,
                confidence=current.confidence if confidence is None else confidence,
                validation_successes=(
                    current.validation_successes
                    if validation_successes is None
                    else validation_successes
                ),
                validation_failures=(
                    current.validation_failures
                    if validation_failures is None
                    else validation_failures
                ),
                status_reason=reason,
            )
            connection.execute(
                "UPDATE memories SET payload = ? WHERE id = ?",
                (_json(updated.artifact()), memory_id),
            )
            payload = {"memory": updated.artifact()}
            connection.execute(
                "INSERT INTO events(event_key, entity_type, entity_id, event_type, payload) "
                "VALUES (?, 'memory', ?, ?, ?)",
                (event_key, memory_id, f"memory.{status.value}", _json(payload)),
            )
            return updated

    def list_memories(self) -> list[MemoryRecord]:
        return [self._decode_memory(value) for value in self._list_payloads("memories")]

    def record_memory_use(self, use: MemoryUseRecord) -> MemoryUseRecord:
        memory = self.get_memory(use.memory_id)
        if memory is None:
            raise StateConflictError(f"memory {use.memory_id!r} does not exist")
        if memory.version != use.memory_version:
            raise StateConflictError("memory-use version does not match stored memory")
        if memory.status is not MemoryStatus.TRUSTED:
            raise StateConflictError("only trusted memory can be used")
        if self.get_attempt(use.attempt_id) is None:
            raise StateConflictError(f"attempt {use.attempt_id!r} does not exist")
        if self.get_attempt(use.attempt_id)["run_id"] != use.run_id:
            raise StateConflictError("memory use run does not match its attempt")
        self._insert_immutable(
            "memory_uses", use.use_id, use.artifact(), "memory.used"
        )
        return use

    def list_memory_uses(self, memory_id: str) -> list[dict[str, Any]]:
        return [
            value
            for value in self._list_payloads("memory_uses")
            if value["memory_id"] == memory_id
        ]

    def events(self, *, after_sequence: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence, event_key, entity_type, entity_id, event_type, payload "
                "FROM events WHERE sequence > ? ORDER BY sequence",
                (after_sequence,),
            ).fetchall()
        return [
            {
                "sequence": row[0],
                "event_key": row[1],
                "entity_type": row[2],
                "entity_id": row[3],
                "event_type": row[4],
                "payload": json.loads(row[5]),
            }
            for row in rows
        ]

    def cache_get(self, cache_key: str) -> dict[str, Any] | None:
        return self._get_payload("advisor_cache", cache_key)

    def cache_put(self, cache_key: str, value: dict[str, Any]) -> None:
        self._insert_immutable("advisor_cache", cache_key, value, "advisor.cached")

    def reserve_assistance(
        self, run_id: str, *, credits: int, reservation_id: str
    ) -> dict[str, int]:
        if credits <= 0:
            raise ValueError("credits must be positive")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT credits, status FROM budget_reservations WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            if existing is not None:
                return self.budget_status(run_id, connection=connection)
            run_row = connection.execute(
                "SELECT payload FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                raise StateConflictError(f"run {run_id!r} does not exist")
            limit = int(json.loads(run_row[0])["budget"]["assistance_credits"])
            used = connection.execute(
                "SELECT COALESCE(SUM(credits), 0) FROM budget_reservations "
                "WHERE run_id = ? AND status = 'committed'",
                (run_id,),
            ).fetchone()[0]
            reserved = connection.execute(
                "SELECT COALESCE(SUM(credits), 0) FROM budget_reservations "
                "WHERE run_id = ? AND status = 'reserved'",
                (run_id,),
            ).fetchone()[0]
            if used + reserved + credits > limit:
                raise StateConflictError("assistance budget exhausted")
            connection.execute(
                "INSERT INTO budget_reservations(id, run_id, credits, status) "
                "VALUES (?, ?, ?, 'reserved')",
                (reservation_id, run_id, credits),
            )
            return self.budget_status(run_id, connection=connection)

    def settle_assistance(self, reservation_id: str, *, commit: bool) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT status FROM budget_reservations WHERE id = ?", (reservation_id,)
            ).fetchone()
            if row is None:
                raise StateConflictError(f"reservation {reservation_id!r} does not exist")
            target = "committed" if commit else "released"
            if row[0] == target:
                return
            if row[0] != "reserved":
                raise StateConflictError(f"cannot settle reservation in state {row[0]}")
            connection.execute(
                "UPDATE budget_reservations SET status = ? WHERE id = ?",
                (target, reservation_id),
            )

    def budget_status(
        self, run_id: str, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, int]:
        owned = connection is None
        active = connection or self._connect()
        try:
            row = active.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise StateConflictError(f"run {run_id!r} does not exist")
            limit = int(json.loads(row[0])["budget"]["assistance_credits"])
            used = int(
                active.execute(
                    "SELECT COALESCE(SUM(credits), 0) FROM budget_reservations "
                    "WHERE run_id = ? AND status = 'committed'",
                    (run_id,),
                ).fetchone()[0]
            )
            reserved = int(
                active.execute(
                    "SELECT COALESCE(SUM(credits), 0) FROM budget_reservations "
                    "WHERE run_id = ? AND status = 'reserved'",
                    (run_id,),
                ).fetchone()[0]
            )
            return {
                "limit": limit,
                "used": used,
                "reserved": reserved,
                "remaining": limit - used - reserved,
            }
        finally:
            if owned:
                active.close()

    def consume_resources(
        self,
        run_id: str,
        *,
        tokens: int = 0,
        execution_seconds: float = 0.0,
        gpu_seconds: float = 0.0,
        event_key: str,
    ) -> dict[str, Any]:
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            raise ValueError("tokens must be a non-negative integer")
        if execution_seconds < 0 or gpu_seconds < 0:
            raise ValueError("time consumption must be non-negative")
        if tokens == 0 and execution_seconds == 0 and gpu_seconds == 0:
            raise ValueError("resource consumption must be non-zero")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM resource_usage WHERE event_key = ?", (event_key,)
            ).fetchone()
            if existing is not None:
                return self.resource_status(run_id, connection=connection)
            status = self.resource_status(run_id, connection=connection)
            if tokens > status["tokens"]["remaining"]:
                raise StateConflictError("token budget exhausted")
            if execution_seconds > status["execution_seconds"]["remaining"]:
                raise StateConflictError("execution-time budget exhausted")
            if gpu_seconds > status["gpu_seconds"]["remaining"]:
                raise StateConflictError("GPU-time budget exhausted")
            connection.execute(
                "INSERT INTO resource_usage(event_key, run_id, tokens, execution_seconds, gpu_seconds) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_key, run_id, tokens, execution_seconds, gpu_seconds),
            )
            payload = {
                "run_id": run_id,
                "tokens": tokens,
                "execution_seconds": execution_seconds,
                "gpu_seconds": gpu_seconds,
            }
            connection.execute(
                "INSERT INTO events(event_key, entity_type, entity_id, event_type, payload) "
                "VALUES (?, 'resource_usage', ?, 'resources.consumed', ?)",
                (f"resource-event:{event_key}", run_id, _json(payload)),
            )
            return self.resource_status(run_id, connection=connection)

    def resource_status(
        self, run_id: str, *, connection: sqlite3.Connection | None = None
    ) -> dict[str, Any]:
        owned = connection is None
        active = connection or self._connect()
        try:
            row = active.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise StateConflictError(f"run {run_id!r} does not exist")
            budget = json.loads(row[0])["budget"]
            used = active.execute(
                "SELECT COALESCE(SUM(tokens), 0), "
                "COALESCE(SUM(execution_seconds), 0), COALESCE(SUM(gpu_seconds), 0) "
                "FROM resource_usage WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            def item(limit: float | int, consumed: float | int) -> dict[str, float | int]:
                return {"limit": limit, "used": consumed, "remaining": limit - consumed}
            return {
                "assistance": self.budget_status(run_id, connection=active),
                "tokens": item(int(budget["token_limit"]), int(used[0])),
                "execution_seconds": item(float(budget["execution_seconds"]), float(used[1])),
                "gpu_seconds": item(float(budget.get("gpu_seconds", 0.0)), float(used[2])),
            }
        finally:
            if owned:
                active.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            for table in (
                "runs",
                "attempts",
                "raw_traces",
                "traces",
                "requests",
                "responses",
                "advisor_cache",
                "memories",
                "memory_uses",
            ):
                connection.execute(
                    f"CREATE TABLE IF NOT EXISTS {table} "
                    "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events ("
                "sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
                "event_key TEXT NOT NULL UNIQUE, entity_type TEXT NOT NULL, "
                "entity_id TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS budget_reservations ("
                "id TEXT PRIMARY KEY, run_id TEXT NOT NULL, credits INTEGER NOT NULL, "
                "status TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS resource_usage ("
                "event_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, tokens INTEGER NOT NULL, "
                "execution_seconds REAL NOT NULL, gpu_seconds REAL NOT NULL)"
            )

    def _insert_immutable(
        self, table: str, identifier: str, payload: dict[str, Any], event_type: str
    ) -> None:
        with self._transaction() as connection:
            inserted = self._insert_immutable_tx(connection, table, identifier, payload)
            if inserted:
                connection.execute(
                    "INSERT INTO events(event_key, entity_type, entity_id, event_type, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (f"{event_type}:{identifier}", table, identifier, event_type, _json(payload)),
                )

    @staticmethod
    def _insert_immutable_tx(
        connection: sqlite3.Connection,
        table: str,
        identifier: str,
        payload: dict[str, Any],
    ) -> bool:
        encoded = _json(payload)
        row = connection.execute(
            f"SELECT payload FROM {table} WHERE id = ?", (identifier,)
        ).fetchone()
        if row is not None:
            if row[0] != encoded:
                raise StateConflictError(
                    f"{table} identifier {identifier!r} already has different content"
                )
            return False
        connection.execute(
            f"INSERT INTO {table}(id, payload) VALUES (?, ?)", (identifier, encoded)
        )
        return True

    def _get_payload(self, table: str, identifier: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT payload FROM {table} WHERE id = ?", (identifier,)
            ).fetchone()
        return None if row is None else json.loads(row[0])

    def _list_payloads(self, table: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT payload FROM {table} ORDER BY id"
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _update_with_event(
        connection: sqlite3.Connection,
        table: str,
        identifier: str,
        payload: dict[str, Any],
        event_key: str,
        event_type: str,
        event_entity_type: str,
    ) -> None:
        connection.execute(
            f"UPDATE {table} SET payload = ? WHERE id = ?",
            (_json(payload), identifier),
        )
        connection.execute(
            "INSERT INTO events(event_key, entity_type, entity_id, event_type, payload) "
            "VALUES (?, ?, ?, ?, ?)",
            (event_key, event_entity_type, identifier, event_type, _json({event_entity_type: payload})),
        )

    @staticmethod
    def _decode_request(payload: dict[str, Any]) -> AttentionRequestRecord:
        from .models import RequestPriority, RequestType
        from ..attention_modes import AssistanceMode

        return AttentionRequestRecord(
            request_id=payload["request_id"],
            run_id=payload["run_id"],
            attempt_id=payload["attempt_id"],
            trace_id=payload["trace_id"],
            request_type=RequestType(payload["request_type"]),
            reason=payload["reason"],
            priority=RequestPriority(payload["priority"]),
            created_at=float(payload["created_at"]),
            deadline_at=payload.get("deadline_at"),
            mode=AssistanceMode(payload["mode"]),
            state=RequestState(payload["state"]),
            response_id=payload.get("response_id"),
            schema_version=payload["schema_version"],
        )

    @staticmethod
    def _decode_run(payload: dict[str, Any]) -> RunRecord:
        from ..attention_modes import AssistanceMode
        from .models import AssistanceBudget

        budget = payload["budget"]
        return RunRecord(
            run_id=payload["run_id"],
            suite=payload["suite"],
            task_id=payload["task_id"],
            seed=int(payload["seed"]),
            policy_id=payload["policy_id"],
            developer_model=payload["developer_model"],
            assistance_mode=AssistanceMode(payload["assistance_mode"]),
            execution_target=payload["execution_target"],
            budget=AssistanceBudget(
                assistance_credits=int(budget["assistance_credits"]),
                token_limit=int(budget["token_limit"]),
                execution_seconds=float(budget["execution_seconds"]),
                gpu_seconds=float(budget.get("gpu_seconds", 0.0)),
            ),
            created_at=float(payload["created_at"]),
            status=RunStatus(payload["status"]),
            schema_version=payload["schema_version"],
        )

    @staticmethod
    def _decode_attempt(payload: dict[str, Any]) -> AttemptRecord:
        return AttemptRecord(
            attempt_id=payload["attempt_id"],
            run_id=payload["run_id"],
            index=int(payload["index"]),
            started_at=float(payload["started_at"]),
            status=AttemptStatus(payload["status"]),
            ended_at=payload.get("ended_at"),
            native_success=payload.get("native_success"),
            artifact_uri=payload.get("artifact_uri"),
            schema_version=payload["schema_version"],
        )

    @staticmethod
    def _decode_memory(payload: dict[str, Any]) -> MemoryRecord:
        return MemoryRecord(
            memory_id=payload["memory_id"],
            version=int(payload["version"]),
            source_trace_id=payload["source_trace_id"],
            guidance=payload["guidance"],
            candidate_repair=payload["candidate_repair"],
            applicability=dict(payload["applicability"]),
            evidence_refs=tuple(payload["evidence_refs"]),
            created_at=float(payload["created_at"]),
            status=MemoryStatus(payload["status"]),
            confidence=float(payload["confidence"]),
            validation_successes=int(payload["validation_successes"]),
            validation_failures=int(payload["validation_failures"]),
            expires_at=payload.get("expires_at"),
            parent_memory_id=payload.get("parent_memory_id"),
            status_reason=payload.get("status_reason"),
            schema_version=payload["schema_version"],
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
