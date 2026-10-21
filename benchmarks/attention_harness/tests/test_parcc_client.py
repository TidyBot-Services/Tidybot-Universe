from __future__ import annotations

import json
from urllib.error import HTTPError, URLError

import pytest

from benchmarks.attention_harness import parcc_client
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


def test_empty_content_is_not_retried() -> None:
    calls = 0

    def transport(_endpoint, _headers, _data, _timeout):
        nonlocal calls
        calls += 1
        content = "recovered" if calls == 2 else None
        return 200, {}, json.dumps({"choices": [{"message": {"content": content}}]}).encode()

    client = ParccClient(
        api_key="do-not-leak", transport=transport, max_attempts=2, retry_delay_seconds=0
    )
    with pytest.raises(ParccError, match="no final content"):
        client.chat(model="parcc/GLM", messages=[], max_tokens=10)
    assert calls == 1


def test_missing_credential_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("PARCC_API_KEY", raising=False)
    monkeypatch.delenv("LITELLM_KEY", raising=False)
    with pytest.raises(ParccError, match="credential unavailable"):
        ParccClient()


def test_503_no_db_twice_then_success_records_failed_attempts(monkeypatch):
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda _delay: None)
    calls = []
    retries = []
    def transport(*_args):
        calls.append(1)
        if len(calls) < 3:
            return 503, {}, b'{"error":{"type":"no_db_connection","message":"secret"}}'
        return 200, {}, b'{"choices":[{"message":{"content":"OK"}}],"usage":{"total_tokens":2}}'
    response = ParccClient(api_key='secret', max_attempts=5, transport=transport,
                           retry_observer=retries.append).chat(model='parcc/GLM', messages=[], max_tokens=1)
    assert response.attempts == 3
    assert [row['reason'] for row in response.artifact()['failed_attempts']] == ['http_503_no_db'] * 2
    assert [row['attempt'] for row in retries] == [2, 3]
    assert all('secret' not in str(row) for row in response.artifact()['failed_attempts'])
    assert response.usage == {'total_tokens': 2}


@pytest.mark.parametrize('status,body', [(429, b''), (502, b'upstream unavailable'),
                                         (504, b'{}'), (500, b'{"error":{"type":"unavailable"}}')])
def test_pre_generation_gateway_errors_retry(monkeypatch, status, body):
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda _delay: None)
    calls = []
    def transport(*_args):
        calls.append(1)
        if len(calls) == 1:return status, {}, body
        return 200, {}, b'{"choices":[{"message":{"content":"OK"}}]}'
    response = ParccClient(api_key='secret', max_attempts=5, transport=transport).chat(
        model='parcc/GLM', messages=[], max_tokens=1)
    assert response.attempts == 2
    assert response.failed_attempts[0]['reason'] == f'http_{status}'


def test_connect_errors_stop_after_five_with_exponential_backoff(monkeypatch):
    clock = [0.0]
    delays = []
    calls = []
    monkeypatch.setattr(parcc_client.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda delay: (delays.append(delay), clock.__setitem__(0, clock[0] + delay)))
    monkeypatch.setattr(parcc_client.random, 'uniform', lambda lo, hi: hi)
    def transport(*_args):
        calls.append(1)
        raise URLError('secret address')
    with pytest.raises(ParccError, match='unreachable after 5 attempts') as caught:
        ParccClient(api_key='secret', max_attempts=5, timeout_seconds=100,
                    transport=transport).chat(model='parcc/Qwen', messages=[], max_tokens=1)
    assert len(calls) == 5
    assert delays == [2.25, 4.25, 8.25, 16.25]
    assert len(caught.value.failed_attempts) == 5
    assert all(row['reason'] == 'connect_error' for row in caught.value.failed_attempts)
    assert 'secret' not in str(caught.value)


def test_deadline_prevents_sleep_and_second_attempt(monkeypatch):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(parcc_client.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda *_: pytest.fail('slept past deadline'))
    monkeypatch.setattr(parcc_client.random, 'uniform', lambda *_: 0)
    def transport(*_args):
        calls.append(1)
        clock[0] = 1.5
        raise URLError('offline')
    with pytest.raises(ParccError, match='after 1 attempts'):
        ParccClient(api_key='secret', max_attempts=5, timeout_seconds=2,
                    transport=transport).chat(model='parcc/GLM', messages=[], max_tokens=1)
    assert len(calls) == 1


@pytest.mark.parametrize('model,status', [('parcc/GLM', 401), ('parcc/GLM', 404),
                                         ('paid/model', 503), ('parcc/GLM', 500)])
def test_nonretryable_status_or_model_is_never_redispatched(monkeypatch, model, status):
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda *_: pytest.fail('unexpected retry'))
    calls = []
    def transport(*_args):
        calls.append(1)
        return status, {}, b'{"error":{"message":"no generation"}}' if status != 500 else b'not JSON'
    with pytest.raises(ParccError):
        ParccClient(api_key='secret', max_attempts=5, transport=transport).chat(model=model, messages=[], max_tokens=1)
    assert len(calls) == 1


def test_single_attempt_success_artifact_golden_bytes():
    response = ParccClient(api_key='secret', transport=lambda *_: (200, {}, b'{"choices":[{"message":{"content":"OK"}}]}')).chat(
        model='parcc/GLM', messages=[], max_tokens=1)
    assert json.dumps(response.artifact(), separators=(',', ':')).encode() == (
        b'{"model":"parcc/GLM","latency_seconds":' + str(response.latency_seconds).encode() +
        b',"attempts":1,"usage":{},"request_id":null,"reasoning_present":false,"content":"OK"}')


def test_http_500_with_generation_evidence_is_not_retried(monkeypatch):
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda *_: pytest.fail('unexpected retry'))
    calls = []
    def transport(*_args):
        calls.append(1)
        return 500, {}, b'{"error":{"type":"internal"},"usage":{"total_tokens":1}}'
    with pytest.raises(ParccError, match='HTTP 500'):
        ParccClient(api_key='secret', max_attempts=5, transport=transport).chat(
            model='parcc/GLM', messages=[], max_tokens=1)
    assert len(calls) == 1


def test_raised_http_401_is_not_mistaken_for_connect_error(monkeypatch):
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda *_: pytest.fail('unexpected retry'))
    calls = []
    def transport(*_args):
        calls.append(1)
        raise HTTPError('https://example.invalid', 401, 'bad key', {}, None)
    with pytest.raises(ParccError, match='HTTP 401'):
        ParccClient(api_key='secret', max_attempts=5, transport=transport).chat(
            model='parcc/GLM', messages=[], max_tokens=1)
    assert len(calls) == 1
