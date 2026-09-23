"""Fixed AdvisorProxy transport with deterministic caching and logical latency."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from typing import Any, Callable

from ..advisor_proxy import advisor_cache_key, build_advisor_request
from .store import AttentionStore
from .models import AdvisorTracePacket


AdvisorTransport = Callable[[dict[str, Any]], str]


@dataclass(frozen=True)
class ProxyReply:
    content: str
    cache_key: str
    cached: bool


class AdvisorProxy:
    def __init__(
        self,
        store: AttentionStore,
        *,
        transport: AdvisorTransport,
        latency_seconds: float = 2.0,
        sleeper: Callable[[float], None],
    ) -> None:
        if latency_seconds < 0:
            raise ValueError("latency_seconds must be non-negative")
        self.store = store
        self.transport = transport
        self.latency_seconds = latency_seconds
        self.sleeper = sleeper

    def answer(
        self,
        *,
        request_type: str,
        trace_packet: Mapping[str, Any] | AdvisorTracePacket,
    ) -> ProxyReply:
        packet = (
            trace_packet.artifact()
            if isinstance(trace_packet, AdvisorTracePacket)
            else trace_packet
        )
        request = build_advisor_request(
            request_type=request_type,
            trace_packet=packet,
        )
        key = advisor_cache_key(request)
        cached = self.store.cache_get(key)
        if cached is None:
            content = self.transport(request)
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError("AdvisorProxy returned an empty response")
            self.store.cache_put(
                key,
                {"model": request["model"], "content": content.strip()},
            )
            was_cached = False
        else:
            content = str(cached["content"])
            was_cached = True
        # Cached and uncached responses use the same logical latency so timing
        # does not reveal cache state or alter benchmark conditions.
        self.sleeper(self.latency_seconds)
        return ProxyReply(content, key, was_cached)
