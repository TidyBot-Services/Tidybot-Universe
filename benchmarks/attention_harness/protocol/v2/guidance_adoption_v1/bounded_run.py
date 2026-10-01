"""Bounded development evidence only; no matrix or held-out entrypoint."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import sqlite3
import time
import traceback
from pathlib import Path

from benchmarks.attention_harness.demo_prior import verify_demo_prior
from benchmarks.attention_harness.formal_runner_boundary import FormalRunRequest, run_with_formal_boundary
from benchmarks.attention_harness.robosuite_memory.formal_runner import RobosuiteFormalSuiteRunner
from benchmarks.attention_harness.robocasa_native.formal_runner import RobocasaFormalSuiteRunner

ROOT = Path(__file__).resolve().parents[5]
SOURCE = Path(__file__).resolve().parent
OLD = SOURCE.parent / "review_packages/seven_primary_dev_2026-09-29"
TASKS = {"robosuite": "cube_lift", "robocasa": "counter_to_sink"}
OUT = ROOT.parent / "attentionbench-guidance-adoption-v1-20260929"
FREEZE = json.loads((SOURCE / "design_freeze.json").read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def make_runner(suite):
    if suite == "robosuite":
        return RobosuiteFormalSuiteRunner(service_source_root=ROOT.parent / "robosuite_sim-depth-recovery-engineering")
    sim_python = Path("/home/truares/miniconda3/envs/maniskill/bin/python")
    return RobocasaFormalSuiteRunner(
        sim_source_root=ROOT.parent / "maniskill_sim-attention-guard-v5",
        agent_source_root=Path("/home/truares/文档/Tidybot-Universe/agent_server-attention-rejection-v5"),
        task_source_root=Path("/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks"),
        sim_python=sim_python, agent_python=sim_python)


def prepare():
    inputs = OUT / "inputs"
    inputs.mkdir(exist_ok=True)
    for suite, task in TASKS.items():
        shutil.copyfile(OLD / f"configs/{suite}_{task}_seed101.json", inputs / f"{suite}_config.json")
        shutil.copyfile(OLD / f"base/{suite}_{task}/policy.py", inputs / f"{suite}_old_policy.py")
        shutil.copyfile(SOURCE / f"{suite}_{task}_v1.py", inputs / f"{suite}_new_policy.py")
        steps = json.loads((OLD / f"demo/{suite}_{task}_actions.json").read_text())["steps"]
        if suite == "robosuite":
            for index in (1, 2, 4):
                steps[index]["arguments"]["tolerance"] = .004
            steps[2]["arguments"]["z"] -= .015
            steps[3]["arguments"]["settle_steps"] = 30
        else:
            for step in steps[:2]:
                step["arguments"]["dx"] = .11
        asset = inputs / f"{suite}_authored_demo_actions.json"
        write(asset, {"source": "public_sdk", "steps": steps})
        manifest = inputs / f"{suite}_demo_manifest.json"
        write(manifest, {"schema_version": "attentionbench.public-demo.v1",
            "suite": suite, "task_id": task,
            "approval": {"id": "guidance-adoption-v1-development-input",
                         "approved_by": "user-goal-authorized-development-fixture"},
            "assets": [{"kind": "action_trajectory", "path": str(asset),
                        "sha256": sha(asset), "source": "public_sdk"}]})
        projected, receipt = verify_demo_prior(manifest.read_text(), suite=suite, task_id=task,
                                               approved_sha256=sha(manifest))
        (inputs / f"{suite}_demo_projection.txt").write_text(projected)
        write(inputs / f"{suite}_demo_receipt.json", {
            **receipt, "provenance": "authored development SDK trajectory derived from old public prior; not a successful robot recording",
            "original_trajectory_sha256": sha(OLD / f"demo/{suite}_{task}_actions.json")})
        memory_db = OLD / f"memory/{suite}/initial.sqlite3"
        with sqlite3.connect(f"file:{memory_db}?mode=ro", uri=True) as db:
            trusted = [json.loads(r[0]) for r in db.execute("SELECT payload FROM memories")
                       if json.loads(r[0])["status"] == "trusted"]
        assert len(trusted) == 1
        write(inputs / f"{suite}_memory_fixture.json", {
            "memory_ids_to_use": [trusted[0]["memory_id"]],
            "memory_guidance": {trusted[0]["memory_id"]: trusted[0]["guidance"]}})
    write(inputs / "input_lock.json", {"files": {
        str(p.relative_to(inputs)): sha(p) for p in inputs.iterdir() if p.is_file() and p.name != "input_lock.json"}})
    write(OUT / "ledger.json", {"schema": "attentionbench.guidance-adoption-live-ledger.v1",
        "formal_eligible": False, "attempt_cap": FREEZE["attempt_cap"], "rows": [], "hard_stopped": False})


def ledger():
    return json.loads((OUT / "ledger.json").read_text())


def reserve(row_id, suite, **details):
    value = ledger()
    if (value["hard_stopped"] or len(value["rows"]) >= FREEZE["attempt_cap"]
            or time.time() >= FREEZE["stop_new_runs_epoch"]):
        raise RuntimeError("predeclared bounded run stop")
    if any(row["id"] == row_id for row in value["rows"]):
        raise RuntimeError("attempt already reserved; no silent rerun")
    row = {"id": row_id, "suite": suite, "task_id": TASKS[suite], "seed": 101,
           "status": "reserved", "started_epoch": time.time(), **details}
    value["rows"].append(row)
    write(OUT / "ledger.json", value)
    return row


def finish(row_id, result):
    value = ledger()
    row = next(row for row in value["rows"] if row["id"] == row_id)
    row.update(status=result["status"], result_path=str(Path(result["artifact_dir"]) / "result.json"),
               result_sha256=sha(Path(result["artifact_dir"]) / "result.json"),
               artifacts=result["artifacts"], native_success=result["native_success"],
               policy_sha256=result["policy_sha256"], config_sha256=result["config_sha256"],
               ended_epoch=time.time())
    safety = json.loads(Path(result["artifacts"]["safety"]["uri"]).read_text())
    row["unsafe_attempts"] = safety["unsafe_attempts"]
    if row["unsafe_attempts"] != 0:
        value["hard_stopped"] = True
        value["stop_reason"] = "independent unsafe/unknown action"
    write(OUT / "ledger.json", value)
    print(json.dumps({key: row.get(key) for key in
                      ("id", "status", "native_success", "unsafe_attempts", "result_path")}), flush=True)
    if value["hard_stopped"]:
        raise RuntimeError(value["stop_reason"])


def run_direct(suite, arm, attention, original=False, policy_override=None):
    row_id = f"adoption-v1-{suite}-{arm}"
    policy = policy_override or OUT / f"inputs/{suite}_{'old' if original else 'new'}_policy.py"
    config = OUT / f"inputs/{suite}_config.json"
    reserve(row_id, suite, arm=arm, source_kind="paired_input_fixture",
            attention_input=attention, guidance_content_sha256=hashlib.sha256(
                json.dumps(attention, ensure_ascii=False, sort_keys=True).encode()).hexdigest())
    request = FormalRunRequest(suite=suite, task_id=TASKS[suite], seed=101,
        policy_code_path=policy, policy_sha256=sha(policy), config_path=config,
        config_sha256=sha(config), artifact_root=OUT / f"direct/{row_id}/attempts",
        overall_deadline_seconds=120, run_id=f"run:{row_id}",
        attempt_id=f"attempt:{row_id}:0", attention_input=attention)
    result = run_with_formal_boundary(request, runner=make_runner(suite))
    finish(row_id, result)


def direct_phase(suite):
    hint = ("AB_CONTROL_V1 grasp_offset_m=.005;approach_tolerance_m=.004;close_settle_steps=30;"
            if suite == "robosuite" else "AB_CONTROL_V1 base_forward_m=.11;grasp_offset_m=.045;open_settle_steps=20;close_settle_steps=30;")
    arms = [("old-baseline", {}, True), ("no-guidance", {}, False),
            ("hint", {"advisor_guidance": hint}, False),
            ("content-ablation", {"advisor_guidance": "Review trace and retry."}, False),
            ("demo", {"demo_prior": (OUT / f"inputs/{suite}_demo_projection.txt").read_text()}, False),
            ("memory", json.loads((OUT / f"inputs/{suite}_memory_fixture.json").read_text()), False)]
    for arm, attention, original in arms:
        run_direct(suite, arm, attention, original)


def scheduler_phase(kind):
    from benchmarks.attention_harness.formal_attention_run import run_formal_attention
    from benchmarks.attention_harness.formal_entry import inspect_formal_entry
    suite = "robosuite"
    policy = OUT / f"inputs/{suite}_new_policy.py"
    config = OUT / f"inputs/{suite}_config.json"
    condition = ("autonomous" if kind == "memory" else "demo_first" if kind == "demo"
                 else "full_trace_aware_attention_planner")
    max_attempts = 3 if kind == "hint" else 2 if kind == "memory" else 1
    common = dict(suite=suite, task_id=TASKS[suite], seed=101, policy_id=condition,
        code=policy, approved_policy_sha256=sha(policy), config=config,
        approved_config_sha256=sha(config), max_attempts=max_attempts,
        assistance_credits=1, token_limit=4096, assistance_mode="benchmark_proxy",
        human_deadline_seconds=30, overall_deadline_seconds=300)
    extra = {}
    if kind == "memory":
        original = OLD / "entries/robosuite_cube_lift_seed101/full_trace_aware_attention_planner/memory_contract.json"
        contract = json.loads(original.read_text())
        path = OUT / "inputs/robosuite_new_memory_contract.json"
        # Original trusted authority and applicability remain byte-for-byte.
        # This new contract/M1 identity is bound to the revised policy.
        write(path, contract)
        # Development use of the existing autonomous retrieval route. The full
        # planner correctly treats this generic native failure as insufficient
        # grounded evidence, so it inspects/asks rather than selecting Memory.
        # Keep that decision logic untouched; use the original legacy context
        # interface with the exact-version gateway, on a fresh snapshot copy.
        from benchmarks.attention_harness.formal_memory_contract import initialize_frozen_memory
        store, evidence, gateway = initialize_frozen_memory(contract,
            artifact_root=OUT / "scheduler/memory")
        extra.update(memory_context=contract["context"],
                     approved_memory_context_sha256=hashlib.sha256(json.dumps(
                         contract["context"], sort_keys=True).encode()).hexdigest(),
                     store_path=store, memory_evidence_root=evidence, memory_gateway=gateway,
                     fresh_advisor_cache=True)
        write(OUT / "scheduler/memory/development_scope.json", {
            "attention_policy": condition, "original_full_planner_unchanged": True,
            "reason": "generic native failure lacks concrete grounded SDK error; full planner would inspect/request",
            "path": "existing legacy development context interface with exact frozen gateway",
            "contract_sha256": sha(path), "exact_context": contract["context"],
            "versions": contract["initial_state"]["versions"], "formal_eligible": False,
            "not_seven_condition_memory_effect_evidence": True})
    if kind == "demo":
        path = OUT / "inputs/robosuite_demo_manifest.json"
        common.update(demo_prior=path, approved_demo_sha256=sha(path))
        extra.update(demo_prior=path.read_text(), approved_demo_sha256=sha(path))
    lock, parsed = inspect_formal_entry(**common)
    write(OUT / f"scheduler/{kind}/m1_entry_lock.json", lock)
    requests = []

    def transport(request):
        payload = json.loads(request["messages"][1]["content"])
        requests.append({"request_type": payload["request_type"],
                         "packet": payload["trace_packet"], "source": "deterministic_development_advisor"})
        write(OUT / f"scheduler/{kind}/advisor_requests.json", requests)
        return json.dumps({"schema_version": "attentionbench.advisor-advice.v1",
            "request_type": payload["request_type"], "diagnosis": "Visible grasp approach needs lower, precise placement.",
            "guidance": "AB_CONTROL_V1 grasp_offset_m=.005;approach_tolerance_m=.004;close_settle_steps=30;",
            "caution": "Retain the independent Safety envelope.", "confidence": .8})

    actual = make_runner(suite)

    class RecordedRunner:
        suite = "robosuite"
        cancel_event = None

        def execute(self, request):
            index = len([r for r in ledger()["rows"] if r.get("scheduler_kind") == kind])
            row_id = f"adoption-v1-scheduler-{kind}-{index}"
            reserve(row_id, suite, scheduler_kind=kind, source_kind="actual_scheduler",
                    attention_input=request.attention_input,
                    entry_sha256=request.entry_sha256)
            actual.cancel_event = self.cancel_event
            result = actual.execute(request)
            finish(row_id, result)
            return result

    result = run_formal_attention(suite=suite, task_id=TASKS[suite], seed=101,
        artifact_root=OUT / f"scheduler/{kind}/runs", policy_id=condition,
        policy_code_path=policy, approved_policy_sha256=sha(policy), config_path=config,
        approved_config_sha256=sha(config), runner=RecordedRunner(), entry_lock=lock,
        overall_deadline_seconds=300, formal_attempt_deadline_seconds=120,
        formal_run_wall_seconds=300, max_attempts=max_attempts, assistance_credits=1,
        token_limit=4096, human_deadline_seconds=30, assistance_mode="benchmark_proxy",
        advisor_transport=transport, **extra)
    write(OUT / f"scheduler/{kind}/summary.json", result)
    print(json.dumps({"scheduler_kind": kind, "native_success": result["native_success"],
                      "attempts": len(result["attempts"]), "decisions": result["decisions"]}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["prepare", "robosuite", "robocasa", "robocasa-repair", "hint", "memory", "demo"], required=True)
    args = parser.parse_args()
    if args.phase == "prepare":
        if (OUT / "ledger.json").exists():
            raise RuntimeError("refuse to overwrite existing live ledger")
        prepare()
    elif args.phase in TASKS:
        direct_phase(args.phase)
    elif args.phase == "robocasa-repair":
        policy = OUT / "inputs/robocasa_new_policy_v1_1.py"
        if policy.exists():
            raise RuntimeError("repair inputs already exist; no silent rerun")
        shutil.copyfile(SOURCE / "robocasa_counter_to_sink_v1_1.py", policy)
        write(OUT / "inputs/repair_v1_1_lock.json", {
            "policy_path": str(policy), "policy_sha256": sha(policy),
            "amendment_freeze_sha256": sha(SOURCE / "design_amendment_freeze_v1_1.json")})
        run_direct("robocasa", "no-guidance-v1-1", {}, policy_override=policy)
        run_direct("robocasa", "memory-v1-1", json.loads(
            (OUT / "inputs/robocasa_memory_fixture.json").read_text()), policy_override=policy)
    else:
        scheduler_phase(args.phase)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        write(OUT / f"failure-{time.time_ns()}.json", {"traceback": traceback.format_exc()})
        raise
