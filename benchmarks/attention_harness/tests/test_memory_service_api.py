from __future__ import annotations

import asyncio
import io
import json
from urllib.error import HTTPError

import pytest

from benchmarks.attention_harness.core.store import StateConflictError
from benchmarks.attention_harness.memory_service_api import create_app
from benchmarks.attention_harness.memory_service_client import MemoryServiceClient
from benchmarks.attention_harness.tests.test_memory_v2 import _candidate, _episode, _safety


KEY = "memory-service-test-key"


def _request(app, method, path, *, payload=None, key=None):
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = [(b"content-type", b"application/json")]
    if key:
        headers.append((b"x-memory-service-key", key.encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "headers": headers,
        "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 8768),
    }
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    content = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
    return status, json.loads(content)


def test_memory_service_requires_auth_and_keeps_promotion_gate(tmp_path):
    shared, manager, memory_id, _ = _candidate(tmp_path)
    app = create_app(shared, api_key=KEY)
    status, health = _request(app, "GET", "/health")
    assert status == 200 and health["status"] == "ok"
    request_id = manager.provenance(memory_id)["request_id"]
    status, _ = _request(app, "GET", f"/sources/{request_id}")
    assert status == 401
    status, source = _request(app, "GET", f"/sources/{request_id}", key=KEY)
    assert status == 200 and source["perception_mode"] == "sim_gt"
    status, _ = _request(app, "POST", f"/memories/{memory_id}/promote", key=KEY)
    assert status == 409
    status, memories = _request(
        app, "POST", "/retrieve", key=KEY,
        payload={"context": {"perception_mode": "sim_gt", "suite": "robocasa", "task_id": "counter_to_sink"}, "now": 1.0},
    )
    assert status == 200 and memories == []


def test_remote_agent_client_uses_auth_and_preserves_service_conflicts(monkeypatch):
    calls = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({"source_kind": "advisor_proxy"}).encode()

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.get_header("X-memory-service-key"), timeout))
        if request.full_url.endswith("/impact"):
            raise HTTPError(
                request.full_url, 409, "Conflict", {},
                io.BytesIO(json.dumps({"detail": "five pairs required"}).encode()),
            )
        return Response()

    monkeypatch.setattr(
        "benchmarks.attention_harness.memory_service_client.urlopen", fake_urlopen,
    )
    client = MemoryServiceClient("http://127.0.0.1:8768", api_key=KEY)
    assert client.provenance("candidate:run:1")["source_kind"] == "advisor_proxy"
    assert calls[0][0].endswith("/memories/candidate%3Arun%3A1/provenance")
    assert calls[0][1] == KEY
    with pytest.raises(StateConflictError, match="five pairs"):
        client.impact_report("candidate:run:1")
    with pytest.raises(ValueError, match="HTTPS"):
        MemoryServiceClient("http://remote-host:8768", api_key=KEY)


def test_http_pair_uploads_safety_artifacts_into_service_store(tmp_path):
    shared, _, memory_id, _ = _candidate(tmp_path)
    control = _episode(tmp_path, shared, 102)
    treatment = _episode(tmp_path, shared, 102, candidate=memory_id)
    control_id = control["attention_trace"]["attempt_id"]
    treatment_id = treatment["attention_trace"]["attempt_id"]
    cpath = _safety(tmp_path / "control-monitor.json", control_id)
    tpath = _safety(tmp_path / "treatment-monitor.json", treatment_id)
    app = create_app(shared, api_key=KEY)
    status, pair = _request(app, "POST", "/pairs", key=KEY, payload={
        "memory_id": memory_id,
        "control_attempt_id": control_id,
        "treatment_attempt_id": treatment_id,
        "control_safety": json.loads(cpath.read_text()),
        "treatment_safety": json.loads(tpath.read_text()),
    })
    assert status == 200
    assert pair["control_safety"]["attempt_id"] == control_id
    assert "memory-safety-evidence" in pair["control_safety"]["uri"]
    assert pair["treatment_safety"]["attempt_id"] == treatment_id


def test_http_memory_plan_and_package_are_authenticated(tmp_path):
    shared, _, memory_id, _ = _candidate(tmp_path)
    app = create_app(shared, api_key=KEY)
    status, _ = _request(app, "GET", f"/memories/{memory_id}/artifact")
    assert status == 401
    status, manifest = _request(app, "GET", f"/memories/{memory_id}/artifact", key=KEY)
    assert status == 200 and manifest["memory_id"] == memory_id
    status, provenance = _request(app, "GET", f"/memories/{memory_id}/provenance", key=KEY)
    assert status == 200
    context = provenance["artifact"]["applicability"]
    payload = {
        "schema_version": "attentionbench.memory-validation-plan.v2",
        "memory_id": memory_id,
        **context,
        "policy_id": provenance["source_policy_id"],
        "assistance_credits": 0,
        "seeds": [102, 103, 104, 105, 106],
    }
    status, plan = _request(app, "PUT", f"/memories/{memory_id}/plan", key=KEY, payload=payload)
    assert status == 200 and plan["seeds"] == payload["seeds"]
    status, manifest = _request(app, "GET", f"/memories/{memory_id}/artifact", key=KEY)
    assert status == 200 and manifest["files"]["validation/plan.json"]
    status, exported = _request(app, "POST", f"/memories/{memory_id}/export", key=KEY)
    assert status == 200 and exported["directory"]
