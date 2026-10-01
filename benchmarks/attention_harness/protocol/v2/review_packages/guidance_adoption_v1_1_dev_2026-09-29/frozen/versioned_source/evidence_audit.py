"""Read-only audit of persisted evidence, separate from the execution driver.

This is an author-produced mechanical audit, not an independent reviewer vote.
It never dispatches actions or changes verdicts, grants, or raw artifacts.
"""
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
OUT = ROOT.parent / "attentionbench-guidance-adoption-v1-20260929"
SOURCE = Path(__file__).resolve().parent


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")).encode()).hexdigest()


def same_demo_content(actual_text, approved_text):
    actual, approved = json.loads(actual_text), json.loads(approved_text)
    # verify_demo_prior relocates the approved asset to a per-run snapshot.
    # That path is not robot guidance. Verify the snapshot's exact bytes and
    # compare all of the asset content that the compiler can consume.
    project = lambda item: {"schema_version": item["schema_version"], "assets": [
        {key: value for key, value in asset.items() if key != "path"} for asset in item["assets"]]}
    return project(actual) == project(approved) and all(
        sha(asset["path"]) == asset["sha256"] for asset in actual["assets"])


def commands(trace, *, backend_semantics=False):
    rows = []
    for event in trace["sdk_events"]:
        if event["event_type"] not in {"sdk.arm_command", "sdk.base_command", "sdk.gripper_command"}:
            continue
        args = dict(event["arguments"])
        if backend_semantics and trace["suite"] == "robocasa" and event["source"] == "robot_sdk.gripper":
            args.pop("settle_steps", None)  # Proven ignored by the pinned backend.
        rows.append({"source": event["source"], "operation": event["operation"],
                     "arguments": args, "status": event["status"]})
    return rows


def first_difference(left, right):
    for index in range(max(len(left), len(right))):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a != b:
            return {"index_zero_based": index, "control": a, "guidance": b}
    return None


def audit():
    checks = []

    def check(ok, label, **details):
        checks.append({"check": label, "pass": bool(ok), **details})

    ledger = read(OUT / "ledger.json")
    freeze = read(SOURCE / "design_freeze.json")
    check(len(ledger["rows"]) <= freeze["attempt_cap"] == 20, "bounded attempt cap",
          used=len(ledger["rows"]), cap=20)
    check(all(row["seed"] == 101 for row in ledger["rows"]), "development seed only")
    check(all(row.get("ended_epoch", 1e20) < freeze["deadline_epoch"] for row in ledger["rows"]),
          "all attempts finished before 90-minute deadline")
    check(sha(SOURCE / "DESIGN.md") == freeze["design_sha256"], "original preimplementation design intact")
    amendment = read(SOURCE / "design_amendment_freeze_v1_1.json")
    change = read(SOURCE / "robocasa_change_receipt_v1_1.json")
    check(sha(SOURCE / "DESIGN_AMENDMENT_v1_1.md") == amendment["amendment_sha256"]
          and amendment["frozen_at_epoch"] < change["implemented_at_epoch"],
          "v1.1 amendment frozen before implementation")
    protected = read(OUT / "protected_before.json")
    drift = []
    for filename, expected in protected.items():
        path = ROOT / filename
        actual = sha(path) if path.is_file() else "missing_or_broken_symlink"
        if actual != expected:
            drift.append({"path": filename, "expected": expected, "actual": actual})
    check(not drift, "old policy/package/runtime/gates preserved", files=len(protected), drift=drift)
    input_lock = read(OUT / "inputs/input_lock.json")
    check(all(sha(OUT / "inputs" / name) == expected for name, expected in input_lock["files"].items()),
          "original new-version input lock intact")
    traces, native, evidence = {}, {}, []
    for row in ledger["rows"]:
        row_id = row["id"]
        check(row["status"] == "completed", "complete retained attempt", row_id=row_id)
        if "artifacts" not in row:
            continue
        refs = row["artifacts"]
        check(set(refs) == {"trace", "safety", "sandbox_receipt", "native_result"}
              and all(sha(ref["uri"]) == ref["sha256"] for ref in refs.values()),
              "four artifact SHAs", row_id=row_id)
        trace = read(refs["trace"]["uri"])
        safety = read(refs["safety"]["uri"])
        receipt = read(refs["sandbox_receipt"]["uri"])
        verdict = read(refs["native_result"]["uri"])
        traces[row_id], native[row_id] = trace, verdict
        check(safety["source"] == "independent_safety_monitor" and safety["unsafe_attempts"] == 0
              and not safety["violations"], "independent Safety zero", row_id=row_id)
        check(verdict["evaluated"] and verdict["status"] == "completed"
              and verdict["service_native_success"] == verdict["native_success"] == row["native_success"]
              and verdict["source"] == ("robosuite_sim/v1/success" if row["suite"] == "robosuite"
                                         else "robocasa/task/success"),
              "native verdict from pinned evaluator", row_id=row_id)
        stop = receipt["service_stop"]
        stops = stop["services"].values() if row["suite"] == "robocasa" else [stop]
        check(all(s["leader_reaped"] and s["process_group_gone"] for s in stops),
              "Service process groups reaped", row_id=row_id)
        check(receipt["service_source_unchanged"]
              and receipt["source_sha256_before"] == receipt["source_sha256_after"] == row["policy_sha256"]
              and receipt["config_sha256_after"] == row["config_sha256"],
              "source/config unchanged during actual attempt", row_id=row_id)
        cmd = commands(trace, backend_semantics=True)
        check(cmd and all(c["status"] == "completed" for c in cmd),
              "completed robot commands", row_id=row_id, count=len(cmd))
        if row["suite"] == "robosuite":
            controls = [step["action"] for step in trace["backend_steps"]]
            check(controls and all(step["action_receipt"]["action_executed"] is True
                  and step["action"] == step["action_receipt"]["action"] for step in trace["backend_steps"]),
                  "actual executed OSC controls, not trace-only fields", row_id=row_id, count=len(controls))
            execution = {"kind": "executed_service_OSC_action_arrays", "count": len(controls),
                         "sha256": digest(controls)}
        else:
            jobs = read(Path(row["result_path"]).parent / "executed_jobs/receipt.json")
            check(len(jobs["jobs"]) == len(cmd) and all(job["completed"] for job in jobs["jobs"]),
                  "actual Agent jobs completed for every SDK command", row_id=row_id,
                  jobs=len(jobs["jobs"]), sdk_commands=len(cmd))
            execution = {"kind": "completed_Agent_job_sequence_and_independent_Safety_samples",
                         "count": len(jobs["jobs"]), "job_receipt_path": str(Path(row["result_path"]).parent / "executed_jobs/receipt.json"),
                         "sha256": digest(cmd), "ignored_parameter": "gripper.settle_steps",
                         "temporary_wrapper_missing_count": sum(not j["original_wrapper_retained"] for j in jobs["jobs"])}
        evidence.append({"row_id": row_id, "suite": row["suite"], "seed": 101,
            "status": row["status"], "native_success": verdict["native_success"],
            "unsafe_attempts": safety["unsafe_attempts"], "initial_observation_sha256": trace["initial_observation_sha256"],
            "reset_attestation": trace["reset_attestation"], "policy_sha256": row["policy_sha256"],
            "config_sha256": row["config_sha256"], "robot_commands": cmd,
            "robot_command_sha256": digest(cmd), "execution": execution, "artifacts": refs})

    pairs = []

    def pair(control_id, treatment_id, kind, expect_changed, *, same_policy=True, expect_improvement=False):
        control, treatment = traces[control_id], traces[treatment_id]
        identical_scene = (control["seed"] == treatment["seed"] == 101
            and control["reset_attestation"] == treatment["reset_attestation"]
            and control["config_sha256"] == treatment["config_sha256"]
            and control["initial_observation_sha256"] == treatment["initial_observation_sha256"])
        same = control["policy_sha256"] == treatment["policy_sha256"]
        a, b = commands(control, backend_semantics=True), commands(treatment, backend_semantics=True)
        changed = a != b
        if control["suite"] == "robosuite":
            raw_a = [s["action"] for s in control["backend_steps"]]
            raw_b = [s["action"] for s in treatment["backend_steps"]]
            executed_changed = raw_a != raw_b
            raw_difference = first_difference(raw_a, raw_b)
        else:
            executed_changed = changed  # every command independently matched to a completed Agent job
            raw_difference = None
        improved = native[control_id]["native_success"] is False and native[treatment_id]["native_success"] is True
        check(identical_scene and (same or not same_policy), "paired identities/reset equality", pair_kind=kind)
        check(changed == expect_changed and executed_changed == expect_changed,
              "observable adoption or expected non-adoption", pair_kind=kind,
              changed=changed, executed_changed=executed_changed, expected=expect_changed)
        if expect_improvement:
            check(improved, "paired native success improvement", pair_kind=kind)
        pairs.append({"kind": kind, "control_id": control_id, "treatment_id": treatment_id,
            "same_seed_scene_config_initial_observation": identical_scene, "same_policy_sha": same,
            "control_policy_sha256": control["policy_sha256"], "treatment_policy_sha256": treatment["policy_sha256"],
            "observable_robot_command_change": changed, "executed_control_change": executed_changed,
            "first_command_divergence": first_difference(a, b), "first_raw_control_divergence": raw_difference,
            "guidance_content_sha256": digest(treatment["attention_input"]),
            "guidance_content": treatment["attention_input"],
            "control_native_success": native[control_id]["native_success"],
            "treatment_native_success": native[treatment_id]["native_success"],
            "native_improved": improved, "expected_observable_adoption": expect_changed})

    for suite in ("robosuite", "robocasa"):
        base = f"adoption-v1-{suite}-"
        pair(base + "old-baseline", base + "no-guidance", suite + " unchanged no-guidance baseline",
             False, same_policy=False)
        pair(base + "no-guidance", base + "content-ablation", suite + " content ablation", False)
        for kind in ("hint", "demo", "memory"):
            # Retain the v1 RoboCasa Memory failure; ignored cadence is not adoption.
            expected = not (suite == "robocasa" and kind == "memory")
            pair(base + "no-guidance", base + kind, suite + " " + kind + " v1", expected,
                 expect_improvement=(suite == "robosuite"))
    pair("adoption-v1-robocasa-old-baseline", "adoption-v1-robocasa-no-guidance-v1-1",
         "robocasa v1.1 unchanged no-guidance baseline", False, same_policy=False)
    pair("adoption-v1-robocasa-no-guidance-v1-1", "adoption-v1-robocasa-memory-v1-1",
         "robocasa memory v1.1", True)
    pair("adoption-v1-scheduler-hint-1", "adoption-v1-scheduler-hint-2",
         "full planner answered hint consumed by next attempt", True, expect_improvement=True)
    pair("adoption-v1-scheduler-memory-0", "adoption-v1-scheduler-memory-1",
         "trusted exact Memory grant consumed by next attempt (autonomous development)", True,
         expect_improvement=True)

    hint = read(OUT / "scheduler/hint/summary.json")
    request_row = hint["requests"][0]
    with sqlite3.connect(f"file:{hint['store']}?mode=ro", uri=True) as db:
        req = json.loads(db.execute("SELECT payload FROM requests WHERE id=?", (request_row["request_id"],)).fetchone()[0])
        response = json.loads(db.execute("SELECT payload FROM responses WHERE id=?", (request_row["response_id"],)).fetchone()[0])
        links = [json.loads(r[0]) for r in db.execute("SELECT payload FROM events WHERE event_type='response.execution_linked'")]
    treatment = traces["adoption-v1-scheduler-hint-2"]
    check(req["state"] == "answered" and req["response_id"] == response["response_id"]
          and treatment["attention_input"]["advisor_guidance"] == request_row["advice"]["guidance"]
          and hint["policy_id"] == "full_trace_aware_attention_planner"
          and any(link.get("execution_id") == hint["attempts"][2]["attention_trace"]["execution_id"] for link in links),
          "full Trace-aware request/answer/execution linkage")
    memory = read(OUT / "scheduler/memory/summary.json")
    treatment = traces["adoption-v1-scheduler-memory-1"]
    attempt_id = treatment["attempt_id"]
    with sqlite3.connect(f"file:{memory['store']}?mode=ro", uri=True) as db:
        grants = [json.loads(r[0]) for r in db.execute("SELECT payload FROM memory_v2_use_grants WHERE attempt_id=?", (attempt_id,))]
        uses = [json.loads(r[0]) for r in db.execute("SELECT payload FROM memory_uses") if json.loads(r[0])["attempt_id"] == attempt_id]
        records = {r[0]: json.loads(r[1]) for r in db.execute("SELECT id,payload FROM memories")}
    check(len(grants) == len(uses) == 1 and grants[0]["version"] == uses[0]["memory_version"] == 1
          and grants[0]["context"] == memory["memory_context"]
          and records[grants[0]["memory_id"]]["status"] == "trusted"
          and treatment["attention_input"]["memory_guidance"][grants[0]["memory_id"]] == records[grants[0]["memory_id"]]["guidance"]
          and uses[0]["outcome"] == "native_success", "actual exact-scope trusted-v1 Memory grant/content/use provenance")
    demo = read(OUT / "scheduler/demo/summary.json")
    check(demo["policy_id"] == "demo_first" and demo["demo_receipt"] is not None
          and same_demo_content(traces["adoption-v1-scheduler-demo-0"]["attention_input"]["demo_prior"],
                                (OUT / "inputs/robosuite_demo_projection.txt").read_text()),
          "verified demo projection reached actual control")
    check(any(p["native_improved"] and p["executed_control_change"] for p in pairs),
          "at least one guidance/action/native-success causal chain")
    failures = [c for c in checks if not c["pass"]]
    result = {"schema": "attentionbench.guidance-adoption-evidence-audit.v1.1",
        "audit_role": "author-produced read-only mechanical audit; no independent reviewer claim",
        "bounded_engineering_acceptance": "PASS" if not failures else "FAIL",
        "formal_eligible": False, "new_external_approval": "pending",
        "heldout_executions": 0, "effect_matrix_executions": 0,
        "attempts_retained": len(ledger["rows"]), "unsafe_attempts_total": sum(r.get("unsafe_attempts", 0) for r in ledger["rows"]),
        "checks": checks, "failures": failures, "pairs": pairs, "attempt_evidence": evidence,
        "hint_provenance": {"request": req, "response_id": response["response_id"], "execution_links": links,
                            "actual_provider": "deterministic development transport, no online GLM call"},
        "memory_provenance": {"attention_policy": memory["policy_id"], "grants": grants, "uses": uses,
                              "full_condition_memory_selection_not_claimed": True},
        "limitations": ["one development seed, no population effect estimate",
                        "authored SDK demo prior, not a successful demonstration recording",
                        "exact phrase/directive compiler, unsupported language is exposure only",
                        "RoboCasa v1 Memory changed trace cadence but not completed backend commands; retained non-adoption",
                        "RoboCasa v1.1 Memory changes base action but native success remains false; downstream reachability unresolved",
                        "RoboCasa hint/demo real-Service evidence belongs to v1; v1.1 changes only Memory phrase recipe",
                        "RoboCasa temporary job wrappers can be removed by existing recorder cleanup; completed-job logs, Safety samples and four primary artifacts retained",
                        "full planner generic-failure Memory selection left unchanged; actual Memory scheduler proof uses existing autonomous development route"]}
    (OUT / "evidence_audit_v1_1.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"acceptance": result["bounded_engineering_acceptance"], "checks": len(checks),
                      "failed_checks": failures, "attempts": len(ledger["rows"]),
                      "audit_sha256": sha(OUT / "evidence_audit_v1_1.json")}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    audit()
