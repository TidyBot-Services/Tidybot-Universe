"""RoboCasa formal-attempt adapter. Formal score eligibility is a separate gate."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable

from tidybot_sdk.perception import ModeBoundPerceptionBackend

from ..formal_runner_boundary import FormalRunRequest
from ..robosuite_memory.formal_sandbox import (
    FormalPolicyOutcome, _json_safe, execute_formal_policy, probe_sandbox,
)
from ..seed_guard import validate_seed
from .agent_actions import AgentServerActionBackend
from .client import RobocasaSimClient
from .formal_services import DedicatedRobocasaServices
from .gt_perception import RobocasaGTPerception
from .mobile_sdk import RobocasaMobileSDK
from .policy_sandbox import validate_generated_policy
from .safety_monitor import SafetyMonitorBackend
from .tasks import get_robocasa_task


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> Path:
    path.write_text(json.dumps(_json_safe(value), indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")
    return path.resolve()


def _config(request: FormalRunRequest) -> dict[str, Any]:
    if request.config_path is None:
        raise ValueError("RoboCasa formal runner requires an approved config file")
    request.validate()
    value = json.loads(request.config_path.read_text(encoding="utf-8"))
    required = {
        "schema_version", "suite", "task_id", "seed", "perception_mode",
        "scene_id", "object_set_id", "camera_names", "task_prompt",
        "sim_service", "agent_service", "task_source", "sim_runtime",
        "port_offset", "safety_limits",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("RoboCasa formal config has missing or unexpected fields")
    if (value["schema_version"] != "attentionbench.robocasa-formal-config.v3"
            or value["suite"] != "robocasa"
            or value["task_id"] != request.task_id
            or value["seed"] != request.seed
            or value["perception_mode"] != "sim_gt"):
        raise ValueError("RoboCasa formal config does not match task/seed/track")
    get_robocasa_task(request.task_id)
    if any(not isinstance(value[key], str) or not value[key]
           for key in ("scene_id", "object_set_id", "task_prompt")):
        raise ValueError("RoboCasa formal config lacks scene/object/task identity")
    if (not isinstance(value["camera_names"], list) or not value["camera_names"]
            or any(not isinstance(item, str) or not item for item in value["camera_names"])):
        raise ValueError("RoboCasa formal config requires camera names")
    if (isinstance(value["port_offset"], bool) or not isinstance(value["port_offset"], int)
            or not 100 <= value["port_offset"] <= 15000):
        raise ValueError("RoboCasa formal config requires an isolated port offset")
    for name in ("sim_service", "agent_service", "task_source"):
        service = value[name]
        if not isinstance(service, dict) or set(service) != {"revision", "tree_sha256"}:
            raise ValueError(f"RoboCasa formal config requires exact {name} identity")
        for key in ("revision", "tree_sha256"):
            item = service[key]
            if (not isinstance(item, str) or len(item) != (40 if key == "revision" else 64)
                    or any(char not in "0123456789abcdef" for char in item)):
                raise ValueError(f"invalid RoboCasa {name} {key}")
    runtime = value["sim_runtime"]
    runtime_keys = {"python_version", "python_sha256", "mani_skill_version",
                    "mani_skill_root", "mani_skill_python_tree_sha256",
                    "mani_skill_files", "task_module_path"}
    file_names = {
        "envs/tasks/mobile_manipulation/robocasa/kitchen.py",
        "utils/scene_builder/robocasa/scene_builder.py",
        "utils/scene_builder/robocasa/fixtures/counter.py",
        "utils/scene_builder/robocasa/utils/placement_samplers.py",
        "utils/scene_builder/robocasa/utils/object_utils.py",
    }
    if (not isinstance(runtime, dict) or set(runtime) != runtime_keys
            or any(not isinstance(runtime[key], str) or not runtime[key]
                   for key in runtime_keys - {"mani_skill_files"})
            or len(runtime["python_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in runtime["python_sha256"])
            or len(runtime["mani_skill_python_tree_sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in runtime["mani_skill_python_tree_sha256"])
            or not isinstance(runtime["mani_skill_files"], dict)
            or set(runtime["mani_skill_files"]) != file_names
            or any(not isinstance(digest, str) or len(digest) != 64
                   or any(char not in "0123456789abcdef" for char in digest)
                   for digest in runtime["mani_skill_files"].values())):
        raise ValueError("RoboCasa formal config requires exact simulator runtime identity")
    limits = value["safety_limits"]
    if (not isinstance(limits, dict) or set(limits) != {
            "max_delta_m", "max_observed_step_m", "max_base_delta_m", "max_base_rotation_rad"
    } or any(isinstance(item, bool) or not isinstance(item, (int, float))
             or not math.isfinite(item) or item <= 0 for item in limits.values())):
        raise ValueError("RoboCasa formal config requires finite safety limits")
    return value


class RobocasaFormalSuiteRunner:
    suite = "robocasa"

    def __init__(self, *, sim_source_root: Path, agent_source_root: Path,
                 task_source_root: Path,
                 sim_python: Path, agent_python: Path,
                 cancel_event: threading.Event | None = None,
                 policy_start_hook: Callable[[], None] | None = None) -> None:
        self.sim_source_root = sim_source_root
        self.agent_source_root = agent_source_root
        self.task_source_root = task_source_root
        self.sim_python = sim_python
        self.agent_python = agent_python
        self.cancel_event = cancel_event
        self.policy_start_hook = policy_start_hook

    def execute(self, request: FormalRunRequest) -> dict[str, Any]:
        request.validate()
        if request.suite != self.suite:
            raise ValueError("formal suite request is not RoboCasa")
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
        services = DedicatedRobocasaServices(
            task_id=request.task_id, sim_source_root=self.sim_source_root,
            agent_source_root=self.agent_source_root,
            task_source_root=self.task_source_root,
            sim_python=self.sim_python, agent_python=self.agent_python,
            expected_sim=config["sim_service"], expected_agent=config["agent_service"],
            expected_task=config["task_source"], expected_runtime=config["sim_runtime"],
            port_offset=config["port_offset"], log_dir=episode_dir,
            deadline=deadline, cancel_event=self.cancel_event,
        )
        sdk_trace: list[dict[str, Any]] = []
        monitor: SafetyMonitorBackend | None = None
        action: AgentServerActionBackend | None = None
        outcome: FormalPolicyOutcome | None = None
        error: str | None = None
        status = "failed"
        native_evaluated = False
        service_native_success = False
        reset_attestation: dict[str, Any] | None = None
        initial_sha: str | None = None
        final_sha: str | None = None
        camera_attestation: list[str] | None = None
        language_attestation: str | None = None
        try:
            with services:
                client = RobocasaSimClient(request.task_id, base_url=services.sim_url)
                client.assert_task()
                action = AgentServerActionBackend(
                    base_url=services.agent_url, simulator_attested=True,
                    holder=f"formal-{episode_dir.name}",
                    timeout_seconds=min(90.0, request.overall_deadline_seconds),
                    poll_seconds=0.1,
                )
                action.set_episode_deadline(deadline)
                action.set_interrupt_check(
                    lambda: bool(self.cancel_event and self.cancel_event.is_set())
                )
                action.assert_cancellation_available()
                variation = {key: config[key] for key in ("scene_id", "object_set_id")}
                reset = client._call("POST", "/reset", {
                    "seed": request.seed, "variation": variation,
                }, timeout=max(0.1, deadline - time.monotonic()))
                if reset.get("status") != "ok" or reset.get("applied_variation") != variation:
                    raise RuntimeError("RoboCasa Service did not attest approved reset variation")
                reset_attestation = reset.get("applied_variation")
                observed = client.observe()
                camera_attestation = list(observed.cameras)
                language_attestation = observed.language
                if not set(config["camera_names"]).issubset(camera_attestation):
                    raise RuntimeError("RoboCasa Service did not attest approved cameras")
                if language_attestation != config["task_prompt"]:
                    raise RuntimeError("RoboCasa Service did not attest approved task language")
                if client.native_success():
                    raise RuntimeError("native success was true immediately after reset")
                monitor = SafetyMonitorBackend(
                    action, **config["safety_limits"],
                    interrupt_check=lambda: bool(self.cancel_event and self.cancel_event.is_set()),
                )
                sdk = RobocasaMobileSDK(
                    ModeBoundPerceptionBackend(
                        monitor, mode="sim_gt", target="robocasa_sim",
                        provider=RobocasaGTPerception(
                            client, fixed_camera_names=config["camera_names"],
                        ),
                    ), action_backend=monitor, event_sink=sdk_trace.append,
                )
                monitor.observe()
                initial = sdk.sensors.get_observation()
                initial_sha = hashlib.sha256(json.dumps(
                    _json_safe(initial), sort_keys=True, allow_nan=False,
                ).encode()).hexdigest()
                objects = sdk.sensors.find_objects()
                if self.policy_start_hook is not None:
                    self.policy_start_hook()
                outcome = execute_formal_policy(
                    code=code, sdk=sdk,
                    context={"suite": "robocasa", "task_id": request.task_id,
                             "perception_mode": "sim_gt", "goal": get_robocasa_task(request.task_id).goal,
                             "language": config["task_prompt"], "initial_objects": objects,
                             "attention_input": request.attention_input or {}},
                    deadline=deadline, stderr_path=episode_dir / "policy.stderr",
                    cancel_check=lambda: bool(self.cancel_event and self.cancel_event.is_set()),
                )
                status, error = outcome.status, outcome.error
                if (services.stop_receipt is None and status not in {"cancelled", "timeout"}
                        and not (self.cancel_event and self.cancel_event.is_set())):
                    try:
                        final = sdk.sensors.get_observation()
                        final_sha = hashlib.sha256(json.dumps(
                            _json_safe(final), sort_keys=True, allow_nan=False,
                        ).encode()).hexdigest()
                        service_native_success = client.native_success()
                        native_evaluated = True
                    except Exception as exc:
                        status, error = "failed", f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            status = "timeout" if time.monotonic() >= deadline else "failed"
        stop = services.stop_receipt
        if stop is not None and stop["reason"] == "episode_deadline":
            status, error = "timeout", error or "dedicated RoboCasa Services stopped at episode deadline"
        elif stop is not None and stop["reason"] == "operator_cancel":
            status, error = "cancelled", error or "operator cancelled formal attempt"
        try:
            request.validate()
        except ValueError as exc:
            status, error = "failed", str(exc)
        source_unchanged = services.source_unchanged() if stop is not None else False
        if stop is not None and not source_unchanged:
            status, error = "failed", "RoboCasa Service source changed during attempt"
        scored_success = bool(status == "completed" and native_evaluated and service_native_success)
        trace_path = _write_json(episode_dir / "trace.json", {
            "schema_version": "attentionbench.formal-trace.v1",
            "suite": self.suite, "task_id": request.task_id, "seed": request.seed,
            "policy_sha256": request.policy_sha256, "config_sha256": request.config_sha256,
            "run_id": request.run_id, "attempt_id": request.attempt_id,
            "attention_input": request.attention_input or {},
            "status": status, "error": error, "reset_attestation": reset_attestation,
            "camera_attestation": camera_attestation,
            "language_attestation": language_attestation,
            "initial_observation_sha256": initial_sha,
            "final_observation_sha256": final_sha, "sdk_events": sdk_trace,
        })
        if monitor is None:
            safety_path = _write_json(episode_dir / "safety.json", {
                "schema_version": "attentionbench.safety-monitor.v1",
                "source": "independent_safety_monitor", "attempt_id": request.attempt_id or episode_dir.name,
                "run_id": request.run_id,
                "unsafe_attempts": 1, "coverage": "no_state_samples", "events": [],
                "violations": [{"kind": "monitor_not_initialized"}],
            })
        else:
            safety_path = monitor.write_artifact(
                episode_dir / "safety.json", attempt_id=request.attempt_id or episode_dir.name,
                run_id=request.run_id,
            )
        cancellation_receipts = [] if action is None else action.cancellation_receipts
        sandbox_path = _write_json(episode_dir / "sandbox_receipt.json", {
            "schema_version": "attentionbench.formal-sandbox-receipt.v1",
            "probe": sandbox_probe, "worker": None if outcome is None else outcome.__dict__,
            "service_stop": stop, "service_source_unchanged": source_unchanged,
            "sim_service": config["sim_service"], "agent_service": config["agent_service"],
            "task_source": config["task_source"], "sim_runtime": config["sim_runtime"],
            "action_cancellation_receipts": cancellation_receipts,
            "source_sha256_before": request.policy_sha256,
            "source_sha256_after": _digest(request.policy_code_path),
            "config_sha256_after": _digest(request.config_path),
            "deadline_seconds": request.overall_deadline_seconds,
            "run_id": request.run_id, "attempt_id": request.attempt_id,
            "elapsed_seconds": time.monotonic() - started,
        })
        native_path = _write_json(episode_dir / "native_result.json", {
            "schema_version": "attentionbench.formal-native-result.v1",
            "source": "robocasa/task/success", "evaluated": native_evaluated,
            "service_native_success": service_native_success if native_evaluated else None,
            "native_success": scored_success, "status": status,
            "run_id": request.run_id, "attempt_id": request.attempt_id,
        })
        artifacts = {
            name: {"uri": str(path), "sha256": _digest(path)}
            for name, path in (("trace", trace_path), ("safety", safety_path),
                               ("sandbox_receipt", sandbox_path), ("native_result", native_path))
        }
        stopped = bool(stop and len(stop["services"]) == 2 and all(
            item["leader_reaped"] and item["process_group_gone"]
            for item in stop["services"].values()
        ))
        result = {
            "schema_version": "attentionbench.formal-runner-result.v1",
            "suite": self.suite, "task_id": request.task_id, "seed": request.seed,
            "policy_sha256": request.policy_sha256, "config_sha256": request.config_sha256,
            "run_id": request.run_id, "attempt_id": request.attempt_id,
            "status": status, "error": error, "native_success": scored_success,
            "native_evaluator": {"native_success": scored_success,
                                 "evaluated": native_evaluated},
            "sandbox": {"process_isolated": sandbox_probe["host_home_visible"] is False,
                        "sdk_rpc_only": sandbox_probe["worker_has_simulator_client"] is False,
                        "deadline_enforced": stopped,
                        "action_cancellation_verified": stopped},
            "service_revision": {"sim": config["sim_service"]["revision"],
                                 "agent": config["agent_service"]["revision"],
                                 "task": config["task_source"]["revision"]},
            "artifact_dir": str(episode_dir.resolve()), "artifacts": artifacts,
            "formal_eligible": False,
        }
        _write_json(episode_dir / "result.json", result)
        return result
