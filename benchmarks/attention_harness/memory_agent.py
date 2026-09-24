"""Memory Agent: distill feedback and orchestrate evidence-gated validation.

This agent is a peer of the development/deployment workflows. It has no
direct database, simulator, or promotion authority; those belong to the
Memory Service and the injected trial executor respectively.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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


@dataclass(frozen=True)
class TrialEvidence:
    attempt_id: str
    safety_artifact: Path


class TrialExecutor(Protocol):
    def __call__(self, trial: ValidationTrial) -> TrialEvidence: ...


class MemoryGateway(Protocol):
    """Implemented by the in-process service and authenticated HTTP client."""

    def source_for_agent(self, request_id: str) -> dict[str, Any]: ...
    def create_candidate(self, **payload: Any) -> MemoryRecord: ...
    def get_memory(self, memory_id: str) -> MemoryRecord: ...
    def provenance(self, memory_id: str) -> dict[str, Any]: ...
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
        self, memory_id: str, *, seeds: tuple[int, ...] | None = None,
        assistance_credits: int = 0,
    ) -> tuple[tuple[ValidationTrial, ValidationTrial], ...]:
        memory = self.service.get_memory(memory_id)
        if memory.status.value != "candidate":
            raise ValueError("validation planning requires a candidate memory")
        provenance = self.service.provenance(memory_id)
        if provenance["source_execution_target"] not in {"robocasa_sim", "robosuite_sim"}:
            raise PermissionError("automatic memory validation is simulator-only")
        selected = tuple(
            seed for seed in range(101, 126)
            if seed != provenance["source_seed"]
        )[:5] if seeds is None else seeds
        if len(selected) < 5 or len(set(selected)) != len(selected):
            raise ValueError("validation needs at least five distinct seeds")
        if isinstance(assistance_credits, bool) or not isinstance(assistance_credits, int) or assistance_credits < 0:
            raise ValueError("assistance_credits must be nonnegative")
        for seed in selected:
            if validate_seed(seed) != "dev":
                raise PermissionError("memory validation is development-only")
        context = provenance["artifact"]["applicability"]
        self.service.record_plan(memory_id, {
            "schema_version": "attentionbench.memory-validation-plan.v2",
            "memory_id": memory_id,
            "suite": context["suite"],
            "task_id": context["task_id"],
            "perception_mode": context["perception_mode"],
            "policy_id": provenance["source_policy_id"],
            "assistance_credits": assistance_credits,
            "seeds": list(selected),
        })
        pairs = []
        for seed in selected:
            common = dict(
                memory_id=memory_id, seed=seed,
                suite=context["suite"], task_id=context["task_id"],
                perception_mode=context["perception_mode"],
                policy_id=provenance["source_policy_id"],
                assistance_credits=assistance_credits,
            )
            pairs.append((
                ValidationTrial(treatment=False, **common),
                ValidationTrial(treatment=True, **common),
            ))
        return tuple(pairs)

    def run_validation(
        self, memory_id: str, *, executor: TrialExecutor,
        seeds: tuple[int, ...] | None = None,
        assistance_credits: int = 0,
    ) -> dict[str, Any]:
        """Run matched trials and report them; promotion remains service-gated."""
        pairs = self.plan_validation(
            memory_id, seeds=seeds, assistance_credits=assistance_credits,
        )
        completed = {item["seed"] for item in self.service.list_pairs(memory_id)}
        for control, treatment in pairs:
            if control.seed in completed:
                continue
            control_evidence = executor(control)
            treatment_evidence = executor(treatment)
            if not isinstance(control_evidence, TrialEvidence) or not isinstance(treatment_evidence, TrialEvidence):
                raise TypeError("trial executor must return TrialEvidence")
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
        else:
            guidance = content.strip()
        if not guidance or len(guidance) > 2000:
            raise ValueError("memory guidance must be 1-2000 characters")
        return MemoryDraft(guidance=guidance, repair=guidance)
