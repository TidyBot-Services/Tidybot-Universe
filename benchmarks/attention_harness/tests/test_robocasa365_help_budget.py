"""Daily help budget v2; all runners and Advisor replies here are explicit fixtures."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmarks.attention_harness.core.advisor import AdvisorTransportReply
from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.ui_launch import load_catalog, public_options, launch_formal_run
from test_robocasa365 import FixtureFormalRunner
from test_robocasa365_attempt_budget import ROOT, NEW as V1, budget as v1_budget, entry_args, sha

NEW = ROOT / 'openarm_bimanual/launch_configs/main-ui-365-help9-v2/catalog.json'


def budget(tmp_path):
    path, value = v1_budget(tmp_path)
    value.update(schema_version='attentionbench.robocasa365-run-budget.v2', assistance_credits=9)
    path.write_text(json.dumps(value) + '\n')
    return path


def test_v2_admits_nine_help_credits_and_retains_both_legacy_limits(tmp_path):
    path = budget(tmp_path)
    args = {**entry_args(), 'assistance_credits': 9}
    lock, _ = inspect_formal_entry(**args, run_budget=path, approved_run_budget_sha256=sha(path))
    assert lock['assistance_credits'] == 9 and lock['approved_run_budget_sha256'] == sha(path)
    for changes in ({'assistance_credits': 10}, {'max_attempts': 11}, {'token_limit': 8001},
                    {'human_deadline_seconds': 31}, {'overall_deadline_seconds': 301}):
        with pytest.raises(ValueError):
            inspect_formal_entry(**{**args, **changes}, run_budget=path, approved_run_budget_sha256=sha(path))
    old, _ = v1_budget(tmp_path)
    for attempts, manifest in ((2, None), (10, old)):
        with pytest.raises(ValueError):
            inspect_formal_entry(**{**args, 'max_attempts': attempts, 'assistance_credits': 2},
                **({'run_budget': manifest, 'approved_run_budget_sha256': sha(manifest)} if manifest else {}))


def test_v2_manifest_rejects_tampering_even_with_a_new_digest(tmp_path):
    from benchmarks.attention_harness.robocasa365.budget import load_run_budget
    path = budget(tmp_path)
    args = entry_args()
    approved = sha(path)
    path.write_text(path.read_text() + ' ')
    with pytest.raises(ValueError):
        load_run_budget(path, approved, config_sha256=args['approved_config_sha256'], policy_sha256=args['approved_policy_sha256'])
    value = json.loads(path.read_bytes()); value['assistance_credits'] = 10
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        load_run_budget(path, sha(path), config_sha256=args['approved_config_sha256'], policy_sha256=args['approved_policy_sha256'])


@pytest.mark.parametrize('policy', ['reactive_help', 'budget_matched_random_escalation'])
def test_runner_consumes_nine_fixture_help_opportunities_before_tenth_attempt(tmp_path, policy):
    path = budget(tmp_path); args = entry_args(); runner = FixtureFormalRunner(); packets = []
    def transport(packet):
        packets.append(packet)
        return AdvisorTransportReply(content=json.dumps(dict(schema_version='attentionbench.advisor-advice.v1',
            request_type='hint', diagnosis='TEST FIXTURE: bounded public action failed',
            guidance='TEST FIXTURE: inspect grasp before retry', caution='Keep frozen limits', confidence=0.5)),
            model='parcc/GLM', latency_seconds=0, attempts=1,
            usage={'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}, request_id=f'fixture-{len(packets)}')
    result = run_formal_attention(suite='robocasa365', task_id=args['task_id'], seed=101,
        artifact_root=tmp_path/'runs', policy_id=policy, policy_code_path=args['code'],
        approved_policy_sha256=args['approved_policy_sha256'], config_path=args['config'],
        approved_config_sha256=args['approved_config_sha256'], runner=runner, max_attempts=10,
        assistance_credits=9, token_limit=8000, human_deadline_seconds=30, overall_deadline_seconds=300,
        formal_attempt_deadline_seconds=120, formal_run_wall_seconds=300,
        run_budget=path, approved_run_budget_sha256=sha(path), advisor_transport=transport, sleeper=lambda _:None,
        policy_config={'target_request_count':9,'total_failure_slots':9,'seed':101} if policy.startswith('budget_') else None)
    assert len(runner.requests) == len(result['attempts']) == 10
    assert len(packets) == len(result['requests']) == 9
    assert result['native_success'] is False and result['formal_eligible'] is False
    assert all(request.attention_input['advisor_guidance'] for request in runner.requests[1:])
    scheduler = result['scheduler_config']
    assert scheduler['assistance_credits'] == scheduler['run_budget']['assistance_credits'] == 9


@pytest.mark.parametrize('task', ['pick_place_counter_to_sink', 'open_cabinet'])
@pytest.mark.parametrize('attempts,credits', [(1,9),(2,9),(5,4),(10,9),(10,0),(10,1)])
def test_random_dispatch_locks_both_selected_budgets(tmp_path, task, attempts, credits):
    assert NEW.is_file(), 'Publish an independent help9 package'
    profiles = load_catalog(NEW)
    profile = next(p for p in profiles if p['suite']=='robocasa365' and p['task']==task)
    option = next(p for p in public_options(profiles)['profiles'] if p['id']==profile['id'])
    assert option['run_limits']['assistance_credits']==9 and option['run_limits']['max_attempts']==10
    selection=dict(profile_id=profile['id'],seed=101,attention_policy='budget_matched_random_escalation',
        execution_target='robocasa365_sim',assistance_mode='benchmark_proxy',max_attempts=attempts,
        assistance_credits=credits,token_limit=8000,human_deadline_seconds=30,overall_deadline_seconds=300)
    calls=[]; store=AttentionStore(tmp_path/'test.sqlite3')
    try:
        result=launch_formal_run(selection,profiles=profiles,store=store,artifact_root=tmp_path/'runs',
            popen=lambda cmd,**kwargs:(calls.append(cmd) or SimpleNamespace(pid=99999999)))
        cmd=calls[0]; plan=Path(cmd[cmd.index('--policy-config')+1])
        assert json.loads(plan.read_bytes())==dict(target_request_count=min(credits,attempts-1),total_failure_slots=attempts-1,seed=101)
        assert sha(plan)==cmd[cmd.index('--approved-policy-config-sha256')+1]
        assert result['locked']['entry_lock']['assistance_credits']==credits
        assert result['locked']['entry_lock']['approved_run_budget_sha256']==sha(Path(profile['run_budget']))
    finally:
        store.close()


def test_random_plan_tamper_and_excess_credit_fail_before_dispatch(tmp_path):
    assert NEW.is_file()
    profiles=load_catalog(NEW); profile=next(p for p in profiles if p['suite']=='robocasa365')
    selection=dict(profile_id=profile['id'],seed=101,attention_policy='budget_matched_random_escalation',
        execution_target='robocasa365_sim',assistance_mode='benchmark_proxy',max_attempts=10,
        assistance_credits=10,token_limit=8000,human_deadline_seconds=30,overall_deadline_seconds=300)
    calls=[]; store=AttentionStore(tmp_path/'test.sqlite3')
    try:
        with pytest.raises(ValueError):
            launch_formal_run(selection,profiles=profiles,store=store,artifact_root=tmp_path/'runs',
                popen=lambda *a,**k:calls.append(a))
        cloned=copy.deepcopy(profiles); p=next(p for p in cloned if p['id']==profile['id'])
        plan=tmp_path/'tampered.json';plan.write_text(json.dumps(dict(target_request_count=0,total_failure_slots=9,seed=101)))
        p['random_policy_configs_by_budget']['10:9']['path']=str(plan)
        with pytest.raises(ValueError):
            launch_formal_run({**selection,'assistance_credits':9},profiles=cloned,store=store,artifact_root=tmp_path/'runs',
                popen=lambda *a,**k:calls.append(a))
        assert not calls and not any(e['event_type']=='run.created' for e in store.events())
    finally:
        store.close()


def test_cli_snapshots_v2_budget_and_dispatches_nine_credit_setting(tmp_path, monkeypatch, capsys):
    import sys
    from test_robocasa365_attempt_budget import OLD
    from benchmarks.attention_harness import formal_attention_cli as cli
    from benchmarks.attention_harness.robocasa365 import formal_runner
    path = budget(tmp_path)
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
            "--max-attempts", "10", "--assistance-credits", "9", "--token-limit", "8000",
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


    assert summary["scheduler_config"]["assistance_credits"] == 9
    assert summary["entry_lock"]["assistance_credits"] == 9
