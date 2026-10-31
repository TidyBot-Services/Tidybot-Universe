"""Published main UI catalog contract; no real robot runs are started here."""
from __future__ import annotations
from benchmarks.attention_harness.workspace_paths import lazy

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.task_dag import load_manifest
from benchmarks.attention_harness.ui_launch import load_catalog, launch_formal_run, public_options

ROOT = Path(__file__).resolve().parents[3]
MAIN = ROOT / "benchmarks/attention_harness/protocol/v2/skill_graphs/main_ui_with_robocasa365_v1/catalog.json"
ORIGINAL = ROOT / "openarm_bimanual/launch_configs/stage-one-final-1/catalog.json"
DELIVERY = lazy('sim.delivery_root')
TASKS = ("pick_place_counter_to_sink", "open_cabinet")
GRAPH_KEYS = {"skill_graph_file", "skill_graph_sha256", "skill_root"}


def test_main_launcher_selects_merged_catalog_and_preserves_explicit_override(tmp_path):
    launcher = tmp_path / "benchmarks/attention_harness/run_ui.sh"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes((ROOT / "benchmarks/attention_harness/run_ui.sh").read_bytes())
    delegate = tmp_path / "openarm_robocasa/run_ui.sh"
    delegate.parent.mkdir()
    delegate.write_text('#!/usr/bin/env bash\nprintf "%s" "${ATTENTION_UI_CATALOG:-unselected}"\n')
    delegate.chmod(0o755)
    import os
    environment = {key: value for key, value in os.environ.items() if key != "ATTENTION_UI_CATALOG"}
    assert subprocess.check_output(["bash", str(launcher)], env=environment, text=True) == "unselected"
    from openarm_bimanual.entry import resolve_daily_catalog
    active = resolve_daily_catalog(root=ROOT)
    assert {p["task"] for p in json.loads(active.read_bytes())["profiles"] if p["suite"] == "robocasa365"} == set(TASKS)
    environment["ATTENTION_UI_CATALOG"] = "/explicit/catalog.json"
    assert subprocess.check_output(["bash", str(launcher)], env=environment, text=True) == "/explicit/catalog.json"


def test_main_catalog_preserves_all_current_entries_and_appends_two_365_tasks():
    assert MAIN.is_file(), "Primary UI must publish its merged catalog"
    merged = json.loads(MAIN.read_bytes())
    original = json.loads(ORIGINAL.read_bytes())
    assert merged["require_skill_graph"] is True
    assert merged["profiles"][:len(original["profiles"])] == original["profiles"]
    added = merged["profiles"][len(original["profiles"]):]
    assert [profile["task"] for profile in added] == list(TASKS)
    assert len({profile["id"] for profile in merged["profiles"]}) == len(merged["profiles"])
    assert all(profile["suite"] == "robocasa365" and profile["execution_target"] == "robocasa365_sim" for profile in added)
    if not DELIVERY.is_dir():
        pytest.skip("Reviewed local RoboCasa365 delivery is unavailable")
    approved = json.loads((DELIVERY / "ui_catalog_with_retry_k.json").read_bytes())["profiles"]
    assert [{key: value for key, value in profile.items() if key not in GRAPH_KEYS} for profile in added] == approved


@pytest.mark.parametrize("task", TASKS)
def test_main_365_dispatch_retains_approved_service_budget_and_task_graph(tmp_path, task):
    assert MAIN.is_file(), "Primary UI must publish its merged catalog"
    if not DELIVERY.is_dir():
        pytest.skip("Reviewed local RoboCasa365 delivery is unavailable")
    profiles = load_catalog(MAIN)
    profile = next(item for item in profiles if item["suite"] == "robocasa365" and item["task"] == task)
    assert {item["task"] for item in public_options(profiles)["profiles"] if item["suite"] == "robocasa365"} == set(TASKS)
    before = hashlib.sha256(Path(profile["config"]).read_bytes()).hexdigest()
    config = json.loads(Path(profile["config"]).read_bytes())
    assert config["formal_eligible"] is False and config["robot"] == "PandaOmron"
    selection = dict(profile_id=profile["id"], seed=101, attention_policy="retry_k_then_ask",
                     execution_target="robocasa365_sim", assistance_mode="benchmark_proxy",
                     max_attempts=2, assistance_credits=1, token_limit=8000,
                     human_deadline_seconds=30, overall_deadline_seconds=300)
    calls = []
    def fake_popen(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(pid=99999999)
    store = AttentionStore(tmp_path / "test.sqlite3")
    try:
        receipt = launch_formal_run(selection, profiles=profiles, store=store,
                                  artifact_root=tmp_path / "artifacts", popen=fake_popen)
        command = calls[0]
        def arg(flag):
            return command[command.index(flag) + 1]
        for key in ("service_source_root", "sim_source_root", "task_source_root", "sim_python", "asset_manifest"):
            assert arg("--" + key.replace("_", "-")) == profile[key]
        assert arg("--suite") == "robocasa365" and arg("--task") == task
        assert arg("--attempt-deadline-seconds") == "120"
        assert float(arg("--whole-case-wall-seconds")) == 300
        assert receipt["locked"]["formal_eligible"] is False
        dag = load_manifest(Path(arg("--skill-dag")), arg("--skill-dag-sha256"),
                            suite="robocasa365", task_id=task,
                            policy_sha256=profile["approved_policy_sha256"])
        assert len(dag["nodes"]) == 1 and dag["root_skill"] == profile["skill_root"]
        assert not any(event["event_type"] == "run.created" for event in store.events())
        with pytest.raises(ValueError):
            launch_formal_run({**selection, "assistance_credits": 2}, profiles=profiles,
                              store=store, artifact_root=tmp_path / "artifacts", popen=fake_popen)
        with pytest.raises(ValueError):
            launch_formal_run({**selection, "execution_target": "robocasa_sim"}, profiles=profiles,
                              store=store, artifact_root=tmp_path / "artifacts", popen=fake_popen)
        with pytest.raises(ValueError):
            launch_formal_run({**selection, "robot_model": "OpenArm", "execution_arm": "left"},
                              profiles=profiles, store=store,
                              artifact_root=tmp_path / "artifacts", popen=fake_popen)
        assert len(calls) == 1
        assert hashlib.sha256(Path(profile["config"]).read_bytes()).hexdigest() == before
    finally:
        store.close()
