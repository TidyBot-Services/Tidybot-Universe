"""Small, auditable client for PARCC's OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_PARCC_URL = "https://litellm.parcc.upenn.edu/v1/chat/completions"
RETRYABLE_STATUS = {404, 429, 500, 502, 503, 504}


class ParccError(RuntimeError):
    """A bounded PARCC request failed or returned an unusable response."""


@dataclass(frozen=True)
class ParccResponse:
    model: str
    content: str
    reasoning: str | None
    latency_seconds: float
    attempts: int
    usage: dict[str, Any]
    request_id: str | None = None

    def artifact(self, *, include_content: bool = True) -> dict[str, Any]:
        value = {
            "model": self.model,
            "latency_seconds": self.latency_seconds,
            "attempts": self.attempts,
            "usage": self.usage,
            "request_id": self.request_id,
            "reasoning_present": bool(self.reasoning),
        }
        if include_content:
            value["content"] = self.content
        return value


Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]


class ParccClient:
    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 180.0,
        max_attempts: int = 3,
        retry_delay_seconds: float = 2.0,
        transport: Transport | None = None,
    ) -> None:
        self.endpoint = endpoint or os.environ.get("PARCC_URL", DEFAULT_PARCC_URL)
        self._api_key = api_key or os.environ.get("PARCC_API_KEY") or os.environ.get("LITELLM_KEY")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._transport = transport or _urlopen_transport
        if not self._api_key:
            raise ParccError(
                "PARCC credential unavailable; set PARCC_API_KEY or LITELLM_KEY "
                "in the harness process"
            )
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float = 0.0,
        reasoning_effort: str = "low",
    ) -> ParccResponse:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
        }
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        last_error = "unknown failure"
        for attempt in range(1, self.max_attempts + 1):
            try:
                status, response_headers, raw = self._transport(
                    self.endpoint, headers, encoded, self.timeout_seconds
                )
                body = json.loads(raw)
                if status >= 400:
                    last_error = _safe_http_error(status, body)
                    if status not in RETRYABLE_STATUS:
                        raise ParccError(last_error)
                else:
                    message = body.get("choices", [{}])[0].get("message", {})
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        return ParccResponse(
                            model=model,
                            content=content,
                            reasoning=message.get("reasoning", message.get("reasoning_content")),
                            latency_seconds=round(time.monotonic() - started, 3),
                            attempts=attempt,
                            usage=dict(body.get("usage") or {}),
                            request_id=response_headers.get("x-request-id"),
                        )
                    last_error = "HTTP 200 response contained no final content"
            except ParccError:
                raise
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.max_attempts:
                jitter = random.uniform(0.0, min(0.25, self.retry_delay_seconds / 4))
                time.sleep(self.retry_delay_seconds + jitter)
        raise ParccError(
            f"{model} failed after {self.max_attempts} attempts: {last_error}"
        )


def _urlopen_transport(
    endpoint: str, headers: dict[str, str], data: bytes, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    request = Request(endpoint, data=data, method="POST", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def _safe_http_error(status: int, body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or error.get("type")
        else:
            detail = error
        if detail:
            return f"PARCC returned HTTP {status}: {str(detail)[:300]}"
    return f"PARCC returned HTTP {status}"
