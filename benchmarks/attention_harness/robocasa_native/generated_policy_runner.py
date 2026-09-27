"""RoboCasa GT generated-policy smoke path with a separate policy process."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..core.store import AttentionStore
from ..artifacts import write_result_artifact
from .agent_actions import AgentServerActionBackend
from .client import RobocasaSimClient
from .deadline_client import DeadlineRobocasaSimClient
from .policy_sandbox import (GeneratedPolicyResult, GeneratedPolicyTimeout,
                             execute_generated_policy, validate_generated_policy)
from .safety_monitor import SafetyMonitorBackend
from .sim_gt_runner import run_robocasa_sim_gt_episode


def run_robocasa_generated_policy_episode(
    *, policy_code_path: Path, timeout_seconds: float = 300.0,
    hypothesis: str = "", prior_attempts: list[dict[str, Any]] | None = None,
    **runner_kwargs: Any,
) -> dict[str, Any]:
    """Run model-authored code without handing it simulator/evaluator clients.

    Service capabilities are checked before reset; runs remain non-formal
    until the complete acceptance matrix and frozen-seed gate pass.
    """
    code = policy_code_path.read_text(encoding="utf-8")
    validate_generated_policy(code)
    action_backend = runner_kwargs.get("action_backend")
    raw_backend = action_backend
    while isinstance(raw_backend, SafetyMonitorBackend):
        raw_backend = raw_backend.backend
    backend_timeout = getattr(raw_backend, "timeout_seconds", None)
    if backend_timeout is not None and float(backend_timeout) > timeout_seconds:
        raise ValueError(
            "action backend timeout must not exceed generated-policy timeout"
        )
    deadline = time.monotonic() + timeout_seconds
    cancellation_capability_verified = isinstance(raw_backend, AgentServerActionBackend)
    if cancellation_capability_verified:
        raw_backend.set_episode_deadline(deadline)
        raw_backend.assert_cancellation_available()
    service = runner_kwargs.get("client")
    if service is None:
        service = RobocasaSimClient(runner_kwargs["task_id"])
    runner_kwargs["client"] = DeadlineRobocasaSimClient(service, deadline=deadline)
    sandbox_result: GeneratedPolicyResult | None = None

    def policy(sdk, context):
        nonlocal sandbox_result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise GeneratedPolicyTimeout("episode deadline reached before policy execution")
        sandbox_result = execute_generated_policy(
            code=code, sdk=sdk, context=context, timeout_seconds=remaining,
        )

    result = run_robocasa_sim_gt_episode(
        policy=policy, policy_code_path=policy_code_path,
        hypothesis=hypothesis, prior_attempts=prior_attempts,
        policy_execution_mode="sandboxed_generated",
        episode_deadline_monotonic=deadline, **runner_kwargs,
    )
    result["generated_policy"] = {
        "boundary": "spawned_process_sdk_rpc",
        "timeout_seconds": timeout_seconds,
        "service_cancellation_capability_verified": cancellation_capability_verified,
        "action_cancellation_receipts": list(
            getattr(raw_backend, "cancellation_receipts", [])
        ),
        "call_count": None if sandbox_result is None else sandbox_result.call_count,
        "status": result["status"] if sandbox_result is None else sandbox_result.status,
    }
    result["formal_eligible"] = False
    result["formal_blockers"] = [
        "generated-policy timeout/cancellation acceptance matrix not frozen across tasks",
        "frozen development-seed stability gate not passed",
    ]
    episode_dir = Path(result["artifact_dir"])
    (episode_dir / "generated_policy_execution.json").write_text(
        json.dumps(result["generated_policy"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_result_artifact(episode_dir, result)
    return result


def run_robocasa_generated_policy_sequence(
    *, attempts: list[tuple[Path, str]], **runner_kwargs: Any,
) -> list[dict[str, Any]]:
    """Run a bounded repair sequence and carry only projected failure history.

    Each attempt resets the same task/seed/config. The next policy sees prior
    public symptoms, its own prior hypothesis, and received guidance; native
    evaluator state never enters that context.
    """
    if not 1 <= len(attempts) <= 8:
        raise ValueError("a sequence requires one to eight policy attempts")
    history: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for index, (code_path, hypothesis) in enumerate(attempts):
        result = run_robocasa_generated_policy_episode(
            policy_code_path=code_path, hypothesis=hypothesis,
            prior_attempts=history, **runner_kwargs,
        )
        results.append(result)
        if result["native_success"]:
            break
        link = result["attention_trace"]
        store = AttentionStore(Path(link["store"]))
        packet = store.get_trace(link["advisor_trace_id"])
        if packet is None:
            break
        failure = packet["failure"]
        summary = {
            "attempt_index": index,
            "policy_sha256": result["policy_sha256"],
            "hypothesis": hypothesis,
            "failure_stage": failure["stage"],
            "error_type": failure["error_type"],
            "observed_symptom": failure.get("observed_symptom") or failure["message"],
        }
        advice = result.get("advisor_advice")
        if isinstance(advice, dict) and isinstance(advice.get("guidance"), str):
            summary["guidance"] = advice["guidance"]
        history.append(summary)
    return results
