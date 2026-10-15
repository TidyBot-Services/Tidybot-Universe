"""One seven-policy Attention scheduler for the two sim_gt suites.

Each physical attempt is executed by the suite's existing native runner. This
development scheduler owns cross-attempt decisions and assistance accounting;
individual attempt traces and evaluator verdicts remain authoritative.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from attention_memory_service import MemoryService, MemoryServiceClient

from .attention_modes import AssistanceMode, MODE_SPECS, RequestState
from .request_flow import (
    ABORTED, ANSWERED, CANCELLED, ERROR, REQUEST_MODES, RequestFlow, WAITING,
)
from .core.advisor import AdvisorTransport
from .core.assessment import assess_trace
from .core.models import AssistanceBudget, AttentionRequestRecord, MemoryStatus, RequestType, RunRecord, RunStatus
from .core.policies import DecisionAction, PolicyContext, build_policy
from .demo_prior import verify_demo_prior
from .core.runtime import AttentionRuntime
from .core.store import AttentionStore
from .memory_ingestion import MemoryIngestion
from .gpu_meter import GpuMeter
from .parcc_advisor import parse_advisor_advice
from .policy_input import public_attention_input
from .seed_guard import validate_seed
from .service_recovery import service_stop_confirmed
from .v2_advisor import SIM_GT_MAX_TOKENS, SimGTAdvisorProxy, SimGTGLMAdvisorTransport


AttemptExecutor = Callable[..., dict[str, Any]]
SafetySignals = Callable[[dict[str, Any]], dict[str, bool]]


_WHY = {
    "request_after_each_failure": "The attempt failed and this policy asks after every failure.",
    "retry_threshold_2_reached": "The attempt failed again after the allowed retries.",
    "budget_matched_random_slot": "This failure slot was pre-registered for escalation.",
    "repeated_grounded_failure": "The same visible failure repeated.",
    "same_failure_after_code_change": "The same visible failure repeated even after the code changed.",
    "unchanged_repeated_failure": "The same visible failure repeated and the code did not change.",
    "repeated_failure_code_unknown": "The same visible failure repeated.",
    "cause_unknown_after_inspection": "The visible trace does not show the cause, even after inspection.",
    "cause_unresolved": "The visible trace does not resolve the cause after repeated failures.",
    "irreversible_action_requires_approval": "An action crossed a soft limit and was withheld before it ran.",
}
_ASK = {
    "hint": "A short written hint for the next attempt.",
    "approval": "Approve or deny the withheld action so the next attempt may perform it.",
    "interrupt": "Safety escalation: the robot was stopped; no action is requested.",
}


def build_request_explanation(decision, *, trace: dict[str, Any], trace_id: str,
                              blocking_reason: str | None,
                              withheld: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Structured statement of why the agent asks, what it asks for and what the Advisor sees."""
    request_type = decision.request_type.value
    evidence = [str(item["evidence_id"]) for item in trace.get("evidence", [])
                if isinstance(item, dict) and item.get("evidence_id")][:8]
    explanation = {
        "why": _WHY.get(decision.reason, decision.reason.replace("_", " ")),
        "reason_code": decision.reason,
        "assistance_requested": _ASK[request_type],
        "evidence_shown": {
            "trace_id": trace_id, "evidence_ids": evidence,
            "event_ids": list(decision.event_ids),
            "projection_sha256": (trace.get("projection") or {}).get("projection_sha256"),
        },
        "agent_hypothesis": trace.get("hypothesis") or None,
        "hypothesis_status": trace.get("hypothesis_status", "unknown"),
        "while_waiting": ("The agent continues with safe work and applies the reply at the next attempt boundary."
                          if blocking_reason is None else
                          f"The agent waits for the reply ({blocking_reason.replace('_', ' ')})."),
    }
    if withheld:
        explanation["withheld_actions"] = [
            {key: value for key, value in item.items() if key in {"rule", "action", "limit_m", "distance_m", "delta_m", "command"}}
            for item in withheld[:4]]
    return explanation


def input_for_action(action: DecisionAction, *, public_demo, trusted_memory_ids: list[str],
                     trace: dict[str, Any], assessment) -> dict[str, Any]:
    """Public input for the next attempt implied by a non-request decision."""
    value: dict[str, Any] = {"demo_prior": public_demo} if public_demo else {}
    if action is DecisionAction.RETRIEVE_MEMORY:
        value["memory_ids_to_use"] = trusted_memory_ids[:8]
    elif action is DecisionAction.INSPECT_TRACE:
        # Only the projected Advisor trace, never evaluator internals.
        value["inspected_trace"] = json.dumps({
            "failure": trace.get("failure"),
            "assessment": assessment.artifact() if assessment else None,
        }, ensure_ascii=False, default=str)[:4000]
    return value


def run_sim_gt_attention(
    *, suite: str, task_id: str, seed: int, artifact_root: Path,
    policy_id: str, attempt_executor: AttemptExecutor, max_attempts: int = 3,
    assistance_credits: int = 1, token_limit: int = 30_000,
    store_path: Path | None = None,
    ui_launch_id: str | None = None,
    skill_dag: dict[str, Any] | None = None,
    robot_configuration: dict[str, Any] | None = None,
    attention_control_binding: dict[str, Any] | None = None,
    policy_config: dict[str, int] | None = None,
    demo_prior: str | None = None,
    approved_demo_sha256: str | None = None,
    advisor_transport: AdvisorTransport | None = None,
    memory_gateway: MemoryService | MemoryServiceClient | None = None,
    memory_evidence_root: Path | None = None,
    memory_applicability_context: dict[str, Any] | None = None,
    safety_signals: SafetySignals | None = None,
    approval_granted: Callable[[dict[str, Any]], bool] | None = None,
    assistance_mode: AssistanceMode | str = AssistanceMode.BENCHMARK_PROXY,
    human_deadline_seconds: float | None = None,
    attempt_budget_seconds: float = 300.0,
    total_execution_seconds: float | None = None,
    whole_case_deadline_monotonic: float | None = None,
    fresh_advisor_cache: bool = False,
    advisor_replay_only: bool = False,
    memory_enabled: bool = True,
    trace_evidence: bool = True,
    strict_admission_budget: bool = False,
    request_mode: str = "async",
    approval_rules: dict[str, Any] | None = None,
    gpu_seconds_limit: float = 0.0,
    gpu_sampler: Callable[[], float | None] | None = None,
    gpu_sample_interval: float = 1.0,
    runner_boundary_mode: str = "trusted_dev",
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run all seven policies against either suite using the same decisions.

    ``attempt_executor`` takes artifact_root, store_path, attention_input,
    assistance_credits, token_limit and memory_gateway. Its returned result
    must be from a native sim_gt runner, including its persisted trace link.
    A demo is an approved, hashed pre-task public SDK artifact manifest.
    Approval is fail-closed unless an external human decision callback exists.
    """
    if any(type(value) is not bool for value in (advisor_replay_only, memory_enabled, trace_evidence)):
        raise ValueError("Advisor replay and ablation flags must be boolean")
    if suite not in {"robocasa", "robosuite", "robocasa365"}:
        raise ValueError("unsupported sim_gt suite")
    if runner_boundary_mode not in {"trusted_dev", "generated_sandbox", "formal"}:
        raise ValueError("unsupported runner boundary")
    if request_mode not in REQUEST_MODES:
        raise ValueError(f"request_mode must be one of {REQUEST_MODES}")
    if (isinstance(gpu_seconds_limit, bool) or not isinstance(gpu_seconds_limit, (int, float))
            or not math.isfinite(gpu_seconds_limit) or gpu_seconds_limit < 0):
        raise ValueError("GPU-time budget must be a non-negative number (0 = not budgeted)")
    assistance_mode = AssistanceMode(assistance_mode)
    if human_deadline_seconds is None:
        human_deadline_seconds = MODE_SPECS[AssistanceMode.LIVE_HUMAN_FIRST].human_deadline_seconds
    if (human_deadline_seconds is None or not isinstance(human_deadline_seconds, (int, float))
            or isinstance(human_deadline_seconds, bool) or not 0 < human_deadline_seconds <= 600):
        raise ValueError("human deadline must be positive and at most 600 seconds")
    if isinstance(attempt_budget_seconds, bool) or not isinstance(attempt_budget_seconds, (int, float)) or not 1 <= attempt_budget_seconds <= 1800:
        raise ValueError("attempt execution budget must be 1–1800 seconds")
    if total_execution_seconds is not None and (
        isinstance(total_execution_seconds, bool)
        or not isinstance(total_execution_seconds, (int, float))
        or not math.isfinite(total_execution_seconds)
        or total_execution_seconds <= 0
    ):
        raise ValueError("total execution budget must be finite and positive")
    if whole_case_deadline_monotonic is not None and (
        isinstance(whole_case_deadline_monotonic, bool)
        or not isinstance(whole_case_deadline_monotonic, (int, float))
        or not math.isfinite(whole_case_deadline_monotonic)
        or whole_case_deadline_monotonic <= monotonic()
    ):
        raise ValueError("whole-case deadline must be finite and in the future")
    validate_seed(seed, allow_heldout=False)
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0
           for x in (max_attempts, assistance_credits, token_limit)) or max_attempts < 1:
        raise ValueError("invalid attempt or assistance budget")
    if policy_id == "demo_first" and not demo_prior:
        raise ValueError("demo_first requires a fixed pre-task demo prior")
    if (demo_prior is None) != (approved_demo_sha256 is None):
        raise ValueError("demo manifest and approved SHA-256 must be supplied together")
    if demo_prior is not None and policy_id != "demo_first":
        raise ValueError("only demo_first may receive a demo prior")
    config = dict(policy_config or {})
    if policy_id == "budget_matched_random_escalation":
        if set(config) - {"target_request_count", "total_failure_slots", "seed"}:
            raise ValueError("random escalation config has unsupported fields")
        if "target_request_count" not in config:
            raise ValueError("random escalation requires a pre-registered target_request_count")
        if (isinstance(config["target_request_count"], bool)
                or not isinstance(config["target_request_count"], int)):
            raise ValueError("random target must be an integer")
        if config.get("total_failure_slots", max_attempts - 1) != max_attempts - 1:
            raise ValueError("random failure slots must equal max_attempts - 1")
        if config.get("seed", seed) != seed:
            raise ValueError("random seed must be the run seed")
        if config["target_request_count"] > assistance_credits:
            raise ValueError("random target exceeds assistance budget")
        config.setdefault("total_failure_slots", max_attempts - 1)
        config.setdefault("seed", seed)
    policy = build_policy(policy_id, **config)
    # Validate the whole public demo, including referenced assets, before a run
    # directory or store can be mistaken for an accepted launch.
    if demo_prior is not None:
        verify_demo_prior(demo_prior, suite=suite, task_id=task_id,
                          approved_sha256=approved_demo_sha256)
    run_dir = artifact_root / f"attention-{suite}-{task_id}-seed{seed}-{uuid4().hex[:12]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    advisor_cache_path = (run_dir if fresh_advisor_cache else artifact_root) / "advisor_cache.sqlite3"
    public_demo, demo_receipt = (
        verify_demo_prior(demo_prior, suite=suite, task_id=task_id,
                          approved_sha256=approved_demo_sha256,
                          snapshot_dir=run_dir / "approved_demo")
        if demo_prior else (None, None))
    random_plan = None
    if policy_id == "budget_matched_random_escalation":
        random_plan = {
            "schema_version": "attentionbench.random-escalation-prereg.v1",
            "rule": "SHA-256(seed:failure_slot) rank; select lowest target_request_count slots",
            "seed": policy.seed, "target_request_count": policy.target_request_count,
            "total_failure_slots": policy.total_failure_slots,
            "selected_slots": sorted(policy.selected_slots),
            "source": "operator_pre_registered_target_no_full_trace_or_result",
        }
        (run_dir / "random_preregistration.json").write_text(
            json.dumps(random_plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        random_plan["sha256"] = hashlib.sha256(
            (run_dir / "random_preregistration.json").read_bytes()).hexdigest()
    unified_run_id = f"run:{run_dir.name}"
    if skill_dag is not None:
        from .task_dag import validate_dag
        skill_dag = validate_dag(skill_dag, suite=suite, task_id=task_id)
    store_path = store_path or run_dir / "attention.sqlite3"
    control_store = AttentionStore(store_path)
    control_store.create_run(RunRecord(
        run_id=unified_run_id, suite=suite, task_id=task_id, seed=seed,
        policy_id=policy_id, developer_model=(
            "formal-generated-policy" if runner_boundary_mode == "formal"
            else "sandboxed-generated-policy" if runner_boundary_mode == "generated_sandbox"
            else "trusted-policy-callback"
        ),
        evaluator_model="native_evaluator", assistance_mode=assistance_mode,
        execution_target=f"{suite}_sim",
        budget=AssistanceBudget(assistance_credits=assistance_credits,
                                token_limit=token_limit, gpu_seconds=float(gpu_seconds_limit),
                                execution_seconds=(float(total_execution_seconds)
                                                   if total_execution_seconds is not None
                                                   else float(attempt_budget_seconds) * max_attempts)),
        created_at=clock(), robot_configuration=robot_configuration,
    ))
    from .attention_control import AttentionControl
    controller = AttentionControl(control_store)
    if attention_control_binding is not None:
        controller.bind(unified_run_id, attention_control_binding)
    policy_cache = {policy_id: policy}
    def sync_policy(kind, boundary_id):
        nonlocal policy
        if attention_control_binding is None:return
        state = controller.boundary(unified_run_id, boundary_id=boundary_id,
            kind=kind, now=clock(), unresolved_hint=controller.unresolved_hint(unified_run_id))
        active_id = state['active_policy']
        if active_id not in policy_cache:policy_cache[active_id] = build_policy(active_id, **config)
        policy = policy_cache[active_id]
    if ui_launch_id is not None:
        control_store.bind_launch_run(ui_launch_id, unified_run_id)
    control_store.record_task_plan(unified_run_id, max_attempts=max_attempts)
    if skill_dag is not None:
        control_store.bind_task_dag(unified_run_id, skill_dag)
    control_store.transition_run(unified_run_id, RunStatus.RUNNING,
                                 event_key=f"start:{unified_run_id}")
    interrupted = lambda: (control_store.interrupt_status(unified_run_id) or {}).get("state") == "requested"
    wall_expired = lambda: (whole_case_deadline_monotonic is not None
                            and monotonic() >= whole_case_deadline_monotonic)
    # A later formal run may reuse the source run's authoritative SQLite store.
    # Its candidate evidence URIs are relative to that source run's attempts/.
    # Keep the original run layout for fresh stores and resolve the source
    # evidence root only when the store belongs to a different run directory.
    memory_artifact_root = memory_evidence_root or store_path.parent
    if (memory_evidence_root is None and runner_boundary_mode == "formal"
            and store_path.parent.resolve() != run_dir.resolve()):
        memory_artifact_root = store_path.parent / "attempts"
    memory_gateway = memory_gateway or MemoryService(
        AttentionStore(store_path), artifact_root=memory_artifact_root,
    )
    if suite == "robocasa365":
        from .robocasa365.memory import ScopedMemoryGateway
        if memory_applicability_context is None:
            raise ValueError("RoboCasa365 requires robot/controller-bound Memory scope")
        memory_gateway = ScopedMemoryGateway(memory_gateway, memory_applicability_context)
    attempts: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    monitored_attempts: list[dict[str, bool]] = []
    previous_traces: list[dict[str, Any]] = []
    attention_input: dict[str, Any] = {}
    credits_used = 0
    tokens_used = 0
    stopped_reason: str | None = None
    pending_execution_link: str | None = None
    flow = RequestFlow(store=control_store, clock=clock, monotonic=monotonic, sleeper=sleeper,
                       human_deadline_seconds=human_deadline_seconds, request_mode=request_mode)
    control_store.record_scheduler_options(unified_run_id, {
        # Keep the established default snapshot shape; non-default switches
        # are explicit here and all switches are in the run/config artifacts.
        **({"advisor_replay_only": True} if advisor_replay_only else {}),
        **({"memory_enabled": False} if not memory_enabled else {}),
        **({"trace_evidence": False} if not trace_evidence else {}),
        "request_mode": request_mode, "approval_rules": dict(approval_rules or {}),
        "assistance_mode": assistance_mode.value, "gpu_seconds_limit": float(gpu_seconds_limit)})
    gpu_usage: list[dict[str, Any]] = []
    if public_demo:
        initial = policy.decide(PolicyContext(
            run_id=str(run_dir), attempt_index=0, failure_index=0,
            consecutive_failures=0, assistance_remaining=assistance_credits,
            evidence_count=0, has_hypothesis=False, demo_available=True,
        ))
        if initial.action is DecisionAction.USE_DEMO:
            attention_input["demo_prior"] = public_demo
            decisions.append({"before_attempt": 0, "action": "use_demo", "reason": initial.reason,
                              "demo_manifest_sha256": demo_receipt["manifest_sha256"]})

    ingestion = MemoryIngestion(control_store, memory_gateway, clock=clock)

    def note_ingestion(outcome: dict[str, Any]) -> None:
        """Mirror a Memory ingestion decision onto the request's summary row."""
        for row in requests:
            if row.get("request_id") == outcome["request_id"]:
                row["memory_ingestion"] = {key: outcome[key] for key in outcome
                                           if key not in {"request_id", "run_id"}}
                if outcome["decision"] == "ingested":
                    row["candidate_memory_id"] = outcome["memory_id"]

    # -- request lifecycle helpers ----------------------------------------------
    def account(outstanding) -> dict[str, Any]:
        """Charge an answered request exactly once and return its stored response."""
        nonlocal credits_used, tokens_used
        response = control_store.get_response(
            control_store.get_request(outstanding.request_id).response_id)
        if not outstanding.summary.get("accounted"):
            credits_used += 1
            tokens_used += int((response.get("token_usage") or {}).get("total_tokens") or 0)
            outstanding.summary["accounted"] = True
        return response

    def request_row(outstanding, response: dict[str, Any]) -> dict[str, Any]:
        row = {
            "request_id": outstanding.request_id, "response_id": response["response_id"],
            "failure_slot": outstanding.failure_slot,
            "request_type": outstanding.request.request_type.value,
            "responder": response["responder"],
            "token_usage": response.get("token_usage") or {},
            "cached": response.get("cached"),
            "request_mode": "blocking" if outstanding.blocking_reason else "async",
            "blocking_reason": outstanding.blocking_reason,
            "waiting_activities": outstanding.activity_count,
            "explanation": outstanding.request.explanation,
        }
        if response["responder"] == "advisor_proxy":
            row["advice"] = parse_advisor_advice(
                response["content"], request_type=outstanding.request.request_type.value).artifact()
        return row

    def apply_answer(outstanding) -> str | None:
        """Adopt an answered request. Returns a stop reason when the run must end."""
        nonlocal attention_input, pending_execution_link
        response = account(outstanding)
        responder = response["responder"]
        row = request_row(outstanding, response)
        requests.append(row)
        request_type = outstanding.request.request_type
        if request_type is RequestType.HINT:
            guidance = row["advice"]["guidance"] if "advice" in row else response["content"]
            compilation = None
            if responder == "human":
                from .hint_compilation import prepare_human_guidance
                compilation = prepare_human_guidance(
                    guidance, suite=suite, task_id=task_id,
                    robot=(robot_configuration or {}).get("robot"),
                    guidance_scope=response.get("guidance_scope"))
                guidance = compilation["compiled"]
                row["guidance_compilation"] = compilation
            attention_input["advisor_guidance"] = guidance
            if response.get("guidance_scope"):
                attention_input["guidance_scope"] = response["guidance_scope"]
            control_store.record_response_use(
                outstanding.request_id, response_id=response["response_id"],
                use="guidance", guidance_compilation=compilation)
            pending_execution_link = outstanding.request_id
            return None
        if request_type is RequestType.INTERRUPT:
            control_store.record_response_use(
                outstanding.request_id, response_id=response["response_id"], use="interrupt")
            return "independent_safety_interrupt"
        approved = (response["content"] == "approve" if responder == "human"
                    else approval_granted(row) if approval_granted else False)
        control_store.record_response_use(
            outstanding.request_id, response_id=response["response_id"],
            use="approval_granted" if approved else "approval_denied")
        if approved is not True:
            return "approval_not_granted"
        attention_input["approval_granted"] = True
        pending_execution_link = outstanding.request_id
        return None

    def advisor_failure(outstanding, exc: BaseException) -> str:
        rejected_path = run_dir / "advisor_rejections" / f"{outstanding.failure_slot}.json"
        if rejected_path.is_file():
            rejected = json.loads(rejected_path.read_bytes())
            requests.append({"request_id": outstanding.request_id,
                             "status": "invalid_provider_usage",
                             "failure_slot": outstanding.failure_slot,
                             "provider_total_tokens_observed": rejected["provider_total_tokens_observed"],
                             "cached": rejected["cached"], "artifact_path": str(rejected_path),
                             "artifact_sha256": hashlib.sha256(rejected_path.read_bytes()).hexdigest()})
        flow.cancel()
        return f"advisor_error:{type(exc).__name__}: {exc}"

    def on_tick(outstanding) -> str | None:
        if outstanding.awaits_human:
            sync_policy('human_wait', f'human-wait:{outstanding.request_id}')
        return "emergency_interrupt" if interrupted() else None

    def settle(*, block: bool) -> tuple[bool, str | None]:
        """Collect the outstanding request. Returns (finished, stop_reason)."""
        outstanding = flow.outstanding
        status = flow.status(block=block, on_tick=on_tick)
        if status == WAITING:
            if outstanding.awaits_human:
                sync_policy('human_wait', f'human-wait:{outstanding.request_id}')
            return False, None
        flow.end_window()
        try:
            if status == ANSWERED:
                stop = apply_answer(outstanding)
            elif status == CANCELLED:
                stop = "human_request_cancelled"
            elif status == ABORTED:
                flow.cancel()
                stop = outstanding.abort_reason
            else:
                stop = advisor_failure(outstanding, outstanding.error)
        except Exception as exc:
            stop = advisor_failure(outstanding, exc)
        flow.release()
        return True, stop

    def record_interrupt_escalation(decision, index, link, trace, trace_id) -> None:
        """A safety stop is raised as a CRITICAL inbox item; it never waits and costs no credit."""
        explanation = build_request_explanation(
            decision, trace=trace, trace_id=trace_id, blocking_reason=None)
        explanation["while_waiting"] = "The robot was stopped before this escalation was raised."
        request = AttentionRequestRecord(
            request_id=f"attention-request:{run_dir.name}:{index}:interrupt",
            run_id=link["run_id"], attempt_id=link["attempt_id"], trace_id=trace_id,
            request_type=RequestType.INTERRUPT, reason=decision.reason,
            priority=decision.priority, created_at=clock(), deadline_at=None,
            mode=assistance_mode, explanation=explanation)
        runtime = AttentionRuntime(control_store, None, clock=clock)
        runtime.open_request(request, reserve_credit=False)
        answered = runtime.close_with_system_response(
            request.request_id, responder="safety_policy",
            content="interrupt_applied: the robot was stopped; no further attempt will run")
        control_store.record_response_use(
            request.request_id, response_id=answered.response_id, use="interrupt")
        requests.append({
            "request_id": request.request_id, "response_id": answered.response_id,
            "failure_slot": index, "request_type": "interrupt", "responder": "safety_policy",
            "token_usage": {}, "cached": False, "credit_exempt": True,
            "request_mode": "blocking", "blocking_reason": "interrupt_applied_before_request",
            "waiting_activities": 0, "explanation": explanation})

    def finish_outstanding() -> None:
        """No attempt remains: close the open request without leaving credit or tokens unaccounted."""
        outstanding = flow.outstanding
        if outstanding is None:
            return
        current = control_store.get_request(outstanding.request_id)
        if current.state in {RequestState.PENDING, RequestState.FALLBACK} and outstanding.future is None:
            flow.cancel()
        else:
            flow.drain()
            current = control_store.get_request(outstanding.request_id)
            if current.state is RequestState.ANSWERED:
                response = account(outstanding)
                row = request_row(outstanding, response)
                succeeded = bool(attempts and attempts[-1]["native_success"] and stopped_reason is None)
                reason = ("native_success_first" if succeeded
                          else "attempts_exhausted" if stopped_reason == "max_attempts"
                          else "stopped_before_use")
                control_store.record_response_ignored(
                    outstanding.request_id, reason=reason, ignored_at=clock())
                row["disposition"] = "ignored"
                row["ignored_reason"] = reason
                requests.append(row)
            else:
                flow.cancel()
        flow.end_window()
        flow.release()

    for index in range(max_attempts):
        sync_policy('attention_decision', f'before-attempt:{index}')
        if wall_expired():
            stopped_reason = "whole_case_deadline"
            break
        if interrupted():
            stopped_reason = "emergency_interrupt"
            break
        remaining_time = control_store.resource_status(unified_run_id)["execution_seconds"]["remaining"]
        if remaining_time < attempt_budget_seconds:
            control_store.record_work_block(
                unified_run_id, work_id=f"trial:{unified_run_id}:{index}",
                resource="execution_seconds", required=attempt_budget_seconds, remaining=remaining_time,
                event_key=f"work-block:{unified_run_id}:{index}",
            )
            stopped_reason = "resource_blocked"
            break
        gpu_budget = control_store.resource_status(unified_run_id)["gpu_seconds"]
        if gpu_budget["budgeted"] and gpu_budget["remaining"] <= 0:
            typical = (max(item["gpu_seconds"] for item in gpu_usage if item["gpu_seconds"])
                       if any(item["gpu_seconds"] for item in gpu_usage) else 1.0)
            control_store.record_work_block(
                unified_run_id, work_id=f"trial:{unified_run_id}:{index}",
                resource="gpu_seconds", required=typical, remaining=0.0,
                event_key=f"work-block-gpu:{unified_run_id}:{index}",
            )
            stopped_reason = "resource_blocked"
            break
        waiting_on = flow.outstanding
        if waiting_on is not None and waiting_on.activity_count > 0:
            # An attempt already ran while this request was open: collect the reply now.
            _, stop = settle(block=False)
            if stop is not None:
                stopped_reason = stop
                break
            waiting_on = flow.outstanding
        if waiting_on is not None:
            activity = ("memory_retrieval" if attention_input.get("memory_ids_to_use")
                        else "trace_inspection" if attention_input.get("inspected_trace")
                        else "autonomous_retry")
            control_store.record_waiting_activity(
                waiting_on.request_id, sequence=waiting_on.activity_count, activity=activity,
                attempt_index=index, robot_motion=True,
                detail={"request_type": waiting_on.request.request_type.value})
            waiting_on.activity_count += 1
        control_store.record_executor_dispatch(unified_run_id, index=index, started_at=clock())
        # Hold the attempt's execution time while it runs and meter the GPU it keeps busy.
        attempt_hold = f"exec:{unified_run_id}:{index}"
        control_store.reserve_resource(unified_run_id, resource="execution_seconds",
                                       amount=attempt_budget_seconds, reservation_id=attempt_hold)
        meter = GpuMeter(gpu_sampler, interval=gpu_sample_interval).start()
        try:
            result = attempt_executor(
                artifact_root=run_dir / "attempts", store_path=store_path,
                attention_input=public_attention_input(attention_input),
                assistance_credits=assistance_credits - credits_used,
                token_limit=token_limit - tokens_used,
                assistance_mode=assistance_mode,
                memory_gateway=memory_gateway,
                attention_run_id=unified_run_id,
                attention_attempt_index=index,
                attention_finalize_run=False,
                attention_run_budget_seconds=(total_execution_seconds
                                             if total_execution_seconds is not None
                                             else attempt_budget_seconds * max_attempts),
                interrupt_check=lambda: interrupted() or wall_expired(),
            )
        except Exception as exc:
            control_store.record_executor_completed(unified_run_id, index=index, ended_at=clock(), sdk_motion_commands=None)
            reason = f"{type(exc).__name__}: {exc}"[:300]
            control_store.record_work_failure(
                unified_run_id, work_id=f"trial:{unified_run_id}:{index}",
                reason=reason, event_key=f"work-failed:{unified_run_id}:{index}",
            )
            stopped_reason = "attempt_executor_failed"
            if runner_boundary_mode == "formal":
                flow.cancel()
                control_store.transition_run(unified_run_id, RunStatus.FAILED,
                                             event_key=f"finish:{unified_run_id}")
                raise RuntimeError(f"formal attempt rejected: {reason}") from exc
            break
        finally:
            control_store.release_resource(attempt_hold)
            gpu = meter.stop()
            gpu_usage.append({"attempt_index": index, **gpu})
            if gpu["gpu_seconds"]:
                control_store.consume_resources(
                    unified_run_id, gpu_seconds=float(gpu["gpu_seconds"]),
                    event_key=f"gpu:{unified_run_id}:{index}")
        if result.get("task_id") != task_id or result.get("seed") != seed or result.get("perception_mode") != "sim_gt":
            raise ValueError("attempt executor returned mismatched task/seed/perception")
        if result.get("execution_target") != f"{suite}_sim":
            raise ValueError("attempt executor returned mismatched suite")
        if runner_boundary_mode == "formal" and (
            result.get("formal_runner_result", {}).get("boundary_checked") is not True
            or result.get("attention_trace", {}).get("run_id") != unified_run_id
            or result.get("attention_trace", {}).get("attempt_id")
                != f"attempt:{run_dir.name}:{index}"
        ):
            flow.cancel()
            control_store.transition_run(unified_run_id, RunStatus.FAILED,
                                         event_key=f"finish:{unified_run_id}")
            raise RuntimeError("formal attempt evidence identity mismatch")
        raw_link = result.get('attention_trace', {}).get('raw_trace_id')
        raw_audit = control_store.get_raw_trace(raw_link) if raw_link else None
        motion_count = None
        if raw_audit and raw_audit.get('run_id') == unified_run_id:
            from .ui_observability import MOTION_EVENT_TYPES
            motion_count = sum(event.get('event_type') in MOTION_EVENT_TYPES for event in raw_audit.get('events', []))
        control_store.record_executor_completed(unified_run_id, index=index, ended_at=clock(), sdk_motion_commands=motion_count)
        attempts.append(result)
        if pending_execution_link is not None:
            control_store.link_response_execution(
                pending_execution_link,
                execution_id=result["attention_trace"]["execution_id"],
            )
            linked_request, pending_execution_link = pending_execution_link, None
            if any(row.get("request_id") == linked_request and row.get("request_type") == "hint"
                   for row in requests):
                # The guided execution has finished: only now is there an outcome to record.
                note_ingestion(ingestion.ingest(linked_request))
        signals = safety_signals(result) if safety_signals else {}
        if not isinstance(signals, dict) or set(signals) - {"unsafe", "approval_required"} or any(
            not isinstance(v, bool) for v in signals.values()
        ):
            raise ValueError("safety signals must be independent boolean flags")
        if result.get("safety_unsafe") is True:
            signals["unsafe"] = True
        if result.get("approval_required") is True:
            signals["approval_required"] = True
        monitored_attempts.append(signals)
        if signals.get("unsafe"):
            decisions.append({
                "after_attempt": index, "action": DecisionAction.STOP.value,
                "reason": "independent_safety_stop", "request_type": None,
                "safety_artifact": result.get("safety_artifact"),
                "monitor_signals": dict(signals),
            })
            stopped_reason = "independent_safety_stop"
            break
        if wall_expired():
            stopped_reason = "whole_case_deadline"
            break
        if runner_boundary_mode == "formal" and (
            result.get("status") != "completed"
            or result.get("formal_runner_result", {}).get("native_evaluator", {}).get("evaluated") is not True
        ):
            stopped_reason = "formal_attempt_incomplete_or_evaluator_unavailable"
            break
        if result["native_success"] and (signals.get("unsafe") or signals.get("approval_required")):
            stopped_reason = "successful_attempt_failed_independent_safety_gate"
            break
        if result["native_success"]:
            break
        if result.get("sdk_budget_exhausted"):
            stopped_reason = "sdk_call_budget_exhausted"
            break
        if result.get("status") in {"timeout", "cancelled"}:
            stopped_reason = ("emergency_interrupt" if interrupted()
                              else f"attempt_{result['status']}")
            break
        if index + 1 >= max_attempts:
            if signals.get("unsafe"):
                stopped_reason = "unsafe_final_attempt"
            elif signals.get("approval_required"):
                stopped_reason = "approval_required_final_attempt"
            else:
                stopped_reason = "max_attempts"
            break
        link = result["attention_trace"]
        store = AttentionStore(store_path)
        trace_id = link.get("advisor_trace_id")
        trace = store.get_trace(trace_id) if trace_id else None
        if trace is None:
            stopped_reason = "no_advisor_safe_trace"
            break
        if (signals.get("approval_required") and flow.outstanding is None
                and assistance_credits - credits_used <= 0):
            stopped_reason = "approval_required_no_budget"
            break
        assessment = None if random_plan or not trace_evidence else assess_trace(trace, previous_traces)
        if assessment is not None:
            previous_traces.append(trace)
        trusted_memory_ids = []
        already_used_memory_ids = {
            memory_id for prior_attempt in attempts
            for memory_id in prior_attempt.get("memory_ids", [])
        }
        for memory_id in ([] if random_plan or not memory_enabled else result.get("available_memory_ids", [])):
            if memory_id in already_used_memory_ids:
                continue
            memory = memory_gateway.get_memory(memory_id)
            expected_scope = (memory_applicability_context or
                              {"suite": suite, "task_id": task_id, "perception_mode": "sim_gt"})
            # The Service verifies the concrete scene/object/camera/prompt
            # against the frozen plan. Candidate applicability records the
            # stable suite/task/mode identity, not those per-seed fields.
            if (getattr(memory.status, "value", memory.status) == MemoryStatus.TRUSTED.value and
                all(memory.applicability.get(key) == expected_scope.get(key)
                    for key in ("suite", "task_id", "perception_mode"))):
                trusted_memory_ids.append(memory_id)
        sync_policy('attention_decision', f'after-attempt:{index}')

        def policy_context(*, pending: int) -> PolicyContext:
            return PolicyContext(
                run_id=link["run_id"], attempt_index=index,
                failure_index=index, consecutive_failures=index + 1,
                assistance_remaining=assistance_credits - credits_used - pending,
                evidence_count=len(trace.get("evidence", [])),
                has_hypothesis=bool(trace.get("hypothesis")),
                matching_memory_count=len(trusted_memory_ids),
                unsafe=signals.get("unsafe", False),
                approval_required=signals.get("approval_required", False),
                assessment=assessment, pending_requests=pending, trace_evidence=trace_evidence,
            )

        decision = policy.decide(policy_context(pending=(1 if flow.outstanding is not None else 0)))
        decisions.append({
            "after_attempt": index, "action": decision.action.value,
            "effective_attention_policy": policy.policy_id,
            "attention_generation": controller.snapshot(unified_run_id)['generation'] if attention_control_binding else 0,
            "reason": decision.reason,
            "request_type": decision.request_type.value if decision.request_type else None,
            "priority": decision.priority.value if decision.priority else None,
            "decision_source": ("independent_monitor" if signals.get("approval_required")
                                else "projected_public_trace" if assessment else "fixed_policy_rule"),
            "event_ids": list(decision.event_ids),
            "evidence_ids": list(decision.evidence_ids),
            "prior_trace_ids": list(decision.prior_trace_ids),
            "prior_event_ids": list(decision.prior_event_ids),
            "assessment": assessment.artifact() if assessment else None,
            "trusted_memory_ids": trusted_memory_ids if decision.action is DecisionAction.RETRIEVE_MEMORY else [],
            "safety_artifact": result.get("safety_artifact") if signals else None,
            "monitor_signals": dict(signals),
            "waiting_for_request": flow.outstanding.request_id if flow.outstanding else None,
        })
        if decision.action is DecisionAction.STOP:
            stopped_reason = decision.reason
            if decision.request_type is RequestType.INTERRUPT:
                record_interrupt_escalation(decision, index, link, trace, trace_id)
            break
        if signals.get("approval_required") and decision.request_type is not RequestType.APPROVAL:
            stopped_reason = "approval_required_policy_cannot_proceed"
            break
        attention_input = input_for_action(
            decision.action, public_demo=public_demo,
            trusted_memory_ids=trusted_memory_ids, trace=trace, assessment=assessment)
        if decision.action is DecisionAction.REQUEST:
            if control_store.resource_status(unified_run_id)["tokens"]["remaining"] <= 0:
                stopped_reason = "advisor_token_budget_exhausted"
                break
            blocking_reason = flow.blocking_reason(
                decision, attempts_after=max_attempts - index - 1)
            request = AttentionRequestRecord(
                request_id=f"attention-request:{run_dir.name}:{index}",
                run_id=link["run_id"], attempt_id=link["attempt_id"], trace_id=trace_id,
                request_type=decision.request_type, reason=decision.reason,
                priority=decision.priority, created_at=clock(),
                deadline_at=(clock() + human_deadline_seconds
                             if assistance_mode is AssistanceMode.LIVE_HUMAN_FIRST else None),
                mode=assistance_mode,
                explanation=build_request_explanation(
                    decision, trace=trace, trace_id=trace_id, blocking_reason=blocking_reason,
                    withheld=result.get("approval_requests")),
            )
            provider_timeout = None
            if strict_admission_budget:
                provider_timeout = min(90.0, whole_case_deadline_monotonic - monotonic() - 10.0
                                       if whole_case_deadline_monotonic is not None else 90.0)
                if provider_timeout <= 0:
                    stopped_reason = "whole_case_deadline"
                    break
            runtime = AttentionRuntime(control_store, SimGTAdvisorProxy(
                control_store, transport=advisor_transport or (replay_transport if advisor_replay_only else default_glm_transport(
                    strict_usage=strict_admission_budget, response_log_dir=run_dir / "provider_replies",
                    token_budget_limit=token_limit - tokens_used)),
                cache_path=advisor_cache_path, replay_only=advisor_replay_only,
                strict_token_budget_remaining=(token_limit - tokens_used
                                               if strict_admission_budget else None),
                provider_timeout_seconds=provider_timeout,
                usage_rejection_path=run_dir / "advisor_rejections" / f"{index}.json",
                latency_seconds=MODE_SPECS[AssistanceMode.BENCHMARK_PROXY].proxy_latency_seconds,
                sleeper=sleeper,
            ), clock=clock, trace_evidence=trace_evidence, memory_enabled=memory_enabled)
            outstanding = flow.open(
                request, decision, runtime, failure_slot=index, trace_id=trace_id,
                link=link, blocking_reason=blocking_reason,
                token_reservation=min(
                    SIM_GT_MAX_TOKENS,
                    int(control_store.resource_status(unified_run_id)["tokens"]["remaining"])))
            if blocking_reason is not None:
                if decision.request_type is RequestType.APPROVAL:
                    # The gated action stays withheld; only read-only work may run.
                    control_store.record_waiting_activity(
                        request.request_id, sequence=outstanding.activity_count,
                        activity="approval_gate_hold", attempt_index=index, robot_motion=False,
                        detail={"withheld_rules": [item.get("rule") for item in
                                                   result.get("approval_requests") or []]})
                    outstanding.activity_count += 1
                if assistance_mode is AssistanceMode.LIVE_HUMAN_FIRST:
                    # A bounded, read-only job is the only work eligible during
                    # a blocking human wait. It consumes Advisor-visible evidence
                    # only; no simulator or robot action is dispatched.
                    control_store.record_waiting_work(
                        request.request_id, work_type="visible_evidence_index",
                        evidence=[{"sha256": item["sha256"], "kind": item["kind"]}
                                  for item in trace.get("evidence", [])[:8]],
                    )
                _, stop = settle(block=True)
                if stop is not None:
                    stopped_reason = stop
                    break
            else:
                # Asynchronous: pick the safe work to do while the reply is pending.
                sync_policy('attention_decision', f'while-waiting:{index}')
                waiting = policy.decide(policy_context(pending=1))
                decisions.append({
                    "after_attempt": index, "action": waiting.action.value,
                    "effective_attention_policy": policy.policy_id,
                    "attention_generation": decisions[-1]["attention_generation"],
                    "reason": waiting.reason, "request_type": None, "priority": None,
                    "decision_source": decisions[-1]["decision_source"],
                    "event_ids": list(waiting.event_ids), "evidence_ids": list(waiting.evidence_ids),
                    "prior_trace_ids": list(waiting.prior_trace_ids),
                    "prior_event_ids": list(waiting.prior_event_ids),
                    "assessment": decisions[-1]["assessment"],
                    "trusted_memory_ids": (trusted_memory_ids
                                           if waiting.action is DecisionAction.RETRIEVE_MEMORY else []),
                    "safety_artifact": None, "monitor_signals": dict(signals),
                    "waiting_for_request": request.request_id,
                })
                if waiting.action is DecisionAction.STOP:
                    stopped_reason = waiting.reason
                    break
                if waiting.action not in {DecisionAction.RETRY, DecisionAction.CONTINUE,
                                          DecisionAction.INSPECT_TRACE,
                                          DecisionAction.RETRIEVE_MEMORY}:
                    stopped_reason = f"unsupported_decision:{waiting.action.value}"
                    break
                attention_input = input_for_action(
                    waiting.action, public_demo=public_demo,
                    trusted_memory_ids=trusted_memory_ids, trace=trace, assessment=assessment)
        elif decision.action not in {DecisionAction.RETRY, DecisionAction.CONTINUE,
                                     DecisionAction.RETRIEVE_MEMORY, DecisionAction.INSPECT_TRACE}:
            stopped_reason = f"unsupported_decision:{decision.action.value}"
            break
    finish_outstanding()
    flow.close()
    for outcome in ingestion.sweep(unified_run_id):
        note_ingestion(outcome)
    if interrupted():
        stopped_reason = "emergency_interrupt"
    if stopped_reason == "emergency_interrupt":
        stop, receipt_sha, attempt_id = None, None, None
        if attempts and runner_boundary_mode == "formal":
            last = attempts[-1]
            attempt_id = (last.get("attention_trace") or {}).get("attempt_id")
            ref = ((last.get("formal_runner_result") or {}).get("artifacts") or {}).get("sandbox_receipt") or {}
            if isinstance(ref.get("uri"), str):
                receipt_bytes = Path(ref["uri"]).read_bytes()
                receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
                if receipt_sha != ref.get("sha256"):
                    raise ValueError("interrupt Service receipt digest mismatch")
                receipt = json.loads(receipt_bytes)
                if receipt.get("run_id") != unified_run_id or receipt.get("attempt_id") != attempt_id:
                    raise ValueError("interrupt Service receipt identity mismatch")
                stop = receipt.get("service_stop")
        if runner_boundary_mode == "formal" and attempts:
            last_formal = attempts[-1].get("formal_runner_result") or {}
            expected_stop_reason = "operator_cancel"
            if (isinstance(stop, dict)
                    and stop.get("mechanism") == "authenticated_v2_native_process_group"):
                operator_action = (control_store.interrupt_status(unified_run_id) or {}).get("operator_action", "stop")
                expected_stop_reason = "operator_stop" if operator_action == "stop" else "operator_cancel"
            if (attempts[-1].get("status") != "cancelled"
                    or last_formal.get("status") != "cancelled"):
                ack_state = "too_late"
                stopped_reason = "interrupt_too_late"
            elif stop is None or stop.get("reason") != expected_stop_reason:
                ack_state = "stop_unconfirmed"
            else:
                ack_state = "stopped" if service_stop_confirmed(stop, suite) else "stop_unconfirmed"
        else:
            ack_state = "stopped" if runner_boundary_mode != "formal" or not attempts else "stop_unconfirmed"
        confirmed = time.time()
        control_store.acknowledge_interrupt(
            unified_run_id,
            state=ack_state,
            event_key=f"stop:{unified_run_id}", confirmed_at=confirmed,
            attempt_id=attempt_id, service_stop=stop,
            service_receipt_sha256=receipt_sha,
        )
    if attention_control_binding is not None:
        controller.boundary(unified_run_id,boundary_id='run-ended',kind='run_ended',now=clock())
    control_store.transition_run(
        unified_run_id,
        RunStatus.CANCELLED if stopped_reason == "emergency_interrupt"
        else RunStatus.COMPLETED if attempts and attempts[-1]["native_success"] and stopped_reason is None
        else RunStatus.FAILED,
        event_key=f"finish:{unified_run_id}",
    )
    summary = {
        "schema_version": "attentionbench.sim-gt-attention-run.v1",
        "attention_control": controller.snapshot(unified_run_id) if attention_control_binding else None,
        "suite": suite, "task_id": task_id, "seed": seed, "policy_id": policy_id,
        "assistance_mode": assistance_mode.value,
        "policy_config": config, "perception_mode": "sim_gt",
        "demo_receipt": demo_receipt,
        "random_preregistration": random_plan,
        "runner_boundary": {
            "mode": runner_boundary_mode,
            "generated_code_sandboxed": runner_boundary_mode in {"generated_sandbox", "formal"},
            "overall_deadline_certified": False,
            "formal_runner_registered": runner_boundary_mode == "formal",
        },
        "formal_blockers": ["development runner has not passed formal acceptance"],
        "formal_eligible": False,
        "native_success": bool(attempts and attempts[-1]["native_success"] and stopped_reason is None),
        "attempts": [{
            "artifact_dir": r["artifact_dir"], "attention_trace": r["attention_trace"],
            "native_success": r["native_success"], "status": r["status"],
            "safety_artifact": r.get("safety_artifact"),
            **({"formal_runner_result": r["formal_runner_result"],
                "memory_ids": r.get("memory_ids", [])}
               if runner_boundary_mode == "formal" else {}),
        } for r in attempts],
        "decisions": decisions, "requests": requests,
        "safety_signals": monitored_attempts,
        "advisor_replay_only": advisor_replay_only,
        "memory_enabled": memory_enabled, "trace_evidence": trace_evidence,
        "request_mode": request_mode,
        "gpu_usage": gpu_usage,
        "approval_rules": dict(approval_rules or {}),
        "request_dispositions": {row["request_id"]: control_store.request_disposition(row["request_id"])
                                 for row in requests if "request_id" in row and row.get("status") is None},
        "resource_usage": {"assistance_credits": credits_used, "tokens": tokens_used},
        "stopped_reason": stopped_reason,
        "store": str(store_path.resolve()), "artifact_dir": str(run_dir.resolve()),
        "advisor_cache": {"path": str(advisor_cache_path.resolve()),
                          "initial_state": "empty_per_run" if fresh_advisor_cache else "shared"},
    }
    if random_plan is not None:
        realized = [row["failure_slot"] for row in requests]
        summary["random_matching"] = {
            "target_request_count": random_plan["target_request_count"],
            "selected_slots": random_plan["selected_slots"],
            "realized_slots": realized, "realized_request_count": len(realized),
            "deviation": random_plan["target_request_count"] - len(realized),
            "deviation_reason": ("early_native_success" if summary["native_success"] and
                                 len(realized) < random_plan["target_request_count"] else
                                 "attempt_or_resource_ended" if len(realized) < random_plan["target_request_count"]
                                 else None),
        }
    (run_dir / "attention_run.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def replay_transport(request):
    raise RuntimeError("advisor_cache_miss")


def default_glm_transport(*, strict_usage: bool = False, response_log_dir: Path | None = None,
                          token_budget_limit: int = 4096) -> AdvisorTransport:
    """Explicit opt-in PARCC GLM transport; tests inject a deterministic fake."""
    if strict_usage:
        from .parcc_client import ParccClient
        return SimGTGLMAdvisorTransport(ParccClient(timeout_seconds=90, max_attempts=1),
            format_attempts=1, strict_usage=True, response_log_dir=response_log_dir,
            token_budget_limit=token_budget_limit)
    return SimGTGLMAdvisorTransport()


def run_robocasa_attention(
    *, task_id: str, seed: int, artifact_root: Path, policy_id: str,
    robot_policy: Callable[..., None], backend_factory: Callable[[], Any],
    client_factory: Callable[[str], Any], hypothesis: str = "",
    runtime_variation: dict[str, Any] | None = None,
    replay_camera_ws: str | None = None,
    approval_rules: dict[str, Any] | None = None, **kwargs: Any,
) -> dict[str, Any]:
    """RoboCasa binding for the shared seven-policy scheduler."""
    from .robocasa_native.safety_monitor import SafetyMonitorBackend
    from .robocasa_native.sim_gt_runner import run_robocasa_sim_gt_episode

    def execute(**attempt: Any) -> dict[str, Any]:
        backend = backend_factory()
        if callable(getattr(backend, "set_interrupt_check", None)):
            backend.set_interrupt_check(attempt["interrupt_check"])
        monitor = SafetyMonitorBackend(
            backend, interrupt_check=attempt["interrupt_check"], approval_rules=approval_rules,
            approval_granted=attempt["attention_input"].get("approval_granted") is True)
        result = run_robocasa_sim_gt_episode(
            task_id=task_id, seed=seed, policy=robot_policy, policy_id=policy_id,
            perception_mode="sim_gt", action_backend=monitor,
            client=client_factory(task_id), hypothesis=hypothesis,
            retrieve_memory=kwargs.get("memory_enabled", True) and policy_id != "trace_aware_hint_only",
            runtime_variation=runtime_variation, replay_camera_ws=replay_camera_ws,
            **attempt,
        )
        result["safety_artifact"] = str(monitor.write_artifact(
            Path(result["artifact_dir"]) / "safety_monitor.json",
            attempt_id=result["attention_trace"]["attempt_id"],
        ))
        result["safety_unsafe"] = bool(monitor.violations)
        result["approval_required"] = bool(monitor.approvals_required)
        result["approval_requests"] = list(monitor.approvals_required)
        return result

    return run_sim_gt_attention(
        suite="robocasa", task_id=task_id, seed=seed,
        artifact_root=artifact_root, policy_id=policy_id,
        attempt_executor=execute, approval_rules=approval_rules, **kwargs,
    )


def run_robosuite_attention(
    *, task_id: str, seed: int, artifact_root: Path, policy_id: str,
    robot_policy: Callable[..., None], adapter_factory: Callable[[str, str], Any],
    camera_name: str = "agentview", hypothesis: str = "",
    runtime_variation: dict[str, Any] | None = None,
    record_replay: bool = False,
    approval_rules: dict[str, Any] | None = None, **kwargs: Any,
) -> dict[str, Any]:
    """Robosuite binding for the same scheduler and decision semantics."""
    from .robocasa_native.safety_monitor import SafetyMonitorBackend
    from .robosuite_memory.sim_gt_runner import run_robosuite_sim_gt_episode

    def execute(**attempt: Any) -> dict[str, Any]:
        adapter = adapter_factory(task_id, camera_name)
        monitor = SafetyMonitorBackend(
            adapter, interrupt_check=attempt["interrupt_check"], approval_rules=approval_rules,
            approval_granted=attempt["attention_input"].get("approval_granted") is True)
        try:
            result = run_robosuite_sim_gt_episode(
                task_id=task_id, seed=seed, policy=robot_policy,
                policy_id=policy_id, perception_mode="sim_gt", adapter=adapter,
                action_backend=monitor,
                hypothesis=hypothesis, runtime_variation=runtime_variation,
                retrieve_memory=kwargs.get("memory_enabled", True) and policy_id != "trace_aware_hint_only",
                replay_camera_url=adapter.service_url if record_replay else None,
                **attempt,
            )
            result["safety_artifact"] = str(monitor.write_artifact(
                Path(result["artifact_dir"]) / "safety_monitor.json",
                attempt_id=result["attention_trace"]["attempt_id"],
            ))
            result["safety_unsafe"] = bool(monitor.violations)
            result["approval_required"] = bool(monitor.approvals_required)
            result["approval_requests"] = list(monitor.approvals_required)
            return result
        finally:
            adapter.close()

    return run_sim_gt_attention(
        suite="robosuite", task_id=task_id, seed=seed,
        artifact_root=artifact_root, policy_id=policy_id,
        attempt_executor=execute, approval_rules=approval_rules, **kwargs,
    )


def run_robosuite_formal_attempt(request, *, service_source_root: Path,
                                 cancel_event=None, policy_start_hook=None) -> dict[str, Any]:
    """Independent formal attempt entry; the seven-policy dev path is unchanged.

    A scored multi-attempt scheduler is deliberately not inferred from a
    single attempt. The boundary keeps formal_eligible false until the
    separately frozen acceptance matrix exists.
    """
    from .formal_runner_boundary import run_with_formal_boundary
    from .robosuite_memory.formal_runner import RobosuiteFormalSuiteRunner

    return run_with_formal_boundary(
        request,
        runner=RobosuiteFormalSuiteRunner(
            service_source_root=service_source_root,
            cancel_event=cancel_event,
            policy_start_hook=policy_start_hook,
        ),
    )


def run_robocasa_formal_attempt(request, *, sim_source_root: Path,
                                agent_source_root: Path, task_source_root: Path,
                                sim_python: Path,
                                agent_python: Path, cancel_event=None,
                                policy_start_hook=None) -> dict[str, Any]:
    """Run a RoboCasa formal-path attempt through the shared evidence boundary."""
    from .formal_runner_boundary import run_with_formal_boundary
    from .robocasa_native.formal_runner import RobocasaFormalSuiteRunner

    return run_with_formal_boundary(
        request,
        runner=RobocasaFormalSuiteRunner(
            sim_source_root=sim_source_root, agent_source_root=agent_source_root,
            task_source_root=task_source_root,
            sim_python=sim_python, agent_python=agent_python,
            cancel_event=cancel_event, policy_start_hook=policy_start_hook,
        ),
    )
