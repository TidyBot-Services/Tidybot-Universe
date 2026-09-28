"""Memory Agent: distill feedback and orchestrate evidence-gated validation.

This agent is a peer of the development/deployment workflows. It has no
direct database, simulator, or promotion authority; those belong to the
Memory Service and the injected trial executor respectively.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Protocol

from .core.models import MemoryRecord
from .parcc_advisor import parse_advisor_advice
from .seed_guard import validate_seed


@dataclass(frozen=True)
class MemoryDraft:
    guidance: str
    repair: str
    artifact_kind: str = "text_hint"


@dataclass(frozen=True)
class ValidationTrial:
    memory_id: str
    seed: int
    treatment: bool
    suite: str
    task_id: str
    perception_mode: str
    policy_id: str
    assistance_credits: int
    variation: dict[str, Any]


@dataclass(frozen=True)
class TrialEvidence:
    attempt_id: str
    safety_artifact: Path
    config_sha256: str
    service_stop_artifact: Path | None = None


class TrialExecutor(Protocol):
    def __call__(self, trial: ValidationTrial) -> TrialEvidence: ...


class MemoryGateway(Protocol):
    """Implemented by the in-process service and authenticated HTTP client."""

    def source_for_agent(self, request_id: str) -> dict[str, Any]: ...
    def create_candidate(self, **payload: Any) -> MemoryRecord: ...
    def get_memory(self, memory_id: str) -> MemoryRecord: ...
    def check_candidate(self, memory_id: str) -> dict[str, Any]: ...
    def provenance(self, memory_id: str) -> dict[str, Any]: ...
    def get_plan(self, memory_id: str) -> dict[str, Any] | None: ...
    def record_plan(self, memory_id: str, plan: dict[str, Any]) -> dict[str, Any]: ...
    def record_pair(self, **payload: Any) -> dict[str, Any]: ...
    def impact_report(self, memory_id: str) -> dict[str, Any]: ...
    def list_pairs(self, memory_id: str) -> list[dict[str, Any]]: ...
    def promote(self, memory_id: str) -> MemoryRecord: ...


class MemoryAgent:
    def __init__(
        self,
        service: MemoryGateway,
        *,
        distiller: Callable[[dict[str, Any]], MemoryDraft] | None = None,
    ) -> None:
        self.service = service
        self.distiller = distiller or self._default_distiller

    def ingest_answered_hint(
        self, request_id: str, *, memory_id: str | None = None,
        human_attention_seconds: float = 0.0,
    ) -> MemoryRecord:
        source = self.service.source_for_agent(request_id)
        mode = source["perception_mode"]
        if mode not in {"sim_gt", "vision"}:
            raise ValueError("memory source requires a known perception mode")
        draft = self.distiller(source)
        if not isinstance(draft, MemoryDraft):
            raise TypeError("distiller must return MemoryDraft")
        return self.service.create_candidate(
            memory_id=memory_id or f"candidate:{request_id}",
            request_id=request_id,
            guidance=draft.guidance,
            repair=draft.repair,
            applicability={
                "suite": source["suite"],
                "task_id": source["task_id"],
                "perception_mode": mode,
            },
            created_at=float(source["response_created_at"]),
            artifact_kind=draft.artifact_kind,
            human_attention_seconds=human_attention_seconds,
        )

    def plan_validation(
        self, memory_id: str, *, cases: tuple[dict[str, Any], ...],
        assistance_credits: int = 0,
    ) -> tuple[tuple[ValidationTrial, ValidationTrial], ...]:
        plan, pairs = self._prepare_validation(
            memory_id, cases=cases, assistance_credits=assistance_credits,
        )
        self.service.record_plan(memory_id, plan)
        return pairs

    def preflight_validation(
        self, memory_id: str, *, cases: tuple[dict[str, Any], ...],
        assistance_credits: int = 0,
        validation_policy_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Check evidence and frozen cases without running or registering trials."""
        if validation_policy_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", validation_policy_sha256):
            raise ValueError("validation policy digest must be a lowercase SHA-256 hex string")
        source = self.service.check_candidate(memory_id)
        plan, pairs = self._prepare_validation(
            memory_id, cases=cases, assistance_credits=assistance_credits,
        )
        existing = self.service.get_plan(memory_id)
        if existing is not None and existing != plan:
            raise ValueError("preflight cases differ from the frozen validation plan")
        completed = {item["seed"] for item in self.service.list_pairs(memory_id)}
        seeds = [control.seed for control, _ in pairs]
        return {
            "memory_id": memory_id,
            "suite": source["suite"],
            "task_id": source["task_id"],
            "perception_mode": source["perception_mode"],
            "source_evidence_verified": source["source_evidence_verified"],
            "source_policy_id": source["source_policy_id"],
            "source_policy_sha256": source["source_policy_sha256"],
            "validation_policy_sha256": validation_policy_sha256,
            "policy_changed_since_source": (
                validation_policy_sha256 != source["source_policy_sha256"]
                if validation_policy_sha256 is not None and source["source_policy_sha256"] is not None
                else None
            ),
            "frozen_plan": existing is not None,
            "total_seeds": len(seeds),
            "completed_seeds": [seed for seed in seeds if seed in completed],
            "remaining_seeds": [seed for seed in seeds if seed not in completed],
            "simulator_executed": False,
        }

    def _prepare_validation(
        self, memory_id: str, *, cases: tuple[dict[str, Any], ...],
        assistance_credits: int,
    ) -> tuple[dict[str, Any], tuple[tuple[ValidationTrial, ValidationTrial], ...]]:
        memory = self.service.get_memory(memory_id)
        if memory.status.value != "candidate":
            raise ValueError("validation planning requires a candidate memory")
        provenance = self.service.provenance(memory_id)
        if provenance["source_execution_target"] not in {"robocasa_sim", "robosuite_sim"}:
            raise PermissionError("automatic memory validation is simulator-only")
        if not isinstance(cases, tuple) or any(not isinstance(case, dict) for case in cases):
            raise ValueError("validation cases must be a tuple of case objects")
        selected = tuple(case.get("seed") for case in cases)
        if (
            len(selected) < 5
            or any(isinstance(seed, bool) or not isinstance(seed, int) for seed in selected)
            or len(set(selected)) != len(selected)
        ):
            raise ValueError("validation needs at least five distinct seeds")
        if isinstance(assistance_credits, bool) or not isinstance(assistance_credits, int) or assistance_credits < 0:
            raise ValueError("assistance_credits must be nonnegative")
        for seed in selected:
            if validate_seed(seed) != "dev":
                raise PermissionError("memory validation is development-only")
        axes = ("scene_id", "object_set_id", "camera_config_id", "task_variant_id")
        for case in cases:
            if set(case) != {"seed", *axes, "camera_names", "task_prompt"} or any(
                not isinstance(case[axis], str) or not case[axis].strip() for axis in axes
            ) or not isinstance(case["camera_names"], list) or not case["camera_names"] or any(
                not isinstance(name, str) or not name for name in case["camera_names"]
            ) or not isinstance(case["task_prompt"], str) or not case["task_prompt"].strip():
                raise ValueError("each validation case needs four explicit variation axes")
        if any(len({case[axis] for case in cases}) < 2 for axis in axes):
            raise ValueError("validation must vary every scene/object/camera/task axis")
        if len({tuple(case["camera_names"]) for case in cases}) < 2 or len({case["task_prompt"] for case in cases}) < 2:
            raise ValueError("camera and task variants need different concrete views and prompts")
        camera_map = {case["camera_config_id"]: tuple(case["camera_names"]) for case in cases}
        task_map = {case["task_variant_id"]: case["task_prompt"] for case in cases}
        if (
            len(camera_map) != len({tuple(case["camera_names"]) for case in cases})
            or len(task_map) != len({case["task_prompt"] for case in cases})
            or any(camera_map[case["camera_config_id"]] != tuple(case["camera_names"]) for case in cases)
            or any(task_map[case["task_variant_id"]] != case["task_prompt"] for case in cases)
        ):
            raise ValueError("camera/task variant IDs must map one-to-one to concrete settings")
        context = provenance["artifact"]["applicability"]
        plan = {
            "schema_version": "attentionbench.memory-validation-plan.v2",
            "memory_id": memory_id,
            "suite": context["suite"],
            "task_id": context["task_id"],
            "perception_mode": context["perception_mode"],
            "policy_id": provenance["source_policy_id"],
            "assistance_credits": assistance_credits,
            "seeds": list(selected),
            "cases": [dict(case) for case in cases],
        }
        pairs = []
        for case in cases:
            seed = case["seed"]
            common = dict(
                memory_id=memory_id, seed=seed,
                suite=context["suite"], task_id=context["task_id"],
                perception_mode=context["perception_mode"],
                policy_id=provenance["source_policy_id"],
                assistance_credits=assistance_credits,
                variation={axis: case[axis] for axis in (*axes, "camera_names", "task_prompt")},
            )
            pairs.append((
                ValidationTrial(treatment=False, **common),
                ValidationTrial(treatment=True, **common),
            ))
        return plan, tuple(pairs)

    def run_validation(
        self, memory_id: str, *, executor: TrialExecutor,
        cases: tuple[dict[str, Any], ...],
        assistance_credits: int = 0,
    ) -> dict[str, Any]:
        """Run matched trials and report them; promotion remains service-gated."""
        pairs = self.plan_validation(
            memory_id, cases=cases, assistance_credits=assistance_credits,
        )
        completed = {item["seed"] for item in self.service.list_pairs(memory_id)}
        for control, treatment in pairs:
            if control.seed in completed:
                continue
            control_evidence = executor(control)
            treatment_evidence = executor(treatment)
            if not isinstance(control_evidence, TrialEvidence) or not isinstance(treatment_evidence, TrialEvidence):
                raise TypeError("trial executor must return TrialEvidence")
            if not control_evidence.config_sha256 or control_evidence.config_sha256 != treatment_evidence.config_sha256:
                raise ValueError("paired trial executor reported different configurations")
            self.service.record_pair(
                memory_id=memory_id,
                control_attempt_id=control_evidence.attempt_id,
                treatment_attempt_id=treatment_evidence.attempt_id,
                control_safety=control_evidence.safety_artifact,
                treatment_safety=treatment_evidence.safety_artifact,
            )
        return self.service.impact_report(memory_id)

    def request_promotion(self, memory_id: str) -> MemoryRecord:
        return self.service.promote(memory_id)

    @staticmethod
    def _default_distiller(source: dict[str, Any]) -> MemoryDraft:
        content = source["response_content"]
        if source["responder"] == "advisor_proxy":
            advice = parse_advisor_advice(content, request_type="hint")
            guidance = advice.guidance
            repair = (
                f"Failure hypothesis: {advice.diagnosis}\n"
                f"Proposed change: {advice.guidance}\n"
                f"Caution: {advice.caution}"
            )
        else:
            guidance = content.strip()
            repair = guidance
        if not guidance or len(guidance) > 2000:
            raise ValueError("memory guidance must be 1-2000 characters")
        return MemoryDraft(guidance=guidance, repair=repair)
