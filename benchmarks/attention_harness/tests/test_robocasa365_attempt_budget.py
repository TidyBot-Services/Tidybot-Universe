"""Versioned daily attempt budget; simulated runners below are test fixtures only."""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.ui_launch import load_catalog, public_options, launch_formal_run
from test_robocasa365 import FixtureFormalRunner, DELIVERY

ROOT = Path(__file__).resolve().parents[3]
OLD = ROOT / "benchmarks/attention_harness/protocol/v2/skill_graphs/main_ui_with_robocasa365_v1/catalog.json"
NEW = ROOT / "openarm_bimanual/launch_configs/main-ui-365-attempt10-v1/catalog.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def budget(tmp_path):
    configs = [DELIVERY / f"configs/{task}_seed101.json" for task in ("pick_place_counter_to_sink", "open_cabinet")]
    value = dict(schema_version="attentionbench.robocasa365-run-budget.v1", suite="robocasa365",
                 formal_eligible=False, max_attempts=10, assistance_credits=1, token_limit=8000,
                 human_deadline_seconds=30, attempt_wall_seconds=120, whole_case_wall_seconds=300,
                 approved_config_sha256s=[sha(path) for path in configs],
                 approved_policy_sha256=sha(DELIVERY / "configs/policy_smoke.py"))
    path = tmp_path / "run-budget.json"
    path.write_text(json.dumps(value) + "\n")
    return path, value


def entry_args():
    code = DELIVERY / "configs/policy_smoke.py"
    config = DELIVERY / "configs/pick_place_counter_to_sink_seed101.json"
    return dict(suite="robocasa365", task_id="pick_place_counter_to_sink", seed=101,
                policy_id="autonomous", code=code, approved_policy_sha256=sha(code),
                config=config, approved_config_sha256=sha(config), max_attempts=10,
                assistance_credits=1, token_limit=8000, assistance_mode="benchmark_proxy",
                human_deadline_seconds=30, overall_deadline_seconds=300)


def test_entry_admits_ten_only_with_new_hash_bound_budget_and_retains_old_caps(tmp_path):
    path, _ = budget(tmp_path)
    args = entry_args()
    lock, _ = inspect_formal_entry(**args, run_budget=path, approved_run_budget_sha256=sha(path))
    assert lock["max_attempts"] == 10 and lock["approved_run_budget_sha256"] == sha(path)
    with pytest.raises(ValueError):
        inspect_formal_entry(**args)
    for key, value in (("max_attempts", 11), ("assistance_credits", 2), ("token_limit", 8001),
                       ("human_deadline_seconds", 31), ("overall_deadline_seconds", 301)):
        with pytest.raises(ValueError):
            inspect_formal_entry(**{**args, key: value}, run_budget=path, approved_run_budget_sha256=sha(path))
    approved = sha(path)
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError):
        inspect_formal_entry(**args, run_budget=path, approved_run_budget_sha256=approved)


def test_cli_snapshots_new_budget_and_reaches_all_ten_fixture_attempts(tmp_path, monkeypatch, capsys):
    from benchmarks.attention_harness import formal_attention_cli as cli
    from benchmarks.attention_harness.robocasa365 import formal_runner
    path, _ = budget(tmp_path)
    args = entry_args()
    class LockedFixtureRunner(FixtureFormalRunner):
        def execute(self, request):
            result = super().execute(request)
            result["entry_lock"] = request.entry_lock
            reference = result["artifacts"]["trace"]
            trace_path = Path(reference["uri"])
            trace = json.loads(trace_path.read_bytes())
            trace["entry_lock"] = request.entry_lock
            trace_path.write_text(json.dumps(trace))
            reference["sha256"] = sha(trace_path)
            return result
    runner = LockedFixtureRunner()
    monkeypatch.setattr(formal_runner, "RoboCasa365FormalSuiteRunner", lambda **kwargs: runner)
    argv = ["test-cli", "--suite", "robocasa365", "--task", args["task_id"], "--seed", "101",
            "--attention-policy", "autonomous", "--code", str(args["code"]),
            "--approved-policy-sha256", args["approved_policy_sha256"], "--config", str(args["config"]),
            "--approved-config-sha256", args["approved_config_sha256"], "--artifact-root", str(tmp_path / "artifacts"),
            "--max-attempts", "10", "--assistance-credits", "0", "--token-limit", "8000",
            "--human-deadline-seconds", "30", "--overall-deadline-seconds", "300",
            "--attempt-deadline-seconds", "120", "--whole-case-wall-seconds", "300",
            "--run-budget", str(path), "--approved-run-budget-sha256", sha(path)]
    profile = next(p for p in json.loads(OLD.read_bytes())["profiles"] if p["suite"] == "robocasa365")
    for key in ("service_source_root", "sim_source_root", "task_source_root", "sim_python", "asset_manifest"):
        argv.extend(["--" + key.replace("_", "-"), profile[key]])
    monkeypatch.setattr(sys, "argv", argv)
    assert cli.main() == 1  # Test fixture intentionally reports native failure.
    summary = json.loads(capsys.readouterr().out)
    assert len(runner.requests) == len(summary["attempts"]) == 10
    assert summary["native_success"] is False and summary["formal_eligible"] is False
    assert all(0 < request.attempt_deadline_seconds <= 120 for request in runner.requests)
    assert summary["budget_guard"]["within_whole_case_wall_limit"] is True
    snapshots = list((tmp_path / "artifacts/entry-locks").glob("*/run_budget.json"))
    assert len(snapshots) == 1 and sha(snapshots[0]) == sha(path)


def test_runner_cannot_bypass_attempt_limit_without_entry_lock(tmp_path):
    path, value = budget(tmp_path)
    args = entry_args()
    with pytest.raises(ValueError):
        run_formal_attention(suite=args["suite"], task_id=args["task_id"], seed=101,
            artifact_root=tmp_path / "artifacts", policy_id="autonomous",
            policy_code_path=args["code"], approved_policy_sha256=args["approved_policy_sha256"],
            config_path=args["config"], approved_config_sha256=args["approved_config_sha256"],
            runner=FixtureFormalRunner(), max_attempts=11, assistance_credits=0, token_limit=8000,
            human_deadline_seconds=30,
            formal_attempt_deadline_seconds=120, formal_run_wall_seconds=300,
            run_budget=path, approved_run_budget_sha256=sha(path))
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("attempts", [1, 2, 5, 10])
def test_daily_random_dispatch_preregisters_selected_failure_slots(tmp_path, attempts):
    assert NEW.is_file(), "New daily budget catalog must be published"
    profiles = load_catalog(NEW)
    profile = next(p for p in profiles if p["suite"] == "robocasa365")
    option = next(p for p in public_options(profiles)["profiles"] if p["id"] == profile["id"])
    assert option["run_limits"]["max_attempts"] == 10
    selection = dict(profile_id=profile["id"], seed=101, attention_policy="budget_matched_random_escalation",
                     execution_target="robocasa365_sim", assistance_mode="benchmark_proxy",
                     max_attempts=attempts, assistance_credits=1, token_limit=8000,
                     human_deadline_seconds=30, overall_deadline_seconds=300)
    calls = []
    def popen(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(pid=99999999)
    store = AttentionStore(tmp_path / "test.sqlite3")
    try:
        receipt = launch_formal_run(selection, profiles=profiles, store=store, artifact_root=tmp_path / "runs", popen=popen)
        command = calls[0]
        policy_file = Path(command[command.index("--policy-config") + 1])
        prereg = json.loads(policy_file.read_bytes())
        assert prereg == {"target_request_count": min(1, attempts - 1), "total_failure_slots": attempts - 1, "seed": 101}
        assert sha(policy_file) == command[command.index("--approved-policy-config-sha256") + 1]
        assert receipt["locked"]["entry_lock"]["approved_run_budget_sha256"] == sha(Path(profile["run_budget"]))
        assert not any(e["event_type"] == "run.created" for e in store.events())
    finally:
        store.close()


def test_new_daily_original_task_bindings_validate_without_rewriting_old_files(tmp_path):
    from benchmarks.attention_harness.attention_control import load_binding, validate_launch_binding
    pointer = json.loads((ROOT / "openarm_bimanual/current.json").read_bytes())
    current_catalog = ROOT / "openarm_bimanual" / pointer["catalog"]
    assert current_catalog.is_file(), "Current daily budget catalog must be published"
    old = json.loads(OLD.read_bytes())["profiles"]
    new = json.loads(current_catalog.read_bytes())["profiles"]
    assert len(old) == 13
    assert [p["id"] for p in new[:13]] == [p["id"] for p in old]
    assert len(new) == 15
    assert all(p["robot"] == {"model":"OpenArm","execution_arm":"left"} and p["suite"] == "robocasa365" for p in new[13:])
    profiles = load_catalog(current_catalog)
    calls = []
    def popen(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(pid=99999999)
    store = AttentionStore(tmp_path / "test.sqlite3")
    try:
        for prior, current in zip(old[:11], new[:11]):
            renewed = {"attention_control_binding", "attention_control_binding_sha256", "config", "approved_config_sha256", "skill_graph_file", "skill_graph_sha256"}
            assert {k: v for k, v in prior.items() if k not in renewed} == {k: v for k, v in current.items() if k not in renewed}
            previous_config, current_config = [json.loads(Path(p["config"]).read_bytes()) for p in (prior, current)]
            assert {k:v for k,v in previous_config.items() if k != "service_files"} == {k:v for k,v in current_config.items() if k != "service_files"}
            binding = load_binding(current["attention_control_binding"], current["attention_control_binding_sha256"])
            validate_launch_binding(binding, current)
            profile = next(p for p in profiles if p["id"] == current["id"])
            selection = dict(profile_id=profile["id"], seed=profile["seed"], attention_policy="autonomous",
                             execution_target=profile["execution_target"], assistance_mode="benchmark_proxy",
                             max_attempts=1, assistance_credits=0, token_limit=30000,
                             human_deadline_seconds=30, overall_deadline_seconds=300)
            launch_formal_run(selection, profiles=profiles, store=store, artifact_root=tmp_path / "runs", popen=popen)
        assert len(calls) == 11
        assert not any(e["event_type"] == "run.created" for e in store.events())
    finally:
        store.close()
