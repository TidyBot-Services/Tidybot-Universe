"""Development-only Robosuite sim_gt runner with shared Memory Service evidence."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from attention_memory_service import MemoryService, MemoryServiceClient
from attention_memory_service.identity import store_id as memory_store_id
from tidybot_sdk import RobotBackend, TidyBotSDK
from tidybot_sdk.perception import ModeBoundPerceptionBackend, PerceptionMode

from ..artifacts import create_episode_dir, write_episode_artifacts, write_result_artifact
from ..core.advisor import AdvisorTransport
from ..core.artifacts import write_run_bundle
from ..core.models import MemoryStatus, MemoryUseRecord
from ..core.store import AttentionStore
from ..episode_trace import persist_episode_trace
from ..seed_guard import validate_seed
from ..sim_gt_memory import ask_advisor_and_create_candidate, policy_fingerprint
from ..task_registry import get_task
from .adapter import RobosuiteSimGTBackend
from .gt_perception import RobosuiteGTPerception


Policy = Callable[[TidyBotSDK, dict[str, Any]], None]
_VARIATION_KEYS = {
    "scene_id", "object_set_id", "camera_config_id", "task_variant_id",
    "camera_names", "task_prompt",
}


def run_robosuite_sim_gt_episode(
    *, task_id: str, seed: int, artifact_root: Path,
    adapter: RobosuiteSimGTBackend, policy: Policy, policy_id: str,
    perception_mode: PerceptionMode | str,
    action_backend: RobotBackend | None = None,
    store_path: Path | None = None,
    validation_memory_id: str | None = None,
    validation_variation: dict[str, Any] | None = None,
    validation_config_sha256: str | None = None,
    runtime_variation: dict[str, Any] | None = None,
    retrieve_memory: bool = True,
    advisor_transport: AdvisorTransport | None = None,
    assistance_credits: int = 0,
    advisor_sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    memory_gateway: MemoryService | MemoryServiceClient | None = None,
) -> dict[str, Any]:
    if PerceptionMode(perception_mode) is not PerceptionMode.SIM_GT:
        raise ValueError("this runner requires explicit perception_mode='sim_gt'")
    validate_seed(seed, allow_heldout=False)
    if isinstance(assistance_credits, bool) or assistance_credits < 0:
        raise ValueError("assistance_credits must be non-negative")
    if adapter.spec.task_id != task_id:
        raise ValueError("adapter task does not match runner task")
    action_backend = action_backend or adapter
    if action_backend.control_frame != "robosuite_world":
        raise ValueError("Robosuite GT positions require robosuite_world action coordinates")
    selected_variation = validation_variation or runtime_variation
    if selected_variation is not None:
        if set(selected_variation) != _VARIATION_KEYS:
            raise ValueError("variation must define scene/object/camera/task identities and concrete camera/prompt settings")
        if (
            selected_variation["camera_names"] != [adapter.camera_name]
            or not isinstance(selected_variation["task_prompt"], str)
            or not selected_variation["task_prompt"].strip()
            or any(not isinstance(selected_variation[key], str) or not selected_variation[key]
                   for key in ("scene_id", "object_set_id", "camera_config_id", "task_variant_id"))
        ):
            raise ValueError("variation does not match the active camera or has invalid identities")
    policy_sha256 = policy_fingerprint(policy)
    spec = get_task(task_id)
    episode_dir = create_episode_dir(artifact_root, task_id, seed)
    store = AttentionStore(store_path or episode_dir / "attention.sqlite3")
    if isinstance(memory_gateway, MemoryServiceClient):
        if memory_gateway.health().get("schema_version") != "attentionbench.memory-service.v2":
            raise RuntimeError("incompatible Memory Service schema")
        if memory_gateway.store_id() != memory_store_id(store.path):
            raise RuntimeError("Memory Service is connected to a different Attention store")
    memory_service = memory_gateway or MemoryService(store, artifact_root=artifact_root)
    context = {"perception_mode": "sim_gt", "suite": "robosuite", "task_id": task_id}
    if selected_variation is not None:
        context.update({key: selected_variation[key] for key in
                        ("scene_id", "object_set_id", "camera_config_id", "task_variant_id")})
    if validation_memory_id is not None:
        candidate = store.get_memory(validation_memory_id)
        if candidate is None or candidate.status is not MemoryStatus.CANDIDATE:
            raise ValueError("validation exposure requires a candidate memory")
        memory_service.provenance(validation_memory_id)
        if any(candidate.applicability.get(key) != context.get(key)
               for key in ("suite", "task_id", "perception_mode")):
            raise ValueError("validation candidate applicability mismatch")
        retrieved = [candidate]
    else:
        retrieved = memory_service.retrieve(context, now=clock()) if retrieve_memory else []
    memory_context = {}
    for item in retrieved:
        provenance = memory_service.provenance(item.memory_id)
        memory_context[item.memory_id] = {
            "memory_id": item.memory_id, "guidance": item.guidance,
            "artifact_kind": provenance["artifact"]["kind"],
            "source_kind": provenance["source_kind"],
            "perception_mode": item.applicability["perception_mode"],
            "validation_status": item.status.value,
        }
    used_memories: list[str] = []
    sdk_trace: list[dict[str, Any]] = [{
        "timestamp": 0.0, "source": "attention_harness.memory",
        "event_type": "attention.memory_catalog", "operation": "list_available",
        "status": "completed", "arguments": {"perception_mode": "sim_gt", "task_id": task_id},
        "result": {"memory_ids": [item.memory_id for item in retrieved]},
    }]

    def retrieve_memory(memory_id: str) -> dict[str, Any]:
        if memory_id not in memory_context:
            raise KeyError("memory is not available for this task and perception mode")
        if memory_id not in used_memories:
            used_memories.append(memory_id)
            sdk_trace.append({
                "timestamp": clock(), "source": "attention_harness.memory",
                "event_type": "attention.memory_retrieval", "operation": "retrieve",
                "status": "completed", "arguments": {"memory_id": memory_id},
                "result": {"source_kind": memory_context[memory_id]["source_kind"]},
            })
        return dict(memory_context[memory_id])

    sdk = TidyBotSDK(
        ModeBoundPerceptionBackend(
            action_backend, mode="sim_gt", target="robosuite_sim",
            provider=RobosuiteGTPerception(
                adapter,
                fixed_camera_names=None if selected_variation is None
                else selected_variation["camera_names"],
            ),
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
        _, applied = adapter.reset_attested(
            seed,
            variation=None if selected_variation is None else {
                key: selected_variation[key] for key in ("scene_id", "object_set_id")
            },
        )
        if selected_variation is not None and applied != {
            key: selected_variation[key] for key in ("scene_id", "object_set_id")
        }:
            raise RuntimeError("Robosuite service did not attest the planned variation")
        no_op_success = adapter.native_success()
        if no_op_success:
            raise RuntimeError("native success is true immediately after reset")
        initial_observation = sdk.sensors.get_observation()
        initial_objects = sdk.sensors.find_objects()
        policy(sdk, {
            "task_id": task_id, "goal": spec.prompt,
            "language": spec.prompt if selected_variation is None else selected_variation["task_prompt"],
            "perception_mode": "sim_gt", "initial_objects": initial_objects,
            "memory_catalog": [
                {key: value for key, value in item.items() if key != "guidance"}
                for item in memory_context.values()
            ],
            "retrieve_memory": retrieve_memory,
        })
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
                native_success = adapter.native_success()
            except Exception as exc:
                execution_status = "failed"
                error = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started
    for item in sdk_trace:
        if item.get("event_type") == "sdk.perception" and isinstance(item.get("result"), list):
            item["result"] = {"objects": item["result"], "perception_mode": "sim_gt"}
    action_trace = [
        {"step": index, "operation": item["operation"], "status": item["status"]}
        for index, item in enumerate(sdk_trace)
        if item.get("event_type") in {"sdk.arm_command", "sdk.gripper_command"}
    ]
    result: dict[str, Any] = {
        "schema_version": "attentionbench.robosuite-sim-gt-episode.v2",
        "score_namespace": "v2/gt_perception_attentionbench",
        "task_id": task_id, "seed": seed, "policy_id": policy_id,
        "policy_sha256": policy_sha256, "perception_mode": "sim_gt",
        "validation_variation": validation_variation,
        "validation_config_sha256": validation_config_sha256,
        "runtime_variation": runtime_variation,
        "execution_target": "robosuite_sim", "trusted_policy_callback": True,
        "formal_eligible": False, "status": execution_status,
        "native_success": bool(native_success and execution_status == "completed"),
        "no_op_success": no_op_success, "error": error,
        "elapsed_seconds": elapsed, "initial_object_count": len(initial_objects),
        "memory_ids": list(used_memories), "artifact_dir": str(episode_dir.resolve()),
    }
    write_episode_artifacts(
        episode_dir, result=result, trace=action_trace,
        initial_observation=initial_observation, final_observation=final_observation,
    )
    link = persist_episode_trace(
        episode_dir=episode_dir, suite="robosuite", task_id=task_id, seed=seed,
        policy_id=policy_id, developer_model="trusted-policy-callback",
        execution_target="robosuite_sim", execution_status=execution_status,
        native_success=result["native_success"], elapsed_seconds=elapsed,
        action_trace=action_trace, sdk_trace=sdk_trace, error=error,
        runtime={
            "perception_mode": "sim_gt", "score_namespace": result["score_namespace"],
            "memory_ids": result["memory_ids"], "policy_sha256": policy_sha256,
            "memory_exposure": "candidate_validation" if validation_memory_id
            else "trusted" if retrieved else "none",
            "validation_variation": validation_variation,
            "validation_config_sha256": validation_config_sha256,
        },
        assistance_credits=assistance_credits, store_path=store_path,
        execution_budget_seconds=300.0,
    )
    result["attention_trace"] = link
    bundle = write_run_bundle(store, link["run_id"], episode_dir / "attention_bundle.json")
    link["bundle"] = str(bundle.resolve())
    for item in retrieved:
        if item.memory_id not in used_memories or item.status is not MemoryStatus.TRUSTED:
            continue
        memory_service.record_use(MemoryUseRecord(
            use_id=f"use:{link['attempt_id']}:{item.memory_id}",
            memory_id=item.memory_id, memory_version=item.version,
            run_id=link["run_id"], attempt_id=link["attempt_id"],
            used_at=clock(),
            outcome="native_success" if result["native_success"] else "failed",
        ))
    if advisor_transport is not None and not result["native_success"] and assistance_credits:
        ask_advisor_and_create_candidate(
            store=store, result=result, transport=advisor_transport,
            sleeper=advisor_sleeper, clock=clock, memory_gateway=memory_service,
        )
    write_result_artifact(episode_dir, result)
    return result
