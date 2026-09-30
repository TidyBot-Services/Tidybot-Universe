"""Generate and inspect 350 immutable development entries; never execute them."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from itertools import product

from benchmarks.attention_harness.core.policies import POLICY_IDS, build_policy
from benchmarks.attention_harness.formal_entry import inspect_formal_entry
from benchmarks.attention_harness.formal_memory_contract import ACCOUNTING

ROOT = Path(__file__).resolve().parents[4]
TASKS = [("robosuite", "cube_lift"), ("robocasa", "counter_to_sink")]
SEEDS = list(range(101, 126))
BUDGET = {"max_attempts": 4, "assistance_credits": 1, "token_limit": 4096,
          "attempt_deadline_seconds": 120, "whole_case_wall_seconds": 300, "sdk_calls_max": 200}
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()
def ref(path):
    return {"path": str(path.resolve()), "sha256": sha(path)}


def generate(package: Path, audit_root: Path, runtime_commit: str):
    if (package / "matrix_plan.json").exists():
        raise ValueError("freeze already exists; never overwrite a frozen plan")
    if runtime_commit != git(ROOT, "rev-parse", "HEAD"):
        raise ValueError("runtime commit must equal the saved code HEAD")
    inventory = json.loads((audit_root / "independent/memory_inventory.json").read_bytes())
    approval_path = audit_root / "independent/demo_input_approval.json"
    demo_approval = json.loads(approval_path.read_bytes())
    if demo_approval.get("approved") is not True:
        raise ValueError("independent demo approval required before freeze")
    package.mkdir(parents=True, exist_ok=True)
    protocol = package / "protocol"
    protocol.mkdir(exist_ok=True)
    spec = ROOT / "docs/attentionbench_seven_policy_experiment_spec.md"
    baseline = Path(__file__).parent / "formal_admission_v2_2_2026-09-29.json"
    resolution = Path(__file__).parent / "formal_admission_v2_4_resolution_2026-09-30.json"
    for source in (spec, baseline, resolution):
        shutil.copyfile(source, protocol / source.name)
    write(package / "accounting_rules.json", {
        **ACCOUNTING, "memory_reset_each_run": True, "automatic_memory_promotion": False,
        "memory_initial_versions": "exact allowlist from task-specific immutable SQLite snapshot",
        "out_of_scope": "Service must deny retrieval/grant; continue with no Memory; never broaden scope",
        "cache": "new empty cache for each run; within-run hits use the same 2s logical latency",
        "tokens": "actual prompt+completion total; cached new-provider tokens=0; unknown/inconsistent usage or total>4096 invalid and retained",
        "rejected_provider_responses": "retain raw HTTP bytes/SHA, response/content/usage and unknown costs; stop without retry; do not report unknown as zero",
        "sdk_calls": "200 accepted policy SDK broker calls summed across attempts; rejected excess RPC is separately reported and not dispatched",
        "budget_exhaustion": "no extra attempts/requests; native success at exact SDK limit remains a success if otherwise valid",
        "all_outcomes_retained": True, "minimum_native_success_numerator": None,
    })
    shutil.copyfile(approval_path, package / "demo/independent_input_approval.json")
    for asset in demo_approval["assets"]:
        path = Path(asset["asset_path"])
        if sha(path) != asset["asset_sha256"]:
            raise ValueError("approved demo asset changed")
        write(package / "demo" / f"{asset['suite']}_{asset['task_id']}_manifest.json", {
            "schema_version": "attentionbench.public-demo.v1", "suite": asset["suite"],
            "task_id": asset["task_id"], "approval": {"id": demo_approval["approval_id"],
                                                       "approved_by": demo_approval["approved_by"]},
            "assets": [{"kind": "action_trajectory", "path": str(path.resolve()),
                        "sha256": asset["asset_sha256"], "source": "public_sdk"}],
        })
    memory_tasks = {}
    for memory in inventory["memories"]:
        suite = memory["suite"]
        destination = package / "memory" / suite
        destination.mkdir(parents=True, exist_ok=True)
        source = Path(memory["source_sqlite"])
        if sha(source) != memory["sha256"]:
            raise ValueError("source authority snapshot changed")
        frozen = destination / "initial.sqlite3"
        # Work only on a copy; lifecycle and promotion history stay intact.
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original:
            with sqlite3.connect(frozen) as copied:
                original.backup(copied)
                count = copied.execute("SELECT COUNT(*) FROM advisor_cache").fetchone()[0]
                copied.execute("DELETE FROM advisor_cache")
        original_evidence = source.parent / "attempts"
        evidence = destination / "evidence"
        shutil.copytree(original_evidence, evidence)
        provenance = {"source": ref(source), "initial_snapshot": ref(frozen),
                      "cleared_advisor_cache_rows": count, "authority_rows_otherwise_unchanged": True,
                      "memory_id": memory["memory_id"], "version": memory["version"],
                      "expires_at": memory["expires_at"], "status": memory["status"],
                      "scope": memory["scope"], "source_evidence": str(original_evidence),
                      "evidence_file_sha256": {str(f.relative_to(evidence)): sha(f)
                                               for f in sorted(evidence.rglob("*")) if f.is_file()}}
        write(destination / "provenance.json", provenance)
        memory_tasks[suite] = {"record": memory, "snapshot": frozen, "evidence": evidence}
    base = ROOT.parent / "Tidybot-Universe-attention-repair-v5/benchmarks/attention_harness/protocol/v2/review_packages/formal_repair_v5_2026-09-30"
    policy_info = {}
    for suite, task in TASKS:
        source = base / f"{suite}_{task}"
        directory = package / "base" / f"{suite}_{task}"
        directory.mkdir(parents=True)
        code = directory / "policy.py"
        code.write_bytes((source / "policy.py").read_bytes())
        original_receipt = source / "generation_receipt.json"
        receipt = json.loads(original_receipt.read_bytes())
        receipt["source"] = str(code.resolve())
        receipt["frozen_source_relocation"] = {"original_receipt": ref(original_receipt),
                                                "original_policy": ref(source / "policy.py"),
                                                "control_bytes_changed": False}
        write(directory / "generation_receipt.json", receipt)
        shutil.copyfile(source / "review.json", directory / "original_policy_review.json")
        policy_info[suite] = {"path": str(code), "sha256": sha(code),
                              "generation_receipt": ref(directory / "generation_receipt.json"),
                              "original_policy_review": ref(directory / "original_policy_review.json"),
                              "original_package_decision": ref(base / "review_decision.json")}
        for seed in SEEDS:
            config = (ROOT.parent / "attentionbench-depth-qualifying-20260929/configs" / f"config_seed{seed}.json"
                      if suite == "robosuite" else source / f"config_seed{seed}.json")
            destination = package / "configs" / f"{suite}_{task}_seed{seed}.json"
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(config.read_bytes())
    write(package / "retry_k.json", {"k": 2})
    write(package / "random_quota_preregistration.json", {
        "predeclared_before_effects": True, "target_request_count": 1,
        "full_method_planned_quota": 1, "total_failure_slots": 3,
        "slot_rule": "SHA-256(seed:failure_slot) rank; select lowest quota slots",
        "source": "fixed protocol help quota, not realized full-method outcomes",
        "early_success": "report realized shortfall without imputing help or adding attempts",
    })
    roots = {"robosuite_service": ROOT.parent / "robosuite_sim-depth-recovery-engineering",
             "maniskill_service": ROOT.parent / "maniskill_sim-attention-guard-v5",
             "agent_service": Path("/home/truares/文档/Tidybot-Universe/agent_server-attention-rejection-v5"),
             "task_service": Path("/home/truares/文档/Tidybot-Universe/sims/robocasa_tasks"),
             "memory_service": ROOT.parent / "attention_memory_service"}
    versions = {"universe": {"path": str(ROOT), "runtime_commit": runtime_commit,
                              "saved_protocol_commit": "a42ab8201277ffa806281b89a4f14e3ef33b1059",
                              "scope": "runtime commit is independent of later delivery commits"}}
    for name, path in roots.items():
        status = git(path, "status", "--porcelain")
        if status:
            raise ValueError(f"{name} source dirty; exact commit insufficient")
        versions[name] = {"path": str(path), "commit": git(path, "rev-parse", "HEAD"), "clean": True}
    write(package / "software_versions.json", versions)
    harness_python = ROOT.parent / "attentionbench-week2-clean-venv/bin/python"
    sim_python = Path("/home/truares/miniconda3/envs/maniskill/bin/python")
    write(package / "execution_contract.json", {
        "execution_authorized": False, "mode": "generation_and_inspection_only",
        "launch_module": "benchmarks.attention_harness.formal_attention_cli",
        "harness_python": ref(harness_python), "robocasa_sim_python": ref(sim_python),
        "robocasa_agent_python": ref(sim_python),
        "environment": {"PYTHONPATH": str(roots["memory_service"]) + ":" + str(ROOT)},
        "model": "parcc/GLM", "provider_http_attempts": 1, "format_attempts": 1,
        "single_glm_call": True, "budget": BUDGET,
        "required_dynamic_output_argument": "--artifact-root (fresh directory outside this frozen package)",
        "service_versions": ref(package / "software_versions.json"),
        "launch_preflight": "verify all manifest SHA and exact clean runtime/Service commits; authorization requires independent overall PASS",
    })
    rows = []
    allowed = {suite: [] for suite, _ in TASKS}
    for order, ((suite, task), seed, condition) in enumerate(product(TASKS, SEEDS, POLICY_IDS), 1):
        directory = package / "entries" / f"{suite}_{task}_seed{seed}" / condition
        config = package / "configs" / f"{suite}_{task}_seed{seed}.json"
        c = json.loads(config.read_bytes())
        policy = policy_info[suite]
        value = {"schema_version": "attentionbench.formal-memory-contract.v1", "suite": suite,
                 "task_id": task, "condition": condition, "visibility": "none",
                 "initial_state": {"kind": "empty", "path": None, "sha256": None, "versions": []},
                 "context": None, "evidence_root": None, "reset_each_run": True,
                 "automatic_promotion": False, "accounting": ACCOUNTING}
        grant_eligible = False
        if condition == "full_trace_aware_attention_planner":
            memory = memory_tasks[suite]
            cases = memory["record"]["scope"]["cases"]
            historical = next((case for case in cases if case["seed"] == seed), None)
            camera_names = c.get("camera_names", [c.get("camera_name")])
            prompt = c.get("task_prompt", "Lift the cube clear of the table.")
            context = {"suite": suite, "task_id": task, "perception_mode": "sim_gt",
                       "scene_id": c["scene_id"], "object_set_id": c["object_set_id"],
                       "camera_names": camera_names, "task_prompt": prompt,
                       "camera_config_id": "camera:" + (c["camera_name"] if suite == "robosuite" else "+".join(camera_names)),
                       "task_variant_id": (historical["task_variant_id"] if historical else
                            "primary-dev-config:" + sha(config)[:20])}
            # For a matched historical camera retain its approved identity.
            if historical and historical["camera_names"] == camera_names:
                context["camera_config_id"] = historical["camera_config_id"]
            grant_eligible = any(all(context[k] == case[k] for k in
                                    ("scene_id", "object_set_id", "camera_names", "task_prompt",
                                     "camera_config_id", "task_variant_id")) for case in cases)
            if grant_eligible:
                allowed[suite].append(seed)
            value.update(visibility="trusted_exact_versions", context=context,
                         evidence_root=str(memory["evidence"].resolve()),
                         initial_state={"kind": "sqlite_snapshot", "path": str(memory["snapshot"].resolve()),
                                        "sha256": sha(memory["snapshot"]),
                                        "versions": [{"memory_id": memory["record"]["memory_id"], "version": 1}]})
        write(directory / "memory_contract.json", value)
        policy_config = None
        if condition == "retry_k_then_ask":
            policy_config = package / "retry_k.json"
        if condition == "budget_matched_random_escalation":
            policy_config = directory / "random.json"
            write(policy_config, {"target_request_count": 1, "total_failure_slots": 3, "seed": seed})
        demo = (package / "demo" / f"{suite}_{task}_manifest.json" if condition == "demo_first" else None)
        kwargs = dict(suite=suite, task_id=task, seed=seed, policy_id=condition,
            code=Path(policy["path"]), approved_policy_sha256=policy["sha256"], config=config,
            approved_config_sha256=sha(config), max_attempts=4, assistance_credits=1, token_limit=4096,
            assistance_mode="benchmark_proxy", human_deadline_seconds=30, overall_deadline_seconds=300,
            demo_prior=demo, approved_demo_sha256=sha(demo) if demo else None,
            policy_config=policy_config, approved_policy_config_sha256=sha(policy_config) if policy_config else None,
            memory_contract=directory / "memory_contract.json",
            approved_memory_contract_sha256=sha(directory / "memory_contract.json"))
        lock, parsed = inspect_formal_entry(**kwargs)
        write(directory / "m1_entry_lock.json", lock)
        launch = ["--suite", suite, "--task", task, "--seed", str(seed),
                  "--attention-policy", condition, "--code", policy["path"],
                  "--approved-policy-sha256", policy["sha256"], "--config", str(config),
                  "--approved-config-sha256", sha(config), "--max-attempts", "4",
                  "--assistance-credits", "1", "--token-limit", "4096",
                  "--assistance-mode", "benchmark_proxy", "--human-deadline-seconds", "30",
                  "--overall-deadline-seconds", "300", "--attempt-deadline-seconds", "120",
                  "--whole-case-wall-seconds", "300", "--single-glm-call",
                  "--memory-contract", str(directory / "memory_contract.json"),
                  "--approved-memory-contract-sha256", sha(directory / "memory_contract.json"),
                  "--expected-entry-sha256", lock["sha256"],
                  "--dev-generation-artifact", policy["generation_receipt"]["path"]]
        if demo:
            launch += ["--demo-prior", str(demo), "--approved-demo-sha256", sha(demo)]
        if policy_config:
            launch += ["--policy-config", str(policy_config),
                       "--approved-policy-config-sha256", sha(policy_config)]
        if suite == "robosuite":
            launch += ["--service-source-root", str(roots["robosuite_service"])]
        else:
            for arg, path in (("--sim-source-root", roots["maniskill_service"]),
                              ("--agent-source-root", roots["agent_service"]),
                              ("--task-source-root", roots["task_service"]),
                              ("--sim-python", sim_python), ("--agent-python", sim_python)):
                launch += [arg, str(path)]
        write(directory / "launch_arguments.json", {"execution_authorized": False,
              "fixed_arguments": launch, "dynamic_output_argument_required": "--artifact-root"})
        row = {"order": order, "suite": suite, "task_id": task, "seed": seed, "condition": condition,
               "policy": policy, "config": ref(config), "memory_contract": ref(directory / "memory_contract.json"),
               "m1_entry_lock": ref(directory / "m1_entry_lock.json"), "m1_entry_identity_sha256": lock["sha256"],
               "demo": ref(demo) if demo else None, "policy_config": ref(policy_config) if policy_config else None,
               "trusted_memory_grant_eligible": grant_eligible, "run_status": "not_executed",
               "budget": BUDGET, "accounting_rules_sha256": sha(package / "accounting_rules.json"),
               "launch_arguments": ref(directory / "launch_arguments.json"),
               "execution_contract_sha256": sha(package / "execution_contract.json")}
        if condition == "budget_matched_random_escalation":
            row["random_selected_slots"] = sorted(build_policy(condition, **parsed).selected_slots)
        rows.append(row)
    write(package / "matrix_plan.json", {
        "schema_version": "attentionbench.seven-primary-development-freeze.v1",
        "scope": "本开发集主实验；held-out与消融另行冻结", "predeclared_before_effects": True,
        "runtime_commit": runtime_commit, "specification": ref(protocol / spec.name),
        "base_protocol": ref(protocol / baseline.name), "resolution": ref(protocol / resolution.name),
        "original_protocol_sources": {"specification": ref(spec), "baseline": ref(baseline),
                                      "resolution": ref(resolution)},
        "grid": {"tasks": TASKS, "seeds": SEEDS, "conditions": list(POLICY_IDS), "repeats": 1},
        "planned_slots": len(rows), "executed_slots": 0, "execution_authorized": False,
        "heldout_executions": 0, "ablation_executions": 0,
        "reporting": {"aggregation": "per_task", "primary_axis": "credits", "secondary_axis": "tokens", "interval": "Wilson 95%"},
        "heldout_protocol_pool": list(range(1001, 1101)), "heldout_later_sample_size": 10,
        "memory_grant_eligible_seeds": allowed, "memory_coverage_threshold": None,
        "native_success_threshold": None, "slots": rows,
    })
    source = {}
    for rel in git(ROOT, "ls-files").splitlines():
        if rel.endswith(".py") and any(rel.startswith(prefix) for prefix in
              ("benchmarks/attention_harness/", "tidybot_sdk/", "robosuite_sim/", "skill-agent-setup/claude-code/")):
            path = ROOT / rel
            committed = subprocess.check_output(["git", "-C", str(ROOT), "show", runtime_commit + ":" + rel])
            if path.read_bytes() != committed:
                raise ValueError("runtime source differs from exact commit: " + rel)
            source[rel] = sha(path)
    write(package / "runtime_source_lock.json", {"runtime_commit": runtime_commit, "file_sha256": source})
    write(package / "sha256_manifest.json", {
        "schema_version": "attentionbench.freeze-file-sha256.v1",
        "files": {str(f.relative_to(package)): sha(f) for f in sorted(package.rglob("*"))
                  if f.is_file() and f.name != "sha256_manifest.json"},
    })
    print(json.dumps({"package": str(package), "slots": len(rows), "executed": 0,
                      "plan_sha256": sha(package / "matrix_plan.json"), "grant_eligible": allowed}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--runtime-commit", required=True)
    args = parser.parse_args()
    generate(args.package.resolve(), args.audit_root.resolve(), args.runtime_commit)

if __name__ == "__main__":
    main()
