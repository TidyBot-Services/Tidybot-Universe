"""Development runner for the explicitly GT-labelled RoboCasa track.

The policy callback is trusted lab code. Formal generated-policy sandboxing
and the 25-seed stability gate are separate prerequisites for freezing a v2
score. The callback receives only the shared SDK and policy context, never
the task evaluator client.
"""

from __future__ import annotations

import json
import hashlib
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from attention_memory_service import MemoryService, MemoryServiceClient
from attention_memory_service.identity import store_id as memory_store_id
from tidybot_sdk import RobotBackend, TidyBotSDK
from tidybot_sdk.perception import ModeBoundPerceptionBackend, PerceptionMode

from ..attention_modes import AssistanceMode
from ..artifacts import create_episode_dir, write_episode_artifacts, write_result_artifact
from ..core.advisor import AdvisorTransport
from ..core.artifacts import write_run_bundle
from ..core.models import MemoryStatus, MemoryUseRecord
from ..core.store import AttentionStore
from ..core.control import EmergencyInterrupt, raise_if_interrupted
from ..episode_trace import persist_episode_trace
from ..policy_input import public_attention_input
from ..seed_guard import validate_seed
from ..ui_media import AdvisorCameraRecorder, robocasa_camera_frame
from ..sim_gt_memory import ask_advisor_and_create_candidate as _ask_advisor_and_create_candidate
from ..sim_gt_memory import policy_fingerprint as _policy_fingerprint
from .client import RobocasaSimClient
from .gt_perception import RobocasaGTPerception
from .mobile_sdk import RobocasaMobileSDK
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
    validation_memory_id: str | None = None,
    validation_variation: dict[str, Any] | None = None,
    validation_config_sha256: str | None = None,
    runtime_variation: dict[str, Any] | None = None,
    retrieve_memory: bool = True,
    advisor_transport: AdvisorTransport | None = None,
    assistance_credits: int = 0,
    assistance_mode: AssistanceMode = AssistanceMode.BENCHMARK_PROXY,
    token_limit: int = 30_000,
    advisor_sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    memory_gateway: MemoryService | MemoryServiceClient | None = None,
    policy_code_path: Path | None = None,
    hypothesis: str = "",
    prior_attempts: list[dict[str, Any]] | None = None,
    attention_input: dict[str, Any] | None = None,
    policy_execution_mode: str = "trusted_callback",
    episode_deadline_monotonic: float | None = None,
    attention_run_id: str | None = None,
    attention_attempt_index: int = 0,
    attention_finalize_run: bool = True,
    attention_run_budget_seconds: float = 300.0,
    interrupt_check: Callable[[], bool] | None = None,
    replay_camera_ws: str | None = None,
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
    if token_limit < 0 or isinstance(token_limit, bool):
        raise ValueError("token_limit must be non-negative")
    if action_backend.control_frame != "arm_base":
        raise ValueError("RoboCasa GT positions require arm_base action coordinates")
    if policy_code_path is not None:
        source = policy_code_path.read_bytes()
        if len(source) > 20_000:
            raise ValueError("policy source exceeds 20 KB limit")
        policy_sha256 = hashlib.sha256(source).hexdigest()
    else:
        policy_sha256 = _policy_fingerprint(policy)
    if not isinstance(hypothesis, str) or len(hypothesis) > 4000:
        raise ValueError("hypothesis must be a string of at most 4000 characters")
    history = _public_attempt_history(prior_attempts or [])
    attention_input = public_attention_input(attention_input)
    if policy_execution_mode not in {"trusted_callback", "sandboxed_generated"}:
        raise ValueError("unsupported policy execution mode")
    if policy_execution_mode == "sandboxed_generated" and policy_code_path is None:
        raise ValueError("sandboxed generated policy requires source code")
    spec = get_robocasa_task(task_id)
    service = client or RobocasaSimClient(task_id)
    if service.spec.task_id != task_id:
        raise ValueError("task client does not match runner task")
    task_info = service.assert_task()
    episode_dir = create_episode_dir(artifact_root, task_id, seed)
    recorder = (AdvisorCameraRecorder(
        episode_dir, lambda: robocasa_camera_frame(replay_camera_ws), image_format="jpg"
    ) if replay_camera_ws else None)
    store = AttentionStore(store_path or episode_dir / "attention.sqlite3")
    if isinstance(memory_gateway, MemoryServiceClient):
        health = memory_gateway.health()
        if health.get("schema_version") != "attentionbench.memory-service.v2":
            raise RuntimeError("incompatible Memory Service schema")
        if "attempt_bound_use_grants" not in health.get("capabilities", []):
            raise RuntimeError("Memory Service lacks attempt-bound use grants")
        if memory_gateway.store_id() != memory_store_id(store.path):
            raise RuntimeError("Memory Service is connected to a different Attention store")
    memory_service = memory_gateway or MemoryService(store, artifact_root=artifact_root)
    selected_variation = validation_variation or runtime_variation
    if selected_variation is not None:
        required = {"scene_id", "object_set_id", "camera_config_id", "task_variant_id", "camera_names", "task_prompt"}
        if set(selected_variation) != required:
            raise ValueError("variation must define scene/object/camera/task identities and concrete camera/prompt settings")
        if (
            not isinstance(selected_variation["camera_names"], list)
            or not selected_variation["camera_names"]
            or any(not isinstance(name, str) or not name for name in selected_variation["camera_names"])
            or not isinstance(selected_variation["task_prompt"], str)
            or not selected_variation["task_prompt"].strip()
        ):
            raise ValueError("invalid variation camera names or task prompt")
    context = {"perception_mode": "sim_gt", "suite": "robocasa", "task_id": task_id}
    if selected_variation is not None:
        context.update({key: selected_variation[key] for key in (
            "scene_id", "object_set_id", "camera_config_id", "task_variant_id",
            "camera_names", "task_prompt",
        )})
    if validation_memory_id is not None:
        candidate = store.get_memory(validation_memory_id)
        if candidate is None or candidate.status is not MemoryStatus.CANDIDATE:
            raise ValueError("validation exposure requires a candidate memory")
        memory_service.provenance(validation_memory_id)
        if any(candidate.applicability.get(key) != context.get(key) for key in ("suite", "task_id", "perception_mode")):
            raise ValueError("validation candidate applicability mismatch")
        retrieved = [candidate]
    else:
        retrieved = memory_service.retrieve(context, now=clock()) if retrieve_memory else []
    memory_context = {}
    for item in retrieved:
        provenance = memory_service.provenance(item.memory_id)
        memory_context[item.memory_id] = {
            "memory_id": item.memory_id,
            "version": item.version,
            "guidance": item.guidance,
            "artifact_kind": provenance["artifact"]["kind"],
            "source_kind": provenance["source_kind"],
            "perception_mode": item.applicability["perception_mode"],
            "validation_status": item.status.value,
        }
    used_memories: list[str] = []
    used_memory_at: dict[str, float] = {}
    used_memory_grants: dict[str, str] = {}
    task_language = task_info["lang"] if selected_variation is None else selected_variation["task_prompt"]
    sdk_trace: list[dict[str, Any]] = [
        {
            "timestamp": 0.0,
            "source": "attention_harness.task",
            "event_type": "attention.task_spec",
            "operation": "present_goal",
            "status": "completed",
            "arguments": {},
            "result": {
                "task_id": task_id,
                "goal": spec.goal,
                "language": task_language,
                "perception_mode": "sim_gt",
            },
        },
        {
            "timestamp": 0.0,
            "source": "attention_harness.memory",
            "event_type": "attention.memory_catalog",
            "operation": "list_available",
            "status": "completed",
            "arguments": {"perception_mode": "sim_gt", "task_id": task_id},
            "result": {"memory_ids": [item.memory_id for item in retrieved]},
        }
    ]
    if history:
        sdk_trace.append({
            "timestamp": 0.0,
            "source": "attention_harness.history",
            "event_type": "attention.prior_attempts",
            "operation": "summarize_prior_failures",
            "status": "completed",
            "arguments": {},
            "result": {"attempts": history},
        })
    if attention_input:
        sdk_trace.append({
            "timestamp": 0.0, "source": "attention_harness.policy",
            "event_type": "attention.policy_input", "operation": "present_attention_input",
            "status": "completed", "arguments": {}, "result": attention_input,
        })

    def retrieve_memory(memory_id: str) -> dict[str, Any]:
        if memory_id not in memory_context:
            raise KeyError("memory is not available for this task and perception mode")
        if validation_memory_id is None:
            current = next((item for item in memory_service.retrieve(context, now=clock())
                            if item.memory_id == memory_id), None)
            if current is None or current.version != memory_context[memory_id]["version"]:
                raise KeyError("memory is no longer trusted or applicable")
        else:
            current = memory_service.get_memory(memory_id)
            if current.status.value != MemoryStatus.CANDIDATE.value or current.version != memory_context[memory_id]["version"]:
                raise KeyError("validation candidate is no longer available")
        if memory_id not in used_memories:
            if validation_memory_id is None:
                grant = memory_service.authorize_use(
                    context, memory_id=memory_id,
                    attempt_id=f"attempt:{episode_dir.name}:0", now=clock(),
                )
                if grant["version"] != memory_context[memory_id]["version"]:
                    raise KeyError("memory version changed before use")
                used_memory_at[memory_id] = grant["used_at"]
                used_memory_grants[memory_id] = grant["grant_id"]
            else:
                used_memory_at[memory_id] = clock()
            used_memories.append(memory_id)
            sdk_trace.append({
                "timestamp": used_memory_at[memory_id],
                "source": "attention_harness.memory",
                "event_type": "attention.memory_retrieval",
                "operation": "retrieve",
                "status": "completed",
                "arguments": {"memory_id": memory_id},
                "result": {
                    "source_kind": memory_context[memory_id]["source_kind"],
                    "memory_version": memory_context[memory_id]["version"],
                    "grant_id": used_memory_grants.get(memory_id),
                },
            })
        return dict(memory_context[memory_id])
    sdk = RobocasaMobileSDK(
        ModeBoundPerceptionBackend(
            action_backend,
            mode="sim_gt",
            target="robocasa_sim",
            provider=RobocasaGTPerception(
                service,
                fixed_camera_names=None if selected_variation is None else selected_variation["camera_names"],
            ),
        ),
        action_backend=action_backend,
        event_sink=sdk_trace.append,
    )

    started = time.monotonic()
    execution_status = "completed"
    error: str | None = None
    native_success = False
    native_evaluated = False
    no_op_success = False
    initial_observation: dict[str, Any] = {}
    final_observation: dict[str, Any] = {}
    initial_objects: list[dict[str, Any]] = []
    try:
        reset_payload: dict[str, Any] = {"seed": seed}
        if selected_variation is not None:
            reset_payload["variation"] = {
                key: selected_variation[key] for key in ("scene_id", "object_set_id")
            }
        reset = service._call("POST", "/reset", reset_payload, timeout=120.0)
        if reset.get("status") != "ok":
            raise RuntimeError(f"RoboCasa reset failed: {reset}")
        if selected_variation is not None and reset.get("applied_variation") != reset_payload["variation"]:
            raise RuntimeError("RoboCasa service did not attest the planned variation")
        if selected_variation is not None and service.assert_task()["lang"] != selected_variation["task_prompt"]:
            raise RuntimeError("RoboCasa service did not attest the planned task prompt")
        no_op_success = service.native_success()
        if no_op_success:
            raise RuntimeError("native success is true immediately after reset")
        initial_observation = sdk.sensors.get_observation()
        initial_objects = sdk.sensors.find_objects()
        selected_memories = [retrieve_memory(memory_id) for memory_id in attention_input.get("memory_ids_to_use", [])]
        if recorder is not None:
            recorder.start()
        raise_if_interrupted(interrupt_check)
        policy(
            sdk,
            {
                "task_id": task_id,
                "goal": spec.goal,
                "language": task_language,
                "perception_mode": "sim_gt",
                "initial_objects": initial_objects,
                "memory_catalog": [
                    {key: value for key, value in item.items() if key != "guidance"}
                    for item in memory_context.values()
                ],
                "retrieve_memory": retrieve_memory,
                "prior_attempts": history,
                "attention_input": {**attention_input, "selected_memories": selected_memories},
            },
        )
        raise_if_interrupted(interrupt_check)
    except Exception as exc:
        execution_status = ("cancelled" if isinstance(exc, EmergencyInterrupt)
                            else "timeout" if isinstance(exc, TimeoutError) else "failed")
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if recorder is not None:
            recorder.stop()
        if (episode_deadline_monotonic is not None
                and time.monotonic() >= episode_deadline_monotonic):
            execution_status = "timeout"
            error = error or "TimeoutError: RoboCasa episode deadline reached"
        if execution_status != "timeout":
            try:
                final_observation = sdk.sensors.get_observation()
            except Exception as exc:
                if error is None:
                    execution_status = "timeout" if isinstance(exc, TimeoutError) else "failed"
                    error = f"{type(exc).__name__}: {exc}"
        if not no_op_success and execution_status != "timeout":
            try:
                native_success = service.native_success()
                native_evaluated = True
            except Exception as exc:
                execution_status = "timeout" if isinstance(exc, TimeoutError) else "failed"
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
        if item.get("event_type") in {"sdk.arm_command", "sdk.gripper_command", "sdk.base_command"}
    ]
    result: dict[str, Any] = {
        "schema_version": "attentionbench.robocasa-sim-gt-episode.v2",
        "score_namespace": "v2/gt_perception_attentionbench",
        "task_id": task_id,
        "seed": seed,
        "policy_id": policy_id,
        "policy_sha256": policy_sha256,
        "hypothesis": hypothesis,
        "prior_attempt_count": len(history),
        "perception_mode": "sim_gt",
        "validation_variation": validation_variation,
        "validation_config_sha256": validation_config_sha256,
        "runtime_variation": runtime_variation,
        "execution_target": "robocasa_sim",
        "trusted_policy_callback": policy_execution_mode == "trusted_callback",
        "policy_execution_mode": policy_execution_mode,
        "formal_eligible": False,
        "status": execution_status,
        "timed_out": execution_status == "timeout",
        "native_success": bool(native_success and execution_status == "completed"),
        "no_op_success": no_op_success,
        "error": error,
        "elapsed_seconds": elapsed,
        "initial_object_count": len(initial_objects),
        "memory_ids": list(used_memories),
        "available_memory_ids": list(memory_context),
        "artifact_dir": str(episode_dir.resolve()),
        "media_capture": None if recorder is None else {
            "frames": len(recorder.frames),
            "replay_generated": (episode_dir / "advisor_replay.mp4").is_file(),
            "error": recorder.error,
        },
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
        evaluator_model="native_evaluator",
        execution_target="robocasa_sim",
        execution_status=execution_status,
        native_success=result["native_success"],
        evaluator_authoritative=native_evaluated,
        elapsed_seconds=elapsed,
        action_trace=action_trace,
        sdk_trace=sdk_trace,
        error=error,
        runtime={
            "perception_mode": "sim_gt", "score_namespace": result["score_namespace"],
            "memory_ids": result["memory_ids"],
            "policy_sha256": policy_sha256,
            "policy_execution_mode": policy_execution_mode,
            "memory_exposure": "candidate_validation" if validation_memory_id else "trusted" if retrieved else "none",
            "memory_context": context,
            "validation_variation": validation_variation,
            "validation_config_sha256": validation_config_sha256,
        },
        assistance_credits=assistance_credits,
        assistance_mode=assistance_mode,
        token_limit=token_limit,
        store_path=store_path,
        run_id=attention_run_id, attempt_index=attention_attempt_index,
        finalize_run=attention_finalize_run,
        execution_budget_seconds=attention_run_budget_seconds,
        code_path=policy_code_path,
        code_sha256=policy_sha256,
        hypothesis=hypothesis,
        timed_out=execution_status == "timeout",
    )
    result["attention_trace"] = link
    # The frozen trace assembler writes beside the DB. A shared cross-run DB
    # would overwrite that file, so v2 keeps a stable per-episode copy.
    bundle = write_run_bundle(store, link["run_id"], episode_dir / "attention_bundle.json")
    link["bundle"] = str(bundle.resolve())
    for item in retrieved:
        if item.memory_id not in used_memories:
            continue
        if item.status.value != MemoryStatus.TRUSTED.value:
            continue  # candidate exposure is attested in the raw trace, not v1 memory_uses
        memory_service.record_use(
            MemoryUseRecord(
                use_id=f"use:{link['attempt_id']}:{item.memory_id}",
                memory_id=item.memory_id,
                memory_version=item.version,
                run_id=link["run_id"],
                attempt_id=link["attempt_id"],
                used_at=used_memory_at[item.memory_id],
                outcome=(
                    "timeout" if execution_status == "timeout"
                    else "cancelled" if execution_status == "cancelled"
                    else "evaluator_unavailable" if not native_evaluated
                    else "native_success" if result["native_success"] else "failed"
                ),
            )
        )
    if advisor_transport is not None and not result["native_success"] and assistance_credits:
        _ask_advisor_and_create_candidate(
            store=store,
            result=result,
            transport=advisor_transport,
            sleeper=advisor_sleeper,
            clock=clock,
            memory_gateway=memory_service,
        )
    write_result_artifact(episode_dir, result)
    return result


def _public_attempt_history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(items) > 8:
        raise ValueError("at most eight prior attempts may be provided")
    allowed = {"attempt_index", "policy_sha256", "hypothesis", "failure_stage",
               "error_type", "observed_symptom", "guidance"}
    normalized = []
    for item in items:
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError("prior attempts must contain only advisor-visible summary fields")
        if any(not isinstance(value, (str, int)) or isinstance(value, bool)
               for value in item.values()):
            raise ValueError("prior attempt values must be public strings or integers")
        if len(json.dumps(item)) > 6000:
            raise ValueError("prior attempt summary is too large")
        normalized.append(dict(item))
    return normalized
