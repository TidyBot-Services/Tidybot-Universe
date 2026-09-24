"""RoboCasa GT paired-trial executor for Memory Agent development validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from attention_memory_service import MemoryService, MemoryServiceClient
from tidybot_sdk import RobotBackend

from ..memory_agent import TrialEvidence, ValidationTrial
from .client import RobocasaSimClient
from .safety_monitor import SafetyMonitorBackend
from .sim_gt_runner import Policy, _policy_fingerprint, run_robocasa_sim_gt_episode


class RoboCasaPairedTrialExecutor:
    """Run both arms through the same policy, reset seed and simulator config.

    A trial's only experimental difference is candidate-memory exposure. The
    Memory Service independently checks the persisted raw trace and outcome.
    """

    def __init__(
        self, *, policy: Policy, policy_id: str, artifact_root: Path,
        store_path: Path, backend_factory: Callable[[], RobotBackend],
        client_factory: Callable[[str], RobocasaSimClient],
        memory_gateway: MemoryService | MemoryServiceClient | None = None,
        max_delta_m: float = 0.25, max_observed_step_m: float = 0.5,
    ) -> None:
        self.policy = policy
        self.policy_id = policy_id
        self.artifact_root = artifact_root
        self.store_path = store_path
        self.backend_factory = backend_factory
        self.client_factory = client_factory
        self.memory_gateway = memory_gateway
        self.max_delta_m = max_delta_m
        self.max_observed_step_m = max_observed_step_m
        self.policy_sha256 = _policy_fingerprint(policy)

    def __call__(self, trial: ValidationTrial) -> TrialEvidence:
        if trial.suite != "robocasa" or trial.perception_mode != "sim_gt":
            raise ValueError("RoboCasa paired executor only supports robocasa/sim_gt")
        if trial.policy_id != self.policy_id:
            raise ValueError("validation policy differs from executor policy")
        backend = self.backend_factory()
        client = self.client_factory(trial.task_id)
        task_info = client.assert_task()
        monitor = SafetyMonitorBackend(
            backend, max_delta_m=self.max_delta_m,
            max_observed_step_m=self.max_observed_step_m,
        )
        common = {
            "schema_version": "attentionbench.paired-trial-config.v1",
            "memory_id": trial.memory_id,
            "suite": trial.suite,
            "task_id": trial.task_id,
            "seed": trial.seed,
            "perception_mode": trial.perception_mode,
            "policy_id": trial.policy_id,
            "policy_sha256": self.policy_sha256,
            "assistance_credits": trial.assistance_credits,
            "variation": trial.variation,
            # The live task-info language describes the *previous* episode
            # before reset. It may legitimately differ between pair arms;
            # only the static environment ID belongs in the matched config.
            "environment_id": task_info["task"],
            "sim_url": client.base_url,
            "action_backend": f"{type(backend).__module__}.{type(backend).__qualname__}",
            "agent_url": getattr(backend, "base_url", None),
            "safety_limits": {
                "max_delta_m": self.max_delta_m,
                "max_observed_step_m": self.max_observed_step_m,
            },
        }
        config_digest = hashlib.sha256(
            json.dumps(common, sort_keys=True).encode("utf-8")
        ).hexdigest()
        result = run_robocasa_sim_gt_episode(
            task_id=trial.task_id, seed=trial.seed,
            artifact_root=self.artifact_root, store_path=self.store_path,
            action_backend=monitor, policy=self.policy, policy_id=self.policy_id,
            perception_mode="sim_gt", client=client,
            validation_memory_id=trial.memory_id if trial.treatment else None,
            validation_variation=trial.variation,
            validation_config_sha256=config_digest,
            retrieve_memory=False, advisor_transport=None,
            assistance_credits=trial.assistance_credits,
            memory_gateway=self.memory_gateway,
        )
        if result["policy_sha256"] != self.policy_sha256:
            raise RuntimeError("policy changed during paired trial")
        episode_dir = Path(result["artifact_dir"])
        attempt_id = result["attention_trace"]["attempt_id"]
        safety_path = monitor.write_artifact(
            episode_dir / "safety_monitor.json", attempt_id=attempt_id,
        )
        manifest = {
            **common,
            "config_sha256": config_digest,
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
