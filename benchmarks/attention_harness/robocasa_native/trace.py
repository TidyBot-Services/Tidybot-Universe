"""RoboCasa entry point for the shared episode trace assembler.

The current RoboCasa code in this repository is an infrastructure validator,
not a public policy runner. Production agent_server or a future native runner
should call this function with its public SDK events and artifacts. The
privileged teleport probe must never call it as an agent execution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..attention_modes import AssistanceMode
from ..episode_trace import persist_episode_trace


def persist_robocasa_episode_trace(
    *,
    episode_dir: Path,
    task_id: str,
    seed: int,
    policy_id: str,
    developer_model: str,
    execution_status: str,
    native_success: bool,
    elapsed_seconds: float,
    action_trace: Sequence[Mapping[str, Any]],
    sdk_trace: Sequence[Mapping[str, Any]],
    error: str | None = None,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    exit_code: int | None = None,
    stop_reason: str | None = None,
    code_path: Path | None = None,
    runtime: Mapping[str, Any] | None = None,
    hypothesis: str = "",
    token_limit: int = 0,
    tokens_used: int = 0,
    assistance_credits: int = 0,
    assistance_mode: AssistanceMode = AssistanceMode.BENCHMARK_PROXY,
) -> dict[str, Any]:
    return persist_episode_trace(
        episode_dir=episode_dir,
        suite="robocasa",
        task_id=task_id,
        seed=seed,
        policy_id=policy_id,
        developer_model=developer_model,
        execution_target="robocasa_sim",
        execution_status=execution_status,
        native_success=native_success,
        elapsed_seconds=elapsed_seconds,
        action_trace=action_trace,
        sdk_trace=sdk_trace,
        error=error,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        exit_code=exit_code,
        stop_reason=stop_reason,
        code_path=code_path,
        runtime=runtime,
        hypothesis=hypothesis,
        attention_eligible=True,
        token_limit=token_limit,
        tokens_used=tokens_used,
        assistance_credits=assistance_credits,
        assistance_mode=assistance_mode,
    )
