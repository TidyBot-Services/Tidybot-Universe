"""Catalog/Wishlist/Deploy evidence is based on their real public schemas."""

import json
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service_evidence import assess_dependency, prepare_deploy_plan


def _sources(tmp_path):
    catalog = tmp_path / "catalog.json"
    wishlist = tmp_path / "wishlist.json"
    catalog.write_text(json.dumps({"updated": "2026-02-18", "capabilities": {
        "yolo-detection": {"type": "model", "client_sdk": "https://example.test/client.py",
                           "host": "http://stale.example.test:8000"},
    }}))
    wishlist.write_text(json.dumps({"updated": "2026-02-16", "items": [
        {"id": "existing-service", "status": "pending"},
    ]}))
    return catalog, wishlist


def test_capability_requires_explicit_runtime_binding_and_snapshot(tmp_path, monkeypatch):
    catalog, wishlist = _sources(tmp_path)
    monkeypatch.setattr("service_evidence._running_services", lambda url: {
        "yolo": {"name": "yolo", "status": "healthy", "host": "http://127.0.0.1:8010"},
    })
    args = dict(capability="yolo-detection", catalog_path=catalog,
                wishlist_path=wishlist, deploy_agent_url="http://127.0.0.1:9000",
                requested_by="graph:lift", reason="Need detection")
    unbound = assess_dependency(runtime_name=None, **args)
    assert unbound["state"] == "catalog_only" and unbound["endpoint"] is None
    assert unbound["catalog_sha256"] and unbound["deploy_inventory_sha256"]
    bound = assess_dependency(runtime_name="yolo", **args)
    assert bound["state"] == "ready"
    assert bound["endpoint"] == "http://127.0.0.1:8010"
    assert "stale.example.test" not in json.dumps(bound)


def test_missing_capability_produces_reviewable_not_written_wishlist_request(tmp_path):
    catalog, wishlist = _sources(tmp_path)
    before = wishlist.read_bytes()
    args = dict(catalog_path=catalog, wishlist_path=wishlist,
                deploy_agent_url=None, requested_by="graph:lift", reason="Need calibration")
    missing = assess_dependency(capability="new-calibration", runtime_name=None, **args)
    assert missing["state"] == "requested"
    assert missing["proposed_wishlist_item"]["id"] == "new-calibration"
    assert missing["deployment_authority"] == "operator_approval_required"
    assert wishlist.read_bytes() == before
    existing = assess_dependency(capability="existing-service", runtime_name=None, **args)
    assert existing["wishlist_status"] == "pending"
    assert existing["proposed_wishlist_item"] is None


def test_invalid_capability_or_wishlist_fails_closed(tmp_path):
    catalog, wishlist = _sources(tmp_path)
    with pytest.raises(ValueError):
        assess_dependency(capability="../oops", runtime_name=None, catalog_path=catalog,
                          wishlist_path=wishlist, deploy_agent_url=None,
                          requested_by="graph:lift", reason="need")
    wishlist.write_text(json.dumps({"items": {}}))
    with pytest.raises(ValueError, match="items array"):
        assess_dependency(capability="missing", runtime_name=None, catalog_path=catalog,
                          wishlist_path=wishlist, deploy_agent_url=None,
                          requested_by="graph:lift", reason="need")



def test_deploy_plan_requires_operator_and_immutable_image(tmp_path, monkeypatch):
    monkeypatch.setattr("service_evidence._running_services", lambda url: {})
    catalog, wishlist = _sources(tmp_path)
    evidence = assess_dependency(
        capability="yolo-detection", runtime_name="yolo", catalog_path=catalog,
        wishlist_path=wishlist, deploy_agent_url="http://127.0.0.1:9000",
        requested_by="graph:lift", reason="Need detection",
    )
    manifest = {"name": "yolo", "image": "tidybot/yolo@sha256:" + "a" * 64,
                "port": 8000, "gpu": True}
    digest = hashlib.sha256(json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    with pytest.raises(PermissionError):
        prepare_deploy_plan(dependency_evidence=evidence, manifest=manifest,
                            approved_by="", approved_manifest_sha256=digest)
    with pytest.raises(ValueError, match="digest"):
        prepare_deploy_plan(dependency_evidence=evidence, manifest=manifest,
                            approved_by="operator", approved_manifest_sha256="0" * 64)
    plan = prepare_deploy_plan(dependency_evidence=evidence, manifest=manifest,
                               approved_by="operator", approved_manifest_sha256=digest)
    assert plan["execution_state"] == "not_submitted"
    assert plan["body"] == manifest
