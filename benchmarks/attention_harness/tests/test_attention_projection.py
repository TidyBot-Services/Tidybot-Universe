from dataclasses import replace
from pathlib import Path

from benchmarks.attention_harness.attention_modes import AssistanceMode
from benchmarks.attention_harness.core.projection import AttentionProjection
from benchmarks.attention_harness.tests.test_attention_store import populated_store


def test_projection_exposes_six_ui_areas_and_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "attention.sqlite3"
    store, request = populated_store(path)
    snapshot = AttentionProjection(store).snapshot(request.run_id)
    assert set(snapshot) == {
        "schema_version",
        "run_context",
        "resource_budget",
        "autonomous_work",
        "attention_inbox",
        "request_detail",
        "live_station",
    }
    assert snapshot["run_context"]["assistance_mode"] == AssistanceMode.BENCHMARK_PROXY.value
    assert snapshot["attention_inbox"][0]["request_id"] == request.request_id
    restarted = AttentionProjection(type(store)(path)).snapshot(request.run_id)
    assert restarted == snapshot
