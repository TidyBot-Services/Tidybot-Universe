"""Robosuite v2 formal-attempt adapter; formal scoring remains gated separately."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable

from tidybot_sdk import TidyBotSDK
from tidybot_sdk.perception import ModeBoundPerceptionBackend

from ..formal_runner_boundary import FormalRunRequest
from ..robocasa_native.safety_monitor import SafetyMonitorBackend
from ..robocasa_native.policy_sandbox import validate_generated_policy
from ..seed_guard import validate_seed
from ..public_station import publish_public_station, save_public_frame
from ..ui_media import robosuite_camera_frame
from .adapter import RobosuiteSimGTBackend
from .formal_sandbox import FormalPolicyOutcome, _json_safe, execute_formal_policy, probe_sandbox
from .formal_service import DedicatedRobosuiteService
from .gt_perception import RobosuiteGTPerception


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    path.write_text(json.dumps(_json_safe(value), indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")
    return path.resolve()


def _config(request: FormalRunRequest) -> dict[str, Any]:
    if request.config_path is None:
        raise ValueError("Robosuite formal runner requires the approved config file")
    request.validate()
    value = json.loads(request.config_path.read_text(encoding="utf-8"))
    required = {
        "schema_version", "suite", "task_id", "seed", "perception_mode",
        "camera_name", "horizon", "camera_height", "camera_width",
        "scene_id", "object_set_id", "service_revision", "safety_limits",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("formal config has missing or unexpected fields")
    if (value["schema_version"] != "attentionbench.robosuite-formal-config.v1"
            or value["suite"] != "robosuite"
            or value["task_id"] != request.task_id
            or value["seed"] != request.seed
            or value["perception_mode"] != "sim_gt"
            or value["camera_name"] != "agentview"):
        raise ValueError("formal config does not match requested task/seed/track")
    revision = value["service_revision"]
    if (not isinstance(revision, str) or len(revision) != 40
            or any(char not in "0123456789abcdef" for char in revision)):
        raise ValueError("formal config requires a full Service commit SHA")
    if any(not isinstance(value[key], str) or not value[key]
           for key in ("scene_id", "object_set_id")):
        raise ValueError("formal config requires scene and object identities")
    for name, minimum, maximum in (("horizon", 1, 2000),
                                   ("camera_height", 32, 1024),
                                   ("camera_width", 32, 1024)):
        item = value[name]
        if isinstance(item, bool) or not isinstance(item, int) or not minimum <= item <= maximum:
            raise ValueError(f"invalid formal config {name}")
    limits = value["safety_limits"]
    if not isinstance(limits, dict) or set(limits) != {"max_delta_m", "max_observed_step_m"}:
        raise ValueError("formal config requires exact safety limits")
    if any(isinstance(item, bool) or not isinstance(item, (int, float))
           or not math.isfinite(item) or item <= 0 for item in limits.values()):
        raise ValueError("formal safety limits must be finite and positive")
    return value


class RobosuiteFormalSuiteRunner:
    suite = "robosuite"

    def __init__(self, *, service_source_root: Path,
                 cancel_event: threading.Event | None = None,
                 policy_start_hook: Callable[[], None] | None = None) -> None:
        self.service_source_root = service_source_root
        self.cancel_event = cancel_event
        self.policy_start_hook = policy_start_hook

    def execute(self, request: FormalRunRequest) -> dict[str, Any]:
        request.validate()
        if request.suite != self.suite:
            raise ValueError("formal suite request is not Robosuite")
        validate_seed(request.seed, allow_heldout=False)
        config = _config(request)
        code = request.policy_code_path.read_text(encoding="utf-8")
        validate_generated_policy(code)
        started = time.monotonic()
        deadline = started + request.overall_deadline_seconds
        episode_dir = request.artifact_root / (
            f"{request.task_id}-seed{request.seed}-formal-{time.time_ns()}"
        )
        episode_dir.mkdir(parents=True, exist_ok=False)
        _write_json(episode_dir / "approved_config.json", config)
        (episode_dir / "approved_policy.py").write_bytes(code.encode("utf-8"))
        sandbox_probe = probe_sandbox()
        service = DedicatedRobosuiteService(
            source_root=self.service_source_root,
            expected_revision=config["service_revision"],
            log_path=episode_dir / "service.log", deadline=deadline,
            cancel_event=self.cancel_event,
        )
        sdk_trace: list[dict[str, Any]] = []
        monitor: SafetyMonitorBackend | None = None
        adapter: RobosuiteSimGTBackend | None = None
        outcome: FormalPolicyOutcome | None = None
        error: str | None = None
        status = "failed"
        native_evaluated = False
        service_native_success = False
        reset_attestation: dict[str, str] | None = None
        initial_sha: str | None = None
        final_sha: str | None = None
        try:
            with service:
                adapter = RobosuiteSimGTBackend(
                    request.task_id, service_url=service.base_url,
                    camera_name=config["camera_name"], horizon=config["horizon"],
                    camera_height=config["camera_height"],
                    camera_width=config["camera_width"],
                )
                initial, reset_attestation = adapter.reset_attested(
                    request.seed,
                    variation={key: config[key] for key in ("scene_id", "object_set_id")},
                )
                initial_sha = hashlib.sha256(json.dumps(
                    _json_safe(initial), sort_keys=True, allow_nan=False,
                ).encode()).hexdigest()
                if adapter.native_success():
                    raise RuntimeError("native success was true immediately after reset")
                if request.run_id and request.attempt_id and service.base_url:
                    publish_public_station(
                        run_dir=request.artifact_root.parent, run_id=request.run_id,
                        attempt_id=request.attempt_id, suite=self.suite,
                        origin=service.base_url, camera_name=config["camera_name"],
                    )
                    try:
                        save_public_frame(request.artifact_root.parent, suite=self.suite,
                                          frame=robosuite_camera_frame(
                                              service.base_url, config["camera_name"]))
                    except Exception:
                        pass  # Camera errors are visible through the UI; native execution proceeds.
                monitor = SafetyMonitorBackend(
                    adapter, max_delta_m=config["safety_limits"]["max_delta_m"],
                    max_observed_step_m=config["safety_limits"]["max_observed_step_m"],
                    interrupt_check=lambda: bool(self.cancel_event and self.cancel_event.is_set()),
                )
                sdk = TidyBotSDK(ModeBoundPerceptionBackend(
                    monitor, mode="sim_gt", target="robosuite_sim",
                    provider=RobosuiteGTPerception(
                        adapter, fixed_camera_names=[config["camera_name"]],
                    ),
                ), event_sink=sdk_trace.append)
                monitor.observe()
                objects = sdk.sensors.find_objects()
                if self.policy_start_hook is not None:
                    self.policy_start_hook()
                outcome = execute_formal_policy(
                    code=code, sdk=sdk,
                    context={"suite": "robosuite", "task_id": request.task_id,
                             "perception_mode": "sim_gt", "initial_objects": objects,
                             "attention_input": request.attention_input or {}},
                    deadline=deadline, stderr_path=episode_dir / "policy.stderr",
                    cancel_check=lambda: bool(self.cancel_event and self.cancel_event.is_set()),
                )
                status, error = outcome.status, outcome.error
                if service.stop_receipt is None:
                    try:
                        final = sdk.sensors.get_observation()
                        final_sha = hashlib.sha256(json.dumps(
                            _json_safe(final), sort_keys=True, allow_nan=False,
                        ).encode()).hexdigest()
                        service_native_success = adapter.native_success()
                        native_evaluated = True
                    except Exception as exc:
                        status = "failed"
                        error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            status = "timeout" if time.monotonic() >= deadline else "failed"
        stop = service.stop_receipt
        if stop is not None and stop["reason"] == "episode_deadline":
            status = "timeout"
            error = error or "dedicated Service stopped at episode deadline"
        elif stop is not None and stop["reason"] == "operator_cancel":
            status = "cancelled"
            error = error or "operator cancelled formal attempt"
        try:
            request.validate()  # Source/config must remain approved after execution.
        except ValueError as exc:
            status, error = "failed", str(exc)
        if service.revision is not None and not service.source_unchanged():
            status, error = "failed", "Robosuite Service source changed during formal attempt"
        scored_success = bool(status == "completed" and native_evaluated
                              and service_native_success)
        trace = {
            "schema_version": "attentionbench.formal-trace.v1",
            "suite": self.suite, "task_id": request.task_id, "seed": request.seed,
            "policy_sha256": request.policy_sha256,
            "config_sha256": request.config_sha256,
            "entry_lock": request.entry_lock,
            "run_id": request.run_id, "attempt_id": request.attempt_id,
            "attention_input": request.attention_input or {},
            "status": status, "error": error,
            "reset_attestation": reset_attestation,
            "initial_observation_sha256": initial_sha,
            "final_observation_sha256": final_sha,
            "backend_steps": [] if adapter is None else adapter.trace,
            "sdk_events": sdk_trace,
        }
        trace_path = _write_json(episode_dir / "trace.json", trace)
        if monitor is None:
            cancelled_before_monitor = status == "cancelled" and bool(
                self.cancel_event and self.cancel_event.is_set())
            safety = {
                "schema_version": "attentionbench.safety-monitor.v1",
                "source": "independent_safety_monitor", "attempt_id": request.attempt_id or episode_dir.name,
                "run_id": request.run_id,
                "unsafe_attempts": 0 if cancelled_before_monitor else 1,
                "coverage": "no_actions_started" if cancelled_before_monitor else "no_state_samples",
                "events": [],
                "violations": [] if cancelled_before_monitor else [{"kind": "monitor_not_initialized"}],
            }
            safety_path = _write_json(episode_dir / "safety.json", safety)
        else:
            safety_path = monitor.write_artifact(
                episode_dir / "safety.json", attempt_id=request.attempt_id or episode_dir.name,
                run_id=request.run_id,
            )
        sandbox_receipt = {
            "schema_version": "attentionbench.formal-sandbox-receipt.v1",
            "probe": sandbox_probe, "worker": None if outcome is None else outcome.__dict__,
            "service_revision": service.revision,
            "service_source_unchanged": service.source_unchanged() if service.revision else False,
            "service_stop": stop,
            "source_sha256_before": request.policy_sha256,
            "source_sha256_after": _digest(request.policy_code_path),
            "config_sha256_after": _digest(request.config_path),
            "deadline_seconds": request.overall_deadline_seconds,
            "run_id": request.run_id, "attempt_id": request.attempt_id,
            "elapsed_seconds": time.monotonic() - started,
        }
        sandbox_path = _write_json(episode_dir / "sandbox_receipt.json", sandbox_receipt)
        native_result = {
            "schema_version": "attentionbench.formal-native-result.v1",
            "source": "robosuite_sim/v1/success",
            "evaluated": native_evaluated,
            "service_native_success": service_native_success if native_evaluated else None,
            "native_success": scored_success,
            "status": status,
            "run_id": request.run_id, "attempt_id": request.attempt_id,
        }
        native_path = _write_json(episode_dir / "native_result.json", native_result)
        artifacts = {
            name: {"uri": str(path), "sha256": _digest(path)}
            for name, path in (
                ("trace", trace_path), ("safety", safety_path),
                ("sandbox_receipt", sandbox_path), ("native_result", native_path),
            )
        }
        result = {
            "schema_version": "attentionbench.formal-runner-result.v1",
            "suite": self.suite, "task_id": request.task_id, "seed": request.seed,
            "policy_sha256": request.policy_sha256,
            "config_sha256": request.config_sha256,
            "entry_lock": request.entry_lock,
            "run_id": request.run_id, "attempt_id": request.attempt_id,
            "status": status, "error": error,
            "native_success": scored_success,
            "native_evaluator": {"native_success": scored_success,
                                 "evaluated": native_evaluated},
            "sandbox": {
                "process_isolated": sandbox_probe["host_home_visible"] is False,
                "sdk_rpc_only": sandbox_probe["worker_has_simulator_client"] is False,
                "deadline_enforced": bool(stop and stop["leader_reaped"]
                                          and stop["process_group_gone"]),
                "action_cancellation_verified": bool(stop and stop["leader_reaped"]
                                                      and stop["process_group_gone"]),
            },
            "service_revision": service.revision,
            "artifact_dir": str(episode_dir.resolve()),
            "artifacts": artifacts,
            "formal_eligible": False,
        }
        _write_json(episode_dir / "result.json", result)
        return result
