"""Seven-policy engineering run through each suite's dedicated formal runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from uuid import uuid4

from .attention_modes import AssistanceMode
from .core.policies import POLICY_IDS
from .formal_attention_run import run_formal_attention
from .formal_entry import inspect_formal_entry
from .parcc_client import ParccClient
from .robocasa_native.formal_runner import RobocasaFormalSuiteRunner
from .robosuite_memory.formal_runner import RobosuiteFormalSuiteRunner
from .v2_advisor import SimGTGLMAdvisorTransport


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("robocasa", "robosuite"), required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--attention-policy", choices=POLICY_IDS, required=True)
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--approved-policy-sha256", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--approved-config-sha256", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--store-path", type=Path)
    parser.add_argument("--memory-source-run", type=Path,
                        help="persisted source run whose attempts own trusted Memory evidence")
    parser.add_argument("--approved-memory-source-sha256")
    parser.add_argument("--summary-path", type=Path,
                        help="write a UI launch result before exiting, including native failure")
    parser.add_argument("--single-glm-call", action="store_true",
                        help="allow at most one provider HTTP call per Advisor request")
    parser.add_argument("--public-lift-progress-check", action="store_true",
                        help="project public SDK before/after lift evidence after guidance")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--assistance-credits", type=int, default=1)
    parser.add_argument("--token-limit", type=int, default=30000)
    parser.add_argument("--assistance-mode", choices=[mode.value for mode in AssistanceMode],
                        default=AssistanceMode.BENCHMARK_PROXY.value)
    parser.add_argument("--human-deadline-seconds", type=float, default=60.0)
    parser.add_argument("--overall-deadline-seconds", type=float, default=300.0)
    parser.add_argument("--policy-config", type=Path)
    parser.add_argument("--approved-policy-config-sha256")
    parser.add_argument("--expected-entry-sha256",
                        help="UI lock digest; reject if files or selected conditions changed")
    parser.add_argument("--demo-prior", type=Path)
    parser.add_argument("--approved-demo-sha256")
    parser.add_argument("--memory-context", type=Path)
    parser.add_argument("--approved-memory-context-sha256")
    parser.add_argument("--dev-generation-artifact", type=Path,
                        help="Dev-produced generation receipt bound to the approved source hash")
    parser.add_argument("--service-source-root", type=Path)
    parser.add_argument("--sim-source-root", type=Path)
    parser.add_argument("--agent-source-root", type=Path)
    parser.add_argument("--task-source-root", type=Path)
    parser.add_argument("--sim-python", type=Path)
    parser.add_argument("--agent-python", type=Path)
    args = parser.parse_args()
    if (args.memory_source_run is None) != (args.approved_memory_source_sha256 is None):
        parser.error("Memory source run and approved SHA-256 must be supplied together")
    memory_evidence_root = None
    if args.memory_source_run is not None:
        source = args.memory_source_run.resolve()
        if (source.name != "attention_run.json" or args.store_path is None
                or hashlib.sha256(source.read_bytes()).hexdigest()
                   != args.approved_memory_source_sha256):
            parser.error("approved Memory source run differs from persisted bytes")
        source_run = json.loads(source.read_text(encoding="utf-8"))
        if (source_run.get("store") != str(args.store_path.resolve())
                or source_run.get("artifact_dir") != str(source.parent)
                or source_run.get("formal_eligible") is not False
                or source_run.get("runner_boundary", {}).get("mode") != "formal"):
            parser.error("Memory source run identity or store differs")
        memory_evidence_root = source.parent / "attempts"
    if (args.memory_context is None) != (args.approved_memory_context_sha256 is None):
        parser.error("formal Memory context and approved digest must be supplied together")
    if args.memory_context is not None:
        if hashlib.sha256(args.memory_context.read_bytes()).hexdigest() != args.approved_memory_context_sha256:
            parser.error("formal Memory context differs from approved digest")
    dev_hypothesis = None
    dev_hypothesis_evidence = None
    if args.dev_generation_artifact is not None:
        receipt_bytes = args.dev_generation_artifact.read_bytes()
        generation = json.loads(receipt_bytes)
        if (not isinstance(generation, dict)
                or generation.get("sha256") != args.approved_policy_sha256
                or generation.get("source") != str(args.code.resolve())):
            parser.error("Dev generation receipt does not match approved source")
        dev_hypothesis = generation.get("hypothesis")
        if dev_hypothesis is not None and (not isinstance(dev_hypothesis, str)
                                           or not dev_hypothesis.strip()):
            parser.error("Dev generation receipt has an invalid hypothesis")
        dev_hypothesis_evidence = {"uri": str(args.dev_generation_artifact.resolve()),
                                   "sha256": hashlib.sha256(receipt_bytes).hexdigest()}
    if args.suite == "robosuite":
        if args.service_source_root is None:
            parser.error("Robosuite formal runner requires --service-source-root")
        runner = RobosuiteFormalSuiteRunner(service_source_root=args.service_source_root)
    else:
        required = ("sim_source_root", "agent_source_root", "task_source_root",
                    "sim_python", "agent_python")
        if any(getattr(args, key) is None for key in required):
            parser.error("RoboCasa formal runner requires both Service roots, task root and Python paths")
        runner = RobocasaFormalSuiteRunner(
            sim_source_root=args.sim_source_root,
            agent_source_root=args.agent_source_root,
            task_source_root=args.task_source_root,
            sim_python=args.sim_python, agent_python=args.agent_python,
        )
    try:
        entry_lock, policy_config = inspect_formal_entry(
            suite=args.suite, task_id=args.task, seed=args.seed,
            policy_id=args.attention_policy, code=args.code,
            approved_policy_sha256=args.approved_policy_sha256,
            config=args.config, approved_config_sha256=args.approved_config_sha256,
            max_attempts=args.max_attempts, assistance_credits=args.assistance_credits,
            token_limit=args.token_limit, assistance_mode=args.assistance_mode,
            human_deadline_seconds=args.human_deadline_seconds,
            overall_deadline_seconds=args.overall_deadline_seconds,
            demo_prior=args.demo_prior, approved_demo_sha256=args.approved_demo_sha256,
            policy_config=args.policy_config,
            approved_policy_config_sha256=args.approved_policy_config_sha256,
        )
        if (args.expected_entry_sha256 is not None
                and entry_lock["sha256"] != args.expected_entry_sha256):
            raise ValueError("formal entry changed since UI lock")
    except (ValueError, TypeError, PermissionError, OSError) as error:
        parser.error(str(error))
    # Snapshot source bytes before any Attention run is created. The formal
    # runners read these approved snapshots, not mutable operator paths.
    lock_dir = args.artifact_root / "entry-locks" / uuid4().hex
    lock_dir.mkdir(parents=True, exist_ok=False)
    locked_code = lock_dir / "policy.py"
    locked_config = lock_dir / "sim_config.json"
    locked_code.write_bytes(args.code.read_bytes())
    locked_config.write_bytes(args.config.read_bytes())
    if (hashlib.sha256(locked_code.read_bytes()).hexdigest() != args.approved_policy_sha256
            or hashlib.sha256(locked_config.read_bytes()).hexdigest() != args.approved_config_sha256):
        shutil.rmtree(lock_dir)
        parser.error("approved source changed during entry snapshot")
    (lock_dir / "entry_lock.json").write_text(
        json.dumps(entry_lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    result = run_formal_attention(
        suite=args.suite, task_id=args.task, seed=args.seed,
        artifact_root=args.artifact_root, policy_id=args.attention_policy,
        policy_code_path=locked_code, approved_policy_sha256=args.approved_policy_sha256,
        config_path=locked_config, approved_config_sha256=args.approved_config_sha256,
        runner=runner, overall_deadline_seconds=args.overall_deadline_seconds,
        store_path=args.store_path, max_attempts=args.max_attempts,
        memory_evidence_root=memory_evidence_root,
        assistance_credits=args.assistance_credits, token_limit=args.token_limit,
        assistance_mode=AssistanceMode(args.assistance_mode),
        human_deadline_seconds=args.human_deadline_seconds,
        policy_config=policy_config,
        demo_prior=None if args.demo_prior is None else args.demo_prior.read_text(),
        approved_demo_sha256=args.approved_demo_sha256,
        memory_context=None if args.memory_context is None else json.loads(args.memory_context.read_text()),
        approved_memory_context_sha256=args.approved_memory_context_sha256,
        public_lift_progress_check=args.public_lift_progress_check,
        dev_hypothesis=dev_hypothesis,
        dev_hypothesis_evidence=dev_hypothesis_evidence,
        advisor_transport=(lambda request: SimGTGLMAdvisorTransport(
            ParccClient(timeout_seconds=90, max_attempts=1), format_attempts=1)(request)
            if args.single_glm_call else None),
        entry_lock=entry_lock,
        approved_policy_config_sha256=args.approved_policy_config_sha256,
    )
    if args.summary_path is not None:
        args.summary_path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result["native_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
