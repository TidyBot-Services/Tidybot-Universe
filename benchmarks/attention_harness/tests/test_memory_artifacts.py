from __future__ import annotations

import json

import pytest

from attention_memory_service.core.store import StateConflictError
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.memory_agent import MemoryAgent
from benchmarks.attention_harness.memory_artifacts import safe_memory_directory
from benchmarks.attention_harness.memory_service import MemoryService
from benchmarks.attention_harness.tests.test_memory_v2 import _candidate, _episode, _safety


def test_memory_package_is_service_materialized_and_rebuildable(tmp_path):
    shared, _, memory_id, origin = _candidate(tmp_path)
    service = MemoryService(shared)
    directory = service.artifacts.directory(memory_id)
    assert directory.parent == tmp_path / "memory"
    assert directory.is_dir()
    assert service.verify_package(memory_id)["status"] == "candidate"
    assert (directory / "knowledge/guidance.md").read_text().strip()
    assert (directory / "knowledge/repair.md").read_text().strip()
    source = json.loads((directory / "source/provenance.json").read_text())
    assert source["episode_result_uri"]
    assert "response_content" not in source
    assert "object_pose" not in json.dumps(source)
    assert json.loads((directory / "validation/plan.json").read_text())["status"] == "unplanned"
    assert (directory / "validation/pairs.jsonl").read_text() == ""
    assert origin["memory_candidate_id"] == memory_id

    MemoryAgent(service).plan_validation(memory_id)
    plan = json.loads((directory / "validation/plan.json").read_text())
    assert plan["seeds"] == [102, 103, 104, 105, 106]
    assert service.verify_package(memory_id)["files"]["validation/plan.json"]

    control = _episode(tmp_path, shared, 102)
    treatment = _episode(tmp_path, shared, 102, candidate=memory_id)
    service.record_pair(
        memory_id=memory_id,
        control_attempt_id=control["attention_trace"]["attempt_id"],
        treatment_attempt_id=treatment["attention_trace"]["attempt_id"],
        control_safety=_safety(tmp_path / "control.json", control["attention_trace"]["attempt_id"]),
        treatment_safety=_safety(tmp_path / "treatment.json", treatment["attention_trace"]["attempt_id"]),
    )
    assert len((directory / "validation/pairs.jsonl").read_text().splitlines()) == 1
    assert json.loads((directory / "validation/impact.json").read_text())["paired_dev_seeds"] == 1
    assert service.verify_package(memory_id)["status"] == "candidate"

    (directory / "knowledge/guidance.md").write_text("tampered\n")
    with pytest.raises(StateConflictError, match="hash mismatch"):
        service.verify_package(memory_id)
    restarted = MemoryService(AttentionStore(shared))
    restarted.export_package(memory_id)
    assert restarted.verify_package(memory_id)["status"] == "candidate"
    manifest_path = directory / "MEMORY.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["status"] = "trusted"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(StateConflictError, match="manifest differs"):
        restarted.verify_package(memory_id)


def test_memory_package_detects_unpublished_db_changes_and_safe_id(tmp_path):
    shared, _, memory_id, _ = _candidate(tmp_path)
    service = MemoryService(shared)
    # A direct DB-side change leaves the generated view stale until explicitly republished.
    source = service.provenance(memory_id)
    context = source["artifact"]["applicability"]
    service.v2.record_plan(memory_id, {
        "schema_version": "attentionbench.memory-validation-plan.v2",
        "memory_id": memory_id,
        **context,
        "policy_id": source["source_policy_id"],
        "assistance_credits": 0,
        "seeds": [102, 103, 104, 105, 106],
    })
    with pytest.raises(StateConflictError, match="stale"):
        service.verify_package(memory_id)
    service.export_package(memory_id)
    directory = service.artifacts.directory(memory_id)
    (directory / "validation/plan.json").unlink()
    with pytest.raises(StateConflictError, match="missing"):
        service.verify_package(memory_id)
    service.export_package(memory_id)
    assert service.verify_package(memory_id)["memory_id"] == memory_id
    assert "/" not in safe_memory_directory("../../outside")
    assert ".." not in safe_memory_directory("../../outside")
