"""The Attention entrypoint gives Dev agents verified service inventory only."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
import websockets  # keep the legacy test helper from installing a global stub


HERE = Path(__file__).resolve().parents[1]
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))


def test_service_prompt_reports_unverified_endpoint_without_guessing(tmp_path, monkeypatch):
    from test_orchestrator_pipeline import import_orchestrator, make_graph, make_entry

    entry = make_entry("lift")
    entry["service_dependencies"] = ["yolo"]
    graph = tmp_path / "graph.json"
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"services": [
        {"name": "yolo", "client": "yolo.client:Client"},
    ]}))
    graph.write_text(json.dumps({"entries": [entry], "service_catalog": "catalog.json"}))
    orch = import_orchestrator(str(graph))
    orch._load_entries()
    sys.modules["agent_orchestrator"] = orch
    sys.modules.pop("attention_orchestrator", None)
    sys.modules.pop("attention_app", None)
    app = importlib.import_module("attention_app")
    app.install()
    prompt = orch._get_system_prompt("dev", "lift")
    assert "yolo: catalog_only" in prompt
    assert "endpoint=unverified" in prompt
    assert "Do not guess URLs" not in prompt
    assert "deployment is a separate operator-approved action" in prompt


def test_service_prompt_fails_closed_without_catalog(tmp_path):
    from test_orchestrator_pipeline import import_orchestrator, make_entry

    entry = make_entry("lift")
    entry["service_dependencies"] = ["grasp"]
    graph = tmp_path / "graph.json"
    graph.write_text(json.dumps({"entries": [entry]}))
    orch = import_orchestrator(str(graph))
    orch._load_entries()
    sys.modules["agent_orchestrator"] = orch
    sys.modules.pop("attention_orchestrator", None)
    sys.modules.pop("attention_app", None)
    app = importlib.import_module("attention_app")
    app.install()
    prompt = orch._get_system_prompt("dev", "lift")
    assert "Do not guess URLs or silently deploy services" in prompt
