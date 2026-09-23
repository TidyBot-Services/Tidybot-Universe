"""Generate deterministic evidence for the seven policy contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .core.policies import DecisionAction, POLICY_IDS, PolicyContext, build_policy


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "benchmarks/attention_harness/protocol/v1/evidence/attention_core_report.json"


def validate() -> dict:
    rows = []
    for policy_id in POLICY_IDS:
        config = (
            {"target_request_count": 3, "total_failure_slots": 8, "seed": 41}
            if policy_id == "budget_matched_random_escalation"
            else {"k": 2} if policy_id == "retry_k_then_ask" else {}
        )
        policy = build_policy(policy_id, **config)
        decisions = []
        for failure_index in range(8):
            decision = policy.decide(
                PolicyContext(
                    run_id=f"scripted-{policy_id}",
                    attempt_index=failure_index,
                    failure_index=failure_index,
                    consecutive_failures=failure_index + 1,
                    assistance_remaining=8,
                    evidence_count=1,
                    has_hypothesis=True,
                    demo_available=failure_index == 0,
                )
            )
            decisions.append(
                {
                    "failure_index": failure_index,
                    "action": decision.action.value,
                    "request_type": decision.request_type.value if decision.request_type else None,
                    "reason": decision.reason,
                }
            )
        request_count = sum(item["action"] == DecisionAction.REQUEST.value for item in decisions)
        row_passed = True
        if policy_id in {"autonomous", "demo_first"}:
            row_passed = request_count == 0
        elif policy_id == "budget_matched_random_escalation":
            row_passed = request_count == 3
        elif policy_id == "trace_aware_hint_only":
            row_passed = all(
                item["request_type"] in {None, "hint"} for item in decisions
            )
        rows.append(
            {
                "policy_id": policy_id,
                "request_count": request_count,
                "decisions": decisions,
                "passed": row_passed,
            }
        )
    return {
        "schema_version": "attentionbench.core-validation.v1",
        "source": "scripted failures only; not experiment data",
        "policies": rows,
        "passed": len(rows) == 7 and all(row["passed"] for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = validate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "policies": len(report["policies"])}, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
