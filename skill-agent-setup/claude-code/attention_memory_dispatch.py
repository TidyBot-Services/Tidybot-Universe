"""Dispatch failed formal-run candidates to durable Memory validation tasks."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.attention_harness.memory_agent import MemoryAgent, TrialEvidence
from benchmarks.attention_harness.memory_service import MemoryService
from benchmarks.attention_harness.memory_validation_task import MemoryValidationTask, _save
from benchmarks.attention_harness.robocasa_native.sim_gt_cli import _load_policy
from benchmarks.attention_harness.robosuite_memory.adapter import RobosuiteSimGTBackend
from benchmarks.attention_harness.robosuite_memory.formal_service import DedicatedRobosuiteService
from benchmarks.attention_harness.robosuite_memory.paired_trials import RobosuitePairedTrialExecutor
from benchmarks.attention_harness.robocasa_native.agent_actions import AgentServerActionBackend
from benchmarks.attention_harness.robocasa_native.client import RobocasaSimClient
from benchmarks.attention_harness.robocasa_native.formal_services import (
    DedicatedRobocasaServices, simulator_runtime_identity, source_identity,
)
from benchmarks.attention_harness.robocasa_native.paired_trials import RoboCasaPairedTrialExecutor


def _robocasa_executor(validation: dict, config: dict, *, policy_file: Path,
                       policy_id: str, task_dir: Path, name: str, store: Path,
                       service: MemoryService):
    """Return an arm executor that owns and attests both services per arm."""
    if (validation.get("formal_eligible") is not False
            or validation.get("assistance_credits") != 0
            or validation.get("sdk_calls_max") != 200
            or not 0 < float(validation["agent_job_timeout_seconds"]) <= 90
            or not 0 < float(validation["policy_timeout_seconds"]) <= 240
            or not 0 < float(validation["service_deadline_seconds"]) <= 300
            or float(validation["agent_job_timeout_seconds"]) > float(validation["policy_timeout_seconds"])):
        raise ValueError("RoboCasa validation exceeds the frozen development budget")
    roots = {
        "sim": Path(config["sim_source_root"]).resolve(),
        "agent": Path(config["agent_source_root"]).resolve(),
        "task": Path(config["task_source_root"]).resolve(),
    }
    sim_python = Path(config["sim_python"]).resolve()
    agent_python = Path(config["agent_python"]).resolve()
    expected = validation["approved_robocasa_sources"]
    for key, root in roots.items():
        if source_identity(root) != expected[key]:
            raise ValueError(f"approved RoboCasa {key} Service identity changed")
    if simulator_runtime_identity(sim_python, roots["task"]) != expected["runtime"]:
        raise ValueError("approved RoboCasa simulator runtime changed")
    offset = int(validation["port_offset"])
    limits = validation["safety_limits"]
    if (not 0 < float(limits["max_delta_m"]) <= 0.25
            or not 0 < float(limits["max_observed_step_m"]) <= 0.5):
        raise ValueError("RoboCasa validation relaxes independent Safety limits")

    def execute(trial):
        if hashlib.sha256(policy_file.read_bytes()).hexdigest() != validation["approved_policy_sha256"]:
            raise ValueError("approved RoboCasa policy changed before arm")
        label = "treatment" if trial.treatment else "control"
        logs = task_dir / name / "services" / str(trial.seed) / label
        dedicated = DedicatedRobocasaServices(
            task_id=trial.task_id,
            sim_source_root=roots["sim"], agent_source_root=roots["agent"],
            task_source_root=roots["task"], sim_python=sim_python,
            agent_python=agent_python, expected_sim=expected["sim"],
            expected_agent=expected["agent"], expected_task=expected["task"],
            expected_runtime=expected["runtime"], port_offset=offset,
            log_dir=logs,
            deadline=time.monotonic() + float(validation["service_deadline_seconds"]),
        )
        try:
            with dedicated:
                executor = RoboCasaPairedTrialExecutor(
                    policy_code_path=policy_file, policy_id=policy_id,
                    artifact_root=task_dir / name / "arms", store_path=store,
                    backend_factory=lambda: AgentServerActionBackend(
                        base_url=dedicated.agent_url, simulator_attested=True,
                        holder=f"m5-{trial.seed}-{label}",
                        timeout_seconds=float(validation["agent_job_timeout_seconds"]),
                        poll_seconds=0.1,
                    ),
                    client_factory=lambda task_id: RobocasaSimClient(task_id, base_url=dedicated.sim_url),
                    memory_gateway=service,
                    max_delta_m=float(limits["max_delta_m"]),
                    max_observed_step_m=float(limits["max_observed_step_m"]),
                    timeout_seconds=float(validation["policy_timeout_seconds"]),
                )
                evidence = executor(trial)
        finally:
            receipt = dedicated.stop_receipt
            stop_path = logs / "service-stop.json"
            _save(stop_path, receipt or {"missing": True})
        if receipt is None or not all(row["process_group_gone"] for row in receipt["services"].values()):
            raise RuntimeError("RoboCasa service process group remained after arm")
        return TrialEvidence(evidence.attempt_id, evidence.safety_artifact,
                             evidence.config_sha256, stop_path)

    return execute


def dispatch_memory_candidates(config: dict, result: dict, *, repo_root: Path = REPO_ROOT) -> list[dict]:
    """Schedule every candidate; execute only a digest-approved validation recipe.

    Existing registered pairs are reported as reused evidence. A missing repair
    is a durable awaiting state and never an instruction to promote.
    """
    if result.get("runner_boundary", {}).get("mode") != "formal":
        return []
    if result.get("formal_eligible") is not False:
        raise ValueError("Memory validation source must remain engineering-only")
    artifact = Path(result["artifact_dir"]) / "attention_run.json"
    if json.loads(artifact.read_text(encoding="utf-8")) != result:
        raise ValueError("formal run artifact differs from result")
    from attention_eval import build_eval_packet
    build_eval_packet(artifact)
    source_sha = hashlib.sha256(artifact.read_bytes()).hexdigest()
    candidate_ids = list(dict.fromkeys(
        row["candidate_memory_id"] for row in result.get("requests", [])
        if isinstance(row, dict) and isinstance(row.get("candidate_memory_id"), str)
    ))
    validation = config.get("memory_validation")
    if isinstance(validation, dict) and validation.get("candidate_memory_id"):
        if not isinstance(validation["candidate_memory_id"], str):
            raise ValueError("approved candidate_memory_id must be a string")
        candidate_ids = [validation["candidate_memory_id"]]
    if not candidate_ids:
        return []
    records = []
    for memory_id in candidate_ids:
        task_dir = Path(result["artifact_dir"]) / "memory_validation"
        task_dir.mkdir(parents=True, exist_ok=True)
        name = hashlib.sha256(memory_id.encode()).hexdigest()[:16]
        state_path = task_dir / f"{name}.json"
        source_service = MemoryService(Path(result["store"]),
                                      artifact_root=Path(result["artifact_dir"]) / "attempts")
        source_memory = source_service.get_memory(memory_id)
        source_service.v2.verify_source_evidence(memory_id)
        provenance = source_service.provenance(memory_id)
        if (not any(row.get("request_id") == provenance["request_id"]
                    for row in result.get("requests", []) if isinstance(row, dict))
                or not any(row.get("attention_trace", {}).get("attempt_id")
                           == provenance["source_attempt_id"]
                           and row.get("attention_trace", {}).get("run_id")
                           == provenance["source_run_id"]
                           for row in result.get("attempts", []) if isinstance(row, dict))):
            raise ValueError("approved candidate is not linked to this formal source run")
        if source_memory.status.value == "candidate":
            source_service.check_candidate(memory_id)
        elif source_memory.status.value != "trusted":
            raise ValueError("candidate task has an unsupported Memory status")
        if validation is None:
            state = {"schema_version": "attentionbench.memory-validation-task.v1",
                     "memory_id": memory_id, "source_run": str(artifact.resolve()),
                     "source_run_sha256": source_sha, "status": "awaiting_approved_repair",
                     "blocker": "No approved effective repair, validation policy, or frozen paired cases",
                     "formal_eligible": False}
            if state_path.exists():
                old = json.loads(state_path.read_text(encoding="utf-8"))
                if old.get("source_run_sha256") != source_sha or old.get("memory_id") != memory_id:
                    raise ValueError("candidate task source changed on restart")
                if old.get("status") == "awaiting_approved_repair" and not old.get("blocker"):
                    old["blocker"] = state["blocker"]
                    _save(state_path, old)
                state = old
            else:
                _save(state_path, state)
            records.append({"memory_id": memory_id, "state_path": str(state_path),
                            "status": state["status"]})
            continue
        if not isinstance(validation, dict):
            raise ValueError("memory_validation must be an approved object")
        if validation.get("formal_eligible") is not False or validation.get("suite") != result["suite"]:
            raise ValueError("validation approval must be development-only and match source suite")
        if validation.get("task_id") != result.get("task_id"):
            raise ValueError("validation task differs from source task")
        if result["suite"] not in {"robosuite", "robocasa"}:
            raise ValueError("no paired validation executor for source suite")
        cases_file = (repo_root / validation["cases_file"]).resolve()
        if result["suite"] == "robosuite":
            policy_ref = validation["policy_ref"]
            module = policy_ref.partition(":")[0]
            policy_file = repo_root.joinpath(*module.split(".")).with_suffix(".py").resolve()
        else:
            policy_file = (repo_root / validation["policy_file"]).resolve()
        for path, field in ((cases_file, "approved_cases_sha256"),
                            (policy_file, "approved_policy_sha256")):
            if hashlib.sha256(path.read_bytes()).hexdigest() != validation[field]:
                raise ValueError(f"{field} differs from approved file")
        cases = tuple(json.loads(cases_file.read_text(encoding="utf-8")))
        if state_path.exists():
            earlier = json.loads(state_path.read_text(encoding="utf-8"))
            if earlier.get("status") == "awaiting_approved_repair" or (
                earlier.get("status") == "blocked" and "spec" not in earlier
            ):
                state_path = task_dir / f"{name}.approved.json"
            elif earlier.get("status") in {"trusted", "blocked"}:
                if earlier.get("spec", {}).get("source_run_sha256") != source_sha:
                    raise ValueError("candidate source changed since validation")
                if (earlier.get("spec", {}).get("approved_policy_sha256")
                        != validation["approved_policy_sha256"]):
                    raise ValueError("validation approval changed on restart")
                if ("approval" in earlier.get("spec", {})
                        and earlier["spec"]["approval"] != validation):
                    raise ValueError("frozen validation profile changed on restart")
                for arms in earlier.get("arms", {}).values():
                    for receipt in arms.values():
                        MemoryValidationTask._read_receipt(receipt)
                service = MemoryService(Path(result["store"]),
                                        artifact_root=Path(result["artifact_dir"]) / "attempts")
                current_pairs = {
                    str(item["seed"]): item for item in service.list_pairs(memory_id)
                }
                if earlier.get("pairs") != current_pairs:
                    raise ValueError("saved validation pairs differ from Memory Service")
                current_impact = service.impact_report(memory_id) if current_pairs else None
                if earlier.get("impact") is not None and current_impact != earlier["impact"]:
                    raise ValueError("saved validation impact differs from Memory Service")
                if earlier["status"] == "trusted":
                    if service.get_memory(memory_id).status.value != "trusted":
                        raise ValueError("trusted task receipt disagrees with Memory Service")
                records.append({"memory_id": memory_id, "state_path": str(state_path),
                                "status": earlier["status"],
                                "reused_existing_pairs": list(earlier.get("reused_existing_pairs", []))})
                continue
        store = Path(result["store"])
        service = MemoryService(store, artifact_root=Path(result["artifact_dir"]) / "attempts")
        agent = MemoryAgent(service)
        if result["suite"] == "robocasa":
            executor = _robocasa_executor(
                validation, config, policy_file=policy_file,
                policy_id=service.provenance(memory_id)["source_policy_id"],
                task_dir=task_dir, name=name, store=store, service=service,
            )
            active = None
        else:
            source_root = Path(config["service_source_root"]).resolve()
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=source_root, text=True,
            ).strip()
            if revision != validation["approved_service_revision"]:
                raise ValueError("validation Service revision changed")
            active = DedicatedRobosuiteService(
                source_root=source_root, expected_revision=revision,
                log_path=task_dir / f"{name}.service.log",
                deadline=time.monotonic() + float(validation.get("service_deadline_seconds", 300)),
            )
        def run_task(executor):
            task = MemoryValidationTask(
                agent=agent, memory_id=memory_id, source_run=artifact,
                source_run_sha256=source_sha, cases=cases,
                validation_policy=policy_file,
                approved_policy_sha256=validation["approved_policy_sha256"],
                executor=executor, state_path=state_path,
                assistance_credits=int(validation.get("assistance_credits", 0)),
                approval=validation,
            )
            return task.run()
        if active is None:
            state = run_task(executor)
            records.append({"memory_id": memory_id, "state_path": str(state_path),
                            "status": state["status"], "arm_receipts": state.get("arms", {})})
        else:
            with active:
                executor = RobosuitePairedTrialExecutor(
                    policy=_load_policy(policy_ref),
                    policy_id=service.provenance(memory_id)["source_policy_id"],
                    artifact_root=task_dir / name, store_path=store,
                    memory_gateway=service,
                    adapter_factory=lambda task, camera: RobosuiteSimGTBackend(
                        task, service_url=active.base_url, camera_name=camera,
                        horizon=500, camera_height=64, camera_width=64,
                    ),
                )
                state = run_task(executor)
            receipt_path = task_dir / f"{name}.service-stop.json"
            _save(receipt_path, active.stop_receipt)
            records.append({"memory_id": memory_id, "state_path": str(state_path),
                            "status": state["status"], "service_stop_receipt": str(receipt_path),
                            "service_stop_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest()})
    return records
