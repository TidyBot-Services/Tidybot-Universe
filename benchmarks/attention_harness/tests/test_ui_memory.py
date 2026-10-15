"""The UI shows and controls the same Memory records the agent uses."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from attention_memory_service.core.memory import MemoryManager as ServiceMemoryManager
from attention_memory_service.core.store import AttentionStore as ServiceStore

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_view import MemoryView
from benchmarks.attention_harness.ui_server import create_server, dashboard_snapshot

from test_async_requests import guided_policy, run


@pytest.fixture
def scenario(tmp_path):
    """A finished run whose hint produced a candidate, promoted so it can be operated on."""
    result = run(tmp_path, guided_policy([]))
    store_path = Path(result["store"])
    memory_id = result["requests"][0]["candidate_memory_id"]
    manager = ServiceMemoryManager(ServiceStore(store_path))
    manager.record_validation(memory_id, successes=5, failures=0)
    manager.promote(memory_id)
    run_id = result["attempts"][0]["attention_trace"]["run_id"]
    return result, AttentionStore(store_path), memory_id, run_id


def serve(store, view):
    server = create_server(store=store, port=0, memory_view=view)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    with urlopen(base + "/ui/") as response:
        token = re.search(r'data-control-token="([^"]+)"', response.read().decode()).group(1)

    def get(path):
        with urlopen(base + path) as response:
            return json.load(response)

    def post(path, body, *, authorized=True):
        headers = {"Content-Type": "application/json"}
        if authorized:
            headers["X-Attention-Token"] = token
        return urlopen(Request(base + path, data=json.dumps(body).encode(), method="POST", headers=headers))

    def stop():
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    return get, post, stop


def test_snapshot_links_the_run_to_the_service_records(scenario):
    result, store, memory_id, run_id = scenario
    snapshot = dashboard_snapshot(store, run_id, memory_view=MemoryView.local(store.path))
    memory = snapshot["memory"]
    assert memory["control"] == {"source": "local", "can_operate": False}
    assert [(i["decision"], i["memory_id"], i["guided_outcome"]) for i in memory["ingestion"]] == [
        ("ingested", memory_id, "native_success")]
    record = memory["records"][memory_id]
    assert record["record"]["status"] == "trusted" and record["record"]["version"] == 1
    assert record["provenance"]["source_kind"] == "advisor_proxy"
    assert record["provenance"]["request_id"] == result["requests"][0]["request_id"]
    assert record["provenance"]["source_trace_id"]
    evidence = record["provenance"]["raw_evidence"]
    assert evidence and all(set(item) == {"evidence_id", "kind", "sha256"} for item in evidence)
    assert [item["status"] for item in record["history"]] == ["validated", "trusted"]
    assert record["validation"]["planned_seeds"] is None and record["uses"] == []


def test_without_a_memory_connection_the_snapshot_reports_it(scenario):
    _, store, memory_id, run_id = scenario
    memory = dashboard_snapshot(store, run_id)["memory"]
    assert memory["control"] is None and memory["records"] == {}
    assert memory["ingestion"][0]["memory_id"] == memory_id  # the run's own links remain


def test_catalogue_and_detail_endpoints(scenario):
    _, store, memory_id, _ = scenario
    get, _, stop = serve(store, MemoryView.local(store.path))
    try:
        assert get("/api/memories")["control"]["can_operate"] is False
        # The catalogue lists memories with v2 provenance; a candidate made by the run has it.
        rows = get("/api/memories?status=trusted")["memories"]
        assert [row["memory_id"] for row in rows] == [memory_id]
        assert rows[0]["validation"] == {"planned_seeds": None, "completed_pairs": 0,
                                         "decision": None, "failed_gates": []}
        assert get("/api/memories?status=rejected")["memories"] == []
        assert get(f"/api/memories/{quote(memory_id, safe='')}")["record"]["memory_id"] == memory_id
        with pytest.raises(HTTPError) as missing:
            get("/api/memories/does-not-exist")
        assert missing.value.code == 404
    finally:
        stop()


def test_operator_actions_are_authorized_audited_and_final_when_rolled_back(scenario):
    _, store, memory_id, run_id = scenario
    get, post, stop = serve(store, MemoryView.local(store.path, operate=True))
    base = f"/api/memories/{quote(memory_id, safe='')}"
    try:
        with pytest.raises(HTTPError) as no_token:
            post(base + "/disable", {"actor": "alice", "reason": "camera mismatch"}, authorized=False)
        assert no_token.value.code == 403
        for bad in ({"actor": "alice"}, {"actor": "", "reason": "x"}, {"actor": "a", "reason": "x", "x": 1}):
            with pytest.raises(HTTPError) as invalid:
                post(base + "/disable", bad)
            assert invalid.value.code == 400
        with post(base + "/disable", {"actor": "alice", "reason": "camera mismatch"}) as reply:
            assert json.load(reply)["memory"]["status"] == "disabled"
        with post(base + "/enable", {"actor": "bob", "reason": "camera fixed"}) as reply:
            assert json.load(reply)["memory"]["status"] == "trusted"
        expiry = time.time() + 3600
        with post(base + "/expiry", {"actor": "bob", "reason": "review in an hour",
                                     "expires_at": expiry}) as reply:
            assert json.load(reply)["memory"]["expires_at"] == pytest.approx(expiry)
        with pytest.raises(HTTPError) as bad_expiry:
            post(base + "/expiry", {"actor": "bob", "reason": "x", "expires_at": "tomorrow"})
        assert bad_expiry.value.code == 400
        with post(base + "/rollback", {"actor": "carol", "reason": "regressed on seed 107"}) as reply:
            assert json.load(reply)["memory"]["status"] == "rolled_back"
        with pytest.raises(HTTPError) as revoked:
            post(base + "/enable", {"actor": "dave", "reason": "second thoughts"})
        assert revoked.value.code == 409  # a rollback is permanent
        history = get(base)["history"]
        assert [(h["status"], h["actor"]) for h in history] == [
            ("validated", None), ("trusted", None), ("disabled", "alice"), ("trusted", "bob"),
            ("expiry_set", "bob"), ("rolled_back", "carol")]
        assert history[2]["reason"] == "camera mismatch"
        snapshot = get(f"/api/runs/{quote(run_id, safe='')}")
        assert snapshot["memory"]["records"][memory_id]["record"]["status"] == "rolled_back"
    finally:
        stop()


def test_a_read_only_view_cannot_operate(scenario):
    _, store, memory_id, _ = scenario
    get, post, stop = serve(store, MemoryView.local(store.path))
    try:
        with pytest.raises(HTTPError) as refused:
            post(f"/api/memories/{quote(memory_id, safe='')}/disable",
                 {"actor": "alice", "reason": "camera mismatch"})
        assert refused.value.code == 403
        assert get(f"/api/memories/{quote(memory_id, safe='')}")["record"]["status"] == "trusted"
    finally:
        stop()


def test_without_memory_the_endpoints_are_unavailable(scenario):
    _, store, memory_id, _ = scenario
    get, post, stop = serve(store, None)
    try:
        with pytest.raises(HTTPError) as unavailable:
            post(f"/api/memories/{quote(memory_id, safe='')}/disable",
                 {"actor": "alice", "reason": "camera mismatch"})
        assert unavailable.value.code == 503
    finally:
        stop()


def test_a_failed_validation_is_rejected_and_explained_everywhere(tmp_path, monkeypatch):
    from attention_memory_service import MemoryService

    from benchmarks.attention_harness.memory_agent import MemoryAgent

    result = run(tmp_path, guided_policy([]))
    store = AttentionStore(Path(result["store"]))
    memory_id = result["requests"][0]["candidate_memory_id"]
    service = MemoryService(store.path)
    failing = {
        "paired_dev_seeds": 5, "control_successes": 3, "treatment_successes": 2,
        "empirical_confidence": 0.4, "treatment_success_rate_95ci": None, "success_gain": -1,
        "both_success_assistance_credits_saved": 0, "validation_policy_hashes": ["p"],
        "safety_regressions": [], "control_unsafe_attempts": 0, "treatment_unsafe_attempts": 0,
        "validated_scope": [], "supporting_successes": [], "counterexamples": [{"seed": 104}],
    }
    monkeypatch.setattr(service.v2, "verify_source_evidence", lambda memory_id: None)
    monkeypatch.setattr(service.v2, "_plan_complete", lambda memory_id: {"seeds": [1, 2, 3, 4, 5]})
    monkeypatch.setattr(service.v2, "impact_report", lambda memory_id: failing)
    rejected = MemoryAgent(service).finalize(memory_id)
    assert rejected.status.value == "rejected"
    assert rejected.status_reason.startswith("validation_gate_failed: success_regression")
    view = MemoryView(service, source="local", can_operate=False)
    detail = view.detail(memory_id)
    assert detail["validation"]["decision"]["decision"] == "rejected"
    assert "success_regression" in detail["validation"]["decision"]["failed_gates"]
    assert detail["validation"]["counterexamples"] == [{"seed": 104}]
    assert [row["memory_id"] for row in view.catalogue(status="rejected")] == [memory_id]
    assert view.catalogue(status="trusted") == []
    assert view.catalogue()[0]["validation"]["decision"] == "rejected"
