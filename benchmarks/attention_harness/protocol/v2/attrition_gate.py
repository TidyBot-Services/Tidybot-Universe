#!/usr/bin/env python3
"""Fail-closed, read-only admission check for a predeclared comparison grid.

The plan and ledger hashes must be supplied by an independent reviewer.  A pass
means only that this evidence has no selective attrition or unreviewed depth
recovery; it does not approve a policy, task, simulator, or statistical effect.
"""
import argparse
import hashlib
import json
from collections import Counter
from itertools import product
from pathlib import Path

from benchmarks.attention_harness.protocol.v2.depth_recovery_evidence import verify_recovery

ARTIFACTS = {
    "native_result": "native_result.json",
    "safety": "safety.json",
    "sandbox_receipt": "sandbox_receipt.json",
    "trace": "trace.json",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def identity(row):
    return tuple(row.get(k) for k in ("suite", "task_id", "seed", "condition"))


def trace_condition(trace):
    """Resolve a formal condition only from its hash-bound M1 identity."""
    if trace.get("schema_version") != "attentionbench.formal-trace.v1":
        return trace.get("condition")
    lock = trace.get("entry_lock")
    if not isinstance(lock, dict):
        raise ValueError("formal trace entry lock missing")
    payload = dict(lock)
    digest = payload.pop("sha256", None)
    actual = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")).encode()).hexdigest()
    if (digest != actual or lock.get("schema_version") != "attentionbench.formal-entry-lock.v1"
            or any(lock.get(k) != trace.get(k) for k in ("suite", "task_id", "seed"))
            or lock.get("approved_policy_sha256") != trace.get("policy_sha256")
            or lock.get("approved_config_sha256") != trace.get("config_sha256")
            or not isinstance(lock.get("attention_policy"), str)
            or not lock["attention_policy"]):
        raise ValueError("formal trace entry identity invalid")
    condition = lock["attention_policy"]
    if "condition" in trace and trace["condition"] != condition:
        raise ValueError("formal trace condition conflicts with entry lock")
    return condition


def assess(plan_path, ledger_path, expected_plan_sha, expected_ledger_sha,
           recovery_review_path=None, expected_recovery_review_sha=None):
    plan_path, ledger_path = Path(plan_path), Path(ledger_path)
    issues = []
    if sha(plan_path) != expected_plan_sha:
        issues.append("plan_sha_mismatch")
    if sha(ledger_path) != expected_ledger_sha:
        issues.append("ledger_sha_mismatch")
    plan, ledger = read(plan_path), read(ledger_path)
    planned = plan.get("slots", [])
    recorded = ledger.get("rows", [])
    # No deduplication: a duplicate is an invalid result, even if another row
    # has an apparently good outcome.
    pcount, rcount = Counter(map(identity, planned)), Counter(map(identity, recorded))
    grid = plan.get("grid", {})
    tasks, seeds, conditions = (grid.get(k) for k in ("tasks", "seeds", "conditions"))
    if (not isinstance(tasks, list) or not isinstance(seeds, list) or
            not isinstance(conditions, list) or not tasks or not seeds or not conditions):
        issues.append("declared_cartesian_grid_missing")
    else:
        expected_grid = Counter((suite, task, seed, condition)
                                for (suite, task), seed, condition
                                in product((tuple(t) for t in tasks), seeds, conditions))
        if pcount != expected_grid:
            issues.append("planned_grid_mismatch")
    if not planned or any(n != 1 for n in pcount.values()):
        issues.append("empty_or_duplicate_plan")
    if pcount != rcount:
        issues.append("missing_extra_or_duplicate_recorded_slot")
    if len(planned) != len(recorded):
        issues.append("cardinality_mismatch")
    if ledger.get("plan_sha256") != expected_plan_sha:
        issues.append("ledger_plan_sha_mismatch")
    if not plan.get("predeclared_before_runs", False):
        issues.append("plan_predeclaration_unattested")

    reviewed_recoveries = {}
    used_recoveries = set()
    if recovery_review_path is not None or expected_recovery_review_sha is not None:
        try:
            if not expected_recovery_review_sha or sha(recovery_review_path) != expected_recovery_review_sha:
                raise ValueError("review SHA mismatch")
            review = read(recovery_review_path)
            if (review.get("schema_version") != "attentionbench.depth-recovery-review.v1"
                    or review.get("reviewer_role") != "independent_auditor"
                    or not review.get("reviewer_id")
                    or review.get("plan_sha256") != expected_plan_sha
                    or review.get("ledger_sha256") != expected_ledger_sha):
                raise ValueError("review authority or frozen grid mismatch")
            for item in review.get("recoveries", []):
                key = (item.get("attempt_id"), item.get("step"))
                if key in reviewed_recoveries or item.get("approved") is not True:
                    raise ValueError("duplicate or unapproved review")
                reviewed_recoveries[key] = item
        except Exception as exc:
            issues.append("invalid_recovery_review:" + str(exc))
            reviewed_recoveries = {}
    outcomes = []
    owners = {"run_id": {}, "attempt_id": {}, "artifact_dir": {}, "trace_sha256": {}}

    def register(kind, value, owner):
        if not value:
            issues.append(f"{owner}:missing_{kind}")
        elif value in owners[kind]:
            issues.append(f"{owner}:reused_{kind}")
        else:
            owners[kind][value] = owner

    for slot in planned:
        key = identity(slot)
        matches = [r for r in recorded if identity(r) == key]
        if len(matches) != 1:
            continue
        row = matches[0]
        label = f"{slot.get('suite')}:{slot.get('task_id')}:{slot.get('seed')}:{slot.get('condition')}"
        if (slot.get("order") != row.get("order") or
                not slot.get("policy_sha256") or not slot.get("config_sha256") or
                not slot.get("service_revision") or
                not slot.get("artifact_root")):
            issues.append(f"{label}:plan_identity_or_version_missing")
        if row.get("invalid_reasons") or row.get("case_complete") is not True:
            issues.append(f"{label}:invalid_case")
        audit_path = Path(row.get("audit_path", "/missing"))
        if not audit_path.is_file() or sha(audit_path) != row.get("audit_sha256"):
            issues.append(f"{label}:case_audit_hash")
            continue
        audit = read(audit_path)
        if (audit.get("suite"), audit.get("task_id"), audit.get("seed")) != key[:3] or audit.get("order") != slot.get("order"):
            issues.append(f"{label}:case_audit_identity")
        if audit.get("condition") != key[3]:
            issues.append(f"{label}:case_audit_condition")
        if (audit.get("profile_case_complete", audit.get("chain_case_complete")) is not True or
                audit.get("issues") or not audit.get("attempts") or
                audit.get("attempt_count") != len(audit["attempts"])):
            issues.append(f"{label}:case_audit_incomplete")
        attempts = audit.get("attempts", [])
        if sorted(a.get("index") for a in attempts if type(a.get("index")) is int) != list(range(len(attempts))):
            issues.append(f"{label}:attempt_index_grid")
        case_run_ids = set()
        for attempt in attempts:
            aid = f"{label}:attempt{attempt.get('index')}"
            if (attempt.get("issues") or attempt.get("status") != "completed" or
                    attempt.get("policy_error") is not None or
                    attempt.get("native_evaluated") is not True or
                    type(attempt.get("native_success")) is not bool or
                    attempt.get("safety_source") != "independent_safety_monitor" or
                    attempt.get("safety_unsafe_attempts") != 0 or
                    attempt.get("safety_violations")):
                issues.append(f"{aid}:invalid_attempt")
            bundle = Path(attempt.get("raw_trace_link", {}).get("bundle", "/missing"))
            if not bundle.is_file():
                issues.append(f"{aid}:missing_bundle")
            root = bundle.parent
            artifact_root = Path(slot.get("artifact_root") or "/missing").resolve()
            if not root.resolve().is_relative_to(artifact_root):
                issues.append(f"{aid}:artifact_root_mismatch")
            register("artifact_dir", str(root.resolve()), aid)
            register("attempt_id", attempt.get("raw_trace_link", {}).get("attempt_id"), aid)
            case_run_ids.add(attempt.get("raw_trace_link", {}).get("run_id"))
            documents = {}
            for name, filename in ARTIFACTS.items():
                path = root / filename
                if not path.is_file() or sha(path) != attempt.get("artifact_sha256", {}).get(name):
                    issues.append(f"{aid}:{name}_hash")
                else:
                    documents[name] = read(path)
            if len(documents) != 4:
                continue
            native, safety, receipt, trace = (documents[k] for k in ARTIFACTS)
            attempt_id = attempt.get("raw_trace_link", {}).get("attempt_id")
            if any(doc.get("attempt_id") != attempt_id for doc in documents.values()):
                issues.append(f"{aid}:attempt_id_mismatch")
            run_id = attempt.get("raw_trace_link", {}).get("run_id")
            if any(doc.get("run_id") != run_id for doc in documents.values()):
                issues.append(f"{aid}:run_id_mismatch")
            register("trace_sha256", attempt["artifact_sha256"]["trace"], aid)
            if (native.get("evaluated") is not True or
                    type(native.get("native_success")) is not bool or
                    native["native_success"] != attempt.get("native_success")):
                issues.append(f"{aid}:native_boolean")
            if (safety.get("source") != "independent_safety_monitor" or
                    safety.get("unsafe_attempts") != 0 or safety.get("violations")):
                issues.append(f"{aid}:independent_safety")
            if (trace.get("suite"), trace.get("task_id"), trace.get("seed")) != key[:3]:
                issues.append(f"{aid}:trace_identity")
            try:
                condition = trace_condition(trace)
            except ValueError as exc:
                condition = None
                issues.append(f"{aid}:trace_condition_identity:{exc}")
            if condition != key[3]:
                issues.append(f"{aid}:trace_condition")
            if (trace.get("policy_sha256") != slot.get("policy_sha256") or
                    trace.get("config_sha256") != slot.get("config_sha256") or
                    receipt.get("service_revision") != slot.get("service_revision") or
                    receipt.get("service_source_unchanged") is not True):
                issues.append(f"{aid}:version_mismatch")
            stop = receipt.get("service_stop", {})
            services = stop.get("services", {"simulator": stop})
            if not services or any(s.get("leader_reaped") is not True or
                                   s.get("process_group_gone") is not True
                                   for s in services.values()):
                issues.append(f"{aid}:service_not_reaped")
            steps = trace.get("backend_steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict) or "depth_recovery" not in step:
                        continue
                    recovery_key = (attempt_id, step.get("step"))
                    approval = reviewed_recoveries.get(recovery_key, {})
                    details = step["depth_recovery"]
                    if (approval.get("trace_sha256") != attempt["artifact_sha256"]["trace"]
                            or approval.get("service_revision") != slot.get("service_revision")
                            or approval.get("first_capture_sha256") != details.get("capture_sha256")
                            or approval.get("repeat_capture_sha256") != details.get("repeat_observation", {}).get("capture_sha256")
                            or approval.get("equivalence_sha256") != details.get("equivalence", {}).get("sha256")):
                        issues.append(f"{aid}:unreviewed_depth_recovery")
                    else:
                        used_recoveries.add(recovery_key)
                        issues.extend(f"{aid}:depth_recovery:{reason}" for reason in verify_recovery(details))
            if key[0] == "robosuite" and (not isinstance(steps, list) or not steps or
                                             any(not isinstance(step, dict) for step in steps)):
                issues.append(f"{aid}:backend_trace_missing")
            if trace.get("error") or trace.get("status") != "completed":
                issues.append(f"{aid}:trace_error")
        if len(case_run_ids) != 1:
            issues.append(f"{label}:case_run_id_inconsistent")
        else:
            register("run_id", next(iter(case_run_ids)), label)
        if type(row.get("native_success")) is not bool or row.get("native_success") != audit.get("native_success"):
            issues.append(f"{label}:case_native_boolean")
        if type(audit.get("native_success")) is not bool or audit.get("native_success") != any(
                a.get("native_success") is True for a in attempts):
            issues.append(f"{label}:native_aggregation_mismatch")
        outcomes.append({"identity": key, "native_success": row.get("native_success"),
                         "valid": not any(i.startswith(label + ":") for i in issues)})

    if set(reviewed_recoveries) != used_recoveries:
        issues.append("unused_or_unmatched_recovery_approval")
    return {"schema_version": "attentionbench.engineering.attrition-gate.v2",
            "recovery_review_sha256": expected_recovery_review_sha,
            "reviewed_recoveries": len(used_recoveries),
            "plan_sha256": sha(plan_path), "ledger_sha256": sha(ledger_path),
            "planned_slots": len(planned), "recorded_slots": len(recorded),
            "valid_slots": sum(r["valid"] for r in outcomes),
            "native_successes": sum(r["native_success"] is True for r in outcomes if r["valid"]),
            "valid_native_failures": sum(r["native_success"] is False for r in outcomes if r["valid"]),
            "issues": issues, "structural_attrition_gate": not issues,
            "formal_eligible": False,
            "effect_comparison_authorized": False}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--ledger", type=Path, required=True)
    ap.add_argument("--plan-sha256", required=True)
    ap.add_argument("--ledger-sha256", required=True)
    ap.add_argument("--recovery-review", type=Path)
    ap.add_argument("--recovery-review-sha256")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    report = assess(args.plan, args.ledger, args.plan_sha256, args.ledger_sha256,
                    args.recovery_review, args.recovery_review_sha256)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(args.output, "pass=" + str(report["structural_attrition_gate"]),
          "issues=" + str(len(report["issues"])))


if __name__ == "__main__":
    main()
