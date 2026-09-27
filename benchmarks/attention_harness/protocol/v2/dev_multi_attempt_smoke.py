"""Two-attempt real-Service engineering smoke with a labelled Advisor fixture.

This is never a formal result. It exercises the real dedicated suite Service,
native evaluator and persisted Trace/Advisor chain without depending on GLM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import traceback
from pathlib import Path
from uuid import uuid4

from benchmarks.attention_harness.core.store import AttentionStore
from benchmarks.attention_harness.formal_attention_run import run_formal_attention
from benchmarks.attention_harness.robocasa_native.formal_runner import RobocasaFormalSuiteRunner
from benchmarks.attention_harness.robosuite_memory.formal_runner import RobosuiteFormalSuiteRunner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("robocasa", "robosuite"), required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--overall-deadline-seconds", type=float, required=True)
    parser.add_argument("--service-source-root", type=Path)
    parser.add_argument("--sim-source-root", type=Path)
    parser.add_argument("--agent-source-root", type=Path)
    parser.add_argument("--task-source-root", type=Path)
    parser.add_argument("--sim-python", type=Path)
    parser.add_argument("--agent-python", type=Path)
    args = parser.parse_args()
    args.artifact_root.mkdir(parents=True, exist_ok=True)
    manifest_path = args.artifact_root / f"smoke_manifest-{uuid4().hex[:12]}.json"
    manifest = {"schema_version": "attentionbench.real-service-multi-attempt-smoke.v1",
                "command_argv": [sys.executable, *sys.argv], "suite": args.suite,
                "task": args.task, "seed": args.seed, "formal_eligible": False,
                "advisor_source": "deterministic_test_transport", "attempt_limit": 2,
                "advisor_requests": []}

    def transport(request):
        payload = json.loads(request["messages"][1]["content"])
        packet = payload["trace_packet"]
        manifest["advisor_requests"].append({
            "request_type": payload["request_type"],
            "trace_id": packet["trace_id"],
            "hypothesis_status": packet.get("hypothesis_status"),
            "code_sha256": packet.get("code", {}).get("sha256"),
            "failure_history": packet.get("failure_history", []),
            "evidence": [{"sha256": item["sha256"], "kind": item["kind"]}
                         for item in packet["evidence"]],
            "private_state_keys_absent": all(key not in json.dumps(packet)
                                             for key in ("native_success", "simulator_state",
                                                         "oracle_state", "evaluator_verdict")),
        })
        return json.dumps({"schema_version": "attentionbench.advisor-advice.v1",
                           "request_type": payload["request_type"],
                           "diagnosis": "The visible trace records an incomplete attempt.",
                           "guidance": "Review the visible failure and retry within the approved policy.",
                           "caution": "Do not exceed the independent safety limits.",
                           "confidence": 0.3})

    if args.suite == "robosuite":
        if args.service_source_root is None:
            parser.error("Robosuite requires --service-source-root")
        runner = RobosuiteFormalSuiteRunner(service_source_root=args.service_source_root)
    else:
        required = ("sim_source_root", "agent_source_root", "task_source_root",
                    "sim_python", "agent_python")
        if any(getattr(args, field) is None for field in required):
            parser.error("RoboCasa requires both Service roots, task root and Python paths")
        runner = RobocasaFormalSuiteRunner(
            sim_source_root=args.sim_source_root, agent_source_root=args.agent_source_root,
            task_source_root=args.task_source_root, sim_python=args.sim_python,
            agent_python=args.agent_python)
    try:
        summary = run_formal_attention(
            suite=args.suite, task_id=args.task, seed=args.seed,
            artifact_root=args.artifact_root, policy_id="reactive_help",
            policy_code_path=args.code,
            approved_policy_sha256=hashlib.sha256(args.code.read_bytes()).hexdigest(),
            config_path=args.config,
            approved_config_sha256=hashlib.sha256(args.config.read_bytes()).hexdigest(),
            runner=runner, overall_deadline_seconds=args.overall_deadline_seconds,
            max_attempts=2, assistance_credits=1, advisor_transport=transport,
            sleeper=lambda _: None,
        )
        store = AttentionStore(Path(summary["store"]))
        manifest["run_artifact"] = str(Path(summary["artifact_dir"]) / "attention_run.json")
        manifest["run_id"] = f"run:{Path(summary['artifact_dir']).name}"
        manifest["attempts"] = []
        for item in summary["attempts"]:
            link = item["attention_trace"]
            trace = store.get_trace(link["advisor_trace_id"]) if link["advisor_trace_id"] else None
            receipt_path = Path(item["formal_runner_result"]["artifacts"]["sandbox_receipt"]["uri"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            manifest["attempts"].append({
                "attempt_id": link["attempt_id"], "status": item["status"],
                "native_success": item["native_success"],
                "raw_trace_id": link["raw_trace_id"], "advisor_trace_id": link["advisor_trace_id"],
                "failure": trace.get("failure") if trace else None,
                "formal_artifacts": item["formal_runner_result"]["artifacts"],
                "safety_artifact": item["safety_artifact"],
                "service_stop": receipt.get("service_stop"),
            })
        manifest["requests"] = summary["requests"]
        manifest["stopped_reason"] = summary["stopped_reason"]
        manifest["status"] = "completed"
    except Exception as error:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(error).__name__}: {error}"
        manifest["traceback"] = traceback.format_exc()[-8000:]
        raise
    finally:
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False,
                                            sort_keys=True) + "\n", encoding="utf-8")
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
