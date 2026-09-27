"""Run the five seed-101 RoboCasa formal-path engineering probes."""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .formal_services import simulator_runtime_identity, source_identity


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim-source-root", type=Path, required=True)
    parser.add_argument("--agent-source-root", type=Path, required=True)
    parser.add_argument("--task-source-root", type=Path, required=True)
    parser.add_argument("--sim-python", type=Path, required=True)
    parser.add_argument("--agent-python", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--case", choices=("normal", "timeout", "cancel", "action_cancel", "safety_reject"),
                        help="rerun one probe and replace its row in the existing report")
    args = parser.parse_args()
    harness = Path(__file__).resolve().parents[1]
    repo = harness.parents[1]
    config = harness / "protocol/v2/formal_robocasa_counter_to_sink_seed101.json"
    approved = json.loads(config.read_text(encoding="utf-8"))
    source_files = (
        "formal_runner_boundary.py", "sim_gt_attention_run.py",
        "robocasa_native/formal_runner.py", "robocasa_native/formal_services.py",
        "robocasa_native/formal_cli.py", "robocasa_native/formal_acceptance.py",
        "robocasa_native/agent_actions.py", "robocasa_native/client.py",
        "robocasa_native/gt_perception.py", "robocasa_native/mobile_sdk.py",
        "robocasa_native/safety_monitor.py", "robosuite_memory/formal_sandbox.py",
        "robosuite_memory/formal_policy_worker.py",
    )
    harness_sources = {name: _sha(harness / name) for name in source_files}
    cases = (
        ("normal", "robocasa_noop.py", "completed", "normal_cleanup", 180, None),
        ("timeout", "robocasa_spin_timeout.py", "timeout", "episode_deadline", 65, None),
        ("cancel", "robocasa_spin_timeout.py", "cancelled", "operator_cancel", 180, 0.5),
        ("action_cancel", "robocasa_arm_delta_smoke.py", "cancelled", "operator_cancel", 180, 1.0),
        ("safety_reject", "formal_robosuite_safety_reject.py", "failed", "normal_cleanup", 180, None),
    )
    report = {
        "schema_version": "attentionbench.robocasa-formal-acceptance.v1",
        "suite": "robocasa", "task_id": approved["task_id"], "seed": approved["seed"],
        "formal_eligible": False,
        "harness_branch": _git(repo, "branch", "--show-current"),
        "harness_head": _git(repo, "rev-parse", "HEAD"),
        "harness_dirty": bool(_git(repo, "status", "--porcelain")),
        "sim_source_root": str(args.sim_source_root.resolve()),
        "agent_source_root": str(args.agent_source_root.resolve()),
        "task_source_root": str(args.task_source_root.resolve()),
        "sim_source": source_identity(args.sim_source_root),
        "agent_source": source_identity(args.agent_source_root),
        "task_source": source_identity(args.task_source_root),
        "sim_runtime": simulator_runtime_identity(args.sim_python, args.task_source_root),
        "config_uri": str(config.resolve()), "config_sha256": _sha(config),
        "harness_source_sha256": harness_sources,
        "cases": [],
    }
    if args.case and args.evidence.is_file():
        previous = json.loads(args.evidence.read_text(encoding="utf-8"))
        if (previous.get("schema_version") != report["schema_version"]
                or previous.get("config_sha256") != report["config_sha256"]
                or previous.get("harness_source_sha256") != harness_sources):
            raise ValueError("cannot resume a different RoboCasa acceptance report")
        report["cases"] = [item for item in previous["cases"] if item["name"] != args.case]
    if (report["sim_source"]["tree_sha256"] != approved["sim_service"]["tree_sha256"]
            or report["agent_source"]["tree_sha256"] != approved["agent_service"]["tree_sha256"]
            or report["task_source"]["tree_sha256"] != approved["task_source"]["tree_sha256"]
            or report["sim_runtime"] != approved["sim_runtime"]):
        raise ValueError("Service working trees changed since config approval")
    for name, filename, expected_status, expected_stop, deadline, cancel_after in cases:
        if args.case and name != args.case:
            continue
        if any(_sha(harness / source) != digest for source, digest in harness_sources.items()):
            raise RuntimeError("AttentionHarness source changed during acceptance")
        policy = harness / "protocol/v2/policies" / filename
        command = [
            sys.executable, "-m", "benchmarks.attention_harness.robocasa_native.formal_cli",
            "--task", approved["task_id"], "--seed", str(approved["seed"]),
            "--code", str(policy), "--approved-policy-sha256", _sha(policy),
            "--config", str(config), "--approved-config-sha256", _sha(config),
            "--sim-source-root", str(args.sim_source_root),
            "--agent-source-root", str(args.agent_source_root),
            "--task-source-root", str(args.task_source_root),
            "--sim-python", str(args.sim_python), "--agent-python", str(args.agent_python),
            "--artifact-root", str(args.artifact_root),
            "--overall-deadline-seconds", str(deadline),
        ]
        if cancel_after is not None:
            command += ["--cancel-after-policy-start-seconds", str(cancel_after)]
        completed = subprocess.run(command, cwd=repo, capture_output=True, text=True,
                                   timeout=deadline + 25, check=False)
        case = {"name": name, "command": shlex.join(command),
                "exit_code": completed.returncode, "stderr": completed.stderr[-2000:],
                "checks": {}, "passed": False}
        try:
            result = json.loads(completed.stdout)
            artifact_dir = Path(result["artifact_dir"])
            trace = json.loads((artifact_dir / "trace.json").read_text())
            safety = json.loads((artifact_dir / "safety.json").read_text())
            sandbox = json.loads((artifact_dir / "sandbox_receipt.json").read_text())
            native = json.loads((artifact_dir / "native_result.json").read_text())
            saved_result_path = artifact_dir / "result.json"
            saved_result = json.loads(saved_result_path.read_text())
            stop = sandbox["service_stop"]
            violations = [item["kind"] for item in safety["violations"]]
            checks = {
                "status": result["status"] == expected_status,
                "boundary_checked": result["boundary_checked"] is True,
                "formal_ineligible": result["formal_eligible"] is False,
                "service_stopped": stop["reason"] == expected_stop
                    and set(stop["services"]) == {"agent", "simulator"}
                    and all(item["leader_reaped"] and item["process_group_gone"]
                            for item in stop["services"].values()),
                "sandbox_receipt": all(result["sandbox"].values())
                    and sandbox["probe"]["host_home_visible"] is False
                    and sandbox["probe"]["host_root_visible"] is False
                    and sandbox["probe"]["network_reachable"] is False
                    and sandbox["probe"]["worker_sha256"] == _sha(
                        harness / "robosuite_memory/formal_policy_worker.py"),
                "artifact_hashes": all(_sha(Path(ref["uri"])) == ref["sha256"]
                                       for ref in result["artifacts"].values()),
                "harness_source_unchanged": all(
                    _sha(harness / source) == digest
                    for source, digest in harness_sources.items()
                ),
                "task_source_attested": sandbox["task_source"] == approved["task_source"]
                    and sandbox["sim_runtime"] == approved["sim_runtime"]
                    and sandbox["service_source_unchanged"] is True,
                "run_artifact": all(saved_result[key] == result[key] for key in (
                    "suite", "task_id", "seed", "status", "native_success",
                    "policy_sha256", "config_sha256", "artifacts",
                )),
                "approved_digests": sandbox["source_sha256_before"] == _sha(policy)
                    and sandbox["source_sha256_after"] == _sha(policy)
                    and sandbox["config_sha256_after"] == _sha(config),
                "reset_attestation": trace["reset_attestation"] == {
                    key: approved[key] for key in ("scene_id", "object_set_id")
                } and trace["camera_attestation"] == approved["camera_names"]
                    and trace["language_attestation"] == approved["task_prompt"],
                "native_result": native["native_success"] is False
                    and native["evaluated"] is (name in {"normal", "safety_reject"})
                    and native["source"] == "robocasa/task/success",
                "independent_safety_source": safety["source"] == "independent_safety_monitor",
                "safety": (not violations if name == "normal"
                           else "delta_exceeds_limit" in violations if name == "safety_reject"
                           else True),
                "action_cancel_receipt": (
                    any(item.get("event_type") == "sdk.arm_command"
                        for item in trace["sdk_events"])
                    and any(item.get("kind") == "move_arm_delta" for item in safety["events"])
                    and any(item.get("job_status") == "cancelled"
                            and item.get("execution_status") == "stopped"
                            for item in sandbox["action_cancellation_receipts"])
                ) if name == "action_cancel" else True,
            }
            case.update({
                "artifact_dir": str(artifact_dir.resolve()),
                "run_artifact": {"uri": str(saved_result_path.resolve()),
                                 "sha256": _sha(saved_result_path)},
                "artifacts": result["artifacts"], "observed_status": result["status"],
                "native_success": result["native_success"], "service_stop": stop,
                "safety_violations": violations, "checks": checks,
                "passed": completed.returncode == 0 and all(checks.values()),
            })
        except Exception as exc:
            case["error"] = f"{type(exc).__name__}: {exc}"
            case["stdout_tail"] = completed.stdout[-1000:]
        report["cases"].append(case)
        args.evidence.parent.mkdir(parents=True, exist_ok=True)
        args.evidence.write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                            sort_keys=True) + "\n")
    report["cases"].sort(key=lambda item: [case[0] for case in cases].index(item["name"]))
    report["all_seed101_probes_passed"] = (
        len(report["cases"]) == len(cases)
        and all(item["passed"] for item in report["cases"])
    )
    args.evidence.write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                        sort_keys=True) + "\n")
    print(json.dumps({"all_seed101_probes_passed": report["all_seed101_probes_passed"],
                      "evidence": str(args.evidence.resolve()),
                      "cases": [(item["name"], item["passed"]) for item in report["cases"]]},
                     ensure_ascii=False))
    return 0 if report["all_seed101_probes_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
