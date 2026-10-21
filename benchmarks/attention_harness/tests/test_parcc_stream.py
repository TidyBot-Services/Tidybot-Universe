from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from benchmarks.attention_harness import dev_provider_worker, parcc_client
from benchmarks.attention_harness.parcc_client import ParccClient, ParccError, ParccResponse


def event(value):
    return b"data: " + json.dumps(value).encode() + b"\n"


def fake_stream(lines, *, status=200, headers=None, captured=None):
    @contextmanager
    def transport(endpoint, request_headers, data, timeout):
        if captured is not None:
            captured.update(endpoint=endpoint, headers=request_headers, data=data, timeout=timeout)
        yield status, headers or {}, iter(lines), lambda seconds: None

    return transport


def chat(client):
    return client.chat(model="parcc/GLM", messages=[{"role": "user", "content": "hello"}], max_tokens=32)


@pytest.mark.parametrize("reasoning_key", ["reasoning_content", "reasoning"])
def test_stream_assembles_completion_and_observer_matches_nonstream(reasoning_key):
    wire = {
        "id": "chat-1", "model": "parcc/GLM", "created": 123, "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Hello world", reasoning_key: "think twice"},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }
    chunks = [
        event({"id": "chat-1", "model": "parcc/GLM", "created": 123,
               "choices": [{"delta": {"content": "Hello ", reasoning_key: "think "}}]}),
        event({"choices": [{"delta": {"content": "world", reasoning_key: "twice"},
                            "finish_reason": "stop"}]}),
        event({"choices": [], "usage": wire["usage"]}), b"data: [DONE]\n",
    ]
    observed = []
    streamed = ParccClient(api_key="secret", max_attempts=1,
                           stream=True, stream_transport=fake_stream(chunks, headers={"x-request-id": "req-1"}),
                           response_observer=lambda status, headers, raw: observed.append((status, headers, json.loads(raw))))
    ordinary = ParccClient(api_key="secret", max_attempts=1,
                           transport=lambda *_: (200, {"x-request-id": "req-1"}, json.dumps(wire).encode()))
    stream_response = chat(streamed)
    plain_response = chat(ordinary)
    assert observed == [(200, {"x-request-id": "req-1"}, wire)]
    assert (stream_response.model, stream_response.content, stream_response.reasoning,
            stream_response.usage, stream_response.request_id, stream_response.finish_reason) == (
            plain_response.model, plain_response.content, plain_response.reasoning,
            plain_response.usage, plain_response.request_id, plain_response.finish_reason)


def test_missing_usage_is_not_invented():
    observed = []
    client = ParccClient(api_key="secret", stream=True, max_attempts=5,
                         stream_transport=fake_stream([event({"choices": [{"delta": {"content": "OK"}}]}),
                                                       b"data: [DONE]\n"]),
                         response_observer=lambda _status, _headers, raw: observed.append(json.loads(raw)))
    response = chat(client)
    assert response.usage == {}
    assert response.attempts == 1
    assert "usage" not in observed[0]
    assert not all(type(response.usage.get(k)) is int for k in
                   ("total_tokens", "prompt_tokens", "completion_tokens"))


@pytest.mark.parametrize("line, message", [
    (event({"error": {"message": "secret request body"}}), "PARCC stream returned error"),
    (b"data: {invalid}\n", "invalid JSON chunk"),
])
def test_midstream_errors_are_safe(line, message):
    client = ParccClient(api_key="secret", stream=True, max_attempts=1,
                         stream_transport=fake_stream([event({"choices": [{"delta": {"content": "partial"}}]}), line]))
    with pytest.raises(ParccError, match=message) as exc:
        chat(client)
    assert "secret" not in str(exc.value)
    assert "request body" not in str(exc.value)


def test_truncated_stream_rejected():
    client = ParccClient(api_key="secret", stream=True, max_attempts=5,
                         stream_transport=fake_stream([event({"choices": [{"delta": {"content": "partial"}}]})]))
    with pytest.raises(ParccError, match="before \\[DONE\\]"):
        chat(client)


def test_empty_final_content_has_existing_error_style():
    client = ParccClient(api_key="secret", stream=True, max_attempts=1,
                         stream_transport=fake_stream([event({"choices": [{"delta": {"content": " "}}]}),
                                                       b"data: [DONE]\n"]))
    with pytest.raises(ParccError, match="HTTP 200 response contained no final content"):
        chat(client)


def test_total_deadline_expires_while_chunks_keep_arriving(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(parcc_client.time, "monotonic", lambda: clock[0])
    timeouts = []

    def lines():
        for _ in range(4):
            clock[0] += 0.3
            yield event({"choices": [{"delta": {"content": "x"}}]})
        yield b"data: [DONE]\n"

    @contextmanager
    def transport(*_args):
        yield 200, {}, lines(), timeouts.append

    client = ParccClient(api_key="secret", stream=True, timeout_seconds=1.0,
                         max_attempts=1, stream_transport=transport)
    with pytest.raises(ParccError, match="total deadline"):
        chat(client)
    assert timeouts and timeouts[-1] < timeouts[0]


def test_stream_open_and_reads_use_full_remaining_deadline(monkeypatch):
    clock = [5.0]
    initial_times = iter([0.0, 5.0])
    monkeypatch.setattr(parcc_client.time, "monotonic",
                        lambda: next(initial_times, clock[0]))
    opened = []
    read_timeouts = []

    def lines():
        clock[0] = 10.0
        yield event({"choices": [{"delta": {"content": "OK"}}]})
        yield b"data: [DONE]\n"

    @contextmanager
    def transport(_endpoint, _headers, _data, timeout):
        opened.append(timeout)
        clock[0] = 7.0
        yield 200, {}, lines(), read_timeouts.append

    client = ParccClient(api_key="secret", stream=True, timeout_seconds=300.0,
                         max_attempts=1, stream_transport=transport)
    assert chat(client).content == "OK"
    assert opened == [295.0]
    assert opened[0] > 60.0
    assert read_timeouts == [293.0, 290.0]


def test_stream_header_wait_past_deadline_raises_timeout_without_retry():
    calls = []

    @contextmanager
    def transport(_endpoint, _headers, _data, timeout):
        calls.append(timeout)
        time.sleep(0.03)
        yield 200, {}, iter([b"data: [DONE]\n"]), lambda _seconds: None

    client = ParccClient(api_key="secret", stream=True, timeout_seconds=0.01,
                         max_attempts=2, stream_transport=transport)
    with pytest.raises(ParccError, match="unreachable after 1 attempts"):
        chat(client)
    assert len(calls) == 1


def test_no_first_byte_timeout_then_success_within_deadline(monkeypatch):
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda _delay: None)
    calls = []
    @contextmanager
    def transport(*_args):
        calls.append(1)
        if len(calls) == 1:
            def stalled():
                raise TimeoutError('waiting for first byte')
                yield b''
            yield 200, {}, stalled(), lambda _seconds: None
        else:
            yield 200, {}, iter([event({'choices': [{'delta': {'content': 'OK'}}]}), b'data: [DONE]\n']), lambda _seconds: None
    client = ParccClient(api_key='secret', stream=True, max_attempts=5,
                         stream_transport=transport)
    response = chat(client)
    assert response.attempts == 2
    assert response.artifact()['failed_attempts'][0]['reason'] == 'no_first_byte'
    assert len(calls) == 2


def test_stream_503_no_db_twice_then_success(monkeypatch):
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda _delay: None)
    calls = []
    @contextmanager
    def transport(*_args):
        calls.append(1)
        if len(calls) < 3:
            yield 503, {}, iter([b'{"error":{"type":"no_db_connection"}}']), lambda _seconds: None
        else:
            yield 200, {}, iter([event({'choices': [{'delta': {'content': 'OK'}}]}),
                                 event({'choices': [], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}),
                                 b'data: [DONE]\n']), lambda _seconds: None
    response = chat(ParccClient(api_key='secret', stream=True, max_attempts=5,
                                stream_transport=transport))
    assert response.attempts == 3
    assert len(response.artifact()['failed_attempts']) == 2
    assert [row['reason'] for row in response.artifact()['failed_attempts']] == ['http_503_no_db'] * 2


def test_partial_stream_timeout_never_retries(monkeypatch):
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda *_: pytest.fail('unexpected retry'))
    calls = []
    @contextmanager
    def transport(*_args):
        calls.append(1)
        def lines():
            yield event({'choices': [{'delta': {'content': 'partial'}}]})
            raise TimeoutError('late failure')
        yield 200, {}, lines(), lambda _seconds: None
    with pytest.raises(ParccError, match='after HTTP 200 body started'):
        chat(ParccClient(api_key='secret', stream=True, max_attempts=5, stream_transport=transport))
    assert len(calls) == 1


def test_keepalive_and_blank_lines_are_ignored():
    client = ParccClient(api_key="secret", stream=True, max_attempts=1,
                         stream_transport=fake_stream([b": keepalive\n", b"\n",
                                                       event({"choices": [{"delta": {"content": "OK"}}]}),
                                                       b": still alive\n", b"data: [DONE]\n"]))
    assert chat(client).content == "OK"


def test_http_error_body_is_safe():
    observed = []
    client = ParccClient(api_key="secret", stream=True, max_attempts=1,
                         stream_transport=fake_stream([b'{"error":{"message":"secret request body"}}'], status=400),
                         response_observer=lambda status, _headers, raw: observed.append((status, json.loads(raw))))
    with pytest.raises(ParccError, match="PARCC returned HTTP 400") as exc:
        chat(client)
    assert "secret" not in str(exc.value)
    assert observed[0][0] == 400


def test_stream_payload_and_nonstream_golden_bytes():
    streamed = {}
    ordinary = {}
    stream_client = ParccClient(api_key="secret", stream=True, max_attempts=1,
                                stream_transport=fake_stream([event({"choices": [{"delta": {"content": "OK"}}]}),
                                                              b"data: [DONE]\n"], captured=streamed))
    def plain_transport(endpoint, headers, data, timeout):
        ordinary["data"] = data
        return 200, {}, b'{"choices":[{"message":{"content":"OK"}}]}'

    plain_client = ParccClient(api_key="secret", max_attempts=1, transport=plain_transport)
    chat(stream_client)
    chat(plain_client)
    assert ordinary["data"] == (b'{"model":"parcc/GLM","messages":[{"role":"user","content":"hello"}],'
                                b'"temperature":0.0,"max_tokens":32,"reasoning_effort":"low"}')
    assert json.loads(streamed["data"]) == {**json.loads(ordinary["data"]),
                                               "stream": True, "stream_options": {"include_usage": True}}


def test_default_client_uses_nonstream_transport():
    calls = []
    timeouts = []

    def transport(_endpoint, _headers, _data, timeout):
        calls.append("ordinary")
        timeouts.append(timeout)
        return 200, {}, b'{"choices":[{"message":{"content":"OK"}}]}'

    client = ParccClient(api_key="secret", max_attempts=1,
                         transport=transport,
                         stream_transport=lambda *_: pytest.fail("stream transport used"))
    assert client.stream is False
    assert chat(client).content == "OK"
    assert calls == ["ordinary"]
    assert len(timeouts) == 1 and 179.9 < timeouts[0] <= 180.0


def test_urlopen_stream_transport_sets_socket_read_timeout(monkeypatch):
    timeouts = []

    class Response:
        status = 200
        headers = {"X-Request-Id": "req-1"}
        fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append)))

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def __iter__(self):
            return iter([event({"choices": [{"delta": {"content": "OK"}}]}), b"data: [DONE]\n"])

    monkeypatch.setattr(parcc_client, "urlopen", lambda _request, timeout: Response())
    client = ParccClient(api_key="secret", stream=True, max_attempts=1, timeout_seconds=5)
    assert chat(client).request_id == "req-1"
    assert timeouts and all(0 < value <= 5 for value in timeouts)


def test_urlopen_partial_line_timeout_is_not_retried(monkeypatch):
    calls = []
    class Response:
        status = 200
        headers = {}
        def __enter__(self):return self
        def __exit__(self, *_args):return False
        def read(self, _size):return b'd'
        def readline(self):raise TimeoutError('partial line')
    def open_response(*_args, **_kwargs):
        calls.append(1)
        return Response()
    monkeypatch.setattr(parcc_client, 'urlopen', open_response)
    monkeypatch.setattr(parcc_client.time, 'sleep', lambda *_: pytest.fail('unexpected retry'))
    with pytest.raises(ParccError, match='after HTTP 200 body started'):
        chat(ParccClient(api_key='secret', stream=True, max_attempts=5))
    assert len(calls) == 1


def test_urlopen_http_error_uses_safe_message(monkeypatch):
    timeouts = []

    class ErrorBody:
        raw = SimpleNamespace(_sock=SimpleNamespace(settimeout=timeouts.append))
        closed = False

        def __iter__(self):
            return iter([b'{"error":{"message":"secret request body"}}'])

        def close(self):
            self.closed = True

    def urlopen_error(_request, timeout):
        raise HTTPError("https://example.invalid", 400, "bad request", {}, ErrorBody())

    monkeypatch.setattr(parcc_client, "urlopen", urlopen_error)
    client = ParccClient(api_key="secret", stream=True, max_attempts=1)
    with pytest.raises(ParccError, match="PARCC returned HTTP 400") as exc:
        chat(client)
    assert "secret" not in str(exc.value)
    assert timeouts


def test_worker_enables_stream(monkeypatch, tmp_path):
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"model": "parcc/GLM", "connectivity_retry_max_attempts": 5, "max_tokens": 8, "messages": [],
                                   "temperature": 0, "reasoning_effort": "low"}))
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def chat(self, **kwargs):
            return ParccResponse(kwargs["model"], "OK", None, 0.1, 1,
                                 {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})

    monkeypatch.setenv("PARCC_API_KEY", "fake")
    monkeypatch.setattr(dev_provider_worker, "ParccClient", FakeClient)
    monkeypatch.setattr(sys, "argv", ["worker", "--request", str(request), "--directory", str(tmp_path),
                                      "--kind", "model", "--deadline", str(time.time() + 10)])
    dev_provider_worker.main()
    assert captured["stream"] is True
    assert captured["max_attempts"] == 5
    assert (tmp_path / "model-output.json").is_file()


@pytest.mark.parametrize('model,expected', [('parcc/Qwen', 5), ('paid/model', 1)])
def test_worker_retries_only_free_parcc_models(monkeypatch, tmp_path, model, expected):
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'model': model, 'connectivity_retry_max_attempts': 5,
                                   'max_tokens': 1, 'messages': [], 'temperature': 0,
                                   'reasoning_effort': 'low'}))
    seen = []
    class FakeClient:
        def __init__(self, **kwargs):seen.append(kwargs['max_attempts'])
        def chat(self, **kwargs):return ParccResponse(kwargs['model'], 'OK', None, 0, 1, {})
    monkeypatch.setenv('PARCC_API_KEY', 'fake')
    monkeypatch.setattr(dev_provider_worker, 'ParccClient', FakeClient)
    monkeypatch.setattr(sys, 'argv', ['worker', '--request', str(request), '--directory', str(tmp_path),
                                     '--kind', 'model', '--deadline', str(time.time() + 10)])
    dev_provider_worker.main()
    assert seen == [expected]


def test_worker_codex_source_uses_no_parcc_retry(monkeypatch, tmp_path):
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'model': 'gpt-6-astra', 'model_source': 'codex',
                                   'connectivity_retry_max_attempts': 5, 'max_tokens': 1,
                                   'messages': [], 'temperature': 0, 'reasoning_effort': 'high'}))
    seen = []
    class FakeCodex:
        quota_before = quota_after = None
        def __init__(self, **kwargs):seen.append(kwargs)
        def chat(self, **kwargs):return ParccResponse(kwargs['model'], 'OK', None, 0, 1, {})
    monkeypatch.setattr(dev_provider_worker, 'CodexClient', FakeCodex)
    monkeypatch.setattr(dev_provider_worker, 'ParccClient', lambda **_: pytest.fail('PARCC client used'))
    monkeypatch.setattr(sys, 'argv', ['worker', '--request', str(request), '--directory', str(tmp_path),
                                     '--kind', 'model', '--deadline', str(time.time() + 10)])
    dev_provider_worker.main()
    assert len(seen) == 1 and 'max_attempts' not in seen[0]


def test_worker_failed_retries_write_immutable_attempts_record(monkeypatch, tmp_path):
    from urllib.error import URLError
    request = tmp_path / 'request.json'
    request.write_text(json.dumps({'model': 'parcc/GLM', 'connectivity_retry_max_attempts': 5,
                                   'max_tokens': 1, 'messages': [], 'temperature': 0,
                                   'reasoning_effort': 'low'}))
    calls = []
    def transport(*_args):
        calls.append(1)
        raise URLError('secret endpoint')
    def factory(**kwargs):
        kwargs['stream'] = False
        return ParccClient(**kwargs, transport=transport, retry_delay_seconds=0)
    monkeypatch.setenv('PARCC_API_KEY', 'fixture')
    monkeypatch.setattr(dev_provider_worker, 'ParccClient', factory)
    monkeypatch.setattr(sys, 'argv', ['worker', '--request', str(request), '--directory', str(tmp_path),
                                     '--kind', 'model', '--deadline', str(time.time() + 10)])
    with pytest.raises(ParccError, match='after 5 attempts'):
        dev_provider_worker.main()
    assert len(calls) == 5
    rows = json.loads((tmp_path / 'model-attempts.json').read_text())
    assert [row['attempt'] for row in rows] == [1, 2, 3, 4, 5]
    assert all(row['reason'] == 'connect_error' for row in rows)
    assert sorted(path.name for path in tmp_path.glob('model-retry-*.json')) == [f'model-retry-{i}.json' for i in range(2, 6)]
