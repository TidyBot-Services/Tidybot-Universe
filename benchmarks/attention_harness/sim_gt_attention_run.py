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
from .core.advisor import AdvisorTransport
from .core.assessment import assess_trace
from .core.models import AssistanceBudget, AttentionRequestRecord, MemoryStatus, RequestType, RunRecord, RunStatus
from .core.policies import DecisionAction, PolicyContext, build_policy
from .demo_prior import verify_demo_prior
from .core.runtime import AttentionRuntime
from .core.store import AttentionStore
from .memory_agent import MemoryAgent
from .parcc_advisor import parse_advisor_advice
from .policy_input import public_attention_input
from .seed_guard import validate_seed
from .service_recovery import service_stop_confirmed
from .v2_advisor import SimGTAdvisorProxy, SimGTGLMAdvisorTransport


AttemptExecutor = Callable[..., dict[str, Any]]
SafetySignals = Callable[[dict[str, Any]], dict[str, bool]]


def run_sim_gt_attention(
    *, suite: str, task_id: str, seed: int, artifact_root: Path,
    policy_id: str, attempt_executor: AttemptExecutor, max_attempts: int = 3,
    assistance_credits: int = 1, token_limit: int = 30_000,
    store_path: Path | None = None,
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
    if suite not in {"robocasa", "robosuite"}:
        raise ValueError("unsupported sim_gt suite")
    if runner_boundary_mode not in {"trusted_dev", "generated_sandbox", "formal"}:
        raise ValueError("unsupported runner boundary")
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
                                token_limit=token_limit,
                                execution_seconds=(float(total_execution_seconds)
                                                   if total_execution_seconds is not None
                                                   else float(attempt_budget_seconds) * max_attempts)),
        created_at=clock(),
    ))
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
    for index in range(max_attempts):
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
            reason = f"{type(exc).__name__}: {exc}"[:300]
            control_store.record_work_failure(
                unified_run_id, work_id=f"trial:{unified_run_id}:{index}",
                reason=reason, event_key=f"work-failed:{unified_run_id}:{index}",
            )
            stopped_reason = "attempt_executor_failed"
            if runner_boundary_mode == "formal":
                control_store.transition_run(unified_run_id, RunStatus.FAILED,
                                             event_key=f"finish:{unified_run_id}")
                raise RuntimeError(f"formal attempt rejected: {reason}") from exc
            break
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
            control_store.transition_run(unified_run_id, RunStatus.FAILED,
                                         event_key=f"finish:{unified_run_id}")
            raise RuntimeError("formal attempt evidence identity mismatch")
        attempts.append(result)
        if pending_execution_link is not None:
            store = AttentionStore(store_path)
            store.link_response_execution(
                pending_execution_link,
                execution_id=result["attention_trace"]["execution_id"],
            )
            pending_execution_link = None
        signals = safety_signals(result) if safety_signals else {}
        if not isinstance(signals, dict) or set(signals) - {"unsafe", "approval_required"} or any(
            not isinstance(v, bool) for v in signals.values()
        ):
            raise ValueError("safety signals must be independent boolean flags")
        if result.get("safety_unsafe") is True:
            signals["unsafe"] = True
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
        if result.get("status") in {"timeout", "cancelled"}:
            stopped_reason = ("emergency_interrupt" if interrupted()
                              else f"attempt_{result['status']}")
            break
        if index + 1 >= max_attempts:
            if signals.get("unsafe"):
                stopped_reason = "unsafe_final_attempt"
            elif signals.get("approval_required"):
                stopped_reason = "approval_required_final_attempt"
            break
        link = result["attention_trace"]
        store = AttentionStore(store_path)
        trace_id = link.get("advisor_trace_id")
        trace = store.get_trace(trace_id) if trace_id else None
        if trace is None:
            stopped_reason = "no_advisor_safe_trace"
            break
        if signals.get("approval_required") and assistance_credits - credits_used <= 0:
            stopped_reason = "approval_required_no_budget"
            break
        assessment = None if random_plan else assess_trace(trace, previous_traces)
        if assessment is not None:
            previous_traces.append(trace)
        trusted_memory_ids = []
        already_used_memory_ids = {
            memory_id for prior_attempt in attempts
            for memory_id in prior_attempt.get("memory_ids", [])
        }
        for memory_id in ([] if random_plan else result.get("available_memory_ids", [])):
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
        decision = policy.decide(PolicyContext(
            run_id=link["run_id"], attempt_index=index,
            failure_index=index, consecutive_failures=index + 1,
            assistance_remaining=assistance_credits - credits_used,
            evidence_count=len(trace.get("evidence", [])),
            has_hypothesis=bool(trace.get("hypothesis")),
            matching_memory_count=len(trusted_memory_ids),
            unsafe=signals.get("unsafe", False),
            approval_required=signals.get("approval_required", False),
            assessment=assessment,
        ))
        decisions.append({
            "after_attempt": index, "action": decision.action.value,
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
        })
        if decision.action is DecisionAction.STOP:
            stopped_reason = decision.reason
            break
        if signals.get("approval_required") and decision.request_type is not RequestType.APPROVAL:
            stopped_reason = "approval_required_policy_cannot_proceed"
            break
        attention_input = {"demo_prior": public_demo} if public_demo else {}
        if decision.action is DecisionAction.RETRIEVE_MEMORY:
            attention_input["memory_ids_to_use"] = trusted_memory_ids[:8]
        elif decision.action is DecisionAction.INSPECT_TRACE:
            # Only the projected Advisor trace, never evaluator internals.
            attention_input["inspected_trace"] = json.dumps({
                "failure": trace.get("failure"),
                "assessment": assessment.artifact() if assessment else None,
            }, ensure_ascii=False, default=str)[:4000]
        elif decision.action is DecisionAction.REQUEST:
            if control_store.resource_status(unified_run_id)["tokens"]["remaining"] <= 0:
                stopped_reason = "advisor_token_budget_exhausted"
                break
            request = AttentionRequestRecord(
                request_id=f"attention-request:{run_dir.name}:{index}",
                run_id=link["run_id"], attempt_id=link["attempt_id"], trace_id=trace_id,
                request_type=decision.request_type, reason=decision.reason,
                priority=decision.priority, created_at=clock(),
                deadline_at=(clock() + human_deadline_seconds
                             if assistance_mode is AssistanceMode.LIVE_HUMAN_FIRST else None),
                mode=assistance_mode,
            )
            runtime = AttentionRuntime(store, SimGTAdvisorProxy(
                store, transport=advisor_transport or default_glm_transport(),
                cache_path=artifact_root / "advisor_cache.sqlite3",
                latency_seconds=MODE_SPECS[AssistanceMode.BENCHMARK_PROXY].proxy_latency_seconds,
                sleeper=sleeper,
            ), clock=clock)
            runtime.open_request(request)
            try:
                if assistance_mode is AssistanceMode.BENCHMARK_PROXY:
                    answered = runtime.resolve_benchmark_proxy(request.request_id)
                else:
                    # A bounded, read-only job is the only work eligible during
                    # a human wait. It consumes Advisor-visible evidence only;
                    # no simulator or robot action is dispatched.
                    visible_evidence = trace.get("evidence", [])[:8]
                    store.record_waiting_work(
                        request.request_id, work_type="visible_evidence_index",
                        evidence=[{"sha256": item["sha256"], "kind": item["kind"]}
                                  for item in visible_evidence],
                    )
                    monotonic_limit = monotonic() + human_deadline_seconds + 1.0
                    while True:
                        if interrupted():
                            runtime.cancel(request.request_id)
                            stopped_reason = "emergency_interrupt"
                            break
                        current = store.get_request(request.request_id)
                        if current.state is RequestState.ANSWERED:
                            answered = current
                            break
                        if current.state is RequestState.CANCELLED:
                            stopped_reason = "human_request_cancelled"
                            break
                        if monotonic() >= monotonic_limit and clock() < request.deadline_at:
                            runtime.cancel(request.request_id)
                            stopped_reason = "human_deadline_clock_stalled"
                            break
                        if clock() >= request.deadline_at:
                            answered = runtime.handle_deadline(request.request_id)
                            break
                        sleeper(min(0.25, max(0.0, request.deadline_at - clock())))
                    if stopped_reason in {"emergency_interrupt", "human_request_cancelled",
                                          "human_deadline_clock_stalled"}:
                        break
                response = store.get_response(answered.response_id)
                credits_used += 1
                usage = response.get("token_usage") or {}
                tokens_used += int(usage.get("total_tokens") or 0)
                responder = response["responder"]
                advice = (parse_advisor_advice(response["content"], request_type=decision.request_type.value)
                          if responder == "advisor_proxy" else None)
                request_row = {
                    "request_id": request.request_id, "response_id": answered.response_id,
                    "failure_slot": index,
                    "responder": responder, "token_usage": usage,
                    "cached": response.get("cached"),
                }
                if advice is not None:
                    request_row["advice"] = advice.artifact()
                requests.append(request_row)
                if decision.request_type is RequestType.HINT:
                    attention_input["advisor_guidance"] = (
                        advice.guidance if advice is not None else response["content"]
                    )
                    store.record_response_use(request.request_id, response_id=answered.response_id,
                                              use="guidance")
                    pending_execution_link = request.request_id
                    raw = store.get_raw_trace(link["raw_trace_id"])
                    if advice is not None and raw and raw["outcome"].get("evaluator_authoritative") is True:
                        candidate = MemoryAgent(memory_gateway).ingest_answered_hint(
                            request.request_id, memory_id=f"candidate:{link['attempt_id']}",
                        )
                        requests[-1]["candidate_memory_id"] = candidate.memory_id
                elif decision.request_type is RequestType.INTERRUPT:
                    store.record_response_use(request.request_id, response_id=answered.response_id,
                                              use="interrupt")
                    stopped_reason = "independent_safety_interrupt"
                    break
                else:
                    approved = (response["content"] == "approve" if responder == "human"
                                else approval_granted(requests[-1]) if approval_granted else False)
                    store.record_response_use(request.request_id, response_id=answered.response_id,
                                              use="approval_granted" if approved else "approval_denied")
                    if approved is not True:
                        stopped_reason = "approval_not_granted"
                        break
                    attention_input["approval_granted"] = True
                    pending_execution_link = request.request_id
            except Exception as exc:
                current = store.get_request(request.request_id)
                if current is not None and current.state is RequestState.PENDING:
                    runtime.cancel(request.request_id)
                stopped_reason = f"advisor_error:{type(exc).__name__}: {exc}"
                break
        elif decision.action not in {DecisionAction.RETRY, DecisionAction.CONTINUE}:
            stopped_reason = f"unsupported_decision:{decision.action.value}"
            break
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
            if (attempts[-1].get("status") != "cancelled"
                    or last_formal.get("status") != "cancelled"):
                ack_state = "too_late"
                stopped_reason = "interrupt_too_late"
            elif stop is None or stop.get("reason") != "operator_cancel":
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
    control_store.transition_run(
        unified_run_id,
        RunStatus.CANCELLED if stopped_reason == "emergency_interrupt"
        else RunStatus.COMPLETED if attempts and attempts[-1]["native_success"] and stopped_reason is None
        else RunStatus.FAILED,
        event_key=f"finish:{unified_run_id}",
    )
    summary = {
        "schema_version": "attentionbench.sim-gt-attention-run.v1",
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
        "resource_usage": {"assistance_credits": credits_used, "tokens": tokens_used},
        "stopped_reason": stopped_reason,
        "store": str(store_path.resolve()), "artifact_dir": str(run_dir.resolve()),
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


def default_glm_transport() -> AdvisorTransport:
    """Explicit opt-in PARCC GLM transport; tests inject a deterministic fake."""
    return SimGTGLMAdvisorTransport()


def run_robocasa_attention(
    *, task_id: str, seed: int, artifact_root: Path, policy_id: str,
    robot_policy: Callable[..., None], backend_factory: Callable[[], Any],
    client_factory: Callable[[str], Any], hypothesis: str = "",
    runtime_variation: dict[str, Any] | None = None,
    replay_camera_ws: str | None = None, **kwargs: Any,
) -> dict[str, Any]:
    """RoboCasa binding for the shared seven-policy scheduler."""
    from .robocasa_native.safety_monitor import SafetyMonitorBackend
    from .robocasa_native.sim_gt_runner import run_robocasa_sim_gt_episode

    def execute(**attempt: Any) -> dict[str, Any]:
        backend = backend_factory()
        if callable(getattr(backend, "set_interrupt_check", None)):
            backend.set_interrupt_check(attempt["interrupt_check"])
        monitor = SafetyMonitorBackend(backend, interrupt_check=attempt["interrupt_check"])
        result = run_robocasa_sim_gt_episode(
            task_id=task_id, seed=seed, policy=robot_policy, policy_id=policy_id,
            perception_mode="sim_gt", action_backend=monitor,
            client=client_factory(task_id), hypothesis=hypothesis,
            retrieve_memory=policy_id != "trace_aware_hint_only",
            runtime_variation=runtime_variation, replay_camera_ws=replay_camera_ws,
            **attempt,
        )
        result["safety_artifact"] = str(monitor.write_artifact(
            Path(result["artifact_dir"]) / "safety_monitor.json",
            attempt_id=result["attention_trace"]["attempt_id"],
        ))
        result["safety_unsafe"] = bool(monitor.violations)
        return result

    return run_sim_gt_attention(
        suite="robocasa", task_id=task_id, seed=seed,
        artifact_root=artifact_root, policy_id=policy_id,
        attempt_executor=execute, **kwargs,
    )


def run_robosuite_attention(
    *, task_id: str, seed: int, artifact_root: Path, policy_id: str,
    robot_policy: Callable[..., None], adapter_factory: Callable[[str, str], Any],
    camera_name: str = "agentview", hypothesis: str = "",
    runtime_variation: dict[str, Any] | None = None,
    record_replay: bool = False, **kwargs: Any,
) -> dict[str, Any]:
    """Robosuite binding for the same scheduler and decision semantics."""
    from .robocasa_native.safety_monitor import SafetyMonitorBackend
    from .robosuite_memory.sim_gt_runner import run_robosuite_sim_gt_episode

    def execute(**attempt: Any) -> dict[str, Any]:
        adapter = adapter_factory(task_id, camera_name)
        monitor = SafetyMonitorBackend(adapter, interrupt_check=attempt["interrupt_check"])
        try:
            result = run_robosuite_sim_gt_episode(
                task_id=task_id, seed=seed, policy=robot_policy,
                policy_id=policy_id, perception_mode="sim_gt", adapter=adapter,
                action_backend=monitor,
                hypothesis=hypothesis, runtime_variation=runtime_variation,
                retrieve_memory=policy_id != "trace_aware_hint_only",
                replay_camera_url=adapter.service_url if record_replay else None,
                **attempt,
            )
            result["safety_artifact"] = str(monitor.write_artifact(
                Path(result["artifact_dir"]) / "safety_monitor.json",
                attempt_id=result["attention_trace"]["attempt_id"],
            ))
            result["safety_unsafe"] = bool(monitor.violations)
            return result
        finally:
            adapter.close()

    return run_sim_gt_attention(
        suite="robosuite", task_id=task_id, seed=seed,
        artifact_root=artifact_root, policy_id=policy_id,
        attempt_executor=execute, **kwargs,
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
