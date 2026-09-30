"""Mode-aware Advisor request for GT-perception AttentionBench v2."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

from .advisor_proxy import ADVISOR_MODEL, build_advisor_request
from .core.advisor import AdvisorProxy, AdvisorTransportReply, ProxyReply
from .core.models import AdvisorTracePacket
from .parcc_advisor import parse_advisor_advice
from .parcc_client import ParccClient


SIM_GT_SYSTEM_PROMPT = """You are the fixed TidyBot AdvisorProxy for the
GT-perception AttentionBench track. Object names and positions produced by
the agent-visible sim_gt find_objects() SDK are permitted evidence. Their
source is simulator GT perception, not visual recognition. Use only the
supplied trace and public evidence. Never request evaluator success/debug,
hidden physics state, actor IDs, or actions outside the SDK. Return exactly
one JSON object with schema_version, request_type, diagnosis, guidance,
caution, and confidence, following attentionbench.advisor-advice.v1."""

SIM_GT_MAX_TOKENS = 1024


class SimGTGLMAdvisorTransport:
    """Validate GLM's JSON and account for bounded format retries in v2."""

    def __init__(self, client: ParccClient | None = None, *, format_attempts: int = 2,
                 strict_usage: bool = False, response_log_dir: Path | None = None,
                 token_budget_limit: int = 4096) -> None:
        if format_attempts < 1:
            raise ValueError("format_attempts must be positive")
        self.client = client or ParccClient()
        self.format_attempts = format_attempts
        self.strict_usage = strict_usage
        self.response_log_dir = response_log_dir
        self.token_budget_limit = token_budget_limit
        if strict_usage and (format_attempts != 1 or getattr(self.client, "max_attempts", 1) != 1):
            raise ValueError("frozen Advisor requires one provider call and no format retry")

    def __call__(self, request: dict[str, Any]) -> AdvisorTransportReply:
        if request.get("model") != ADVISOR_MODEL:
            raise ValueError("unexpected sim_gt Advisor model")
        payload = json.loads(request["messages"][1]["content"])
        request_type = payload["request_type"]
        usage: dict[str, int] = {}
        latency = 0.0
        attempts = 0
        last_error: ValueError | None = None
        for _ in range(self.format_attempts):
            log_path = None
            if self.strict_usage and self.response_log_dir is not None:
                self.response_log_dir.mkdir(parents=True, exist_ok=True)
                log_path = self.response_log_dir / f"{uuid4().hex}.json"
                def retain_http(status, headers, body):
                    raw_path = log_path.with_suffix(".http-body")
                    raw_path.write_bytes(body)
                    log_path.with_suffix(".http.json").write_text(json.dumps({
                        "status": status, "request_id": headers.get("x-request-id"),
                        "body_path": str(raw_path),
                        "body_sha256": hashlib.sha256(body).hexdigest(),
                    }, indent=2) + "\n")
                self.client.response_observer = retain_http
            if self.strict_usage and "provider_timeout_seconds" in request:
                timeout = request["provider_timeout_seconds"]
                if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 90:
                    raise ValueError("strict Advisor timeout outside remaining case budget")
                self.client.timeout_seconds = min(getattr(self.client, "timeout_seconds", 90), timeout)
            try:
                response = self.client.chat(
                    model=ADVISOR_MODEL,
                    messages=list(request["messages"]),
                    max_tokens=int(request["max_tokens"]),
                    temperature=float(request["temperature"]),
                    reasoning_effort=str(request["reasoning_effort"]),
                )
            except Exception as exc:
                if log_path is not None:
                    log_path.write_text(json.dumps({"status": "invalid_provider_response",
                        "error": f"{type(exc).__name__}: {exc}", "usage": None,
                        "content": None, "provider_cost_known": False}, indent=2) + "\n")
                raise
            raw = {"content": response.content, "usage": response.usage,
                   "model": response.model, "attempts": response.attempts,
                   "latency_seconds": response.latency_seconds, "request_id": response.request_id,
                   "status": "received", "provider_cost_known": True}
            if log_path is not None:
                log_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            if self.strict_usage:
                keys = ("prompt_tokens", "completion_tokens", "total_tokens")
                counts = response.usage
                usage_valid = (isinstance(counts, dict) and all(type(counts.get(k)) is int and counts[k] >= 0 for k in keys)
                         and counts["total_tokens"] == counts["prompt_tokens"] + counts["completion_tokens"]
                         and response.attempts == 1)
                if not usage_valid or counts["total_tokens"] > self.token_budget_limit:
                    raw.update(status="invalid_provider_usage", provider_cost_known=usage_valid)
                    if log_path is not None:
                        log_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                    raise ValueError("invalid or over-budget raw provider usage retained")
            latency += response.latency_seconds
            attempts += response.attempts
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = response.usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[key] = usage.get(key, 0) + value
            try:
                advice = parse_advisor_advice(response.content, request_type=request_type)
            except (ValueError, TypeError) as exc:
                if self.strict_usage:
                    raw.update(status="invalid_advisor_format", error=str(exc))
                    if log_path is not None:
                        log_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                    raise ValueError("invalid Advisor format; raw provider response and usage retained") from exc
                last_error = ValueError(str(exc))
                continue
            return AdvisorTransportReply(
                content=advice.canonical_json(),
                model=response.model,
                latency_seconds=latency,
                attempts=attempts,
                usage=usage,
                request_id=response.request_id,
            )
        raise ValueError(
            f"GLM returned no valid Advisor JSON after {self.format_attempts} format attempts: {last_error}"
        )


_VOLATILE_FIELDS = frozenset({
    "trace_id", "raw_trace_id", "run_id", "attempt_id", "execution_id",
    "created_at", "timestamp", "duration_ms", "elapsed_seconds",
    "artifact_uri", "uri",
    "event_id", "evidence_id", "first_failed_event_id",
    "last_successful_event_id",
})


class SharedAdvisorCache:
    """Persistent response cache shared by runs under one experiment root."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS replies (key TEXT PRIMARY KEY, payload TEXT NOT NULL)")

    def get(self, key: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path, timeout=30) as db:
            row = db.execute("SELECT payload FROM replies WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("INSERT OR IGNORE INTO replies(key, payload) VALUES (?, ?)",
                       (key, json.dumps(value, sort_keys=True, ensure_ascii=False)))


def semantic_advisor_cache_key(request: Mapping[str, Any]) -> str:
    """Hash advisor-visible substance, excluding run-local identities and clocks.

    The actual transport request remains untouched for audit. This key is
    versioned so future normalization changes cannot reuse older responses.
    Image data, code, action results, failure history, model and prompt remain
    part of the identity.
    """

    def stable(value: Any, path: tuple[str, ...] = ()) -> Any:
        if isinstance(value, Mapping):
            return {
                key: stable(item, (*path, key)) for key, item in value.items()
                if key not in _VOLATILE_FIELDS
                and not (path == ("trace_packet", "projection")
                         and key in {"source_sha256", "projection_sha256"})
            }
        if isinstance(value, list):
            return [stable(item, path) for item in value]
        return value

    normalized = dict(request)
    messages = list(normalized["messages"])
    user = dict(messages[1])
    payload = json.loads(user["content"])
    user["content"] = stable(payload)
    messages[1] = user
    normalized["messages"] = messages
    encoded = json.dumps(
        {"cache_schema": "attentionbench.sim-gt-semantic-cache.v2", "request": normalized},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SimGTAdvisorProxy(AdvisorProxy):
    """Keep v1 request safety checks but use a distinct prompt/cache identity."""

    def __init__(self, *args: Any, cache_path: Path | None = None,
                 strict_token_budget_remaining: int | None = None,
                 provider_timeout_seconds: float | None = None,
                 usage_rejection_path: Path | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.shared_cache = SharedAdvisorCache(cache_path) if cache_path is not None else None
        self.strict_token_budget_remaining = strict_token_budget_remaining
        self.provider_timeout_seconds = provider_timeout_seconds
        self.usage_rejection_path = usage_rejection_path

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
        request["max_tokens"] = SIM_GT_MAX_TOKENS
        key = semantic_advisor_cache_key(request)
        cached = (self.shared_cache.get(key) if self.shared_cache is not None
                  else self.store.cache_get(key))
        if cached is None:
            if self.provider_timeout_seconds is not None:
                request["provider_timeout_seconds"] = self.provider_timeout_seconds
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
            if self.shared_cache is not None:
                self.shared_cache.put(key, artifact)
            else:
                self.store.cache_put(key, artifact)
            was_cached = False
        else:
            artifact = cached
            was_cached = True
        if self.strict_token_budget_remaining is not None:
            usage = artifact.get("usage")
            keys = ("prompt_tokens", "completion_tokens", "total_tokens")
            valid = (isinstance(usage, dict) and all(type(usage.get(k)) is int
                                                     and usage[k] >= 0 for k in keys)
                     and usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"])
            reason = ("missing_or_invalid_provider_token_usage" if not valid else
                      "provider_total_tokens_exceed_run_budget" if not was_cached
                      and usage["total_tokens"] > self.strict_token_budget_remaining else None)
            if reason:
                if self.usage_rejection_path is not None:
                    self.usage_rejection_path.parent.mkdir(parents=True, exist_ok=True)
                    self.usage_rejection_path.write_text(json.dumps({
                        "schema_version": "attentionbench.advisor-usage-rejection.v1",
                        "reason": reason, "request": request, "response": artifact,
                        "cached": was_cached, "cache_key": key,
                        "remaining_tokens": self.strict_token_budget_remaining,
                        "provider_total_tokens_observed": usage.get("total_tokens") if isinstance(usage, dict) else None,
                    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                raise ValueError(reason)
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
