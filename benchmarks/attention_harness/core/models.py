"""Versioned, backend-neutral records for the Attention System."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from ..attention_modes import AssistanceMode, RequestState


SCHEMA_VERSION = "attentionbench.core.v1"
RAW_TRACE_SCHEMA_VERSION = "attentionbench.raw-execution-trace.v1"
ADVISOR_TRACE_SCHEMA_VERSION = "attentionbench.advisor-trace-packet.v1"


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


class TraceVisibility(str, Enum):
    """Audiences allowed to receive an event or evidence reference.

    INTERNAL is deliberately the default in the raw trace. Data reaches an
    advisor only after it is explicitly labelled ADVISOR or PUBLIC and passed
    through the visibility projector.
    """

    INTERNAL = "internal"
    AGENT = "agent"
    ADVISOR = "advisor"
    PUBLIC = "public"


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
class EvidenceRef:
    evidence_id: str
    kind: str
    uri: str
    sha256: str
    created_at: float
    visibility: tuple[TraceVisibility, ...] = (TraceVisibility.INTERNAL,)
    source_event_id: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _required(self.evidence_id, "evidence_id")
        _required(self.kind, "evidence kind")
        _required(self.uri, "evidence uri")
        _sha256(self.sha256, "evidence sha256")
        if not self.visibility:
            raise ValueError("evidence visibility must not be empty")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class TraceEvent:
    event_id: str
    sequence: int
    timestamp: float
    source: str
    event_type: str
    operation: str
    status: str
    visibility: tuple[TraceVisibility, ...] = (TraceVisibility.INTERNAL,)
    duration_ms: float | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for value, name in (
            (self.event_id, "event_id"),
            (self.source, "event source"),
            (self.event_type, "event_type"),
            (self.operation, "event operation"),
            (self.status, "event status"),
        ):
            _required(value, name)
        if self.sequence < 0:
            raise ValueError("event sequence must be non-negative")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("event duration must be non-negative")
        if not self.visibility:
            raise ValueError("event visibility must not be empty")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class FailureSummary:
    stage: str
    error_type: str
    message: str
    consecutive_failures: int
    first_failed_event_id: str | None = None
    last_successful_event_id: str | None = None
    observed_symptom: str | None = None
    inferred_cause: str | None = None
    termination_reason: str | None = None
    retryable: bool | None = None
    safety_relevant: bool = False
    classification_source: str = "provided"
    confidence: float = 1.0

    def __post_init__(self) -> None:
        _required(self.stage, "failure stage")
        _required(self.error_type, "failure error_type")
        if self.consecutive_failures < 1:
            raise ValueError("consecutive_failures must be positive")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("failure confidence must be between zero and one")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    stop_reason: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    elapsed_seconds: float | None = None
    native_success: bool | None = None
    evaluator_verdict: str | None = None
    evaluator_authoritative: bool = False

    def __post_init__(self) -> None:
        _required(self.status, "execution outcome status")
        if self.elapsed_seconds is not None and self.elapsed_seconds < 0:
            raise ValueError("execution elapsed_seconds must be non-negative")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class RawExecutionTrace:
    """Complete internal execution ledger before any advisor projection."""

    raw_trace_id: str
    run_id: str
    attempt_id: str
    execution_id: str
    created_at: float
    agent_state: str
    events: tuple[TraceEvent, ...]
    evidence: tuple[EvidenceRef, ...]
    hypothesis: str = ""
    failure: FailureSummary | None = None
    code: dict[str, Any] = field(default_factory=dict)
    outcome: ExecutionOutcome | None = None
    memory_refs: tuple[str, ...] = field(default_factory=tuple)
    complete: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = RAW_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for value, name in (
            (self.raw_trace_id, "raw_trace_id"),
            (self.run_id, "run_id"),
            (self.attempt_id, "attempt_id"),
            (self.execution_id, "execution_id"),
            (self.agent_state, "agent_state"),
        ):
            _required(value, name)
        event_ids = [event.event_id for event in self.events]
        sequences = [event.sequence for event in self.events]
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("raw trace event IDs must be unique")
        if len(sequences) != len(set(sequences)):
            raise ValueError("raw trace event sequences must be unique")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("raw trace evidence IDs must be unique")
        known_evidence = set(evidence_ids)
        missing = sorted(
            {
                evidence_id
                for event in self.events
                for evidence_id in event.evidence_refs
                if evidence_id not in known_evidence
            }
        )
        if missing:
            raise ValueError(f"trace events reference missing evidence: {missing}")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


@dataclass(frozen=True)
class TracePacket:
    """Advisor-visible projection of a RawExecutionTrace.

    The legacy name is retained because request, UI, and memory records already
    link to ``trace_id``. New packets should set ``raw_trace_id``.
    """

    trace_id: str
    run_id: str
    attempt_id: str
    created_at: float
    agent_state: str
    failure: FailureSummary
    evidence: tuple[dict[str, Any], ...]
    hypothesis: str
    memory_refs: tuple[str, ...] = field(default_factory=tuple)
    raw_trace_id: str | None = None
    execution_id: str | None = None
    code: dict[str, Any] = field(default_factory=dict)
    events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    outcome: dict[str, Any] = field(default_factory=dict)
    projection: dict[str, Any] = field(default_factory=dict)
    schema_version: str = ADVISOR_TRACE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.trace_id, "trace_id")
        _required(self.run_id, "run_id")
        _required(self.attempt_id, "attempt_id")
        _required(self.agent_state, "agent_state")
        if not self.evidence:
            raise ValueError("trace packet requires at least one evidence item")

    def artifact(self) -> dict[str, Any]:
        return _artifact(self)


# Explicit public name used by the new raw -> projection pipeline. TracePacket
# remains a compatibility alias for the already-persisted request contract.
AdvisorTracePacket = TracePacket


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
    provider_model: str | None = None
    provider_latency_seconds: float = 0.0
    logical_latency_seconds: float = 0.0
    provider_attempts: int = 0
    token_usage: dict[str, Any] = field(default_factory=dict)
    provider_request_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _required(self.response_id, "response_id")
        _required(self.request_id, "request_id")
        _required(self.responder, "responder")
        _required(self.content, "response content")
        if self.provider_latency_seconds < 0 or self.logical_latency_seconds < 0:
            raise ValueError("Advisor response latency must be non-negative")
        if self.provider_attempts < 0:
            raise ValueError("Advisor provider attempts must be non-negative")

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


def _sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{name} must be a 64-character hexadecimal digest")
    try:
        int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{name} must be a 64-character hexadecimal digest") from exc


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
