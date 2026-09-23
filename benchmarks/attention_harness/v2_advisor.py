"""Mode-aware Advisor request for GT-perception AttentionBench v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .advisor_proxy import advisor_cache_key, build_advisor_request
from .core.advisor import AdvisorProxy, AdvisorTransportReply, ProxyReply
from .core.models import AdvisorTracePacket


SIM_GT_SYSTEM_PROMPT = """You are the fixed TidyBot AdvisorProxy for the
GT-perception AttentionBench track. Object names and positions produced by
the agent-visible sim_gt find_objects() SDK are permitted evidence. Their
source is simulator GT perception, not visual recognition. Use only the
supplied trace and public evidence. Never request evaluator success/debug,
hidden physics state, actor IDs, or actions outside the SDK. Return exactly
one JSON object with schema_version, request_type, diagnosis, guidance,
caution, and confidence, following attentionbench.advisor-advice.v1."""


class SimGTAdvisorProxy(AdvisorProxy):
    """Keep v1 request safety checks but use a distinct prompt/cache identity."""

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
        request["messages"][0] = {"role": "system", "content": SIM_GT_SYSTEM_PROMPT}
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
            artifact = transported.cache_artifact()
            self.store.cache_put(key, artifact)
            was_cached = False
        else:
            artifact = cached
            was_cached = True
        self.sleeper(self.latency_seconds)
        return ProxyReply(
            content=str(artifact["content"]),
            cache_key=key,
            cached=was_cached,
            model=str(artifact.get("model", request["model"])),
            provider_latency_seconds=(
                0.0 if was_cached else float(artifact.get("latency_seconds", 0.0))
            ),
            logical_latency_seconds=self.latency_seconds,
            provider_attempts=0 if was_cached else int(artifact.get("attempts", 1)),
            usage={} if was_cached else dict(artifact.get("usage") or {}),
            provider_request_id=None if was_cached else artifact.get("request_id"),
        )
