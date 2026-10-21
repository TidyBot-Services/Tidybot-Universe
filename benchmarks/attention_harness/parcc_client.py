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
from .free_models import FREE_MODELS


DEFAULT_PARCC_URL = "https://litellm.parcc.upenn.edu/v1/chat/completions"
RETRYABLE_STATUS = {429, 502, 503, 504}


class ParccError(RuntimeError):
    """A bounded PARCC request failed or returned an unusable response."""


class _NoFirstByte(TimeoutError):
    """The successful response has not supplied any body bytes."""


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
    failed_attempts: tuple[dict[str, Any], ...] = ()

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
        if self.failed_attempts:
            value["failed_attempts"] = list(self.failed_attempts)
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
        retry_observer: Callable[[dict[str, Any]], None] | None = None,
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
        self.retry_observer = retry_observer
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
        deadline = started + self.timeout_seconds
        allowed_attempts = min(self.max_attempts, 5) if model in FREE_MODELS else 1
        failed_attempts: list[dict[str, Any]] = []
        last_error = "deadline exceeded"
        retry_reason = None
        for attempt in range(1, allowed_attempts + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if retry_reason is not None and self.retry_observer is not None:
                self.retry_observer({'attempt': attempt, 'reason': retry_reason,
                                     'max_attempts': allowed_attempts})
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
            attempt_started = time.monotonic()
            reason = None
            try:
                if self.stream:
                    status, response_headers, raw = self._read_stream(headers, encoded, deadline)
                else:
                    status, response_headers, raw = self._transport(self.endpoint, headers, encoded,
                                                                     self.timeout_seconds if attempt == 1 else remaining)
                if status == 200 and time.monotonic() >= deadline:
                    raise ParccError("PARCC total deadline exceeded")
                if status >= 400:
                    try:
                        body = json.loads(raw)
                    except (ValueError, UnicodeDecodeError):
                        body = None
                    last_error = f"PARCC returned HTTP {status}"
                    has_generation = isinstance(body, dict) and bool({'choices', 'usage'} & body.keys())
                    if not has_generation and (status in RETRYABLE_STATUS or
                                               status == 500 and isinstance(body, dict) and 'error' in body):
                        error = body.get('error') if isinstance(body, dict) else None
                        reason = (f"http_{status}_no_db" if status == 503 and
                                  isinstance(error, dict) and error.get('type') == 'no_db_connection'
                                  else f"http_{status}")
                    if reason is None:
                        if self.response_observer is not None:
                            self.response_observer(status, response_headers, raw if isinstance(body, dict) else b'{}')
                        raise ParccError(last_error)
                else:
                    try:
                        body = json.loads(raw)
                    except (ValueError, UnicodeDecodeError) as exc:
                        raise ParccError("PARCC HTTP 200 response contained invalid JSON") from exc
                    if not isinstance(body, dict):
                        raise ParccError("PARCC HTTP 200 response contained invalid body")
                    if self.response_observer is not None:
                        self.response_observer(status, response_headers, raw)
                    choices = body.get('choices')
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
                        raise ParccError("PARCC HTTP 200 response contained invalid choices")
                    choice = choices[0]
                    message = choice.get("message", {})
                    if not isinstance(message, dict):
                        raise ParccError("PARCC HTTP 200 response contained invalid message")
                    content = message.get("content")
                    if isinstance(content, str) and content.strip():
                        usage = body.get('usage') or {}
                        if not isinstance(usage, dict):
                            raise ParccError("PARCC HTTP 200 response contained invalid usage")
                        return ParccResponse(
                            model=model, content=content,
                            reasoning=message.get("reasoning", message.get("reasoning_content")),
                            latency_seconds=round(time.monotonic() - started, 3),
                            attempts=attempt, usage=dict(usage),
                            request_id=response_headers.get("x-request-id"),
                            finish_reason=choice.get("finish_reason"),
                            failed_attempts=tuple(failed_attempts),
                        )
                    raise ParccError("HTTP 200 response contained no final content")
            except ParccError:
                raise
            except HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS:
                    raise ParccError(f"PARCC returned HTTP {exc.code}") from exc
                reason = f'http_{exc.code}'
                last_error = f"PARCC returned HTTP {exc.code}"
            except (TimeoutError, URLError, OSError) as exc:
                reason = 'no_first_byte' if isinstance(exc, TimeoutError) else 'connect_error'
                last_error = reason
            failed_attempts.append({'attempt': attempt, 'reason': reason,
                                    'seconds_spent': round(time.monotonic() - attempt_started, 3)})
            if attempt < allowed_attempts:
                delay = self.retry_delay_seconds * (2 ** (attempt - 1))
                jitter = random.uniform(0.0, min(0.25, self.retry_delay_seconds / 4))
                if time.monotonic() + delay + jitter >= deadline:
                    break
                retry_reason = reason
                time.sleep(delay + jitter)
        error = ParccError(f"{model} unreachable after {len(failed_attempts)} attempts: {last_error}")
        error.failed_attempts = tuple(failed_attempts)
        raise error

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
                    try:
                        set_read_timeout(remaining)
                    except OSError as exc:
                        if status not in RETRYABLE_STATUS:
                            raise ParccError(f"PARCC returned HTTP {status}") from exc
                        raise
                    try:
                        part = next(iterator)
                    except StopIteration:
                        break
                    except (TimeoutError, OSError) as exc:
                        if status not in RETRYABLE_STATUS:
                            raise ParccError(f"PARCC returned HTTP {status}") from exc
                        raise
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
            body_started = False
            content: list[str] = []
            reasoning: dict[str, list[str]] = {}
            role = "assistant"
            finish_reason = None
            usage = None
            done = False
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if body_started:
                        raise ParccError("PARCC stream total deadline exceeded after body started")
                    raise _NoFirstByte("PARCC response supplied no first byte")
                try:
                    set_read_timeout(remaining)
                except OSError as exc:
                    if body_started:
                        raise ParccError("PARCC stream failed after HTTP 200 body started") from exc
                    raise _NoFirstByte("PARCC response supplied no first byte") from exc
                try:
                    line = next(iterator)
                except StopIteration:
                    break
                except (TimeoutError, OSError) as exc:
                    if body_started:
                        raise ParccError("PARCC stream failed after HTTP 200 body started") from exc
                    raise _NoFirstByte("PARCC response supplied no first byte") from exc
                if time.monotonic() >= deadline:
                    if body_started:
                        raise ParccError("PARCC stream total deadline exceeded after body started")
                    raise _NoFirstByte("PARCC response supplied no first byte")
                if line:
                    body_started = True
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
            try:
                raw = response.read()
            except (TimeoutError, OSError) as exc:
                raise ParccError("PARCC HTTP 200 body read failed") from exc
            return response.status, {k.lower(): v for k, v in response.headers.items()}, raw
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

        lines = _tracked_stream_lines(response) if hasattr(response, 'read') else response
        yield response.status, {k.lower(): v for k, v in response.headers.items()}, lines, set_read_timeout


def _tracked_stream_lines(response: Any) -> Iterable[bytes]:
    """Read one byte first so a stalled partial line cannot look like an empty response."""
    body_started = False
    while True:
        try:
            first = response.read(1)
        except (TimeoutError, OSError) as exc:
            if body_started:
                raise ParccError("PARCC stream failed after HTTP 200 body started") from exc
            raise _NoFirstByte("PARCC response supplied no first byte") from exc
        if not first:
            return
        body_started = True
        try:
            rest = response.readline()
        except (TimeoutError, OSError) as exc:
            raise ParccError("PARCC stream failed after HTTP 200 body started") from exc
        yield first + rest


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
