"""Run the four independent seed-101 Robosuite formal-path probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service-source-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    harness = Path(__file__).resolve().parents[1]
    repo = harness.parents[1]
    config = harness / "protocol/v2/formal_robosuite_cube_lift_seed101.json"
    service_revision = _git(args.service_source_root, "rev-parse", "HEAD")
    cases = (
        ("normal", "formal_robosuite_normal.py", "completed", "normal_cleanup", 90, None),
        ("timeout", "formal_robosuite_long_action.py", "timeout", "episode_deadline", 5, None),
        ("cancel", "formal_robosuite_long_action.py", "cancelled", "operator_cancel", 30, 1),
        ("safety_reject", "formal_robosuite_safety_reject.py", "failed", "normal_cleanup", 30, None),
    )
    report = {
        "schema_version": "attentionbench.robosuite-formal-acceptance.v1",
        "suite": "robosuite", "task_id": "cube_lift", "seed": 101,
        "formal_eligible": False,
        "harness_branch": _git(repo, "branch", "--show-current"),
        "harness_head": _git(repo, "rev-parse", "HEAD"),
        "harness_dirty": bool(_git(repo, "status", "--porcelain")),
        "service_root": str(args.service_source_root.resolve()),
        "service_revision": service_revision,
        "service_clean": not bool(_git(args.service_source_root, "status", "--porcelain")),
        "config_uri": str(config), "config_sha256": _sha(config),
        "cases": [],
    }
    for name, filename, expected_status, expected_stop, deadline, cancel_after in cases:
        policy = harness / "protocol/v2/policies" / filename
        command = [
            sys.executable, "-m", "benchmarks.attention_harness.robosuite_memory.formal_cli",
            "--task", "cube_lift", "--seed", "101",
            "--code", str(policy), "--approved-policy-sha256", _sha(policy),
            "--config", str(config), "--approved-config-sha256", _sha(config),
            "--service-source-root", str(args.service_source_root),
            "--artifact-root", str(args.artifact_root),
            "--overall-deadline-seconds", str(deadline),
        ]
        if cancel_after is not None:
            command += ["--cancel-after-policy-start-seconds", str(cancel_after)]
        completed = subprocess.run(command, cwd=repo, capture_output=True, text=True,
                                   timeout=deadline + 20, check=False)
        case = {
            "name": name, "command": shlex.join(command),
            "exit_code": completed.returncode,
            "stderr": completed.stderr[-2000:],
            "checks": {}, "passed": False,
        }
        try:
            result = json.loads(completed.stdout)
            artifact_dir = Path(result["artifact_dir"])
            trace = json.loads((artifact_dir / "trace.json").read_text())
            safety = json.loads((artifact_dir / "safety.json").read_text())
            sandbox = json.loads((artifact_dir / "sandbox_receipt.json").read_text())
            native = json.loads((artifact_dir / "native_result.json").read_text())
            hashes_valid = all(
                _sha(Path(ref["uri"])) == ref["sha256"]
                for ref in result["artifacts"].values()
            )
            stop = sandbox["service_stop"]
            violations = [item["kind"] for item in safety["violations"]]
            approved_config = json.loads(config.read_text())
            checks = {
                "status": result["status"] == expected_status,
                "boundary_checked": result["boundary_checked"] is True,
                "formal_ineligible": result["formal_eligible"] is False,
                "service_revision": result["service_revision"] == service_revision,
                "service_stopped": stop["reason"] == expected_stop
                                   and stop["leader_reaped"] is True
                                   and stop["process_group_gone"] is True
                                   and Path(stop["service_module_origin"]).resolve().is_relative_to(
                                       args.service_source_root.resolve()),
                "sandbox_receipt": all(result["sandbox"].values())
                                   and sandbox["probe"]["host_home_visible"] is False
                                   and sandbox["probe"]["host_root_visible"] is False
                                   and sandbox["probe"]["network_reachable"] is False
                                   and sandbox["probe"]["worker_sha256"] == _sha(
                                       harness / "robosuite_memory/formal_policy_worker.py"),
                "artifact_hashes": hashes_valid,
                "approved_digests": sandbox["source_sha256_before"] == _sha(policy)
                                    and sandbox["source_sha256_after"] == _sha(policy)
                                    and sandbox["config_sha256_after"] == _sha(config),
                "reset_attestation": trace["reset_attestation"] == {
                    key: approved_config[key] for key in ("scene_id", "object_set_id")
                },
                "native_result": native["native_success"] is False
                                 and native["evaluated"] is (name in {"normal", "safety_reject"})
                                 and native["source"] == "robosuite_sim/v1/success",
                "independent_safety_source": safety["source"] == "independent_safety_monitor",
                "safety": (violations == [] if name == "normal"
                           else "delta_exceeds_limit" in violations if name == "safety_reject"
                           else "action_outcome_unknown" in violations),
                "action_trace": (len(trace["backend_steps"]) == 0 if name == "safety_reject"
                                 else len(trace["backend_steps"]) > 0),
            }
            case.update({
                "artifact_dir": str(artifact_dir.resolve()),
                "artifacts": result["artifacts"],
                "observed_status": result["status"],
                "native_success": result["native_success"],
                "service_stop": stop,
                "backend_step_count": len(trace["backend_steps"]),
                "safety_violations": violations,
                "checks": checks,
                "passed": completed.returncode == 0 and all(checks.values()),
            })
        except Exception as exc:
            case["error"] = f"{type(exc).__name__}: {exc}"
            case["stdout_tail"] = completed.stdout[-1000:]
        report["cases"].append(case)
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                            sort_keys=True) + "\n")
    report["all_seed101_probes_passed"] = all(item["passed"] for item in report["cases"])
    args.evidence.write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                        sort_keys=True) + "\n")
    print(json.dumps({"all_seed101_probes_passed": report["all_seed101_probes_passed"],
                      "evidence": str(args.evidence.resolve()),
                      "cases": [(item["name"], item["passed"]) for item in report["cases"]]},
                     ensure_ascii=False))
    return 0 if report["all_seed101_probes_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
