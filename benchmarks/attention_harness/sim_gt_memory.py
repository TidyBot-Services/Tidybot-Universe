"""Shared sim_gt policy identity and candidate-ingestion path for both suites."""

from __future__ import annotations

import hashlib
import marshal
from collections.abc import Callable
from typing import Any

from attention_memory_service import MemoryService, MemoryServiceClient

from .attention_modes import AssistanceMode, MODE_SPECS, RequestState
from .core.advisor import AdvisorTransport
from .core.models import AttentionRequestRecord, RequestPriority, RequestType
from .core.runtime import AttentionRuntime
from .core.store import AttentionStore
from .memory_agent import MemoryAgent
from .parcc_advisor import parse_advisor_advice
from .v2_advisor import SimGTAdvisorProxy


def policy_fingerprint(policy: Callable[..., Any]) -> str:
    code = getattr(policy, "__code__", None)
    if code is None:
        raise TypeError("v2 dev runner requires a Python function policy")
    closure = tuple(repr(cell.cell_contents) for cell in (policy.__closure__ or ()))
    payload = marshal.dumps(code) + repr((policy.__defaults__, closure)).encode()
    return hashlib.sha256(payload).hexdigest()


def ask_advisor_and_create_candidate(
    *, store: AttentionStore, result: dict[str, Any], transport: AdvisorTransport,
    sleeper: Callable[[float], None], clock: Callable[[], float],
    memory_gateway: MemoryService | MemoryServiceClient,
) -> None:
    link = result["attention_trace"]
    trace_id = link["advisor_trace_id"]
    if trace_id is None:
        result["advisor_error"] = "failed attempt has no Advisor-safe trace"
        return
    request = AttentionRequestRecord(
        request_id=f"advisor-request:{link['attempt_id']}",
        run_id=link["run_id"], attempt_id=link["attempt_id"], trace_id=trace_id,
        request_type=RequestType.HINT,
        reason="GT-perception policy attempt did not achieve native success",
        priority=RequestPriority.NORMAL, created_at=clock(), deadline_at=None,
        mode=AssistanceMode.BENCHMARK_PROXY,
    )
    proxy = SimGTAdvisorProxy(
        store, transport=transport,
        latency_seconds=MODE_SPECS[AssistanceMode.BENCHMARK_PROXY].proxy_latency_seconds,
        sleeper=sleeper,
    )
    runtime = AttentionRuntime(store, proxy, clock=clock)
    runtime.open_request(request)
    try:
        answered = runtime.resolve_benchmark_proxy(request.request_id)
        response = store.get_response(answered.response_id)
        advice = parse_advisor_advice(response["content"], request_type="hint")
        candidate = MemoryAgent(memory_gateway).ingest_answered_hint(
            request.request_id, memory_id=f"candidate:{link['attempt_id']}",
        )
        result["advisor_advice"] = advice.artifact()
        result["memory_candidate_id"] = candidate.memory_id
    except Exception as exc:
        current = store.get_request(request.request_id)
        if current is not None and current.state is RequestState.PENDING:
            runtime.cancel(request.request_id)
        result["advisor_error"] = f"{type(exc).__name__}: {exc}"
