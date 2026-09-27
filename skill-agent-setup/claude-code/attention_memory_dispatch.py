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

from benchmarks.attention_harness.memory_agent import MemoryAgent
from benchmarks.attention_harness.memory_service import MemoryService
from benchmarks.attention_harness.memory_validation_task import MemoryValidationTask, _save
from benchmarks.attention_harness.robocasa_native.sim_gt_cli import _load_policy
from benchmarks.attention_harness.robosuite_memory.adapter import RobosuiteSimGTBackend
from benchmarks.attention_harness.robosuite_memory.formal_service import DedicatedRobosuiteService
from benchmarks.attention_harness.robosuite_memory.paired_trials import RobosuitePairedTrialExecutor


def dispatch_memory_candidates(config: dict, result: dict, *, repo_root: Path = REPO_ROOT) -> list[dict]:
    """Schedule every candidate; execute only a digest-approved validation recipe.

    Existing registered pairs are reported as reused evidence. A missing repair
    is a durable awaiting state and never an instruction to promote.
    """
    if result.get("runner_boundary", {}).get("mode") != "formal":
        return []
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
    if not candidate_ids:
        return []
    validation = config.get("memory_validation")
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
        if result["suite"] != "robosuite":
            state = {"schema_version": "attentionbench.memory-validation-task.v1",
                     "memory_id": memory_id, "source_run": str(artifact.resolve()),
                     "source_run_sha256": source_sha, "status": "blocked",
                     "blocker": "RoboCasa has no approved effective repair or validated paired executor",
                     "formal_eligible": False}
            _save(state_path, state)
            records.append({"memory_id": memory_id, "state_path": str(state_path),
                            "status": "blocked", "blocker": state["blocker"]})
            continue
        cases_file = (repo_root / validation["cases_file"]).resolve()
        policy_ref = validation["policy_ref"]
        module = policy_ref.partition(":")[0]
        policy_file = repo_root.joinpath(*module.split(".")).with_suffix(".py").resolve()
        for path, field in ((cases_file, "approved_cases_sha256"),
                            (policy_file, "approved_policy_sha256")):
            if hashlib.sha256(path.read_bytes()).hexdigest() != validation[field]:
                raise ValueError(f"{field} differs from approved file")
        cases = tuple(json.loads(cases_file.read_text(encoding="utf-8")))
        if state_path.exists():
            earlier = json.loads(state_path.read_text(encoding="utf-8"))
            if earlier.get("status") == "awaiting_approved_repair":
                state_path = task_dir / f"{name}.approved.json"
            elif earlier.get("status") in {"trusted", "blocked"}:
                if earlier.get("spec", {}).get("source_run_sha256") != source_sha:
                    raise ValueError("candidate source changed since validation")
                if (earlier.get("spec", {}).get("approved_policy_sha256")
                        != validation["approved_policy_sha256"]):
                    raise ValueError("validation approval changed on restart")
                if earlier["status"] == "trusted":
                    service = MemoryService(Path(result["store"]),
                                            artifact_root=Path(result["artifact_dir"]) / "attempts")
                    if service.get_memory(memory_id).status.value != "trusted":
                        raise ValueError("trusted task receipt disagrees with Memory Service")
                records.append({"memory_id": memory_id, "state_path": str(state_path),
                                "status": earlier["status"],
                                "reused_existing_pairs": list(earlier.get("reused_existing_pairs", []))})
                continue
        source_root = Path(config["service_source_root"]).resolve()
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source_root, text=True,
        ).strip()
        if revision != validation["approved_service_revision"]:
            raise ValueError("validation Service revision changed")
        store = Path(result["store"])
        service = MemoryService(store, artifact_root=Path(result["artifact_dir"]) / "attempts")
        agent = MemoryAgent(service)
        with DedicatedRobosuiteService(
            source_root=source_root, expected_revision=revision,
            log_path=task_dir / f"{name}.service.log",
            deadline=time.monotonic() + float(validation.get("service_deadline_seconds", 300)),
        ) as active:
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
            task = MemoryValidationTask(
                agent=agent, memory_id=memory_id, source_run=artifact,
                source_run_sha256=source_sha, cases=cases,
                validation_policy=policy_file,
                approved_policy_sha256=validation["approved_policy_sha256"],
                executor=executor, state_path=state_path,
                assistance_credits=int(validation.get("assistance_credits", 0)),
            )
            state = task.run()
        receipt = active.stop_receipt
        receipt_path = task_dir / f"{name}.service-stop.json"
        _save(receipt_path, receipt)
        records.append({"memory_id": memory_id, "state_path": str(state_path),
                        "status": state["status"], "service_stop_receipt": str(receipt_path),
                        "service_stop_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest()})
    return records
