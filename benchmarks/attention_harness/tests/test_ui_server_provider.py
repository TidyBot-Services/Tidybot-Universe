"""Provider key routes on the main UI server: same contract as dev_ui_server, key never echoed."""
from __future__ import annotations

import json
import re
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from benchmarks.attention_harness.tests.fakes.fake_provider import FakeProvider
from benchmarks.attention_harness.tests.test_attention_store import populated_store
from benchmarks.attention_harness.ui_server import create_server

KEY = 'canary-key-0123456789abcdef'


@pytest.fixture
def served(tmp_path):
    store, _ = populated_store(tmp_path / 'attention.sqlite3')
    with FakeProvider(KEY) as fake:
        server = create_server(store=store, port=0, allow_custom=True, allow_private_hosts=('127.0.0.1',))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        with urlopen(base + '/ui/') as response:
            token = re.search(r'data-control-token="([^"]+)"', response.read().decode())[1]
        try:
            yield base, token, fake
        finally:
            server.shutdown()
            server.server_close()


def call(base, token, method, path, body=None, *, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    head = {'X-Attention-Token': token, **({'Content-Type': 'application/json'} if body is not None else {}), **(headers or {})}
    try:
        with urlopen(Request(base + path, method=method, data=data, headers=head)) as response:
            return response.status, response.read().decode(), dict(response.headers)
    except HTTPError as error:
        return error.code, error.read().decode(), dict(error.headers)


def test_credentials_round_trip_without_echoing_the_key(served):
    base, token, fake = served
    status, raw, headers = call(base, token, 'GET', '/api/provider/presets')
    assert status == 200 and {p['id'] for p in json.loads(raw)} >= {'parcc', 'openai', 'deepseek', 'openai-compatible'}
    assert headers['Cache-Control'] == 'no-store'
    status, raw, _ = call(base, token, 'PUT', '/api/provider/credentials/mine', {'provider': 'openai-compatible', 'key': KEY, 'base_url': fake.base_url})
    assert status == 200 and json.loads(raw)['key_set'] is True and KEY not in raw
    status, raw, _ = call(base, token, 'GET', '/api/provider/credentials')
    rows = json.loads(raw)
    assert [r['credential_id'] for r in rows] == ['mine'] and KEY not in raw and 'secret' not in raw
    status, raw, _ = call(base, token, 'POST', '/api/provider/credentials/mine/test', {'model': 'fake-model', 'role': 'dev'})
    result = json.loads(raw)
    assert status == 200 and result['ok'] is True and KEY not in raw
    assert fake.authorizations and set(fake.authorizations) == {'Bearer ' + KEY}
    status, raw, _ = call(base, token, 'DELETE', '/api/provider/credentials/mine')
    assert json.loads(raw) == {'ok': True, 'cleared': 1}
    assert json.loads(call(base, token, 'GET', '/api/provider/credentials')[1]) == []


def test_requests_without_the_control_token_are_refused(served):
    base, token, _ = served
    for method, body in (('GET', None), ('PUT', {'provider': 'deepseek', 'key': KEY}), ('DELETE', None)):
        status, raw, _ = call(base, 'wrong', method, '/api/provider/credentials/x', body)
        assert status == 403 and json.loads(raw)['error']['code'] == 'control_token_required' and KEY not in raw


def test_bad_requests_keep_the_error_contract(served):
    base, token, fake = served
    status, raw, _ = call(base, token, 'PUT', '/api/provider/credentials/mine', {'provider': 'openai-compatible', 'key': KEY, 'extra': 1})
    assert status == 400 and json.loads(raw)['error']['code'] == 'provider_body_invalid' and KEY not in raw
    status, raw, _ = call(base, token, 'GET', '/api/provider/credentials?x=1')
    assert status == 400 and json.loads(raw)['error']['code'] == 'provider_request_invalid'
    status, raw, _ = call(base, token, 'GET', '/api/provider/nope')
    assert status == 400 and json.loads(raw)['error']['code'] == 'provider_route_unknown'
    status, raw, _ = call(base, token, 'POST', '/api/provider/credentials/missing/test', {'model': 'm', 'role': 'dev'})
    assert status == 400 and json.loads(raw)['error']['code'] == 'provider_key_missing'
    status, raw, _ = call(base, token, 'PUT', '/api/provider/credentials/mine', {'provider': 'openai', 'key': KEY + 'x', 'base_url': 'https://example.com'})
    assert status == 400 and KEY not in raw


def test_a_rejecting_provider_cannot_echo_the_key_back(tmp_path):
    store, _ = populated_store(tmp_path / 'attention.sqlite3')
    with FakeProvider(KEY, mode='echo_key_401') as fake:
        server = create_server(store=store, port=0, allow_custom=True, allow_private_hosts=('127.0.0.1',))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'
        try:
            with urlopen(base + '/ui/') as response:
                token = re.search(r'data-control-token="([^"]+)"', response.read().decode())[1]
            call(base, token, 'PUT', '/api/provider/credentials/mine', {'provider': 'openai-compatible', 'key': KEY, 'base_url': fake.base_url})
            status, raw, _ = call(base, token, 'POST', '/api/provider/credentials/mine/test', {'model': 'fake-model', 'role': 'dev'})
            assert KEY not in raw and '[REDACTED' in raw
        finally:
            server.shutdown()
            server.server_close()
