"""Pure agent-policy-v2 decisions over controller-supplied public data.

No run-directory reader lives here. The controller must read canonical public
artifacts with read_beneath (never the writable workspace or private originals),
verify their hashes, and supply the accepted-submit window in acceptance order.
References are logical public paths, not paths this module opens. Dispatch,
question validation, ledger transactions and persistence belong to P2.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any, Mapping

from .core.assessment import _classify, _PASSED
from .dev_help import public_screen

AGENT_POLICY_VERSION = "agent-policy-v2"
AGENT_POLICY_IDS = (
    "autonomous", "demo_first", "reactive_help", "retry_k_then_ask",
    "budget_matched_random_escalation", "trace_aware_hint_only",
    "full_trace_aware_attention_planner", "agent_initiated_help",
)
TRACE_POLICIES = AGENT_POLICY_IDS[5:7]
_DEFAULTS = {
    "retry_k_then_ask": {"k": 2},
    "budget_matched_random_escalation": {
        "target_request_count": 1, "total_failure_slots": 10, "seed": 101},
    "trace_aware_hint_only": {"m": 3, "n": 2},
    "full_trace_aware_attention_planner": {"m": 3, "n": 2},
    "agent_initiated_help": {"min_new_executions": 1},
}
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_COORD = re.compile(r"[\[(]\s*" + _NUMBER + r"(?:\s*,\s*" + _NUMBER + r"){1,}\s*[\])]")
_PATH = re.compile(
    r"(?:https?://|file://)[^\s\"'<>]+"
    r"|(?:[A-Za-z]:[\\/]|\\\\|(?<![\w/])/|\.\.?/|~/)[^\s\"'<>]+"
    r"|(?<![\w.])(?:[\w.-]+/)+[\w.-]+")
_EXCEPTION = re.compile(
    r"^\s*(?:execution_error:\s*)?(?P<type>(?:[A-Za-z_]\w*\.)*"
    r"(?:[A-Za-z_]\w*(?:Error|Exception|Rejected|Failure)|Exception|SystemExit|KeyboardInterrupt))"
    r"\s*:\s*(?P<message>.*)$")
_FAILED = {"failed", "error", "timeout", "timed_out", "cancelled", "rejected"}


def _screen(value: Any) -> None:
    public_screen(value)
    # Compatibility characters must not hide prohibited words before NFKC.
    public_screen(unicodedata.normalize("NFKC", json.dumps(value, ensure_ascii=False, allow_nan=False)))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def normalize_parameters(policy_id: str, parameters: dict | None = None) -> dict:
    if policy_id not in AGENT_POLICY_IDS:
        raise ValueError("unknown agent policy")
    defaults = _DEFAULTS.get(policy_id, {})
    result = defaults.copy() if parameters is None else parameters
    if (not isinstance(result, dict) or set(result) != set(defaults)
            or any(type(v) is not int for v in result.values())):
        raise ValueError("complete preregistered integer policy parameters required")
    for key, value in result.items():
        if key not in {"seed", "target_request_count", "total_failure_slots"} and value < 1:
            raise ValueError("positive policy parameter required")
    if "total_failure_slots" in result and not 0 <= result["target_request_count"] <= result["total_failure_slots"]:
        raise ValueError("target count must fit within failure slots")
    return result.copy()


def parameters_sha256(policy_id: str, parameters: dict | None = None) -> str:
    return _digest(normalize_parameters(policy_id, parameters))


def policy_options() -> dict:
    return {"agent_policy_version": AGENT_POLICY_VERSION,
            "policies": [{"policy_id": p, "parameters": normalize_parameters(p),
                          "memory_exposed": memory_exposed(p)} for p in AGENT_POLICY_IDS]}


def memory_exposed(policy_id: str) -> bool:
    normalize_parameters(policy_id)
    return policy_id != "trace_aware_hint_only"


def selected_random_slots(parameters: dict | None = None) -> tuple[int, ...]:
    p = normalize_parameters(AGENT_POLICY_IDS[4], parameters)
    ranked = sorted(range(p["total_failure_slots"]),
                    key=lambda slot: hashlib.sha256(f'{p["seed"]}:{slot}'.encode()).digest())
    return tuple(sorted(ranked[:p["target_request_count"]]))


def normalize_message(message: str) -> str:
    _screen(message)  # Screen BEFORE deleting paths, numbers, etc.
    text = _ANSI.sub("", unicodedata.normalize("NFKC", message))
    text = re.sub(r'^\s*File\s+".*?",\s*line\s+\d+.*$', "", text, flags=re.M)
    def path_or_division(match):
        value = match.group()
        # The suggested path regex also matches compact arithmetic. Preserve
        # numeric division and single-letter operands (8/2, x/y).
        if re.fullmatch(_NUMBER + "/" + _NUMBER, value) or re.fullmatch(r"[A-Za-z]/[A-Za-z]", value):
            return value
        return "<path>"
    text = _PATH.sub(path_or_division, text)
    text = _COORD.sub("<coordinates>", text)
    text = re.sub(r"\b0x[0-9a-fA-F]+\b", "<hex>", text)
    text = re.sub(_NUMBER, "<number>", text)
    text = " ".join(text.split()).casefold()
    _screen(text)
    return text


@dataclass(frozen=True)
class FailureSignature:
    signature_id: str | None
    stage: str
    code: dict | None
    exception: dict | None
    selection_basis: str


def failure_signature(stage: str, *, code: dict | None = None,
                      stdout: str = "", stderr: str = "") -> FailureSignature:
    _screen({"stage": stage, "code": code, "stdout": stdout, "stderr": stderr})
    exception = None
    basis = "no_public_exception"
    for name, stream in (("stderr", stderr), ("stdout", stdout)):
        found = [_EXCEPTION.match(line) for line in
                 _ANSI.sub("", unicodedata.normalize("NFKC", stream)).splitlines()]
        matches = [m for m in found if m]
        if matches:
            last = matches[-1]
            # A completion wrapper reports no concrete cause; do not turn many
            # unknown outcomes into a repeated-cause signature.
            if last["type"] != "TaskOutcomeFailure":
                exception = {"type": last["type"], "normalized_message": normalize_message(last["message"])}
            basis = "stderr_priority" if name == "stderr" else "stdout_fallback"
            break
    if code is not None:
        if (set(code) != {"source", "value"} or not isinstance(code["value"], str)
                or not _CODE.fullmatch(code["value"]) or not isinstance(code["source"], str)):
            raise ValueError("structured error code required")
        code = code.copy()
    payload = {"stage": stage, "code": code, "exception": exception}
    _screen(payload)
    # A stage alone (including 'unknown') is not evidence of the same cause.
    return FailureSignature(_digest(payload) if code or exception else None,
                            stage, code, exception, basis)


@dataclass(frozen=True)
class ArtifactRef:
    path: str
    sha256: str | None = None
    status: str = "present"  # present / missing / withheld

    def __post_init__(self):
        if (not re.fullmatch(r"(?:executions/e\d{6}/|probes/p\d{6}(?:/|\.))[^\\]*", self.path)
                or any(p in {"", ".", ".."} for p in self.path.split("/"))):
            raise ValueError("public relative artifact path required")
        if self.status not in {"present", "missing", "withheld"}:
            raise ValueError("invalid artifact status")
        if self.sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("invalid artifact hash")
        if self.status == "present" and self.sha256 is None:
            raise ValueError("present artifact requires verified hash")
        _screen(asdict(self))


@dataclass(frozen=True)
class PublicExecution:
    """Already-read canonical public projection; never Eval/native results."""
    status: str = "unknown"
    trace: tuple[dict, ...] = ()
    planner: tuple[dict, ...] = ()
    stdout: str = ""
    stderr: str = ""
    artifacts: tuple[ArtifactRef, ...] = ()
    sequence: int = 0
    op: str = "run"


@dataclass(frozen=True)
class ExecutionEvidence:
    execution_id: str
    sequence: int
    op: str
    status: str
    failed: bool
    stage: str
    passed_steps: int
    signature: FailureSignature
    safety_interrupt: bool
    event_ids: tuple[str, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    unassociated_planner_codes: tuple[str, ...] = ()
    log_tail: str = ""
    log_truncated: bool = False


def _code_value(row: Mapping) -> str | None:
    for key in ("code", "error_code", "reason"):
        value = row.get(key)
        if isinstance(value, str) and _CODE.fullmatch(value) and value != "withheld":
            return value
    return None


def summarize_execution(execution_id: str, public: PublicExecution) -> ExecutionEvidence:
    if not isinstance(public, PublicExecution):
        raise ValueError("already-read public execution required; paths/readers are not accepted")
    if not re.fullmatch(r"e\d{6}", execution_id) or public.op not in {"run", "submit"}:
        raise ValueError("invalid execution identity")
    if type(public.sequence) is not int or public.sequence < 0:
        raise ValueError("nonnegative acceptance sequence required")
    _screen(asdict(public))
    events = list(public.trace)
    failed = next((e for e in events if e.get("status") in _FAILED or e.get("error")), None)
    before = events[:events.index(failed)] if failed is not None else events
    passed = sum(e.get("status") in _PASSED and
                 e.get("event_type") not in {"execution.started", "execution.finished"} for e in before)
    stage = _classify(failed)[0]
    if failed and failed.get("status") == "rejected" and stage == "safety_interrupt":
        stage = "ik_motion_planning"  # refused before motion, not an occurred interrupt
    error = (failed or {}).get("error") or {}
    error = error if isinstance(error, dict) else {}
    code = None
    for value in (error.get("code"), (failed or {}).get("error_code")):
        if isinstance(value, str) and _CODE.fullmatch(value):
            code = {"source": "trace", "value": value}
            break
    candidates = []
    for row in public.planner:
        value = _code_value(row)
        if not value or not (row.get("accepted") is False or row.get("status") in _FAILED):
            continue
        # P2 must publish this source index; legacy unlinked diagnostics stay candidates.
        associated = bool(failed and failed.get("event_id") and
                          row.get("event_id") == failed["event_id"])
        if code is None and associated:
            code = {"source": "planner", "value": value}
        elif not associated:
            candidates.append(value)
    if (code is None and isinstance(error.get("type"), str) and _CODE.fullmatch(error["type"])
            and error["type"] != "TaskOutcomeFailure"):
        code = {"source": "trace.error.type", "value": error["type"]}
    # A planning rejection mentioning collision is not an occurred safety interrupt.
    safety = any(e.get("status") != "rejected" and
                 (e.get("status") in _FAILED or e.get("error")) and
                 _classify(e)[0] == "safety_interrupt" for e in events)
    signature = failure_signature(stage, code=code, stdout=public.stdout, stderr=public.stderr)
    is_failed = failed is not None or public.status in _FAILED
    if not is_failed:
        signature = failure_signature("unknown")
    # Combined stdout/stderr payload <= 2000 chars; explicit stream labels.
    tail = "stdout:\n" + public.stdout[-980:] + "\nstderr:\n" + public.stderr[-980:]
    result = ExecutionEvidence(execution_id, public.sequence, public.op, public.status,
                               is_failed, stage, passed, signature, safety,
                               (str(failed["event_id"]),) if failed and failed.get("event_id") else (),
                               public.artifacts, tuple(candidates), tail,
                               len(public.stdout) > 980 or len(public.stderr) > 980)
    _screen(asdict(result))
    return result


@dataclass(frozen=True)
class ProbeEvidence:
    probe_id: str
    sequence: int
    signature: FailureSignature
    op: str = "plan_dry_run"
    rejected: bool = True
    # validation_error and timeout are not genuine planner rejection evidence.
    outcome: str = "planner_rejected"

    def __post_init__(self):
        if not re.fullmatch(r"p\d{6}", self.probe_id) or type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("invalid probe identity/sequence")


@dataclass(frozen=True)
class VersionEvidence:
    version_id: str
    bundle_sha256: str
    executions: tuple[ExecutionEvidence, ...] = ()
    rejected_probes: tuple[ProbeEvidence, ...] = ()
    execution_histogram: tuple[tuple[str, int], ...] = ()
    probe_histogram: tuple[tuple[str, int], ...] = ()
    probe_only_signatures: tuple[str, ...] = ()
    primary_signature: str | None = None
    representative_execution_id: str | None = None
    stuck_in_version: int = 0
    max_passed_steps: int = 0
    progress: bool = False
    repeated_across_versions: bool = False
    code_changed: bool | None = None
    vague: bool = True
    safety_interrupt: bool = False
    eval_verdict: str = "unavailable"
    agent_policy_version: str = AGENT_POLICY_VERSION
    submit_sequence: int | None = None

    def artifact(self) -> dict:
        return asdict(self)


def _unique_ordered(items, key):
    unique = {}
    for item in items:
        identity = getattr(item, key)
        if identity in unique and item != unique[identity]:
            raise ValueError("conflicting operation evidence")
        unique[identity] = item
    return tuple(sorted(unique.values(), key=lambda x: (x.sequence, getattr(x, key))))


def build_version_evidence(version_id: str, executions: tuple[ExecutionEvidence, ...],
                           rejected_probes: tuple[ProbeEvidence, ...], bundle_sha256: str,
                           previous: tuple[VersionEvidence, ...], *,
                           eval_verdict: str = "unavailable", submit_sequence: int | None = None) -> VersionEvidence:
    """Inputs cover (previous accepted submit, current accepted submit].

    Controller chooses this window, including invalid submit with no execution;
    rejected ask_required requests do not close it. Cross-window reuse is rejected.
    """
    if not re.fullmatch(r"v\d{4}", version_id) or not re.fullmatch(r"[0-9a-f]{64}", bundle_sha256):
        raise ValueError("invalid version identity/hash")
    if eval_verdict not in {"passed", "failed", "unavailable"}:
        raise ValueError("invalid verdict")
    executions = _unique_ordered(executions, "execution_id")
    probes = _unique_ordered(rejected_probes, "probe_id")
    if submit_sequence is not None and (type(submit_sequence) is not int or submit_sequence < 0):
        raise ValueError("invalid submit sequence")
    start = previous[-1].submit_sequence if previous else None
    if ((start is not None and submit_sequence is not None and submit_sequence <= start)
            or any((start is not None and op.sequence <= start) or
                   (submit_sequence is not None and op.sequence > submit_sequence)
                   for op in (*executions, *probes))):
        raise ValueError("operation outside accepted-submit window")
    if any(v.agent_policy_version != AGENT_POLICY_VERSION for v in previous):
        raise ValueError("evidence version mismatch")
    if any(e.execution_id in {old.execution_id for v in previous for old in v.executions} for e in executions):
        raise ValueError("execution outside current window")
    if any(p.probe_id in {old.probe_id for v in previous for old in v.rejected_probes} for p in probes):
        raise ValueError("probe outside current window")
    probes = tuple(p for p in probes if p.op == "plan_dry_run" and p.rejected and p.outcome == "planner_rejected")
    counts = Counter(e.signature.signature_id for e in executions if e.failed and e.signature.signature_id)
    probe_counts = Counter(p.signature.signature_id for p in probes if p.signature.signature_id)
    latest = {sid: max(e.sequence for e in executions if e.failed and e.signature.signature_id == sid) for sid in counts}
    primary = min(counts, key=lambda sid: (-counts[sid], -latest[sid], sid)) if counts else None
    representatives = [e for e in executions if e.failed and primary and e.signature.signature_id == primary]
    representative = representatives[-1] if representatives else (executions[-1] if executions else None)
    best = max((e.passed_steps for e in executions), default=0)
    progress = bool(previous and best > max(v.max_passed_steps for v in previous))
    prior = previous[-1] if previous else None
    result = VersionEvidence(
        version_id, bundle_sha256, executions, probes, tuple(sorted(counts.items())),
        tuple(sorted(probe_counts.items())), tuple(sorted(probe_counts.keys() - counts.keys())),
        primary, representative.execution_id if representative else None,
        counts[primary] if primary else 0, best, progress,
        bool(prior and prior.eval_verdict != "passed" and primary and primary == prior.primary_signature and not progress),
        bundle_sha256 != prior.bundle_sha256 if prior else None,
        not any(e.failed and e.signature.signature_id for e in executions),
        any(e.safety_interrupt for e in executions), eval_verdict, submit_sequence=submit_sequence)
    _screen(result.artifact())
    return result


@dataclass(frozen=True)
class PendingAsk:
    origin_version_id: str
    execution_id: str | None
    decision_id: str
    reason: str


@dataclass(frozen=True)
class PolicyState:
    schema: str = "attentionbench.agent-policy-state.v2"
    agent_policy_version: str = AGENT_POLICY_VERSION
    policy_id: str = ""
    parameters_sha256: str = ""
    retry_failures: int = 0
    cumulative_failed_versions: int = 0
    trace_failed_versions: int = 0
    last_boundary_version: str | None = None
    last_submit_sequence: int = -1
    latest_execution_id: str | None = None
    completed_execution_ids: tuple[str, ...] = ()
    last_help_execution_count: int = 0
    pending_ask: PendingAsk | None = None
    demo_dispatched: bool = False

    @property
    def completed_execution_count(self):
        return len(self.completed_execution_ids)


def initial_state(policy_id: str, parameters: dict | None = None) -> PolicyState:
    return PolicyState(policy_id=policy_id, parameters_sha256=parameters_sha256(policy_id, parameters))


@dataclass(frozen=True)
class PolicyEvent:
    # submit includes the completed submission execution, if any. Alternatively
    # register it first with execution; duplicate eid registration is a no-op.
    kind: str
    version_id: str | None = None
    execution_id: str | None = None
    sequence: int = 0
    eval_verdict: str = "unavailable"
    op: str = "run"


@dataclass(frozen=True)
class Transition:
    next_state: PolicyState
    action: str
    reason: str
    evidence_refs: tuple[str, ...] = ()
    execution_id: str | None = None
    # Pure precomputed miss continuation: resolving Memory never increments counters.
    memory_miss: Transition | None = field(default=None, repr=False)


def decide_agent(policy_id: str, parameters: dict, state: PolicyState,
                 event: PolicyEvent, evidence: VersionEvidence | None, *,
                 help_remaining: int, request_pending: bool,
                 demo_available: bool = False) -> Transition:
    p = normalize_parameters(policy_id, parameters)
    if (state.schema != "attentionbench.agent-policy-state.v2" or
            state.agent_policy_version != AGENT_POLICY_VERSION or state.policy_id != policy_id or
            state.parameters_sha256 != parameters_sha256(policy_id, p)):
        raise ValueError("policy state version/parameters mismatch")
    if event.kind not in {"bootstrap", "execution", "submit", "ask_help", "help_dispatched", "demo_dispatched"}:
        raise ValueError("unknown policy event")
    if event.eval_verdict not in {"passed", "failed", "unavailable"}:
        raise ValueError("invalid verdict")
    if event.kind == "submit" and (not isinstance(event.version_id, str) or
            not re.fullmatch(r"v\d{4}", event.version_id) or
            type(event.sequence) is not int or event.sequence < 0):
        raise ValueError("submit version and nonnegative acceptance sequence required")
    if event.kind == "submit" and (event.version_id == state.last_boundary_version or
                                   event.sequence <= state.last_submit_sequence):
        # Check before registering an eid; replay must not mutate execution state.
        return Transition(state, "continue", "boundary_already_processed")
    if evidence and evidence.agent_policy_version != AGENT_POLICY_VERSION:
        raise ValueError("evidence version mismatch")
    refs = (evidence.version_id,) if evidence else ()
    target = state.latest_execution_id
    if state.pending_ask:
        target = state.pending_ask.execution_id

    def out(action, reason, current=None):
        return Transition(current or state, action, reason, refs, target)

    if event.kind in {"help_dispatched", "demo_dispatched"}:
        # Call ONLY after durable provider handoff, even if provider later fails.
        # Invalid questions/view failures/pre-deadline rejections never send this event.
        if event.kind == "demo_dispatched":
            if policy_id != "demo_first":
                raise ValueError("demo dispatch requires demo policy")
            return out("continue", "demo_recorded", replace(state, demo_dispatched=True))
        if not event.execution_id or event.execution_id not in state.completed_execution_ids:
            raise ValueError("help requires completed execution")
        if state.pending_ask and event.execution_id != state.pending_ask.execution_id:
            raise ValueError("help must discharge original execution obligation")
        return out("continue", "help_recorded", replace(state, retry_failures=0, pending_ask=None,
                   last_help_execution_count=state.completed_execution_count))
    if event.kind == "bootstrap":
        if policy_id == "demo_first" and not state.demo_dispatched and not state.last_boundary_version and demo_available:
            if help_remaining <= 0:
                return out("continue", "assistance_budget_exhausted")
            if request_pending:
                return out("continue", "request_already_pending")
            return out("use_demo", "fixed_pre_task_demo")
        return out("continue", "no_online_assistance")
    if event.kind in {"execution", "submit"}:
        if event.execution_id and event.execution_id not in state.completed_execution_ids:
            if not re.fullmatch(r"e\d{6}", event.execution_id) or event.op not in {"run", "submit"}:
                raise ValueError("invalid completed execution")
            state = replace(state, latest_execution_id=event.execution_id,
                            completed_execution_ids=state.completed_execution_ids + (event.execution_id,))
        target = state.pending_ask.execution_id if state.pending_ask else state.latest_execution_id
    if event.kind == "execution":
        return out("continue", "execution_recorded")
    if event.kind == "ask_help":
        if policy_id != "agent_initiated_help" and not state.pending_ask:
            return out("continue", "not_eligible")
        if help_remaining <= 0:
            return out("continue", "assistance_budget_exhausted")
        if request_pending:
            return out("continue", "request_already_pending")
        if not state.completed_execution_count:
            return out("continue", "no_completed_execution")
        if policy_id == "agent_initiated_help" and (
                state.completed_execution_count - state.last_help_execution_count < p["min_new_executions"]):
            return out("continue", "new_execution_required")
        return out("request", state.pending_ask.reason if state.pending_ask else "agent_initiated_help")
    if state.pending_ask:
        raise ValueError("ask_required: pending obligation before next accepted submit")
    if evidence and evidence.version_id != event.version_id:
        raise ValueError("evidence boundary mismatch")
    failed = event.eval_verdict == "failed"
    passed = event.eval_verdict == "passed"
    state = replace(state, last_boundary_version=event.version_id, last_submit_sequence=event.sequence,
                    cumulative_failed_versions=state.cumulative_failed_versions + int(failed),
                    retry_failures=(0 if passed else min(p.get("k", 2), state.retry_failures + int(failed))),
                    trace_failed_versions=(0 if passed else state.trace_failed_versions + int(failed)))
    if policy_id in TRACE_POLICIES and evidence and evidence.safety_interrupt:
        return out("stop", "trace_safety_interrupt")
    if not failed:
        return out("continue", "eval_passed" if passed else "eval_unavailable")
    if policy_id in {"autonomous", "demo_first", "agent_initiated_help"}:
        return out("continue", "no_online_assistance")
    if policy_id == "retry_k_then_ask" and state.retry_failures < p["k"]:
        return out("continue", "retry_before_threshold")
    if policy_id == AGENT_POLICY_IDS[4] and state.cumulative_failed_versions - 1 not in selected_random_slots(p):
        return out("continue", "random_slot_not_selected")
    if help_remaining <= 0:
        return out("continue", "assistance_budget_exhausted")
    if request_pending:
        return out("continue", "request_already_pending")
    if policy_id in {"reactive_help", "retry_k_then_ask"}:
        reason = "request_after_each_failure" if policy_id == "reactive_help" else "retry_threshold_reached"
        pending = PendingAsk(event.version_id, event.execution_id,
                             f"{policy_id}:{event.version_id}", reason)
        return out("pending_ask", reason, replace(state, pending_ask=pending))
    if policy_id == AGENT_POLICY_IDS[4]:
        return out("request", "budget_matched_random_slot")
    if evidence is None:
        raise ValueError("trace policy requires version evidence")
    target = evidence.representative_execution_id
    if evidence.stuck_in_version >= p["m"]:
        miss = out("request", "stuck_within_version")
    elif evidence.repeated_across_versions:
        miss = out("request", "same_failure_after_code_change" if evidence.code_changed else "unchanged_repeated_failure")
    elif evidence.progress:
        miss = out("continue", "progress")
    elif state.trace_failed_versions >= p["n"]:
        miss = out("request", "cause_unresolved")
    else:
        miss = out("continue", "no_trace_trigger")
    if policy_id == "full_trace_aware_attention_planner":
        return replace(out("check_memory", "check_matching_memory"), memory_miss=miss)
    return miss


def resolve_memory(transition: Transition, *, matched: bool) -> Transition:
    if (transition.action != "check_memory" or transition.memory_miss is None or
            transition.next_state.policy_id != "full_trace_aware_attention_planner"):
        raise ValueError("full planner memory check required")
    if type(matched) is not bool:
        raise ValueError("boolean memory result required")
    return (replace(transition, action="retrieve_memory", reason="trusted_memory_matches", memory_miss=None)
            if matched else transition.memory_miss)


class TerminationReason(str, Enum):
    WALL_STOP = "wall_stop"
    QUOTA_STOP = "quota_stop"
    QUOTA_UNAVAILABLE = "quota_unavailable"
    RUN_END = "run_end"
    SESSION_FAILURE = "session_failure"
    FATAL = "fatal"
    CANCEL = "cancel"
    PAUSE = "pause"
    NEXT_SUBMIT = "next_submit"


@dataclass(frozen=True)
class FallbackDecision:
    can_attempt: bool
    forced_fallback_blocked: str | None = None
    # Intent is always free. Only the P2 dispatcher can actually charge a job.
    charged: bool = False


def forced_fallback(pending_ask: PendingAsk | None, reason: TerminationReason) -> FallbackDecision:
    reason = TerminationReason(reason)
    if pending_ask is None:
        return FallbackDecision(False)
    if reason in {TerminationReason.RUN_END, TerminationReason.SESSION_FAILURE, TerminationReason.NEXT_SUBMIT}:
        return FallbackDecision(True)
    return FallbackDecision(False, reason.value)


def question_for_agent(reason: str, assessment: dict | None) -> str:
    from .dev_v2_loop import question_for
    _screen({"reason": reason, "assessment": assessment})
    suffix = {
        "stuck_within_version": "Repeated executions in this version show the same failure.",
        "same_failure_after_code_change": "The same failure persists after a code change.",
        "unchanged_repeated_failure": "The unchanged code repeats the previous version's failure.",
        "cause_unresolved": "The cause remains unresolved across failed versions.",
        "request_after_each_failure": "This submitted version failed its review.",
        "retry_threshold_reached": "The retry threshold has been reached.",
    }.get(reason, "")
    question = (question_for(assessment) + " " + suffix).strip()
    _screen(question)
    return question
