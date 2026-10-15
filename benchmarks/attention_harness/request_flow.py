"""Lifecycle of the single outstanding Attention request of a run.

The scheduler asks for help after a failed attempt. In ``async`` mode it does
not stop: the responder (AdvisorProxy call, or a human with an AdvisorProxy
fallback at the deadline) works in the background while the agent keeps doing
safe work, and the reply is incorporated at the next attempt boundary. In
``blocking`` mode the scheduler waits for the reply, which is the baseline for
the blocking/asynchronous ablation.

This module owns *when* a reply is available. It never decides what the agent
does with it; the scheduler applies the reply and records adoption.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from typing import Any, Callable

from .attention_modes import AssistanceMode, RequestState
from .core.models import AttentionRequestRecord
from .core.policies import PolicyDecision
from .core.runtime import AttentionRuntime
from .core.store import AttentionStore

REQUEST_MODES = ("async", "blocking")

# Poll results of RequestFlow.status().
ANSWERED = "answered"
WAITING = "waiting"
CANCELLED = "cancelled"
ERROR = "error"
ABORTED = "aborted"


@dataclass
class OutstandingRequest:
    request: AttentionRequestRecord
    decision: PolicyDecision
    runtime: AttentionRuntime
    failure_slot: int
    trace_id: str
    link: dict[str, Any]
    blocking_reason: str | None
    opened_clock: float
    opened_monotonic: float
    future: Future | None = None
    error: BaseException | None = None
    abort_reason: str | None = None
    activity_count: int = 0
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def awaits_human(self) -> bool:
        return self.request.mode is AssistanceMode.LIVE_HUMAN_FIRST


class RequestFlow:
    def __init__(self, *, store: AttentionStore, clock: Callable[[], float],
                 monotonic: Callable[[], float], sleeper: Callable[[float], None],
                 human_deadline_seconds: float, request_mode: str = "async") -> None:
        if request_mode not in REQUEST_MODES:
            raise ValueError(f"request_mode must be one of {REQUEST_MODES}")
        self.store = store
        self.clock = clock
        self.monotonic = monotonic
        self.sleeper = sleeper
        self.human_deadline_seconds = human_deadline_seconds
        self.request_mode = request_mode
        self.outstanding: OutstandingRequest | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="attention-request")

    # -- opening -----------------------------------------------------------------

    def blocking_reason(self, decision: PolicyDecision, *, attempts_after: int) -> str | None:
        """Why this request cannot overlap with robot attempts, or None when it can."""
        if self.request_mode == "blocking":
            return "request_mode_blocking"
        if decision.request_type is not None and decision.request_type.value != "hint":
            return f"{decision.request_type.value}_gates_next_action"
        if attempts_after < 2:
            # One attempt is left: waiting for the guidance beats spending it blind.
            return "last_attempt_remaining"
        return None

    def open(self, request: AttentionRequestRecord, decision: PolicyDecision,
             runtime: AttentionRuntime, *, failure_slot: int, trace_id: str,
             link: dict[str, Any], blocking_reason: str | None) -> OutstandingRequest:
        if self.outstanding is not None:
            raise RuntimeError("only one request may be outstanding")
        runtime.open_request(request)
        outstanding = OutstandingRequest(
            request=request, decision=decision, runtime=runtime, failure_slot=failure_slot,
            trace_id=trace_id, link=link, blocking_reason=blocking_reason,
            opened_clock=self.clock(), opened_monotonic=self.monotonic(),
        )
        self.outstanding = outstanding
        if outstanding.awaits_human or blocking_reason is None:
            # The waiting window audits what the robot did while this request was open.
            self.store.begin_waiting_window(request.request_id, started_at=self.clock())
        if request.mode is AssistanceMode.BENCHMARK_PROXY:
            outstanding.future = self._pool.submit(
                runtime.resolve_benchmark_proxy, request.request_id)
        return outstanding

    # -- polling -----------------------------------------------------------------

    def status(self, *, block: bool = False,
               on_tick: Callable[[OutstandingRequest], str | None] | None = None,
               join_started: bool = True) -> str:
        """Return the request's state; optionally wait for an answer.

        ``join_started`` makes an already-running AdvisorProxy call finish
        before the caller continues, so the attempt that receives the guidance
        does not depend on provider speed. A human who has not answered yet and
        whose deadline has not passed is never waited for unless ``block``.
        """
        outstanding = self.outstanding
        if outstanding is None:
            raise RuntimeError("no outstanding request")
        limit = outstanding.opened_monotonic + self.human_deadline_seconds + 1.0
        while True:
            current = self.store.get_request(outstanding.request_id)
            if current is None:
                raise RuntimeError("outstanding request disappeared")
            if current.state is RequestState.ANSWERED:
                return ANSWERED
            if current.state in {RequestState.CANCELLED, RequestState.TIMEOUT}:
                return CANCELLED
            self._start_fallback_if_due(outstanding, current)
            future = outstanding.future
            if future is not None and future.done():
                error = future.exception()
                if error is not None:
                    outstanding.error = error
                    return ERROR
                continue  # the answer is persisted; re-read the state
            if future is not None:
                if not (block or join_started):
                    return WAITING
                wait([future], timeout=0.25)
                reason = on_tick(outstanding) if on_tick else None
                if reason:
                    outstanding.abort_reason = reason
                    return ABORTED
                continue
            # A human has until the deadline.
            if not block:
                return WAITING
            reason = on_tick(outstanding) if on_tick else None
            if reason:
                outstanding.abort_reason = reason
                return ABORTED
            if (self.monotonic() >= limit and current.deadline_at is not None
                    and self.clock() < current.deadline_at):
                outstanding.abort_reason = "human_deadline_clock_stalled"
                return ABORTED
            self.sleeper(min(0.25, max(0.0, (current.deadline_at or self.clock()) - self.clock())))

    def _start_fallback_if_due(self, outstanding: OutstandingRequest,
                               current: AttentionRequestRecord) -> None:
        if outstanding.future is not None or not outstanding.awaits_human:
            return
        if current.state is RequestState.FALLBACK:
            outstanding.future = self._pool.submit(
                outstanding.runtime.resolve_fallback, outstanding.request_id)
        elif (current.state is RequestState.PENDING and current.deadline_at is not None
              and self.clock() >= current.deadline_at):
            outstanding.future = self._pool.submit(
                outstanding.runtime.handle_deadline, outstanding.request_id)

    # -- closing -----------------------------------------------------------------

    def cancel(self) -> None:
        """Cancel the request if it is still open; a finished reply is left untouched."""
        outstanding = self.outstanding
        if outstanding is None:
            return
        current = self.store.get_request(outstanding.request_id)
        if current is not None and current.state in {RequestState.PENDING, RequestState.FALLBACK}:
            outstanding.runtime.cancel(outstanding.request_id)

    def end_window(self) -> None:
        outstanding = self.outstanding
        if outstanding is None:
            return
        window = self.store.waiting_windows(outstanding.request.run_id).get(outstanding.request_id)
        if window is not None and window.get("ended_at") is None:
            self.store.end_waiting_window(outstanding.request_id, ended_at=self.clock())

    def drain(self, timeout: float = 120.0) -> None:
        """Let a started Advisor call finish so token accounting matches the store."""
        outstanding = self.outstanding
        if outstanding is not None and outstanding.future is not None:
            wait([outstanding.future], timeout=timeout)

    def release(self) -> None:
        self.outstanding = None

    def close(self) -> None:
        self._pool.shutdown(wait=False)
