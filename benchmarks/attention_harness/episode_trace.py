"""Assemble one backend episode into the persistent two-layer trace model."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .attention_modes import AssistanceMode
from .core.artifacts import write_run_bundle
from .core.models import (
    AssistanceBudget,
    AttemptRecord,
    AttemptStatus,
    EvidenceRef,
    ExecutionOutcome,
    RawExecutionTrace,
    RunRecord,
    RunStatus,
    TraceEvent,
    TraceVisibility,
)
from .core.store import AttentionStore
from .core.trace import TracePipeline


def persist_episode_trace(
    *,
    episode_dir: Path,
    suite: str,
    task_id: str,
    seed: int,
    policy_id: str,
    developer_model: str,
    execution_target: str,
    execution_status: str,
    native_success: bool,
    elapsed_seconds: float,
    action_trace: Sequence[Mapping[str, Any]],
    sdk_trace: Sequence[Mapping[str, Any]] = (),
    error: str | None = None,
    stdout: str = "",
    stderr: str = "",
    timed_out: bool = False,
    exit_code: int | None = None,
    stop_reason: str | None = None,
    code_path: Path | None = None,
    runtime: Mapping[str, Any] | None = None,
    hypothesis: str = "",
    attention_eligible: bool = True,
    token_limit: int = 0,
    tokens_used: int = 0,
    assistance_credits: int = 0,
    assistance_mode: AssistanceMode = AssistanceMode.BENCHMARK_PROXY,
    store_path: Path | None = None,
    run_id: str | None = None,
    attempt_index: int = 0,
    finalize_run: bool = True,
    execution_budget_seconds: float | None = None,
) -> dict[str, Any]:
    """Persist a complete raw ledger and, on eligible failure, its projection.

    The caller writes normal episode artifacts first. This function then hashes
    those files, creates a per-episode Attention store, and returns stable IDs
    for inclusion in ``result.json``.
    """

    episode_dir = episode_dir.resolve()
    if not episode_dir.is_dir():
        raise FileNotFoundError(f"episode directory does not exist: {episode_dir}")
    if elapsed_seconds < 0:
        raise ValueError("elapsed_seconds must be non-negative")
    if tokens_used < 0 or token_limit < 0:
        raise ValueError("token counts must be non-negative")
    if attempt_index < 0:
        raise ValueError("attempt_index must be non-negative")

    identity = episode_dir.name
    run_id = run_id or f"run:{identity}"
    attempt_id = f"attempt:{identity}:{attempt_index}"
    execution_id = f"execution:{identity}:{attempt_index}"
    raw_trace_id = f"raw-trace:{identity}:{attempt_index}"
    advisor_trace_id = f"trace:{identity}:{attempt_index}"
    failed = execution_status != "completed" or not native_success

    database_path = (store_path or episode_dir / "attention.sqlite3").resolve()
    store = AttentionStore(database_path)
    existing_run = store.get_run(run_id)
    existing_attempt = store.get_attempt(attempt_id)
    result_path = episode_dir / "result.json"
    attempt_started_at = (
        float(existing_attempt["started_at"])
        if existing_attempt is not None
        else result_path.stat().st_mtime - elapsed_seconds
        if result_path.is_file()
        else episode_dir.stat().st_mtime
    )
    created_at = (
        float(existing_run["created_at"])
        if existing_run is not None
        else attempt_started_at
    )
    execution_budget = max(
        float(execution_budget_seconds if execution_budget_seconds is not None else elapsed_seconds),
        0.001,
    )
    run = RunRecord(
        run_id=run_id,
        suite=suite,
        task_id=task_id,
        seed=seed,
        policy_id=policy_id,
        developer_model=developer_model,
        assistance_mode=assistance_mode,
        execution_target=execution_target,
        budget=AssistanceBudget(
            assistance_credits=assistance_credits,
            token_limit=max(token_limit, tokens_used),
            execution_seconds=execution_budget,
        ),
        created_at=created_at,
    )
    if existing_run is None:
        store.create_run(run)
    store.transition_run(run_id, RunStatus.RUNNING, event_key=f"start:{run_id}")
    if existing_attempt is None:
        store.create_attempt(
            AttemptRecord(
                attempt_id=attempt_id,
                run_id=run_id,
                index=attempt_index,
                started_at=attempt_started_at,
            )
        )

    public_visibility = (
        (TraceVisibility.ADVISOR,)
        if attention_eligible
        else (TraceVisibility.INTERNAL,)
    )
    code = _code_artifact(code_path, episode_dir) if attention_eligible else {}
    evidence = _evidence_refs(
        episode_dir,
        public_visibility=public_visibility,
        stdout=stdout,
        stderr=stderr,
        context={
            "suite": suite,
            "task_id": task_id,
            "seed": seed,
            "policy_id": policy_id,
            "execution_status": execution_status,
            "timed_out": timed_out,
            "stop_reason": stop_reason,
            "error": error,
        },
    )
    visible_evidence_ids = tuple(
        item.evidence_id
        for item in evidence
        if TraceVisibility.ADVISOR in item.visibility
        or TraceVisibility.PUBLIC in item.visibility
    )
    events = _trace_events(
        sdk_trace=sdk_trace,
        action_trace=action_trace,
        failed=failed,
        attention_eligible=attention_eligible,
        execution_status=execution_status,
        error=error,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        exit_code=exit_code,
        native_success=native_success,
        evidence_refs=visible_evidence_ids,
    )
    raw = RawExecutionTrace(
        raw_trace_id=raw_trace_id,
        run_id=run_id,
        attempt_id=attempt_id,
        execution_id=execution_id,
        created_at=attempt_started_at,
        agent_state="blocked_after_failure" if failed else "completed",
        events=events,
        evidence=evidence,
        hypothesis=hypothesis,
        code=code,
        outcome=ExecutionOutcome(
            status=execution_status,
            stop_reason=stop_reason,
            exit_code=exit_code,
            timed_out=timed_out,
            elapsed_seconds=elapsed_seconds,
            native_success=native_success,
            evaluator_verdict="succeeded" if native_success else "failed",
            evaluator_authoritative=True,
        ),
        complete=not timed_out,
        metadata={
            "suite": suite,
            "task_id": task_id,
            "seed": seed,
            "policy_id": policy_id,
            "runtime": dict(runtime or {}),
        },
    )
    advisor_packet = None
    if failed and attention_eligible:
        advisor_packet = TracePipeline(store).persist(
            raw, trace_id=advisor_trace_id
        )
    else:
        store.put_raw_trace(raw)

    attempt_status = _attempt_status(
        execution_status=execution_status,
        native_success=native_success,
        timed_out=timed_out,
    )
    store.complete_attempt(
        attempt_id,
        attempt_status,
        ended_at=attempt_started_at + elapsed_seconds,
        native_success=native_success,
        artifact_uri=_artifact_uri(episode_dir, "result.json"),
        event_key=f"finish:{attempt_id}",
    )
    if finalize_run:
        run_status = (
            RunStatus.COMPLETED
            if attempt_status is AttemptStatus.SUCCEEDED
            else RunStatus.FAILED
        )
        store.transition_run(run_id, run_status, event_key=f"finish:{run_id}")
    if tokens_used or elapsed_seconds:
        store.consume_resources(
            run_id,
            tokens=tokens_used,
            execution_seconds=elapsed_seconds,
            event_key=f"resources:{attempt_id}",
        )
    bundle_path = write_run_bundle(
        store, run_id, database_path.parent / "attention_bundle.json"
    )
    return {
        "schema_version": "attentionbench.episode-trace-link.v1",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "execution_id": execution_id,
        "raw_trace_id": raw_trace_id,
        "advisor_trace_id": advisor_packet.trace_id if advisor_packet else None,
        "store": str(database_path),
        "bundle": str(bundle_path.resolve()),
    }


def _trace_events(
    *,
    sdk_trace: Sequence[Mapping[str, Any]],
    action_trace: Sequence[Mapping[str, Any]],
    failed: bool,
    attention_eligible: bool,
    execution_status: str,
    error: str | None,
    stdout: str,
    stderr: str,
    timed_out: bool,
    exit_code: int | None,
    native_success: bool,
    evidence_refs: tuple[str, ...],
) -> tuple[TraceEvent, ...]:
    events: list[TraceEvent] = []
    public = (
        (TraceVisibility.ADVISOR,)
        if attention_eligible
        else (TraceVisibility.INTERNAL,)
    )
    events.append(
        TraceEvent(
            event_id="execution-started",
            sequence=0,
            timestamp=0.0,
            source="attention_harness",
            event_type="execution.started",
            operation="run_policy",
            status="completed",
            visibility=public,
        )
    )
    sequence = 1
    for index, item in enumerate(sdk_trace):
        events.append(
            TraceEvent(
                event_id=f"sdk-{index}",
                sequence=sequence,
                timestamp=float(item.get("timestamp", 0.0)),
                source=str(item.get("source", "robot_sdk")),
                event_type=str(item.get("event_type", "sdk.call")),
                operation=str(item.get("operation", "unknown")),
                status=str(item.get("status", "unknown")),
                visibility=public,
                duration_ms=_optional_float(item.get("duration_ms")),
                arguments=_mapping(item.get("arguments")),
                result=_mapping(item.get("result")),
                error=_optional_mapping(item.get("error")),
            )
        )
        sequence += 1
    # Low-level action records have no timestamps, so inventing an ordering
    # relative to semantic SDK calls would be misleading. Keep their full JSONL
    # as internal evidence and put only the count in the event stream.
    events.append(
        TraceEvent(
            event_id="action-log",
            sequence=sequence,
            timestamp=_next_timestamp(events),
            source="robot_backend",
            event_type="robot.action_log",
            operation="step_sequence",
            status="completed",
            visibility=(TraceVisibility.INTERNAL,),
            result={"action_count": len(action_trace)},
        )
    )
    sequence += 1

    final_error = None
    if failed:
        if error:
            error_type, _, message = error.partition(":")
            final_error = {
                "type": error_type or "ExecutionFailure",
                "message": message.strip() or error,
            }
        elif timed_out:
            final_error = {
                "type": "TimeoutError",
                "message": "execution exceeded its fixed timeout",
            }
        else:
            final_error = {
                "type": "TaskOutcomeFailure",
                "message": "execution finished but the task goal was not verified",
            }
    events.append(
        TraceEvent(
            event_id="execution-finished",
            sequence=sequence,
            timestamp=_next_timestamp(events),
            source="attention_harness",
            event_type="execution.finished",
            operation="run_policy",
            status="failed" if failed else "completed",
            visibility=public,
            arguments={},
            result={
                "execution_status": execution_status,
                "timed_out": timed_out,
                "exit_code": exit_code,
                "stdout_tail": stdout[-4000:],
                "stderr_tail": stderr[-4000:],
            },
            error=final_error,
            evidence_refs=evidence_refs,
        )
    )
    sequence += 1
    events.append(
        TraceEvent(
            event_id="native-evaluator",
            sequence=sequence,
            timestamp=_next_timestamp(events),
            source="native_evaluator",
            event_type="evaluator.result",
            operation="native_success",
            status="completed",
            visibility=(TraceVisibility.INTERNAL,),
            result={"native_success": native_success},
        )
    )
    return tuple(events)


def _evidence_refs(
    episode_dir: Path,
    *,
    public_visibility: tuple[TraceVisibility, ...],
    stdout: str,
    stderr: str,
    context: Mapping[str, Any],
) -> tuple[EvidenceRef, ...]:
    context_path = episode_dir / "trace_context.json"
    _write_text_if_changed(
        context_path,
        json.dumps(dict(context), indent=2, sort_keys=True) + "\n",
    )
    if stdout:
        _write_text_if_changed(episode_dir / "stdout.txt", stdout)
    if stderr:
        _write_text_if_changed(episode_dir / "stderr.txt", stderr)

    public_files = {
        "trace_context.json": ("execution_context", "application/json"),
        "initial_observation.npz": ("initial_observation", "application/x-npz"),
        "final_observation.npz": ("final_observation", "application/x-npz"),
        "stdout.txt": ("stdout", "text/plain"),
        "stderr.txt": ("stderr", "text/plain"),
        "generated_policy.py": ("policy_code", "text/x-python"),
        "policy.py": ("policy_code", "text/x-python"),
    }
    internal_files = {
        "trace.jsonl": ("action_trace", "application/x-ndjson"),
        "execution.json": ("sandbox_execution", "application/json"),
        "developer_request.json": ("developer_request", "application/json"),
        "developer_response.json": ("developer_response", "application/json"),
        "evaluator.json": ("evaluator_result", "application/json"),
        "service.log": ("service_log", "text/plain"),
    }
    values: list[EvidenceRef] = []
    for name, (kind, mime_type) in public_files.items():
        path = episode_dir / name
        if path.is_file():
            values.append(
                _evidence_ref(
                    episode_dir,
                    path,
                    kind=kind,
                    mime_type=mime_type,
                    visibility=public_visibility,
                )
            )
    for name, (kind, mime_type) in internal_files.items():
        path = episode_dir / name
        if path.is_file():
            values.append(
                _evidence_ref(
                    episode_dir,
                    path,
                    kind=kind,
                    mime_type=mime_type,
                    visibility=(TraceVisibility.INTERNAL,),
                )
            )
    return tuple(values)


def _evidence_ref(
    episode_dir: Path,
    path: Path,
    *,
    kind: str,
    mime_type: str,
    visibility: tuple[TraceVisibility, ...],
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=f"evidence:{path.name}",
        kind=kind,
        uri=_artifact_uri(episode_dir, path.name),
        sha256=_sha256_file(path),
        created_at=path.stat().st_mtime,
        visibility=visibility,
        mime_type=mime_type,
        metadata={"bytes": path.stat().st_size},
    )


def _code_artifact(path: Path | None, episode_dir: Path) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    source = path.resolve()
    if source.parent != episode_dir:
        target = episode_dir / "policy.py"
        if not target.is_file() or _sha256_file(target) != _sha256_file(source):
            shutil.copy2(source, target)
        source = target
    return {
        "artifact_uri": _artifact_uri(episode_dir, source.name),
        "sha256": _sha256_file(source),
        "content": source.read_text(encoding="utf-8"),
    }


def _attempt_status(
    *, execution_status: str, native_success: bool, timed_out: bool
) -> AttemptStatus:
    if timed_out or execution_status == "timeout":
        return AttemptStatus.TIMED_OUT
    if execution_status == "cancelled":
        return AttemptStatus.CANCELLED
    if execution_status == "completed" and native_success:
        return AttemptStatus.SUCCEEDED
    return AttemptStatus.FAILED


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _next_timestamp(events: Sequence[TraceEvent]) -> float:
    return max((event.timestamp for event in events), default=0.0) + 0.000001


def _artifact_uri(episode_dir: Path, name: str) -> str:
    return f"artifact://{episode_dir.name}/{name}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_text_if_changed(path: Path, content: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")
