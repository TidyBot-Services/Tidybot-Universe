"""Persistent request lifecycle for benchmark-proxy and live-human-first modes."""

from __future__ import annotations

from collections.abc import Mapping
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
        proxy: AdvisorProxy,
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
    ) -> AttentionRequestRecord:
        request = self._request(request_id)
        if request.mode is not AssistanceMode.BENCHMARK_PROXY:
            raise StateConflictError("request is not in benchmark-proxy mode")
        return self._resolve_proxy(request, trace_packet=trace_packet)

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
    ) -> AttentionRequestRecord:
        request = self._request(request_id)
        if request.mode is not AssistanceMode.LIVE_HUMAN_FIRST:
            raise StateConflictError("benchmark-proxy requests have no human deadline")
        if request.deadline_at is None or self.clock() < request.deadline_at:
            raise StateConflictError("request deadline has not elapsed")
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
        return self._resolve_proxy(fallback, trace_packet=trace_packet)

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
    ) -> AttentionRequestRecord:
        if request.state is RequestState.ANSWERED:
            return request
        if request.state not in {RequestState.PENDING, RequestState.FALLBACK}:
            raise StateConflictError(
                f"request in state {request.state.value} cannot use AdvisorProxy"
            )
        try:
            packet = self._advisor_packet(request, trace_packet)
            reply = self.proxy.answer(
                request_type=request.request_type.value,
                trace_packet=packet,
            )
            response = AttentionResponseRecord(
                response_id=f"proxy-response:{request.request_id}",
                request_id=request.request_id,
                responder="advisor_proxy",
                content=reply.content,
                created_at=self.clock(),
                cache_key=reply.cache_key,
                cached=reply.cached,
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
            return persisted
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
