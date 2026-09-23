"""Two-attempt PARCC development run with a GLM Advisor between attempts."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

import numpy as np

from .attention_modes import AssistanceMode, MODE_SPECS, RequestState
from .core.advisor import AdvisorProxy, AdvisorTransport
from .core.artifacts import write_run_bundle
from .core.models import AttentionRequestRecord, RunStatus
from .core.policies import DecisionAction, PolicyContext, build_policy
from .core.runtime import AttentionRuntime
from .core.store import AttentionStore
from .parcc_advisor import ParccGLMAdvisorTransport, parse_advisor_advice
from .parcc_runner import run_parcc_episode
from .model_protocol import image_data_url
from .seed_guard import validate_seed
from .task_registry import TASKS


AttemptExecutor = Callable[..., dict[str, Any]]
ADVISOR_RUN_POLICY_IDS = ("reactive_help", "retry_k_then_ask")


def run_advised_parcc_episode(
    *,
    task_id: str,
    seed: int,
    artifact_root: Path,
    policy_id: str = "reactive_help",
    max_attempts: int = 2,
    assistance_credits: int = 1,
    token_limit: int = 30000,
    policy_timeout_seconds: float = 90.0,
    codegen_timeout_seconds: float = 240.0,
    review_timeout_seconds: float = 180.0,
    skip_review: bool = False,
    service_url: str | None = None,
    advisor_transport: AdvisorTransport | None = None,
    attempt_executor: AttemptExecutor = run_parcc_episode,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Execute, request a trace-grounded hint when policy says so, then retry.

    This is a development runner. It uses the existing PARCC developer and
    native Robosuite execution path, while one shared AttentionStore holds all
    attempts and requests for the run.
    """

    if task_id not in TASKS:
        raise KeyError(f"unknown task: {task_id}")
    if policy_id not in ADVISOR_RUN_POLICY_IDS:
        raise ValueError(f"Advisor run supports: {', '.join(ADVISOR_RUN_POLICY_IDS)}")
    if validate_seed(seed, allow_heldout=False) != "dev":
        raise PermissionError("Advisor development runs require a development seed")
    if max_attempts < 1 or assistance_credits < 0 or token_limit < 0:
        raise ValueError("attempts and budgets must be non-negative")
    run_dir = artifact_root / f"advisor-{task_id}-seed{seed}-{uuid4().hex[:12]}"
    run_dir.mkdir(parents=True)
    store_path = run_dir / "attention.sqlite3"
    run_id = f"run:{run_dir.name}"
    execution_budget = max_attempts * (
        policy_timeout_seconds + codegen_timeout_seconds + review_timeout_seconds + 60.0
    )
    policy = build_policy(policy_id)
    attempts: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    advisor_error: str | None = None
    guidance: str | None = None
    previous_policy: str | None = None
    consecutive_failures = 0

    for index in range(max_attempts):
        result = attempt_executor(
            task_id=task_id,
            seed=seed,
            artifact_root=run_dir / "attempts",
            service_url=service_url,
            policy_timeout_seconds=policy_timeout_seconds,
            codegen_timeout_seconds=codegen_timeout_seconds,
            review_timeout_seconds=review_timeout_seconds,
            skip_review=skip_review,
            advisor_guidance=guidance,
            previous_policy=previous_policy,
            attention_store_path=store_path,
            attention_run_id=run_id,
            attention_attempt_index=index,
            attention_finalize_run=False,
            attention_assistance_credits=assistance_credits,
            attention_token_limit=token_limit,
            attention_execution_budget_seconds=execution_budget,
        )
        attempts.append(result)
        store = AttentionStore(store_path)
        if result["native_success"]:
            break
        consecutive_failures += 1
        if index + 1 >= max_attempts:
            break
        trace_id = result["attention_trace"]["advisor_trace_id"]
        if trace_id is None:
            advisor_error = "failed attempt has no Advisor-safe trace"
            break
        trace = store.get_trace(trace_id)
        if trace is None:
            advisor_error = f"Advisor trace {trace_id} was not persisted"
            break
        decision = policy.decide(
            PolicyContext(
                run_id=run_id,
                attempt_index=index,
                failure_index=index,
                consecutive_failures=consecutive_failures,
                assistance_remaining=store.budget_status(run_id)["remaining"],
                evidence_count=len(trace["evidence"]),
                has_hypothesis=bool(trace.get("hypothesis")),
            )
        )
        decisions.append(
            {
                "after_attempt": index,
                "action": decision.action.value,
                "reason": decision.reason,
            }
        )
        guidance = None
        if decision.action is DecisionAction.REQUEST:
            request = AttentionRequestRecord(
                request_id=f"advisor-request:{run_dir.name}:{index}",
                run_id=run_id,
                attempt_id=result["attention_trace"]["attempt_id"],
                trace_id=trace_id,
                request_type=decision.request_type,
                reason=decision.reason,
                priority=decision.priority,
                created_at=clock(),
                deadline_at=None,
                mode=AssistanceMode.BENCHMARK_PROXY,
            )
            proxy = AdvisorProxy(
                store,
                transport=advisor_transport or _default_glm_transport,
                latency_seconds=MODE_SPECS[AssistanceMode.BENCHMARK_PROXY].proxy_latency_seconds,
                sleeper=sleeper,
            )
            runtime = AttentionRuntime(store, proxy, clock=clock)
            runtime.open_request(request)
            try:
                images = _verified_public_images(trace, Path(result["artifact_dir"]))
                answered = runtime.resolve_benchmark_proxy(
                    request.request_id, public_images=images
                )
                response = store.get_response(answered.response_id)
                advice = parse_advisor_advice(
                    response["content"], request_type=decision.request_type.value
                )
                guidance = advice.guidance
                requests.append(
                    {
                        "request_id": request.request_id,
                        "response_id": answered.response_id,
                        "advice": advice.artifact(),
                        "cached": response["cached"],
                        "provider_model": response["provider_model"],
                        "provider_latency_seconds": response["provider_latency_seconds"],
                        "logical_latency_seconds": response["logical_latency_seconds"],
                        "token_usage": response["token_usage"],
                    }
                )
            except Exception as exc:
                current = store.get_request(request.request_id)
                if current is not None and current.state is RequestState.PENDING:
                    runtime.cancel(request.request_id)
                advisor_error = f"{type(exc).__name__}: {exc}"
                break
        elif decision.action is DecisionAction.USE_DEMO:
            advisor_error = "demo-first execution requires a demo runner"
            break
        code_path = Path(result["artifact_dir"]) / "generated_policy.py"
        previous_policy = code_path.read_text(encoding="utf-8") if code_path.is_file() else None

    store = AttentionStore(store_path)
    success = bool(attempts and attempts[-1]["native_success"])
    store.transition_run(
        run_id,
        RunStatus.COMPLETED if success else RunStatus.FAILED,
        event_key=f"finish:{run_id}",
    )
    bundle_path = write_run_bundle(store, run_id, run_dir / "attention_bundle.json")
    summary = {
        "schema_version": "attentionbench.advisor-run.v1",
        "run_id": run_id,
        "task_id": task_id,
        "seed": seed,
        "policy_id": policy_id,
        "status": "completed" if success else "failed",
        "native_success": success,
        "attempts": [
            {
                "attempt_id": row["attention_trace"]["attempt_id"],
                "artifact_dir": row["artifact_dir"],
                "native_success": row["native_success"],
                "status": row["status"],
            }
            for row in attempts
        ],
        "decisions": decisions,
        "requests": requests,
        "advisor_error": advisor_error,
        "resource_usage": store.resource_status(run_id),
        "bundle": str(bundle_path.resolve()),
        "store": str(store_path.resolve()),
    }
    (run_dir / "advisor_run.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def _default_glm_transport(request: dict[str, Any]):
    return ParccGLMAdvisorTransport()(request)


def _verified_public_images(trace: dict[str, Any], episode_dir: Path) -> tuple[str, ...]:
    """Attach only camera artifacts whose hashes match the Advisor packet."""

    evidence = {
        item["kind"]: item
        for item in trace.get("evidence", [])
        if isinstance(item, dict) and item.get("kind") in {"initial_observation", "final_observation"}
    }
    images = []
    for kind, name in (
        ("initial_observation", "initial_observation.npz"),
        ("final_observation", "final_observation.npz"),
    ):
        item = evidence.get(kind)
        path = episode_dir / name
        if item is None or not path.is_file():
            continue
        expected_uri = f"artifact://{episode_dir.name}/{name}"
        if item.get("uri") != expected_uri:
            raise ValueError(f"Advisor evidence URI does not match {name}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item.get("sha256"):
            raise ValueError(f"Advisor evidence hash does not match {name}")
        with np.load(path, allow_pickle=False) as observation:
            if "agentview_image" not in observation:
                continue
            images.append(image_data_url(observation["agentview_image"]))
    return tuple(images)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/attentionbench-advisor"))
    parser.add_argument("--policy", choices=ADVISOR_RUN_POLICY_IDS, default="reactive_help")
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--assistance-credits", type=int, default=1)
    parser.add_argument("--token-limit", type=int, default=30000)
    parser.add_argument("--service-url")
    parser.add_argument("--skip-review", action="store_true")
    args = parser.parse_args()
    result = run_advised_parcc_episode(
        task_id=args.task,
        seed=args.seed,
        artifact_root=args.artifact_root,
        policy_id=args.policy,
        max_attempts=args.max_attempts,
        assistance_credits=args.assistance_credits,
        token_limit=args.token_limit,
        service_url=args.service_url,
        skip_review=args.skip_review,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["native_success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
