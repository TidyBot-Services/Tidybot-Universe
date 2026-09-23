from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path

import pytest

from benchmarks.attention_harness.attention_modes import (
    AssistanceMode,
    AssistanceRequest,
    RequestState,
    lock_run_mode,
    MODE_SPECS,
)


def test_benchmark_mode_is_proxy_only_and_immutable() -> None:
    run = lock_run_mode(
        AssistanceMode.BENCHMARK_PROXY,
        execution_target="simulation",
        assistance_budget=3,
    )
    assert run.assistance.primary_responder == "advisor_proxy"
    assert run.assistance.fallback_responder is None
    assert run.assistance.primary_ranking_eligible is True
    with pytest.raises(FrozenInstanceError):
        run.assistance_budget = 4
    with pytest.raises(TypeError):
        MODE_SPECS[AssistanceMode.BENCHMARK_PROXY] = run.assistance


def test_benchmark_request_rejects_human_response() -> None:
    request = AssistanceRequest("request-1", AssistanceMode.BENCHMARK_PROXY, 0.0)
    with pytest.raises(ValueError, match="advisor_proxy"):
        request.answer(responder="human", response="hint")
    answered = request.answer(responder="advisor_proxy", response="hint")
    assert answered.state is RequestState.ANSWERED


def test_live_mode_uses_proxy_only_after_human_deadline() -> None:
    request = AssistanceRequest("request-2", AssistanceMode.LIVE_HUMAN_FIRST, 10.0)
    with pytest.raises(RuntimeError, match="not elapsed"):
        request.on_deadline(now_seconds=69.9)
    fallback = request.on_deadline(now_seconds=70.0)
    assert fallback.state is RequestState.FALLBACK
    assert fallback.responder == "advisor_proxy"
    answered = fallback.answer_fallback("fallback hint")
    assert answered.state is RequestState.ANSWERED
    assert answered.response == "fallback hint"


def test_live_human_answer_prevents_later_fallback() -> None:
    request = AssistanceRequest("request-3", AssistanceMode.LIVE_HUMAN_FIRST, 0.0)
    answered = request.answer(responder="human", response="stop and regrasp")
    with pytest.raises(RuntimeError, match="pending"):
        answered.on_deadline(now_seconds=100.0)


def test_protocol_mode_values_match_executable_contract() -> None:
    protocol_path = (
        Path(__file__).resolve().parents[1] / "protocol" / "v1" / "protocol.json"
    )
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    benchmark = protocol["assistance_modes"]["benchmark_proxy"]
    live = protocol["assistance_modes"]["live_human_first"]
    assert benchmark["primary_responder"] == MODE_SPECS[
        AssistanceMode.BENCHMARK_PROXY
    ].primary_responder
    assert benchmark["fixed_proxy_latency_seconds"] == MODE_SPECS[
        AssistanceMode.BENCHMARK_PROXY
    ].proxy_latency_seconds
    assert live["human_deadline_seconds"] == MODE_SPECS[
        AssistanceMode.LIVE_HUMAN_FIRST
    ].human_deadline_seconds
    assert live["fallback_responder"] == MODE_SPECS[
        AssistanceMode.LIVE_HUMAN_FIRST
    ].fallback_responder
