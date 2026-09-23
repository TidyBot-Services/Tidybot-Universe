"""Fixed AdvisorProxy transport with deterministic caching and logical latency."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Callable

from ..advisor_proxy import advisor_cache_key, build_advisor_request
from .store import AttentionStore
from .models import AdvisorTracePacket


@dataclass(frozen=True)
class AdvisorTransportReply:
    """Provider result before deterministic cache and logical-latency handling."""

    content: str
    model: str
    latency_seconds: float
    attempts: int
    usage: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("Advisor transport content must not be empty")
        if not self.model.strip():
            raise ValueError("Advisor transport model must not be empty")
        if self.latency_seconds < 0:
            raise ValueError("Advisor transport latency must be non-negative")
        if self.attempts < 1:
            raise ValueError("Advisor transport attempts must be positive")

    def cache_artifact(self) -> dict[str, Any]:
        return {
            "content": self.content.strip(),
            "model": self.model,
            "latency_seconds": self.latency_seconds,
            "attempts": self.attempts,
            "usage": dict(self.usage),
            "request_id": self.request_id,
        }


AdvisorTransport = Callable[[dict[str, Any]], str | AdvisorTransportReply]


@dataclass(frozen=True)
class ProxyReply:
    content: str
    cache_key: str
    cached: bool
    model: str
    provider_latency_seconds: float
    logical_latency_seconds: float
    provider_attempts: int
    usage: dict[str, Any] = field(default_factory=dict)
    provider_request_id: str | None = None

    @property
    def total_tokens(self) -> int:
        if self.cached:
            return 0
        value = self.usage.get("total_tokens", 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        return max(0, int(value))


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
        public_images: Sequence[str] = (),
    ) -> ProxyReply:
        packet = (
            trace_packet.artifact()
            if isinstance(trace_packet, AdvisorTracePacket)
            else trace_packet
        )
        request = build_advisor_request(
            request_type=request_type,
            trace_packet=packet,
            public_images=public_images,
        )
        key = advisor_cache_key(request)
        cached = self.store.cache_get(key)
        if cached is None:
            transported = self.transport(request)
            if isinstance(transported, str):
                transported = AdvisorTransportReply(
                    content=transported,
                    model=str(request["model"]),
                    latency_seconds=0.0,
                    attempts=1,
                )
            if not isinstance(transported, AdvisorTransportReply):
                raise TypeError("Advisor transport returned an unsupported response")
            content = transported.content.strip()
            if not content:
                raise RuntimeError("AdvisorProxy returned an empty response")
            cached_artifact = transported.cache_artifact()
            self.store.cache_put(key, cached_artifact)
            was_cached = False
        else:
            content = str(cached["content"])
            cached_artifact = cached
            was_cached = True
        # Cached and uncached responses use the same logical latency so timing
        # does not reveal cache state or alter benchmark conditions.
        self.sleeper(self.latency_seconds)
        return ProxyReply(
            content=content,
            cache_key=key,
            cached=was_cached,
            model=str(cached_artifact.get("model", request["model"])),
            provider_latency_seconds=(
                0.0 if was_cached else float(cached_artifact.get("latency_seconds", 0.0))
            ),
            logical_latency_seconds=self.latency_seconds,
            provider_attempts=0 if was_cached else int(cached_artifact.get("attempts", 1)),
            usage={} if was_cached else dict(cached_artifact.get("usage") or {}),
            provider_request_id=None if was_cached else cached_artifact.get("request_id"),
        )
