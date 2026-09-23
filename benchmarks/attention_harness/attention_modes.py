"""Frozen D7 assistance-mode and request-routing contract."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType


class AssistanceMode(str, Enum):
    BENCHMARK_PROXY = "benchmark_proxy"
    LIVE_HUMAN_FIRST = "live_human_first"


class RequestState(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class AssistanceModeSpec:
    mode: AssistanceMode
    primary_responder: str
    fallback_responder: str | None
    advisor_model: str
    response_cache_required: bool
    proxy_latency_seconds: float
    logical_cost_per_response: int
    human_deadline_seconds: float | None
    primary_ranking_eligible: bool


MODE_SPECS = MappingProxyType({
    AssistanceMode.BENCHMARK_PROXY: AssistanceModeSpec(
        mode=AssistanceMode.BENCHMARK_PROXY,
        primary_responder="advisor_proxy",
        fallback_responder=None,
        advisor_model="parcc/GLM",
        response_cache_required=True,
        proxy_latency_seconds=2.0,
        logical_cost_per_response=1,
        human_deadline_seconds=None,
        primary_ranking_eligible=True,
    ),
    AssistanceMode.LIVE_HUMAN_FIRST: AssistanceModeSpec(
        mode=AssistanceMode.LIVE_HUMAN_FIRST,
        primary_responder="human",
        fallback_responder="advisor_proxy",
        advisor_model="parcc/GLM",
        response_cache_required=True,
        proxy_latency_seconds=2.0,
        logical_cost_per_response=1,
        human_deadline_seconds=60.0,
        primary_ranking_eligible=False,
    ),
})


@dataclass(frozen=True)
class RunMode:
    """Immutable snapshot stored when a benchmark run starts."""

    assistance: AssistanceModeSpec
    execution_target: str
    assistance_budget: int


def lock_run_mode(
    mode: AssistanceMode | str,
    *,
    execution_target: str,
    assistance_budget: int,
) -> RunMode:
    selected = AssistanceMode(mode)
    if execution_target not in {"simulation", "real_robot"}:
        raise ValueError("execution_target must be simulation or real_robot")
    if (
        isinstance(assistance_budget, bool)
        or not isinstance(assistance_budget, int)
        or assistance_budget < 0
    ):
        raise ValueError("assistance_budget must be a non-negative integer")
    return RunMode(MODE_SPECS[selected], execution_target, assistance_budget)


@dataclass(frozen=True)
class AssistanceRequest:
    request_id: str
    mode: AssistanceMode
    created_at_seconds: float
    state: RequestState = RequestState.PENDING
    responder: str | None = None
    response: str | None = None

    def answer(self, *, responder: str, response: str) -> "AssistanceRequest":
        if self.state is not RequestState.PENDING:
            raise RuntimeError("only a pending request can be answered")
        expected = MODE_SPECS[self.mode].primary_responder
        if responder != expected:
            raise ValueError(f"{self.mode.value} expects {expected!r} first")
        if not response.strip():
            raise ValueError("response must not be empty")
        return replace(
            self,
            state=RequestState.ANSWERED,
            responder=responder,
            response=response,
        )

    def cancel(self) -> "AssistanceRequest":
        if self.state is not RequestState.PENDING:
            raise RuntimeError("only a pending request can be cancelled")
        return replace(self, state=RequestState.CANCELLED)

    def on_deadline(self, *, now_seconds: float) -> "AssistanceRequest":
        if self.state is not RequestState.PENDING:
            raise RuntimeError("only a pending request can reach its deadline")
        spec = MODE_SPECS[self.mode]
        if spec.human_deadline_seconds is None:
            raise RuntimeError("benchmark proxy requests do not wait for a human deadline")
        if now_seconds < self.created_at_seconds + spec.human_deadline_seconds:
            raise RuntimeError("request deadline has not elapsed")
        if spec.fallback_responder is None:
            return replace(self, state=RequestState.TIMEOUT)
        return replace(
            self,
            state=RequestState.FALLBACK,
            responder=spec.fallback_responder,
        )

    def answer_fallback(self, response: str) -> "AssistanceRequest":
        if self.state is not RequestState.FALLBACK or self.responder != "advisor_proxy":
            raise RuntimeError("request is not waiting for AdvisorProxy fallback")
        if not response.strip():
            raise ValueError("response must not be empty")
        return replace(self, state=RequestState.ANSWERED, response=response)
