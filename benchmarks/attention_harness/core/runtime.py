"""Persistent request lifecycle for benchmark-proxy and live-human-first modes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Callable

from ..attention_modes import AssistanceMode, RequestState
from .advisor import AdvisorProxy
from .models import (
    AdvisorTracePacket,
    AttentionRequestRecord,
    AttentionResponseRecord,
)
from .store import AttentionStore, StateConflictError


class AttentionRuntime:
    def __init__(
        self,
        store: AttentionStore,
        proxy: AdvisorProxy | None,
        *,
        clock: Callable[[], float],
    ) -> None:
        self.store = store
        self.proxy = proxy
        self.clock = clock

    def open_request(self, request: AttentionRequestRecord) -> AttentionRequestRecord:
        reservation_id = self._reservation_id(request.request_id)
        self.store.reserve_assistance(
            request.run_id, credits=1, reservation_id=reservation_id
        )
        try:
            return self.store.create_request(request)
        except Exception:
            self.store.settle_assistance(reservation_id, commit=False)
            raise

    def resolve_benchmark_proxy(
        self,
        request_id: str,
        *,
        trace_packet: Mapping[str, Any] | AdvisorTracePacket | None = None,
        public_images: Sequence[str] = (),
    ) -> AttentionRequestRecord:
        request = self._request(request_id)
        if request.mode is not AssistanceMode.BENCHMARK_PROXY:
            raise StateConflictError("request is not in benchmark-proxy mode")
        return self._resolve_proxy(request, trace_packet=trace_packet, public_images=public_images)

    def submit_human_response(
        self, request_id: str, *, response_id: str, content: str
    ) -> AttentionRequestRecord:
        request = self._request(request_id)
        if request.mode is not AssistanceMode.LIVE_HUMAN_FIRST:
            raise StateConflictError("benchmark-proxy requests cannot accept human responses")
        if request.deadline_at is not None and self.clock() >= request.deadline_at:
            raise StateConflictError("human response arrived after the request deadline")
        response = AttentionResponseRecord(
            response_id=response_id,
            request_id=request_id,
            responder="human",
            content=content,
            created_at=self.clock(),
        )
        updated = self.store.transition_request(
            request_id,
            RequestState.ANSWERED,
            response=response,
            event_key=f"human-answer:{request_id}",
        )
        self.store.settle_assistance(self._reservation_id(request_id), commit=True)
        return updated

    def handle_deadline(
        self,
        request_id: str,
        *,
        trace_packet: Mapping[str, Any] | AdvisorTracePacket | None = None,
        public_images: Sequence[str] = (),
    ) -> AttentionRequestRecord:
        request = self._request(request_id)
        if request.mode is not AssistanceMode.LIVE_HUMAN_FIRST:
            raise StateConflictError("benchmark-proxy requests have no human deadline")
        if request.deadline_at is None or self.clock() < request.deadline_at:
            raise StateConflictError("request deadline has not elapsed")
        self.store.record_request_timeout(request_id, timed_out_at=self.clock())
        if request.state is RequestState.PENDING:
            fallback = self.store.transition_request(
                request_id,
                RequestState.FALLBACK,
                event_key=f"human-deadline:{request_id}",
            )
        elif request.state is RequestState.FALLBACK:
            fallback = request
        else:
            raise StateConflictError(
                f"request in state {request.state.value} cannot enter fallback"
            )
        return self._resolve_proxy(fallback, trace_packet=trace_packet, public_images=public_images)

    def cancel(self, request_id: str) -> AttentionRequestRecord:
        request = self._request(request_id)
        updated = self.store.transition_request(
            request_id,
            RequestState.CANCELLED,
            event_key=f"cancel:{request_id}",
        )
        self.store.settle_assistance(self._reservation_id(request.request_id), commit=False)
        return updated

    def _resolve_proxy(
        self,
        request: AttentionRequestRecord,
        *,
        trace_packet: Mapping[str, Any] | AdvisorTracePacket | None,
        public_images: Sequence[str],
    ) -> AttentionRequestRecord:
        if request.state is RequestState.ANSWERED:
            return request
        if request.state not in {RequestState.PENDING, RequestState.FALLBACK}:
            raise StateConflictError(
                f"request in state {request.state.value} cannot use AdvisorProxy"
            )
        if self.proxy is None:
            raise StateConflictError("AdvisorProxy is not configured for this runtime")
        try:
            packet = self._advisor_packet(request, trace_packet)
            reply = self.proxy.answer(
                request_type=request.request_type.value,
                trace_packet=packet,
                public_images=public_images,
            )
            if reply.total_tokens:
                self.store.consume_resources(
                    request.run_id,
                    tokens=reply.total_tokens,
                    event_key=f"advisor-tokens:{request.request_id}",
                )
            response = AttentionResponseRecord(
                response_id=f"proxy-response:{request.request_id}",
                request_id=request.request_id,
                responder="advisor_proxy",
                content=reply.content,
                created_at=self.clock(),
                cache_key=reply.cache_key,
                cached=reply.cached,
                provider_model=reply.model,
                provider_latency_seconds=reply.provider_latency_seconds,
                logical_latency_seconds=reply.logical_latency_seconds,
                provider_attempts=reply.provider_attempts,
                token_usage=reply.usage,
                provider_request_id=reply.provider_request_id,
            )
            updated = self.store.transition_request(
                request.request_id,
                RequestState.ANSWERED,
                response=response,
                event_key=f"proxy-answer:{request.request_id}",
            )
        except Exception:
            # The request remains pending/fallback and its credit remains
            # reserved, allowing a safe retry without silently increasing the
            # budget.
            raise
        self.store.settle_assistance(
            self._reservation_id(request.request_id), commit=True
        )
        return updated

    def _advisor_packet(
        self,
        request: AttentionRequestRecord,
        supplied: Mapping[str, Any] | AdvisorTracePacket | None,
    ) -> Mapping[str, Any] | AdvisorTracePacket:
        if supplied is None:
            persisted = self.store.get_trace(request.trace_id)
            if persisted is None:
                raise StateConflictError(
                    f"trace {request.trace_id!r} does not exist"
                )
            attempt = self.store.get_attempt(request.attempt_id)
            if attempt is None or (persisted.get("raw_trace_id") is not None
                                   and attempt["status"] == "running"):
                raise StateConflictError("Advisor request requires a completed attempt")
            current_index = attempt["index"]
            history = []
            for event in self.store.events():
                if event["event_type"] != "trace.created":
                    continue
                prior = event["payload"]
                prior_attempt = self.store.get_attempt(prior["attempt_id"])
                if (prior["run_id"] != request.run_id or prior_attempt is None
                        or prior_attempt["index"] >= current_index):
                    continue
                failure = {
                    key: value[:300] if isinstance(value, str) else value
                    for key in ("stage", "error_type", "message", "observed_symptom",
                                "termination_reason", "classification_source")
                    if (value := prior.get("failure", {}).get(key)) is not None
                }
                history.append((prior_attempt["index"], {
                    "attempt_index": prior_attempt["index"],
                    "failure": failure,
                    "code_sha256": prior.get("code", {}).get("sha256"),
                    "evidence_refs": [
                        {"evidence_id": item["evidence_id"], "sha256": item["sha256"]}
                        for item in prior.get("evidence", [])[:8]
                    ],
                    "trace_projection_version": prior.get("projection", {}).get("policy_version"),
                }))
            history.sort(key=lambda item: item[0])
            run = self.store.get_run(request.run_id)
            if run is None:
                raise StateConflictError("Advisor request run is missing")
            conditions = {
                "suite": run["suite"], "task_id": run["task_id"], "seed": run["seed"],
                "policy_id": run["policy_id"],
                "execution_target": run["execution_target"],
                "assistance_mode": run["assistance_mode"],
                "budget": {
                    "assistance_credits": run["budget"]["assistance_credits"],
                    "language_budget_units": run["budget"]["token_limit"],
                    "execution_seconds": run["budget"]["execution_seconds"],
                    "gpu_seconds": run["budget"]["gpu_seconds"],
                },
            }
            return {**persisted,
                    "experiment": {**conditions, **persisted.get("experiment", {})},
                    "failure_history": [item for _, item in history[-3:]]}
        packet_id = (
            supplied.trace_id
            if isinstance(supplied, AdvisorTracePacket)
            else supplied.get("trace_id")
        )
        if packet_id is not None and packet_id != request.trace_id:
            raise StateConflictError("supplied trace does not match the request")
        return supplied

    def _request(self, request_id: str) -> AttentionRequestRecord:
        request = self.store.get_request(request_id)
        if request is None:
            raise StateConflictError(f"request {request_id!r} does not exist")
        return request

    @staticmethod
    def _reservation_id(request_id: str) -> str:
        return f"assistance:{request_id}"
