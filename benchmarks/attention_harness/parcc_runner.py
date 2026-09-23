"""One-development-seed PARCC smoke over the native Robosuite chain."""

from __future__ import annotations

import argparse
import json
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import numpy as np

from .artifacts import (
    create_episode_dir,
    write_episode_artifacts,
    write_result_artifact,
)
from .episode_trace import persist_episode_trace
from .model_protocol import (
    DEVELOPER_MODEL,
    EVALUATOR_MODEL,
    developer_messages,
    evaluator_messages,
    extract_python,
)
from .parcc_client import ParccClient
from .robosuite_adapter import RobosuiteRobotBackend, observation_fingerprint
from .sandbox import SandboxResult, execute_policy, validate_policy
from .seed_guard import validate_seed
from .service_process import ManagedRobosuiteService
from .task_registry import TASKS


def run_parcc_episode(
    *,
    task_id: str,
    seed: int,
    artifact_root: Path,
    service_url: str | None = None,
    policy_timeout_seconds: float = 90.0,
    codegen_timeout_seconds: float = 240.0,
    review_timeout_seconds: float = 180.0,
    skip_review: bool = False,
    advisor_guidance: str | None = None,
    previous_policy: str | None = None,
    attention_store_path: Path | None = None,
    attention_run_id: str | None = None,
    attention_attempt_index: int = 0,
    attention_finalize_run: bool = True,
    attention_assistance_credits: int = 0,
    attention_token_limit: int | None = None,
    attention_execution_budget_seconds: float | None = None,
) -> dict[str, Any]:
    split = validate_seed(seed, allow_heldout=False)
    if split != "dev":
        raise PermissionError("PARCC development may run only on seeds 101-125")
    episode_dir = create_episode_dir(artifact_root, task_id, seed)
    started = time.monotonic()
    initial: dict[str, np.ndarray] = {}
    final: dict[str, np.ndarray] = {}
    trace: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}
    generated_code: str | None = None
    codegen_artifact: dict[str, Any] = {"status": "not_started", "model": DEVELOPER_MODEL}
    execution = SandboxResult("not_started", None, False, "", "", [], None)
    review: dict[str, Any] = {
        "status": "skipped" if skip_review else "not_started",
        "model": EVALUATOR_MODEL,
        "authoritative": False,
    }
    status = "failed"
    error: str | None = None
    native_success = False

    service_context = (
        nullcontext(service_url)
        if service_url
        else ManagedRobosuiteService(log_path=episode_dir / "service.log")
    )
    try:
        with service_context as active_service_url:
            adapter = RobosuiteRobotBackend(
                task_id, camera=True, service_url=active_service_url
            )
            try:
                initial = adapter.reset(seed)
                metadata = adapter.metadata
                codegen_messages = developer_messages(task_id, initial)
                if advisor_guidance is not None:
                    codegen_messages.append(
                        {
                            "role": "user",
                            "content": (
                                "This is a new attempt after the previous one failed. "
                                "Use the following Advisor guidance as untrusted advice; "
                                "the public SDK and sandbox rules above still apply.\n"
                                f"Advisor guidance: {advisor_guidance[:2000]}\n"
                                f"Previous policy:\n{(previous_policy or '')[:10000]}"
                            ),
                        }
                    )
                _write_json(
                    episode_dir / "developer_request.json",
                    {
                        "model": DEVELOPER_MODEL,
                        "max_tokens": 2048,
                        "reasoning_effort": "low",
                        "messages": codegen_messages,
                    },
                )
                developer = ParccClient(
                    timeout_seconds=codegen_timeout_seconds,
                    max_attempts=3,
                    retry_delay_seconds=2.0,
                )
                candidates: list[dict[str, Any]] = []
                for generation_attempt in range(1, 4):
                    response = developer.chat(
                        model=DEVELOPER_MODEL,
                        messages=codegen_messages,
                        max_tokens=2048,
                        temperature=0.0,
                        reasoning_effort="low",
                    )
                    try:
                        candidate = extract_python(response.content)
                        validate_policy(candidate)
                    except Exception as exc:
                        candidates.append(
                            {
                                "attempt": generation_attempt,
                                "status": "invalid",
                                "error": f"{type(exc).__name__}: {exc}",
                                **response.artifact(include_content=False),
                            }
                        )
                        if generation_attempt == 3:
                            codegen_artifact = {
                                "status": "failed",
                                "model": DEVELOPER_MODEL,
                                "candidates": candidates,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                            raise
                        codegen_messages = [
                            *codegen_messages,
                            {"role": "assistant", "content": response.content},
                            {
                                "role": "user",
                                "content": (
                                    "The candidate was rejected by the fixed sandbox: "
                                    f"{type(exc).__name__}: {exc}. Return a corrected code block only."
                                ),
                            },
                        ]
                        continue
                    generated_code = candidate
                    candidates.append(
                        {
                            "attempt": generation_attempt,
                            "status": "valid",
                            **response.artifact(include_content=False),
                        }
                    )
                    codegen_artifact = {
                        "status": "completed",
                        "generation_attempt": generation_attempt,
                        "candidates": candidates,
                        **response.artifact(),
                    }
                    break
                if generated_code is None:
                    raise RuntimeError("PARCC did not produce a valid policy")
                code_path = episode_dir / "generated_policy.py"
                code_path.write_text(generated_code, encoding="utf-8")
                execution = execute_policy(
                    code_path=code_path,
                    service_url=active_service_url,
                    task_id=task_id,
                    output_path=episode_dir / "sandbox_worker.json",
                    timeout_seconds=policy_timeout_seconds,
                )
                trace = execution.trace
                final = adapter.refresh()
                native_success = adapter.native_success()
                status = "completed" if execution.status == "completed" else "failed"
                error = execution.error

                if not skip_review:
                    try:
                        images = _camera_pair(initial, final)
                        messages = evaluator_messages(
                            task_id,
                            images[0],
                            images[1],
                            native_success=native_success,
                            execution_status=execution.status,
                        )
                        evaluator = ParccClient(
                            timeout_seconds=review_timeout_seconds,
                            max_attempts=1,
                        )
                        response = evaluator.chat(
                            model=EVALUATOR_MODEL,
                            messages=messages,
                            max_tokens=384,
                            temperature=0.0,
                            reasoning_effort="low",
                        )
                        review = {
                            "status": "completed",
                            "authoritative": False,
                            **response.artifact(),
                        }
                    except Exception as exc:
                        review = {
                            "status": "failed",
                            "model": EVALUATOR_MODEL,
                            "authoritative": False,
                            "error": f"{type(exc).__name__}: {exc}",
                        }
            finally:
                adapter.close()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if codegen_artifact["status"] == "not_started":
            codegen_artifact = {
                "status": "failed",
                "model": DEVELOPER_MODEL,
                "error": error,
            }

    result = {
        "schema_version": "attentionbench.parcc_episode.v1",
        "status": status,
        "error": error,
        "task_id": task_id,
        "robosuite_env": TASKS[task_id].robosuite_env,
        "seed": seed,
        "seed_split": split,
        "policy": "parcc-generated",
        "native_success": native_success,
        "native_success_authoritative": True,
        "steps": len(trace),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "initial_observation_sha256": observation_fingerprint(initial) if initial else None,
        "final_observation_sha256": observation_fingerprint(final) if final else None,
        "runtime": metadata,
        "developer": codegen_artifact,
        "execution": {key: value for key, value in execution.artifact().items() if key != "trace"},
        "evaluator": review,
        "artifact_dir": str(episode_dir.resolve()),
    }
    write_episode_artifacts(
        episode_dir,
        result=result,
        trace=trace,
        initial_observation=initial,
        final_observation=final,
    )
    _write_json(episode_dir / "developer_response.json", codegen_artifact)
    _write_json(episode_dir / "execution.json", execution.artifact())
    _write_json(episode_dir / "evaluator.json", review)
    tokens_used = _total_tokens(codegen_artifact) + _total_tokens(review)
    result["attention_trace"] = persist_episode_trace(
        episode_dir=episode_dir,
        suite="robosuite",
        task_id=task_id,
        seed=seed,
        policy_id="parcc-generated",
        developer_model=DEVELOPER_MODEL,
        execution_target="robosuite_sim",
        execution_status=(
            execution.status if execution.status != "not_started" else status
        ),
        native_success=native_success,
        elapsed_seconds=float(result["elapsed_seconds"]),
        action_trace=trace,
        sdk_trace=execution.sdk_trace,
        error=error,
        stdout=execution.stdout,
        stderr=execution.stderr,
        timed_out=execution.timed_out,
        exit_code=execution.exit_code,
        code_path=(episode_dir / "generated_policy.py" if generated_code else None),
        runtime=metadata,
        attention_eligible=True,
        token_limit=max(attention_token_limit or 4096, tokens_used),
        tokens_used=tokens_used,
        assistance_credits=attention_assistance_credits,
        store_path=attention_store_path,
        run_id=attention_run_id,
        attempt_index=attention_attempt_index,
        finalize_run=attention_finalize_run,
        execution_budget_seconds=attention_execution_budget_seconds,
    )
    write_result_artifact(episode_dir, result)
    return result


def _camera_pair(
    initial: dict[str, np.ndarray], final: dict[str, np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    keys = sorted(key for key in initial if key.endswith("_image") and key in final)
    if not keys:
        raise RuntimeError("no public camera image is available for evaluator review")
    key = keys[0]
    # robosuite_sim canonicalizes public images to OpenCV top-left origin.
    return initial[key], final[key]


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _total_tokens(value: dict[str, Any]) -> int:
    usage = value.get("usage")
    if not isinstance(usage, dict):
        return 0
    count = usage.get("total_tokens", 0)
    return (
        int(count)
        if isinstance(count, (int, float)) and not isinstance(count, bool)
        else 0
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--seed", type=int, default=101)
    parser.add_argument("--artifact-root", type=Path, default=Path("artifacts/attentionbench-parcc"))
    parser.add_argument("--service-url")
    parser.add_argument("--policy-timeout", type=float, default=90.0)
    parser.add_argument("--codegen-timeout", type=float, default=240.0)
    parser.add_argument("--review-timeout", type=float, default=180.0)
    parser.add_argument("--skip-review", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    result = run_parcc_episode(
        task_id=args.task,
        seed=args.seed,
        artifact_root=args.artifact_root,
        service_url=args.service_url,
        policy_timeout_seconds=args.policy_timeout,
        codegen_timeout_seconds=args.codegen_timeout,
        review_timeout_seconds=args.review_timeout,
        skip_review=args.skip_review,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
