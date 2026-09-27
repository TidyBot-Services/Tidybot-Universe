"""Dev Memory prompt exposure requires opt-in, scope and same-store identity."""

import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

from attention_memory_service.identity import store_id
from dev_memory_bridge import prepare_dev_memory


def _entry(tmp_path):
    policy = tmp_path / "lab_policy.py"
    policy.write_text("def run(sdk, context):\n    pass\n")
    store = tmp_path / "memory.sqlite3"
    sqlite3.connect(store).close()
    return {
        "name": "lift",
        "attentionbench": {"suite": "robosuite", "task": "cube_lift",
                           "attention_policy": "trace_aware_full",
                           "robot_policy": "lab_policy:run", "store_path": str(store)},
        "dev_memory": {"enabled": True, "memory_id": "m1",
                       "development_id": "graph:lift:session-1",
                       "context": {"suite": "robosuite", "task_id": "cube_lift",
                                   "perception_mode": "sim_gt"}},
    }, store


def test_dev_memory_requires_service_grant_and_store_identity(tmp_path, monkeypatch):
    entry, store = _entry(tmp_path)

    class Client:
        def health(self):
            return {"schema_version": "attentionbench.memory-service.v2",
                    "capabilities": ["development_use_evidence"]}

        def store_id(self):
            return store_id(store)

        def authorize_dev_use(self, **kwargs):
            return {"schema_version": "attentionbench.dev-memory-exposure.v1",
                    "grant_id": "devgrant:1", "memory_id": "m1", "memory_version": 2,
                    "development_id": kwargs["development_id"],
                    "source_policy_sha256": kwargs["source_policy_sha256"],
                    "source_trace_id": "trace:1", "evidence_refs": ["raw:1"],
                    "guidance": "Align then grasp."}

    monkeypatch.setattr("dev_memory_bridge._client", lambda url: Client())
    guidance, pointer = prepare_dev_memory(
        entry=entry, graph_id="graph", repo_root=tmp_path,
        memory_service_url="http://127.0.0.1:8768",
    )
    assert "Align then grasp" in guidance
    assert pointer["phase"] == "development_exposure_not_runtime_use"
    entry["attentionbench"]["attention_policy"] = "trace_aware_hint_only"
    with pytest.raises(ValueError, match="trace_aware_full"):
        prepare_dev_memory(entry=entry, graph_id="graph", repo_root=tmp_path,
                           memory_service_url="http://127.0.0.1:8768")


def test_dev_memory_fails_on_wrong_store(tmp_path, monkeypatch):
    entry, _ = _entry(tmp_path)

    class Client:
        def health(self):
            return {"schema_version": "attentionbench.memory-service.v2",
                    "capabilities": ["development_use_evidence"]}

        def store_id(self):
            return "wrong-store"

    monkeypatch.setattr("dev_memory_bridge._client", lambda url: Client())
    with pytest.raises(RuntimeError, match="different Attention store"):
        prepare_dev_memory(entry=entry, graph_id="graph", repo_root=tmp_path,
                           memory_service_url="http://127.0.0.1:8768")
