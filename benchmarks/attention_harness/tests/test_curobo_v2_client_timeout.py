"""Request timeout caps for the V2 planning client."""

from io import BytesIO
from types import SimpleNamespace

import pytest

from curobo_v2 import client as client_module


@pytest.mark.parametrize("path,left,expected", [
    ("/plan", 300.0, 180.0),
    ("/plan", 20.0, 20.0),
    ("/warmup", 300.0, 60.0),
    ("/warmup", 20.0, 20.0),
])
def test_request_timeout_is_capped_by_endpoint_and_deadline(monkeypatch, path, left, expected):
    client = object.__new__(client_module.V2Client)
    client.failed = False
    client.cancel_check = lambda: False
    client.deadline = 100.0 + left
    client.url = "http://127.0.0.1:8080"
    client.token = "secret"
    client.nonce = "nonce"
    client.lock_sha = "sha"
    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=lambda: 100.0))
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        response = BytesIO(b"{}")
        response.headers = {"x-curobo-instance": "nonce", "x-curobo-lock": "sha"}
        return response

    monkeypatch.setattr(client_module, "urlopen", fake_urlopen)

    assert client._request(path, {}) == {}
    assert calls == [(client.url + path, expected)]
