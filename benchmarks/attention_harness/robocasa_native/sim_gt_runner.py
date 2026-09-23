"""Development runner for the explicitly GT-labelled RoboCasa track.

The policy callback is trusted lab code. Formal generated-policy sandboxing
and the 25-seed stability gate are separate prerequisites for freezing a v2
score. The callback receives only the shared SDK and policy context, never
the task evaluator client.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tidybot_sdk import RobotBackend, TidyBotSDK
from tidybot_sdk.perception import ModeBoundPerceptionBackend, PerceptionMode

from ..artifacts import create_episode_dir, write_episode_artifacts, write_result_artifact
from ..attention_modes import AssistanceMode, MODE_SPECS, RequestState
from ..core.advisor import AdvisorTransport
from ..core.memory import MemoryManager
from ..core.models import (
    AttentionRequestRecord,
    MemoryRecord,
    MemoryStatus,
    MemoryUseRecord,
    RequestPriority,
    RequestType,
)
from ..core.runtime import AttentionRuntime
from ..core.store import AttentionStore
from ..episode_trace import persist_episode_trace
from ..parcc_advisor import parse_advisor_advice
from ..seed_guard import validate_seed
from ..v2_advisor import SimGTAdvisorProxy
from .client import RobocasaSimClient
from .gt_perception import RobocasaGTPerception
from .tasks import get_robocasa_task


Policy = Callable[[TidyBotSDK, dict[str, Any]], None]


def run_robocasa_sim_gt_episode(
    *,
    task_id: str,
    seed: int,
    artifact_root: Path,
    action_backend: RobotBackend,
    policy: Policy,
    policy_id: str,
    perception_mode: PerceptionMode | str,
    client: RobocasaSimClient | None = None,
    store_path: Path | None = None,
    advisor_transport: AdvisorTransport | None = None,
    assistance_credits: int = 0,
    advisor_sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run one trusted policy attempt, evaluator check, trace, and optional help.

    A configured Advisor is asked only after a failed attempt. Its response
    becomes a mode-tagged *candidate* memory, never trusted automatically.
    Trusted memory in the supplied store is retrievable only when explicitly
    tagged for sim_gt and this task.
    """

    if PerceptionMode(perception_mode) is not PerceptionMode.SIM_GT:
        raise ValueError("this runner requires explicit perception_mode='sim_gt'")
    validate_seed(seed, allow_heldout=False)
    if assistance_credits < 0 or isinstance(assistance_credits, bool):
        raise ValueError("assistance_credits must be non-negative")
    if action_backend.control_frame != "arm_base":
        raise ValueError("RoboCasa GT positions require arm_base action coordinates")
    spec = get_robocasa_task(task_id)
    service = client or RobocasaSimClient(task_id)
    if service.spec.task_id != task_id:
        raise ValueError("task client does not match runner task")
    task_info = service.assert_task()
    episode_dir = create_episode_dir(artifact_root, task_id, seed)
    store = AttentionStore(store_path or episode_dir / "attention.sqlite3")
    memory = MemoryManager(store)
    retrieved = [
        item for item in memory.retrieve(
            {"perception_mode": "sim_gt", "suite": "robocasa", "task_id": task_id},
            now=clock(),
        )
        if item.applicability.get("perception_mode") == "sim_gt"
        and item.applicability.get("suite") == "robocasa"
        and item.applicability.get("task_id") == task_id
        and item.status is MemoryStatus.TRUSTED
    ]
    sdk_trace: list[dict[str, Any]] = [
        {
            "timestamp": 0.0,
            "source": "attention_harness.memory",
            "event_type": "attention.memory_retrieval",
            "operation": "retrieve",
            "status": "completed",
            "arguments": {"perception_mode": "sim_gt", "task_id": task_id},
            "result": {"memory_ids": [item.memory_id for item in retrieved]},
        }
    ]
    sdk = TidyBotSDK(
        ModeBoundPerceptionBackend(
            action_backend,
            mode="sim_gt",
            target="robocasa_sim",
            provider=RobocasaGTPerception(service),
        ),
        event_sink=sdk_trace.append,
    )

    started = time.monotonic()
    execution_status = "completed"
    error: str | None = None
    native_success = False
    no_op_success = False
    initial_observation: dict[str, Any] = {}
    final_observation: dict[str, Any] = {}
    initial_objects: list[dict[str, Any]] = []
    try:
        reset = service._call("POST", "/reset", {"seed": seed}, timeout=120.0)
        if reset.get("status") != "ok":
            raise RuntimeError(f"RoboCasa reset failed: {reset}")
        no_op_success = service.native_success()
        if no_op_success:
            raise RuntimeError("native success is true immediately after reset")
        initial_observation = sdk.sensors.get_observation()
        initial_objects = sdk.sensors.find_objects()
        policy(
            sdk,
            {
                "task_id": task_id,
                "goal": spec.goal,
                "language": task_info["lang"],
                "perception_mode": "sim_gt",
                "initial_objects": initial_objects,
                "memories": [
                    {"memory_id": item.memory_id, "guidance": item.guidance}
                    for item in retrieved
                ],
            },
        )
    except Exception as exc:
        execution_status = "failed"
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            final_observation = sdk.sensors.get_observation()
        except Exception as exc:
            if error is None:
                execution_status = "failed"
                error = f"{type(exc).__name__}: {exc}"
        if not no_op_success:
            try:
                native_success = service.native_success()
            except Exception as exc:
                execution_status = "failed"
                error = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started
    # The frozen v1 trace assembler accepts mapping-valued event results.
    # Preserve v2 object provenance rather than letting a list be discarded.
    for item in sdk_trace:
        if item.get("event_type") == "sdk.perception" and isinstance(item.get("result"), list):
            item["result"] = {
                "objects": item["result"],
                "perception_mode": "sim_gt",
            }
    action_trace = [
        {"step": index, "operation": item["operation"], "status": item["status"]}
        for index, item in enumerate(sdk_trace)
        if item.get("event_type") in {"sdk.arm_command", "sdk.gripper_command"}
    ]
    result: dict[str, Any] = {
        "schema_version": "attentionbench.robocasa-sim-gt-episode.v2",
        "score_namespace": "v2/gt_perception_attentionbench",
        "task_id": task_id,
        "seed": seed,
        "policy_id": policy_id,
        "perception_mode": "sim_gt",
        "execution_target": "robocasa_sim",
        "trusted_policy_callback": True,
        "formal_eligible": False,
        "status": execution_status,
        "native_success": bool(native_success and execution_status == "completed"),
        "no_op_success": no_op_success,
        "error": error,
        "elapsed_seconds": elapsed,
        "initial_object_count": len(initial_objects),
        "memory_ids": [item.memory_id for item in retrieved],
        "artifact_dir": str(episode_dir.resolve()),
    }
    write_episode_artifacts(
        episode_dir,
        result=result,
        trace=action_trace,
        initial_observation=initial_observation,
        final_observation=final_observation,
    )
    link = persist_episode_trace(
        episode_dir=episode_dir,
        suite="robocasa",
        task_id=task_id,
        seed=seed,
        policy_id=policy_id,
        developer_model="trusted-policy-callback",
        execution_target="robocasa_sim",
        execution_status=execution_status,
        native_success=result["native_success"],
        elapsed_seconds=elapsed,
        action_trace=action_trace,
        sdk_trace=sdk_trace,
        error=error,
        runtime={"perception_mode": "sim_gt", "score_namespace": result["score_namespace"]},
        assistance_credits=assistance_credits,
        store_path=store_path,
    )
    result["attention_trace"] = link
    for item in retrieved:
        memory.record_use(
            MemoryUseRecord(
                use_id=f"use:{link['attempt_id']}:{item.memory_id}",
                memory_id=item.memory_id,
                memory_version=item.version,
                run_id=link["run_id"],
                attempt_id=link["attempt_id"],
                used_at=clock(),
                outcome="native_success" if result["native_success"] else "failed",
            )
        )
    if advisor_transport is not None and not result["native_success"] and assistance_credits:
        _ask_advisor_and_create_candidate(
            store=store,
            result=result,
            task_id=task_id,
            transport=advisor_transport,
            sleeper=advisor_sleeper,
            clock=clock,
        )
    write_result_artifact(episode_dir, result)
    return result


def _ask_advisor_and_create_candidate(
    *,
    store: AttentionStore,
    result: dict[str, Any],
    task_id: str,
    transport: AdvisorTransport,
    sleeper: Callable[[float], None],
    clock: Callable[[], float],
) -> None:
    link = result["attention_trace"]
    trace_id = link["advisor_trace_id"]
    if trace_id is None:
        result["advisor_error"] = "failed attempt has no Advisor-safe trace"
        return
    request = AttentionRequestRecord(
        request_id=f"advisor-request:{link['attempt_id']}",
        run_id=link["run_id"],
        attempt_id=link["attempt_id"],
        trace_id=trace_id,
        request_type=RequestType.HINT,
        reason="GT-perception policy attempt did not achieve native success",
        priority=RequestPriority.NORMAL,
        created_at=clock(),
        deadline_at=None,
        mode=AssistanceMode.BENCHMARK_PROXY,
    )
    proxy = SimGTAdvisorProxy(
        store,
        transport=transport,
        latency_seconds=MODE_SPECS[AssistanceMode.BENCHMARK_PROXY].proxy_latency_seconds,
        sleeper=sleeper,
    )
    runtime = AttentionRuntime(store, proxy, clock=clock)
    runtime.open_request(request)
    try:
        answered = runtime.resolve_benchmark_proxy(request.request_id)
        response = store.get_response(answered.response_id)
        advice = parse_advisor_advice(response["content"], request_type="hint")
        trace = store.get_trace(trace_id)
        evidence_ids = tuple(item["evidence_id"] for item in trace["evidence"])
        candidate = MemoryRecord(
            memory_id=f"candidate:{link['attempt_id']}",
            version=1,
            source_trace_id=trace_id,
            guidance=advice.guidance,
            candidate_repair=advice.guidance,
            applicability={
                "perception_mode": "sim_gt",
                "suite": "robocasa",
                "task_id": task_id,
            },
            evidence_refs=evidence_ids,
            created_at=clock(),
        )
        MemoryManager(store).add_candidate(candidate)
        result["advisor_advice"] = advice.artifact()
        result["memory_candidate_id"] = candidate.memory_id
    except Exception as exc:
        current = store.get_request(request.request_id)
        if current is not None and current.state is RequestState.PENDING:
            runtime.cancel(request.request_id)
        result["advisor_error"] = f"{type(exc).__name__}: {exc}"
