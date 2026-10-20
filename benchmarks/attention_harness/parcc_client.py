"""Small, auditable client for PARCC's OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import os
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Iterable
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
    finish_reason: str | None = None

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
StreamResult = tuple[int, dict[str, str], Iterable[bytes], Callable[[float], None]]
StreamTransport = Callable[[str, dict[str, str], bytes, float], ContextManager[StreamResult]]


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
        response_observer: Callable[[int, dict[str, str], bytes], None] | None = None,
        stream: bool = False,
        stream_transport: StreamTransport | None = None,
    ) -> None:
        self.endpoint = endpoint or os.environ.get("PARCC_URL", DEFAULT_PARCC_URL)
        self._api_key = api_key or os.environ.get("PARCC_API_KEY") or os.environ.get("LITELLM_KEY")
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
        self._transport = transport or _urlopen_transport
        self.stream = stream
        self._stream_transport = stream_transport or _urlopen_stream_transport
        self.response_observer = response_observer
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
        if self.stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        last_error = "unknown failure"
        for attempt in range(1, self.max_attempts + 1):
            try:
                if self.stream:
                    status, response_headers, raw = self._read_stream(
                        headers, encoded, started + self.timeout_seconds
                    )
                else:
                    status, response_headers, raw = self._transport(
                        self.endpoint, headers, encoded, self.timeout_seconds
                    )
                if self.response_observer is not None:
                    self.response_observer(status, response_headers, raw)
                body = json.loads(raw)
                if status >= 400:
                    last_error = (f"PARCC returned HTTP {status}" if self.stream
                                  else _safe_http_error(status, body))
                    if status not in RETRYABLE_STATUS:
                        raise ParccError(last_error)
                else:
                    choice = body.get("choices", [{}])[0]
                    message = choice.get("message", {})
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
                            finish_reason=choice.get("finish_reason"),
                        )
                    last_error = "HTTP 200 response contained no final content"
            except ParccError:
                raise
            except TimeoutError as exc:
                if self.stream:
                    raise
                last_error = f"{type(exc).__name__}: {exc}"
            except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.max_attempts:
                jitter = random.uniform(0.0, min(0.25, self.retry_delay_seconds / 4))
                time.sleep(self.retry_delay_seconds + jitter)
        raise ParccError(
            f"{model} failed after {self.max_attempts} attempts: {last_error}"
        )

    def _read_stream(
        self, headers: dict[str, str], encoded: bytes, deadline: float
    ) -> tuple[int, dict[str, str], bytes]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PARCC stream total deadline exceeded")
        with self._stream_transport(self.endpoint, headers, encoded, remaining) as response:
            status, response_headers, lines, set_read_timeout = response
            iterator = iter(lines)
            if status >= 400:
                parts = []
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("PARCC stream total deadline exceeded")
                    set_read_timeout(remaining)
                    try:
                        part = next(iterator)
                    except StopIteration:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("PARCC stream total deadline exceeded")
                    parts.append(part)
                raw = b"".join(parts)
                try:
                    json.loads(raw)
                except (ValueError, TypeError):
                    raw = b"{}"
                return status, response_headers, raw
            first: dict[str, Any] | None = None
            content: list[str] = []
            reasoning: dict[str, list[str]] = {}
            role = "assistant"
            finish_reason = None
            usage = None
            done = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("PARCC stream total deadline exceeded")
                set_read_timeout(remaining)
                try:
                    line = next(iterator)
                except StopIteration:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("PARCC stream total deadline exceeded")
                line = line.strip()
                if not line or line.startswith(b":"):
                    continue
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    done = True
                    break
                try:
                    chunk = json.loads(data)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ParccError("PARCC stream contained invalid JSON chunk") from exc
                if not isinstance(chunk, dict):
                    raise ParccError("PARCC stream contained invalid chunk")
                if "error" in chunk:
                    raise ParccError("PARCC stream returned error")
                if first is None:
                    first = chunk
                if "usage" in chunk and chunk["usage"] is not None:
                    usage = chunk["usage"]
                choices = chunk.get("choices") or []
                if not isinstance(choices, list):
                    raise ParccError("PARCC stream contained invalid chunk")
                if choices:
                    choice = choices[0]
                    if not isinstance(choice, dict):
                        raise ParccError("PARCC stream contained invalid chunk")
                    delta = choice.get("delta") or {}
                    if not isinstance(delta, dict):
                        raise ParccError("PARCC stream contained invalid chunk")
                    if isinstance(delta.get("role"), str):
                        role = delta["role"]
                    if isinstance(delta.get("content"), str):
                        content.append(delta["content"])
                    for key in ("reasoning", "reasoning_content"):
                        if isinstance(delta.get(key), str):
                            reasoning.setdefault(key, []).append(delta[key])
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice["finish_reason"]
            if not done:
                raise ParccError("PARCC stream ended before [DONE]")
            body: dict[str, Any] = {key: first[key] for key in ("id", "model", "created")
                                    if first is not None and key in first}
            body["object"] = "chat.completion"
            message: dict[str, Any] = {"role": role, "content": "".join(content)}
            message.update({key: "".join(parts) for key, parts in reasoning.items()})
            body["choices"] = [{"index": 0, "message": message, "finish_reason": finish_reason}]
            if usage is not None:
                body["usage"] = usage
            return status, response_headers, json.dumps(body, separators=(",", ":")).encode("utf-8")


def _urlopen_transport(
    endpoint: str, headers: dict[str, str], data: bytes, timeout: float
) -> tuple[int, dict[str, str], bytes]:
    request = Request(endpoint, data=data, method="POST", headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


@contextmanager
def _urlopen_stream_transport(
    endpoint: str, headers: dict[str, str], data: bytes, timeout: float
) -> Iterable[StreamResult]:
    request = Request(endpoint, data=data, method="POST", headers=headers)
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        with exc:
            def set_read_timeout(seconds: float) -> None:
                _set_stream_socket_timeout(exc, seconds)

            yield exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc, set_read_timeout
        return
    with response:
        def set_read_timeout(seconds: float) -> None:
            _set_stream_socket_timeout(response, seconds)

        yield response.status, {k.lower(): v for k, v in response.headers.items()}, response, set_read_timeout


def _set_stream_socket_timeout(response: Any, seconds: float) -> None:
    current = response
    for _ in range(4):
        raw = getattr(current, "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.settimeout(seconds)
            return
        current = getattr(current, "fp", None)
        if current is None:
            return


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
