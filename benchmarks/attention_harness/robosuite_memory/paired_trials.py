"""Robosuite sim_gt paired executor backed by the same Memory validation gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from attention_memory_service import MemoryService, MemoryServiceClient

from ..memory_agent import TrialEvidence, ValidationTrial
from ..robocasa_native.safety_monitor import SafetyMonitorBackend
from ..sim_gt_memory import policy_fingerprint
from .adapter import RobosuiteSimGTBackend
from .sim_gt_runner import Policy, run_robosuite_sim_gt_episode


class RobosuitePairedTrialExecutor:
    def __init__(
        self, *, policy: Policy, policy_id: str, artifact_root: Path,
        store_path: Path,
        adapter_factory: Callable[[str, str], RobosuiteSimGTBackend],
        memory_gateway: MemoryService | MemoryServiceClient | None = None,
        max_delta_m: float = 0.25, max_observed_step_m: float = 0.5,
    ) -> None:
        self.policy = policy
        self.policy_id = policy_id
        self.artifact_root = artifact_root
        self.store_path = store_path
        self.adapter_factory = adapter_factory
        self.memory_gateway = memory_gateway
        self.max_delta_m = max_delta_m
        self.max_observed_step_m = max_observed_step_m
        self.policy_sha256 = policy_fingerprint(policy)

    def __call__(self, trial: ValidationTrial) -> TrialEvidence:
        if trial.suite != "robosuite" or trial.perception_mode != "sim_gt":
            raise ValueError("Robosuite paired executor only supports robosuite/sim_gt")
        if trial.policy_id != self.policy_id:
            raise ValueError("validation policy differs from executor policy")
        cameras = trial.variation.get("camera_names")
        if not isinstance(cameras, list) or len(cameras) != 1:
            raise ValueError("Robosuite validation requires one concrete active camera")
        adapter = self.adapter_factory(trial.task_id, cameras[0])
        monitor = SafetyMonitorBackend(
            adapter, max_delta_m=self.max_delta_m,
            max_observed_step_m=self.max_observed_step_m,
        )
        common = {
            "schema_version": "attentionbench.paired-trial-config.v1",
            "memory_id": trial.memory_id,
            "suite": trial.suite, "task_id": trial.task_id,
            "seed": trial.seed, "perception_mode": trial.perception_mode,
            "policy_id": trial.policy_id, "policy_sha256": self.policy_sha256,
            "assistance_credits": trial.assistance_credits,
            "variation": trial.variation,
            "environment_id": adapter.spec.robosuite_env,
            "sim_url": adapter.service_url,
            "action_backend": f"{type(adapter).__module__}.{type(adapter).__qualname__}",
            "safety_limits": {
                "max_delta_m": self.max_delta_m,
                "max_observed_step_m": self.max_observed_step_m,
            },
        }
        config_digest = hashlib.sha256(
            json.dumps(common, sort_keys=True).encode("utf-8")
        ).hexdigest()
        try:
            result = run_robosuite_sim_gt_episode(
                task_id=trial.task_id, seed=trial.seed,
                artifact_root=self.artifact_root, store_path=self.store_path,
                adapter=adapter, action_backend=monitor,
                policy=self.policy, policy_id=self.policy_id,
                perception_mode="sim_gt",
                validation_memory_id=trial.memory_id if trial.treatment else None,
                validation_variation=trial.variation,
                validation_config_sha256=config_digest,
                retrieve_memory=False, advisor_transport=None,
                assistance_credits=trial.assistance_credits,
                memory_gateway=self.memory_gateway,
            )
        finally:
            adapter.close()
        if result["policy_sha256"] != self.policy_sha256:
            raise RuntimeError("policy changed during paired trial")
        episode_dir = Path(result["artifact_dir"])
        attempt_id = result["attention_trace"]["attempt_id"]
        safety_path = monitor.write_artifact(
            episode_dir / "safety_monitor.json", attempt_id=attempt_id,
        )
        manifest = {
            **common, "config_sha256": config_digest,
            "arm": "treatment" if trial.treatment else "control",
            "attempt_id": attempt_id,
            "run_id": result["attention_trace"]["run_id"],
            "result": str((episode_dir / "result.json").resolve()),
            "raw_trace_id": result["attention_trace"]["raw_trace_id"],
            "safety_artifact": str(safety_path),
        }
        (episode_dir / "trial_config.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
        return TrialEvidence(
            attempt_id=attempt_id, safety_artifact=safety_path,
            config_sha256=config_digest,
        )
