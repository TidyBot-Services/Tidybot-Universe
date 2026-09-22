from __future__ import annotations

import json

import pytest

from benchmarks.attention_harness.parcc_client import ParccClient, ParccError


def test_parcc_request_contract_and_response_metadata() -> None:
    captured = {}

    def transport(endpoint, headers, data, timeout):
        captured.update(
            endpoint=endpoint,
            authorization=headers["Authorization"],
            payload=json.loads(data),
            timeout=timeout,
        )
        body = {
            "choices": [{"message": {"content": "OK", "reasoning_content": "trace"}}],
            "usage": {"total_tokens": 12},
        }
        return 200, {"x-request-id": "req-1"}, json.dumps(body).encode()

    client = ParccClient(api_key="protected", transport=transport, timeout_seconds=9)
    response = client.chat(
        model="parcc/GLM",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=32,
    )
    assert response.content == "OK"
    assert response.reasoning == "trace"
    assert response.request_id == "req-1"
    assert response.usage == {"total_tokens": 12}
    assert captured["payload"]["reasoning_effort"] == "low"
    assert captured["authorization"] == "Bearer protected"


def test_empty_content_is_retried_without_recording_secret() -> None:
    calls = 0

    def transport(_endpoint, _headers, _data, _timeout):
        nonlocal calls
        calls += 1
        content = "recovered" if calls == 2 else None
        return 200, {}, json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    client = ParccClient(
        api_key="do-not-leak", transport=transport, max_attempts=2, retry_delay_seconds=0
    )
    assert client.chat(model="parcc/GLM", messages=[], max_tokens=10).content == "recovered"
    assert calls == 2


def test_missing_credential_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("PARCC_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_KEY", raising=False)
    with pytest.raises(ParccError, match="credential unavailable"):
        ParccClient()
