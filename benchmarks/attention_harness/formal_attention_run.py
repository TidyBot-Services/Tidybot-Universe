"""Bridge the seven-policy scheduler to the two dedicated formal attempt runners.

This is an engineering path. Its output remains ineligible for formal scoring
until the protocol and acceptance matrix are frozen independently.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

from attention_memory_service import MemoryServiceClient
from attention_memory_service.identity import store_id as memory_store_id

from .core.artifacts import write_run_bundle
from .core.models import MemoryUseRecord
from .core.store import AttentionStore
from .episode_trace import persist_episode_trace
from .formal_runner_boundary import FormalRunRequest, FormalSuiteRunner, run_with_formal_boundary
from .policy_input import public_attention_input
from .sim_gt_attention_run import run_sim_gt_attention


def run_formal_attention(
    *, suite: str, task_id: str, seed: int, artifact_root: Path,
    policy_id: str, policy_code_path: Path, approved_policy_sha256: str,
    config_path: Path, approved_config_sha256: str, runner: FormalSuiteRunner,
    overall_deadline_seconds: float = 300.0,
    memory_context: dict[str, Any] | None = None,
    approved_memory_context_sha256: str | None = None,
    dev_hypothesis: str | None = None,
    dev_hypothesis_evidence: dict[str, str] | None = None,
    entry_lock: dict[str, Any] | None = None,
    approved_policy_config_sha256: str | None = None,
    **scheduler_options: Any,
) -> dict[str, Any]:
    """Use one approved source/config version for every scheduled attempt."""
    if runner.suite != suite:
        raise ValueError("formal runner suite mismatch")
    template = FormalRunRequest(
        suite=suite, task_id=task_id, seed=seed,
        policy_code_path=policy_code_path, policy_sha256=approved_policy_sha256,
        config_path=config_path, config_sha256=approved_config_sha256,
        artifact_root=artifact_root, overall_deadline_seconds=overall_deadline_seconds,
    )
    template.validate()
    if entry_lock is not None:
        lock = dict(entry_lock)
        digest = lock.pop("sha256", None)
        actual = hashlib.sha256(json.dumps(lock, sort_keys=True, ensure_ascii=False,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        mode = scheduler_options.get("assistance_mode", "benchmark_proxy")
        mode = getattr(mode, "value", mode)
        expected = {"max_attempts": scheduler_options.get("max_attempts", 3),
                    "assistance_credits": scheduler_options.get("assistance_credits", 1),
                    "token_limit": scheduler_options.get("token_limit", 30_000),
                    "assistance_mode": mode,
                    "human_deadline_seconds": float(scheduler_options.get("human_deadline_seconds", 60)),
                    "overall_deadline_seconds": float(overall_deadline_seconds),
                    "approved_demo_sha256": scheduler_options.get("approved_demo_sha256"),
                    "approved_policy_config_sha256": approved_policy_config_sha256}
        if (digest != actual or lock.get("suite") != suite or lock.get("task_id") != task_id
                or lock.get("seed") != seed or lock.get("attention_policy") != policy_id
                or lock.get("approved_config_sha256") != approved_config_sha256
                or lock.get("approved_policy_sha256") != approved_policy_sha256
                or any(lock.get(key) != value for key, value in expected.items())):
            raise ValueError("formal entry lock identity mismatch")
    if dev_hypothesis is not None and (not isinstance(dev_hypothesis, str)
                                      or not dev_hypothesis.strip()
                                      or len(dev_hypothesis) > 2000):
        raise ValueError("Dev hypothesis must be nonempty text of at most 2000 characters")
    scheduler_config = {
        "suite": suite, "task_id": task_id, "seed": seed,
        "attention_policy": policy_id, "policy_sha256": approved_policy_sha256,
        "sim_config_sha256": approved_config_sha256,
        "max_attempts": scheduler_options.get("max_attempts", 3),
        "assistance_credits": scheduler_options.get("assistance_credits", 1),
        "token_limit": scheduler_options.get("token_limit", 30_000),
        "policy_config": scheduler_options.get("policy_config") or {},
        "assistance_mode": getattr(scheduler_options.get("assistance_mode", "benchmark_proxy"),
                                   "value", scheduler_options.get("assistance_mode", "benchmark_proxy")),
        "demo_prior_sha256": (hashlib.sha256(scheduler_options["demo_prior"].encode()).hexdigest()
                              if scheduler_options.get("demo_prior") else None),
        "memory_context": memory_context,
        "overall_deadline_seconds": overall_deadline_seconds,
        "entry_sha256": entry_lock["sha256"] if entry_lock else None,
    }
    scheduler_config_sha256 = hashlib.sha256(json.dumps(
        scheduler_config, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode()).hexdigest()
    if memory_context is not None:
        approved = json.loads(config_path.read_text(encoding="utf-8"))
        required = {"suite", "task_id", "perception_mode", "scene_id", "object_set_id",
                    "camera_config_id", "task_variant_id", "camera_names", "task_prompt"}
        if (not isinstance(memory_context, dict) or set(memory_context) != required
                or memory_context["suite"] != suite or memory_context["task_id"] != task_id
                or memory_context["perception_mode"] != "sim_gt"
                or any(memory_context[key] != approved[key]
                       for key in ("scene_id", "object_set_id"))
                or (suite == "robocasa" and
                    (memory_context["camera_names"] != approved["camera_names"]
                     or memory_context["task_prompt"] != approved["task_prompt"]))
                or (suite == "robosuite" and
                    memory_context["camera_names"] != [approved["camera_name"]])):
            raise ValueError("formal Memory context disagrees with approved simulator config")

    def execute(**attempt: Any) -> dict[str, Any]:
        run_id = attempt["attention_run_id"]
        index = attempt["attention_attempt_index"]
        attempt_id = f"attempt:{run_id.removeprefix('run:')}:{index}"
        attention_input = public_attention_input(attempt["attention_input"])
        gateway = attempt["memory_gateway"]
        if isinstance(gateway, MemoryServiceClient):
            health = gateway.health()
            if (health.get("schema_version") != "attentionbench.memory-service.v2"
                    or "attempt_bound_use_grants" not in health.get("capabilities", [])
                    or gateway.store_id() != memory_store_id(attempt["store_path"])):
                raise RuntimeError("formal Memory Service schema or store identity mismatch")
        # Hint-only has no Memory read path, including the availability probe.
        available = (gateway.retrieve(memory_context, now=time.time())
                     if memory_context and policy_id != "trace_aware_hint_only" else [])
        selected = attention_input.get("memory_ids_to_use", [])
        if selected and policy_id == "trace_aware_hint_only":
            raise RuntimeError("hint-only Attention cannot select Memory")
        if selected and memory_context is None:
            raise RuntimeError("formal Memory use requires an approved applicability context")
        by_id = {item.memory_id: item for item in available}
        grants = []
        if selected:
            guidance = {}
            for memory_id in selected:
                item = by_id.get(memory_id)
                if item is None:
                    raise RuntimeError("selected Memory is no longer trusted or applicable")
                grant = gateway.authorize_use(
                    memory_context, memory_id=memory_id, attempt_id=attempt_id,
                    now=time.time(),
                )
                if grant.get("version") != item.version or grant.get("attempt_id") != attempt_id:
                    raise RuntimeError("Memory use grant disagrees with selected version")
                grants.append(grant)
                guidance[memory_id] = item.guidance
            attention_input["memory_guidance"] = guidance
            public_attention_input(attention_input)
        cancel = threading.Event()
        stop_poll = threading.Event()

        def watch_interrupt() -> None:
            while not stop_poll.wait(0.05):
                if attempt["interrupt_check"]():
                    cancel.set()
                    return

        watcher = threading.Thread(target=watch_interrupt, daemon=True)
        watcher.start()
        if hasattr(runner, "cancel_event"):
            runner.cancel_event = cancel
        request = FormalRunRequest(
            suite=suite, task_id=task_id, seed=seed,
            policy_code_path=policy_code_path, policy_sha256=approved_policy_sha256,
            config_path=config_path, config_sha256=approved_config_sha256,
            artifact_root=attempt["artifact_root"],
            overall_deadline_seconds=overall_deadline_seconds,
            run_id=run_id, attempt_id=attempt_id,
            attention_input=attention_input,
            entry_sha256=entry_lock["sha256"] if entry_lock else None,
            entry_lock=dict(entry_lock) if entry_lock else None,
        )
        try:
            formal = run_with_formal_boundary(request, runner=runner)
        finally:
            stop_poll.set()
            watcher.join(timeout=1)
        episode_dir = Path(formal["artifact_dir"]).resolve()
        if not episode_dir.is_relative_to(request.artifact_root.resolve()):
            raise RuntimeError("formal attempt directory escaped run root")
        trace = json.loads(Path(formal["artifacts"]["trace"]["uri"]).read_text())
        safety = json.loads(Path(formal["artifacts"]["safety"]["uri"]).read_text())
        receipt = json.loads(Path(formal["artifacts"]["sandbox_receipt"]["uri"]).read_text())
        if (safety.get("source") != "independent_safety_monitor"
                or not isinstance(safety.get("unsafe_attempts"), int)
                or isinstance(safety.get("unsafe_attempts"), bool)):
            raise RuntimeError("formal safety monitor evidence is invalid")
        elapsed = receipt.get("elapsed_seconds")
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed < 0:
            raise RuntimeError("formal sandbox elapsed time is invalid")
        native_evaluated = formal["native_evaluator"].get("evaluated") is True
        sdk_events = trace.get("sdk_events")
        if not isinstance(sdk_events, list):
            raise RuntimeError("formal trace has no SDK event ledger")
        sdk_events = [
            {"timestamp": grant["used_at"], "source": "attention_harness.memory",
             "event_type": "attention.memory_retrieval", "operation": "retrieve",
             "status": "completed", "arguments": {"memory_id": grant["memory_id"]},
             "result": {"memory_version": grant["version"], "grant_id": grant["grant_id"]}}
            for grant in grants
        ] + sdk_events
        link = persist_episode_trace(
            episode_dir=episode_dir, suite=suite, task_id=task_id, seed=seed,
            policy_id=policy_id, developer_model="formal-generated-policy",
            evaluator_model="native_evaluator", execution_target=f"{suite}_sim",
            execution_status=formal["status"], native_success=formal["native_success"],
            evaluator_authoritative=native_evaluated, elapsed_seconds=float(elapsed),
            action_trace=trace.get("backend_steps", []), sdk_trace=sdk_events,
            error=formal.get("error"), timed_out=formal["status"] == "timeout",
            stop_reason=formal["status"] if formal["status"] != "completed" else None,
            code_path=policy_code_path, code_sha256=approved_policy_sha256,
            hypothesis=dev_hypothesis.strip() if dev_hypothesis else "",
            runtime={"perception_mode": "sim_gt", "score_namespace": "v2/gt_perception_attentionbench",
                     "runner_boundary": "formal", "policy_sha256": approved_policy_sha256,
                     "config_sha256": approved_config_sha256,
                     "scheduler_config_sha256": scheduler_config_sha256,
                     "entry_sha256": entry_lock["sha256"] if entry_lock else None,
                     "dev_hypothesis_evidence_sha256": (dev_hypothesis_evidence or {}).get("sha256"),
                     "formal_artifacts": formal["artifacts"], "attention_input": attention_input,
                     "memory_ids": selected, "memory_context": memory_context,
                     "memory_exposure": "trusted" if selected else "none"},
            assistance_credits=attempt["assistance_credits"],
            token_limit=attempt["token_limit"],
            assistance_mode=attempt["assistance_mode"],
            store_path=attempt["store_path"], run_id=run_id, attempt_id=attempt_id,
            attempt_index=index, finalize_run=False,
            execution_budget_seconds=attempt["attention_run_budget_seconds"],
        )
        link["bundle"] = str(write_run_bundle(
            AttentionStore(attempt["store_path"]), run_id,
            episode_dir / "attention_bundle.json",
        ).resolve())
        for grant in grants:
            gateway.record_use(MemoryUseRecord(
                use_id=f"use:{attempt_id}:{grant['memory_id']}",
                memory_id=grant["memory_id"], memory_version=grant["version"],
                run_id=run_id, attempt_id=attempt_id, used_at=grant["used_at"],
                outcome=("timeout" if formal["status"] == "timeout" else
                         "cancelled" if formal["status"] == "cancelled" else
                         "evaluator_unavailable" if not native_evaluated else
                         "native_success" if formal["native_success"] else "failed"),
            ))
        return {
            "task_id": task_id, "seed": seed, "perception_mode": "sim_gt",
            "execution_target": f"{suite}_sim", "status": formal["status"],
            "native_success": formal["native_success"], "artifact_dir": str(episode_dir),
            "attention_trace": link, "formal_runner_result": formal,
            "safety_artifact": formal["artifacts"]["safety"]["uri"],
            "safety_unsafe": safety["unsafe_attempts"] > 0,
            "memory_ids": selected, "available_memory_ids": list(by_id),
            "memory_use_grants": grants,
        }

    summary = run_sim_gt_attention(
        suite=suite, task_id=task_id, seed=seed, artifact_root=artifact_root,
        policy_id=policy_id, attempt_executor=execute,
        runner_boundary_mode="formal", attempt_budget_seconds=overall_deadline_seconds,
        memory_applicability_context=memory_context,
        **scheduler_options,
    )
    summary["approved_policy_sha256"] = approved_policy_sha256
    summary["approved_config_sha256"] = approved_config_sha256
    summary["approved_policy_path"] = str(policy_code_path.resolve())
    summary["approved_config_path"] = str(config_path.resolve())
    summary["scheduler_config"] = scheduler_config
    summary["entry_lock"] = entry_lock
    summary["scheduler_config_sha256"] = scheduler_config_sha256
    summary["memory_context"] = memory_context
    summary["approved_memory_context_sha256"] = approved_memory_context_sha256
    summary["dev_hypothesis"] = {"status": "provided" if dev_hypothesis else "unknown",
                                  "content": dev_hypothesis.strip() if dev_hypothesis else None,
                                  "evidence": dev_hypothesis_evidence}
    summary["runner_boundary"]["overall_deadline_certified"] = all(
        item["formal_runner_result"]["sandbox"]["deadline_enforced"]
        for item in summary["attempts"]
    )
    path = Path(summary["artifact_dir"]) / "attention_run.json"
    path.write_text(json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return summary
