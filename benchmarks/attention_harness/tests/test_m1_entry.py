"""Frozen M1 entry contract for both formal simulator suites."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from benchmarks.attention_harness.core.policies import POLICY_IDS
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.tests.test_formal_attention_chain import FakeFormalRunner
from benchmarks.attention_harness.ui_launch import launch_formal_run, load_catalog


ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    "robosuite": ("cube_lift", "formal_robosuite_cube_lift_seed101.json"),
    "robocasa": ("counter_to_sink", "formal_robocasa_counter_to_sink_seed101.json"),
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry(tmp_path: Path, suite: str, policy: str = "autonomous") -> dict:
    task, name = CONFIGS[suite]
    code = tmp_path / "policy.py"
    code.write_text("pass\n")
    config = tmp_path / "config.json"
    config.write_bytes((ROOT / "protocol/v2" / name).read_bytes())
    kwargs = dict(suite=suite, task_id=task, seed=101, policy_id=policy,
                  code=code, approved_policy_sha256=digest(code),
                  config=config, approved_config_sha256=digest(config),
                  max_attempts=2, assistance_credits=1, token_limit=100,
                  assistance_mode="benchmark_proxy", human_deadline_seconds=30,
                  overall_deadline_seconds=90)
    if policy == "demo_first":
        actions = tmp_path / "actions.json"
        actions.write_text(json.dumps({"source": "public_sdk", "steps": []}))
        demo = tmp_path / "demo.json"
        demo.write_text(json.dumps({"schema_version": "attentionbench.public-demo.v1",
            "suite": suite, "task_id": task,
            "approval": {"id": "m1-fixture", "approved_by": "test"},
            "assets": [{"kind": "action_trajectory", "path": str(actions),
                        "sha256": digest(actions), "source": "public_sdk"}]}))
        kwargs.update(demo_prior=demo, approved_demo_sha256=digest(demo))
    if policy == "budget_matched_random_escalation":
        random = tmp_path / "random.json"
        random.write_text(json.dumps({"target_request_count": 1,
                                      "total_failure_slots": 1, "seed": 101}))
        kwargs.update(policy_config=random, approved_policy_config_sha256=digest(random))
    return kwargs


@pytest.mark.parametrize("suite", CONFIGS)
@pytest.mark.parametrize("policy", POLICY_IDS)
def test_all_formal_entry_choices_have_stable_identity(tmp_path, suite, policy):
    kwargs = entry(tmp_path, suite, policy)
    lock, parsed = inspect_formal_entry(**kwargs)
    assert lock["suite"] == suite and lock["task_id"] == kwargs["task_id"]
    assert lock["perception_mode"] == "sim_gt" and lock["seed"] == 101
    assert lock["attention_policy"] == policy and len(lock["sha256"]) == 64
    assert inspect_formal_entry(**kwargs)[0] == lock
    assert (parsed is not None) == (policy == "budget_matched_random_escalation")


@pytest.mark.parametrize("suite,task,config_source", [
    ("robocasa", "counter_to_cab", ROOT / "protocol/v2/formal_robocasa_counter_to_cab_seed101.json"),
    ("robosuite", "cube_stack", ROOT / "protocol/v2/evidence/week2_followup_2026-09-27/continuation/formal_depth_runs/cube_stack-seed101/cube_stack-seed101-formal-1790522565266813489/approved_config.json"),
])
def test_second_supported_task_has_valid_entry_config(tmp_path, suite, task, config_source):
    kwargs = entry(tmp_path, suite)
    kwargs["config"].write_bytes(config_source.read_bytes())
    kwargs.update(task_id=task, approved_config_sha256=digest(kwargs["config"]))
    lock, _ = inspect_formal_entry(**kwargs)
    assert lock["suite"] == suite and lock["task_id"] == task


@pytest.mark.parametrize("suite", CONFIGS)
def test_rejects_invalid_task_seed_config_budget_and_strategy_before_run(tmp_path, suite):
    kwargs = entry(tmp_path, suite)
    cases = [({"task_id": "unknown"}, ValueError),
             ({"task_id": CONFIGS["robocasa" if suite == "robosuite" else "robosuite"][0]}, ValueError),
             ({"seed": 1001}, PermissionError), ({"seed": 9001}, ValueError),
             ({"seed": True}, TypeError), ({"max_attempts": 0}, ValueError),
             ({"max_attempts": 11}, ValueError), ({"assistance_credits": 11}, ValueError),
             ({"token_limit": 100001}, ValueError),
             ({"human_deadline_seconds": float("nan")}, ValueError),
             ({"overall_deadline_seconds": 601}, ValueError),
             ({"policy_id": "made_up"}, ValueError)]
    for change, kind in cases:
        with pytest.raises(kind):
            inspect_formal_entry(**{**kwargs, **change})
    config = json.loads(kwargs["config"].read_text())
    for change in ({"seed": 102}, {"perception_mode": "rgb"},
                   {"suite": "robocasa" if suite == "robosuite" else "robosuite"}):
        changed = tmp_path / "changed.json"
        changed.write_text(json.dumps({**config, **change}))
        with pytest.raises(ValueError):
            inspect_formal_entry(**{**kwargs, "config": changed,
                                    "approved_config_sha256": digest(changed)})


@pytest.mark.parametrize("suite", CONFIGS)
def test_demo_and_random_files_fail_closed_before_run(tmp_path, suite):
    demo = entry(tmp_path, suite, "demo_first")
    for change in ({"demo_prior": None, "approved_demo_sha256": None},
                   {"approved_demo_sha256": "0" * 64}):
        with pytest.raises(ValueError):
            inspect_formal_entry(**{**demo, **change})
    manifest = json.loads(demo["demo_prior"].read_text())
    Path(manifest["assets"][0]["path"]).write_text("tampered")
    with pytest.raises(ValueError, match="asset differs"):
        inspect_formal_entry(**demo)
    random = entry(tmp_path, suite, "budget_matched_random_escalation")
    with pytest.raises(ValueError):
        inspect_formal_entry(**{**random, "policy_config": None,
                                "approved_policy_config_sha256": None})
    changed = json.loads(random["policy_config"].read_text())
    random["policy_config"].write_text(json.dumps({**changed, "seed": 102}))
    with pytest.raises(ValueError, match="SHA-256"):
        inspect_formal_entry(**random)
    with pytest.raises(ValueError, match="seed or budget"):
        inspect_formal_entry(**{**random,
            "approved_policy_config_sha256": digest(random["policy_config"])})


@pytest.mark.parametrize("suite", CONFIGS)
def test_ui_lock_matches_cli_preflight_and_rechecks_displayed_files(tmp_path, suite):
    kwargs = entry(tmp_path, suite)
    task = kwargs["task_id"]
    roots = {"service_source_root": str(tmp_path)} if suite == "robosuite" else {
        "sim_source_root": str(tmp_path), "agent_source_root": str(tmp_path),
        "task_source_root": str(tmp_path), "sim_python": sys.executable,
        "agent_python": sys.executable}
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"profiles": [{"id": "selected", "suite": suite,
        "task": task, "execution_target": f"{suite}_sim",
        "code": str(kwargs["code"]), "approved_policy_sha256": kwargs["approved_policy_sha256"],
        "config": str(kwargs["config"]),
        "approved_config_sha256": kwargs["approved_config_sha256"], **roots}]}))
    profiles = load_catalog(catalog)
    selection = dict(profile_id="selected", seed=101, attention_policy="autonomous",
                     execution_target=f"{suite}_sim", assistance_mode="benchmark_proxy",
                     max_attempts=2, assistance_credits=1, token_limit=100,
                     human_deadline_seconds=30, overall_deadline_seconds=90)
    store = AttentionStore(tmp_path / "store.sqlite3")
    commands = []
    class Process:
        pid = 12345
    launched = launch_formal_run(selection, profiles=profiles, store=store,
        artifact_root=tmp_path / "artifacts",
        popen=lambda command, **_: commands.append(command) or Process())
    lock = inspect_formal_entry(**kwargs)[0]
    assert launched["locked"]["entry_lock"] == lock
    assert lock["sha256"] in commands[0]
    assert store.list_launches()[0]["locked"]["entry_lock"] == lock
    kwargs["config"].write_text("{}")
    with pytest.raises(ValueError, match="changed"):
        launch_formal_run(selection, profiles=profiles, store=store,
                          artifact_root=tmp_path / "artifacts")
    assert len(store.list_launches()) == 1


@pytest.mark.parametrize("suite", CONFIGS)
def test_formal_cli_rejection_leaves_no_run_or_lock(tmp_path, suite):
    kwargs = entry(tmp_path, suite)
    root = tmp_path / "runs"
    command = [sys.executable, "-m", "benchmarks.attention_harness.formal_attention_cli",
        "--suite", suite, "--task", kwargs["task_id"], "--seed", "1001",
        "--attention-policy", "autonomous", "--code", str(kwargs["code"]),
        "--approved-policy-sha256", kwargs["approved_policy_sha256"],
        "--config", str(kwargs["config"]), "--approved-config-sha256",
        kwargs["approved_config_sha256"], "--artifact-root", str(root)]
    if suite == "robosuite":
        command += ["--service-source-root", str(tmp_path)]
    else:
        for flag in ("sim-source-root", "agent-source-root", "task-source-root"):
            command += [f"--{flag}", str(tmp_path)]
        for flag in ("sim-python", "agent-python"):
            command += [f"--{flag}", sys.executable]
    outcome = subprocess.run(command, capture_output=True, text=True)
    assert outcome.returncode == 2 and "held-out" in outcome.stderr
    assert not root.exists()


@pytest.mark.parametrize("suite", CONFIGS)
@pytest.mark.parametrize("policy", ("demo_first", "budget_matched_random_escalation"))
def test_ui_rejects_approval_file_changed_after_catalog_display(tmp_path, suite, policy):
    kwargs = entry(tmp_path, suite, policy)
    roots = {"service_source_root": str(tmp_path)} if suite == "robosuite" else {
        "sim_source_root": str(tmp_path), "agent_source_root": str(tmp_path),
        "task_source_root": str(tmp_path), "sim_python": sys.executable,
        "agent_python": sys.executable}
    extra = ({"demo_prior": str(kwargs["demo_prior"]),
              "approved_demo_sha256": kwargs["approved_demo_sha256"]}
             if policy == "demo_first" else
             {"random_policy_config": str(kwargs["policy_config"]),
              "approved_random_policy_config_sha256": kwargs["approved_policy_config_sha256"]})
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"profiles": [{"id": "selected", "suite": suite,
        "task": kwargs["task_id"], "execution_target": f"{suite}_sim",
        "code": str(kwargs["code"]), "approved_policy_sha256": kwargs["approved_policy_sha256"],
        "config": str(kwargs["config"]),
        "approved_config_sha256": kwargs["approved_config_sha256"], **roots, **extra}]}))
    profiles = load_catalog(catalog)
    approved = kwargs["demo_prior"] if policy == "demo_first" else kwargs["policy_config"]
    approved.write_text("tampered")
    store = AttentionStore(tmp_path / "store.sqlite3")
    selection = dict(profile_id="selected", seed=101, attention_policy=policy,
                     execution_target=f"{suite}_sim", assistance_mode="benchmark_proxy",
                     max_attempts=2, assistance_credits=1, token_limit=100,
                     human_deadline_seconds=30, overall_deadline_seconds=90)
    with pytest.raises(ValueError, match="changed"):
        launch_formal_run(selection, profiles=profiles, store=store,
                          artifact_root=tmp_path / "runs")
    assert not store.list_launches()
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("suite", CONFIGS)
def test_cli_rejects_missing_demo_and_random_before_run(tmp_path, suite):
    kwargs = entry(tmp_path, suite)
    base = [sys.executable, "-m", "benchmarks.attention_harness.formal_attention_cli",
        "--suite", suite, "--task", kwargs["task_id"], "--seed", "101",
        "--code", str(kwargs["code"]),
        "--approved-policy-sha256", kwargs["approved_policy_sha256"],
        "--config", str(kwargs["config"]),
        "--approved-config-sha256", kwargs["approved_config_sha256"],
        "--artifact-root", str(tmp_path / "runs"), "--max-attempts", "2",
        "--assistance-credits", "1"]
    if suite == "robosuite":
        base += ["--service-source-root", str(tmp_path)]
    else:
        for flag in ("sim-source-root", "agent-source-root", "task-source-root"):
            base += [f"--{flag}", str(tmp_path)]
        for flag in ("sim-python", "agent-python"):
            base += [f"--{flag}", sys.executable]
    for policy in ("demo_first", "budget_matched_random_escalation"):
        result = subprocess.run(base + ["--attention-policy", policy],
                                capture_output=True, text=True)
        assert result.returncode == 2
        assert not (tmp_path / "runs").exists()
    changed_after_ui = subprocess.run(base + ["--attention-policy", "autonomous",
        "--expected-entry-sha256", "0" * 64], capture_output=True, text=True)
    assert changed_after_ui.returncode == 2
    assert "changed since UI lock" in changed_after_ui.stderr
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("suite", CONFIGS)
def test_downstream_rejects_changed_budget_against_entry_lock_before_run(tmp_path, suite):
    kwargs = entry(tmp_path, suite)
    lock = inspect_formal_entry(**kwargs)[0]
    root = tmp_path / "runs"
    with pytest.raises(ValueError, match="entry lock identity mismatch"):
        run_formal_attention(
            suite=suite, task_id=kwargs["task_id"], seed=101,
            artifact_root=root, policy_id="autonomous",
            policy_code_path=kwargs["code"],
            approved_policy_sha256=kwargs["approved_policy_sha256"],
            config_path=kwargs["config"],
            approved_config_sha256=kwargs["approved_config_sha256"],
            runner=FakeFormalRunner(suite), entry_lock=lock,
            max_attempts=3, assistance_credits=1, token_limit=100,
            assistance_mode="benchmark_proxy", human_deadline_seconds=30,
            overall_deadline_seconds=90)
    assert not root.exists()
