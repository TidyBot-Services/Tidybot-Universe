"""Versioned, backend-neutral records for the Attention System."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from ..attention_modes import AssistanceMode, RequestState


SCHEMA_VERSION = "attentionbench.core.v1"


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AttemptStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class RequestType(str, Enum):
    HINT = "hint"
    APPROVAL = "approval"
    INTERRUPT = "interrupt"


class RequestPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class MemoryStatus(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    TRUSTED = "trusted"
    REJECTED = "rejected"
    DISABLED = "disabled"
    EXPIRED = "expired"
    ROLLED_BACK = "rolled_back"


@dataclass(frozen=True)
class AssistanceBudget:
    assistance_credits: int
    token_limit: int
    execution_seconds: float
    gpu_seconds: float = 0.0

    def __post_init__(self) -> None:
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.assistance_credits, self.token_limit)
        ):
            raise ValueError("credit and token budgets must be non-negative integers")
        if self.execution_seconds <= 0:
            raise ValueError("execution_seconds must be positive")
        if self.gpu_seconds < 0:
            raise ValueError("gpu_seconds must be non-negative")


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    suite: str
    task_id: str
    seed: int
    policy_id: str
    developer_model: str
    assistance_mode: AssistanceMode
    execution_target: str
    budget: AssistanceBudget
    created_at: float
    status: RunStatus = RunStatus.CREATED
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.run_id, "run_id")
        _required(self.suite, "suite")
        _required(self.task_id, "task_id")
        _required(self.policy_id, "policy_id")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError("seed must be an integer")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str
    run_id: str
    index: int
    started_at: float
    status: AttemptStatus = AttemptStatus.RUNNING
    ended_at: float | None = None
    native_success: bool | None = None
    artifact_uri: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.attempt_id, "attempt_id")
        _required(self.run_id, "run_id")
        if self.index < 0:
            raise ValueError("attempt index must be non-negative")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class FailureSummary:
    stage: str
    error_type: str
    message: str
    consecutive_failures: int

    def __post_init__(self) -> None:
        _required(self.stage, "failure stage")
        _required(self.error_type, "failure error_type")
        if self.consecutive_failures < 1:
            raise ValueError("consecutive_failures must be positive")


@dataclass(frozen=True)
class TracePacket:
    trace_id: str
    run_id: str
    attempt_id: str
    created_at: float
    agent_state: str
    failure: FailureSummary
    evidence: tuple[dict[str, Any], ...]
    hypothesis: str
    memory_refs: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.trace_id, "trace_id")
        _required(self.run_id, "run_id")
        _required(self.attempt_id, "attempt_id")
        _required(self.agent_state, "agent_state")
        if not self.evidence:
            raise ValueError("trace packet requires at least one evidence item")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class AttentionRequestRecord:
    request_id: str
    run_id: str
    attempt_id: str
    trace_id: str
    request_type: RequestType
    reason: str
    priority: RequestPriority
    created_at: float
    deadline_at: float | None
    mode: AssistanceMode
    state: RequestState = RequestState.PENDING
    response_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.request_id, "request_id")
        _required(self.reason, "request reason")
        if self.deadline_at is not None and self.deadline_at < self.created_at:
            raise ValueError("request deadline precedes creation")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class AttentionResponseRecord:
    response_id: str
    request_id: str
    responder: str
    content: str
    created_at: float
    cache_key: str | None = None
    cached: bool = False
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.response_id, "response_id")
        _required(self.request_id, "request_id")
        _required(self.responder, "responder")
        _required(self.content, "response content")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class MemoryRecord:
    memory_id: str
    version: int
    source_trace_id: str
    guidance: str
    candidate_repair: str
    applicability: dict[str, Any]
    evidence_refs: tuple[str, ...]
    created_at: float
    status: MemoryStatus = MemoryStatus.CANDIDATE
    confidence: float = 0.0
    validation_successes: int = 0
    validation_failures: int = 0
    expires_at: float | None = None
    parent_memory_id: str | None = None
    status_reason: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.memory_id, "memory_id")
        _required(self.source_trace_id, "source_trace_id")
        _required(self.guidance, "memory guidance")
        _required(self.candidate_repair, "candidate repair")
        if self.version < 1:
            raise ValueError("memory version must be positive")
        if not self.evidence_refs:
            raise ValueError("memory requires raw evidence references")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("memory confidence must be between zero and one")
        if self.validation_successes < 0 or self.validation_failures < 0:
            raise ValueError("memory validation counts must be non-negative")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class MemoryUseRecord:
    use_id: str
    memory_id: str
    memory_version: int
    run_id: str
    attempt_id: str
    used_at: float
    outcome: str
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value, name in (
            (self.use_id, "use_id"),
            (self.memory_id, "memory_id"),
            (self.run_id, "run_id"),
            (self.attempt_id, "attempt_id"),
            (self.outcome, "memory-use outcome"),
        ):
            _required(value, name)
        if self.memory_version < 1:
            raise ValueError("memory-use version must be positive")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _artifact(value: Any) -> dict[str, Any]:
    def convert(item: Any) -> Any:
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, dict):
            return {str(key): convert(nested) for key, nested in item.items()}
        if isinstance(item, (tuple, list)):
            return [convert(nested) for nested in item]
        return item

    return convert(asdict(value))
