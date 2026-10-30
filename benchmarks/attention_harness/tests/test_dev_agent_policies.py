"""P1 decision tables, public evidence, and pre-v2 compatibility oracles."""
import builtins
import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from benchmarks.attention_harness import dev_agent_policies as p
from benchmarks.attention_harness.core.assessment import assess_trace
from benchmarks.attention_harness.core.policies import POLICY_IDS, PolicyContext, build_policy
from benchmarks.attention_harness.dev_help import decide

HINT, FULL, RANDOM = p.AGENT_POLICY_IDS[5], p.AGENT_POLICY_IDS[6], p.AGENT_POLICY_IDS[4]


def execution(index=1, *, code="IK7", passed=0, sequence=None, **changes):
    trace = tuple(dict(event_id=f"step{i}", event_type="sdk.call", status="completed") for i in range(passed))
    trace += (dict(event_id="failure", event_type="sdk.call", operation="solve_ik",
                   status="failed", error={"code": code} if code else {}),)
    data = dict(status="failed", trace=trace, sequence=index if sequence is None else sequence)
    data.update(changes)
    return p.summarize_execution(f"e{index:06}", p.PublicExecution(**data))


def version(index=1, executions=None, probes=(), previous=(), sha="a", verdict="failed"):
    return p.build_version_evidence(f"v{index:04}", (execution(index),) if executions is None else tuple(executions),
                                    tuple(probes), sha * 64, tuple(previous), eval_verdict=verdict)


def submit(policy=HINT, state=None, index=1, *, verdict="failed", evidence=None,
           remaining=3, pending=False, parameters=None, eid=True):
    parameters = p.normalize_parameters(policy, parameters)
    state = state or p.initial_state(policy, parameters)
    evidence = evidence or version(index, verdict=verdict)
    return p.decide_agent(policy, parameters, state,
                          p.PolicyEvent("submit", f"v{index:04}", f"e{index:06}" if eid else None,
                                        index, verdict, "submit"), evidence,
                          help_remaining=remaining, request_pending=pending)


def event(policy, state, kind, eid=None, *, remaining=3, pending=False, parameters=None, **kw):
    return p.decide_agent(policy, p.normalize_parameters(policy, parameters), state,
                          p.PolicyEvent(kind, execution_id=eid, **kw), None,
                          help_remaining=remaining, request_pending=pending)


@pytest.mark.parametrize("policy", p.AGENT_POLICY_IDS)
def test_registry_defaults_and_memory_exposure(policy):
    options = p.policy_options()
    assert options["agent_policy_version"] == "agent-policy-v2"
    assert tuple(o["policy_id"] for o in options["policies"]) == p.AGENT_POLICY_IDS
    assert p.memory_exposed(policy) is (policy != HINT)
    options["policies"][3]["parameters"]["k"] = 99
    assert p.normalize_parameters("retry_k_then_ask") == {"k": 2}
    assert p.normalize_parameters(HINT) == {"m": 3, "n": 2}
    assert p.normalize_parameters("agent_initiated_help") == {"min_new_executions": 1}


@pytest.mark.parametrize("policy,params", [
    ("bad", {}), ("autonomous", {"k": 1}), (HINT, {"m": 3}),
    (HINT, {"m": True, "n": 2}), (HINT, {"m": 0, "n": 2}),
    (HINT, {"m": 3.0, "n": 2}), (HINT, {"m": 3, "n": 2, "extra": 1}),
    ("retry_k_then_ask", {"k": -1}), ("retry_k_then_ask", []),
    ("agent_initiated_help", {"min_new_executions": False}),
    (RANDOM, {"target_request_count": 2, "total_failure_slots": 1, "seed": 0}),
    (RANDOM, {"target_request_count": 0, "total_failure_slots": -1, "seed": 0}),
    (RANDOM, {"target_request_count": 0, "total_failure_slots": 1, "seed": True}),
])
def test_parameters_are_strict(policy, params):
    with pytest.raises(ValueError):
        p.normalize_parameters(policy, params)


def test_parameter_digest_and_state_version_lock():
    assert p.parameters_sha256(HINT, {"m": 3, "n": 2}) == p.parameters_sha256(HINT, {"n": 2, "m": 3})
    state = p.initial_state(HINT)
    for changed in (replace(state, schema="v1"), replace(state, agent_policy_version="agent-policy-v1"),
                    replace(state, policy_id=FULL), replace(state, parameters_sha256="bad")):
        with pytest.raises(ValueError, match="mismatch"):
            submit(state=changed)
    with pytest.raises(ValueError, match="mismatch"):
        submit(state=state, parameters={"m": 4, "n": 2})


@pytest.mark.parametrize("policy", ["autonomous", "demo_first"])
def test_no_online_help_policies(policy):
    for verdict in ("failed", "passed", "unavailable"):
        assert submit(policy, verdict=verdict).action == "continue"
    assert event(policy, p.initial_state(policy), "ask_help").reason == "not_eligible"


def test_demo_once_before_first_version_and_gates():
    policy = "demo_first"
    state = p.initial_state(policy)
    def boot(s=state, remaining=3, pending=False, available=True):
        return p.decide_agent(policy, {}, s, p.PolicyEvent("bootstrap"), None,
                              help_remaining=remaining, request_pending=pending, demo_available=available)
    assert boot().action == "use_demo"
    assert boot(remaining=0).reason == "assistance_budget_exhausted"
    assert boot(pending=True).reason == "request_already_pending"
    assert boot(available=False).action == "continue"
    dispatched = event(policy, state, "demo_dispatched").next_state
    assert boot(dispatched).action == "continue"
    assert boot(submit(policy).next_state).action == "continue"


@pytest.mark.parametrize("verdict,action", [("failed", "pending_ask"), ("passed", "continue"), ("unavailable", "continue")])
def test_reactive_only_eval_failed(verdict, action):
    t = submit("reactive_help", verdict=verdict)
    assert t.action == action
    assert (t.next_state.pending_ask is not None) is (action == "pending_ask")


@pytest.mark.parametrize("remaining,pending,reason", [(0, False, "assistance_budget_exhausted"), (3, True, "request_already_pending")])
def test_reactive_ineligible_has_no_obligation(remaining, pending, reason):
    t = submit("reactive_help", remaining=remaining, pending=pending)
    assert (t.action, t.reason, t.next_state.pending_ask) == ("continue", reason, None)


def test_retry_periodic_246_and_dispatch_completion():
    policy = "retry_k_then_ask"
    state = p.initial_state(policy)
    asked = []
    for i in range(1, 7):
        t = submit(policy, state, i)
        state = t.next_state
        if t.action == "pending_ask":
            asked.append(i)
            assert state.retry_failures == 2  # intent is not a provider handoff
            state = event(policy, state, "help_dispatched", f"e{i:06}").next_state
            assert state.retry_failures == 0 and state.pending_ask is None
    assert asked == [2, 4, 6]


def test_retry_unavailable_passed_and_blocked_saturation():
    policy = "retry_k_then_ask"
    state = submit(policy).next_state
    state = submit(policy, state, 2, verdict="unavailable").next_state
    assert state.retry_failures == 1
    state = submit(policy, state, 3, remaining=0).next_state
    assert state.retry_failures == 2 and state.pending_ask is None
    state = submit(policy, state, 4, pending=True).next_state
    assert state.retry_failures == 2 and state.pending_ask is None
    assert submit(policy, state, 5).action == "pending_ask"
    assert submit(policy, state, 5, verdict="passed").next_state.retry_failures == 0


def test_pending_target_is_frozen_despite_new_run_and_invalid_help():
    policy = "reactive_help"
    state = submit(policy).next_state
    assert state.pending_ask.execution_id == "e000001"
    run = event(policy, state, "execution", "e000002")
    assert run.next_state.pending_ask == state.pending_ask
    assert event(policy, run.next_state, "ask_help").execution_id == "e000001"
    with pytest.raises(ValueError, match="original execution"):
        event(policy, run.next_state, "help_dispatched", "e000002")
    with pytest.raises(ValueError, match="ask_required"):
        submit(policy, run.next_state, 3)
    assert event(policy, run.next_state, "help_dispatched", "e000001").next_state.pending_ask is None


def test_duplicate_submit_does_not_count_twice():
    first = submit(RANDOM)
    again = submit(RANDOM, first.next_state)
    assert again.reason == "boundary_already_processed"
    assert again.next_state == first.next_state
    replay = p.decide_agent(RANDOM, p.normalize_parameters(RANDOM), first.next_state,
                            p.PolicyEvent("submit", "v0001", "e000099", 1, "failed", "submit"), None,
                            help_remaining=3, request_pending=False)
    assert replay.next_state == first.next_state


@pytest.mark.parametrize("vid,sequence", [(None, 0), ("bad", 0), ("v0001", True), ("v0001", -1)])
def test_invalid_submit_is_not_mistaken_for_replay(vid, sequence):
    with pytest.raises(ValueError, match="submit version"):
        p.decide_agent("autonomous", {}, p.initial_state("autonomous"),
                       p.PolicyEvent("submit", version_id=vid, sequence=sequence), None,
                       help_remaining=3, request_pending=False)


def test_random_exact_slots_cumulative_index_and_json_restore():
    assert p.normalize_parameters(RANDOM) == {"target_request_count": 1, "total_failure_slots": 10, "seed": 101}
    # Frozen oracle, independent of implementation's rank helper.
    assert p.selected_random_slots() == (7,)
    params = dict(target_request_count=3, total_failure_slots=10, seed=9)
    assert p.selected_random_slots(params) == (4, 6, 9)
    state = p.initial_state(RANDOM, params)
    selected = []
    failure_index = 0
    for i in range(1, 14):
        verdict = "passed" if i in {2, 5, 8} else "failed"
        t = submit(RANDOM, state, i, parameters=params, verdict=verdict)
        if verdict == "failed":
            if t.action == "request":
                selected.append(failure_index)
            failure_index += 1
        state = p.PolicyState(**json.loads(json.dumps(asdict(t.next_state))))
        # Convert JSON array back to typed tuple, as a P2 state decoder must.
        state = replace(state, completed_execution_ids=tuple(state.completed_execution_ids))
    assert selected == [4, 6, 9]
    assert state.cumulative_failed_versions == 10
    empty = dict(target_request_count=0, total_failure_slots=0, seed=-1)
    assert submit(RANDOM, parameters=empty).reason == "random_slot_not_selected"


def trace_decision(policy, evidence, **kw):
    t = submit(policy, evidence=evidence, index=int(evidence.version_id[1:]), **kw)
    return p.resolve_memory(t, matched=False) if t.action == "check_memory" else t


@pytest.mark.parametrize("policy", [HINT, FULL])
def test_trace_priority_safety_budget_pending(policy):
    v = version(executions=[execution(1), execution(2), execution(3)])
    safe_stop = replace(v, safety_interrupt=True)
    assert trace_decision(policy, safe_stop, remaining=0, pending=True).action == "stop"
    t = trace_decision(policy, v, remaining=0, pending=True)
    assert (t.action, t.reason) == ("continue", "assistance_budget_exhausted")
    assert trace_decision(policy, v, pending=True).reason == "request_already_pending"
    # Actual safety overrides even an inconsistent passed/unavailable review.
    assert trace_decision(policy, safe_stop, verdict="passed").action == "stop"


def test_memory_precedes_stuck_and_miss_never_recounts():
    v = version(executions=[execution(1), execution(2), execution(3)])
    full = submit(FULL, evidence=v)
    assert full.action == "check_memory"
    match = p.resolve_memory(full, matched=True)
    miss = p.resolve_memory(full, matched=False)
    assert (match.action, match.reason) == ("retrieve_memory", "trusted_memory_matches")
    assert miss.reason == "stuck_within_version"
    assert match.next_state == miss.next_state == full.next_state
    assert match.next_state.trace_failed_versions == 1
    assert p.resolve_memory(full, matched=False) == miss
    with pytest.raises(ValueError):
        p.resolve_memory(submit(HINT, evidence=v), matched=True)


@pytest.mark.parametrize("variant,reason", [
    ({"stuck_in_version": 3, "progress": True}, "stuck_within_version"),
    ({"stuck_in_version": 2}, "no_trace_trigger"),
    ({"repeated_across_versions": True, "code_changed": True}, "same_failure_after_code_change"),
    ({"repeated_across_versions": True, "code_changed": False}, "unchanged_repeated_failure"),
    ({"progress": True}, "progress"),
    ({}, "no_trace_trigger"),
])
def test_trace_rule_table_and_memory_ablation(variant, reason):
    v = replace(version(), **variant)
    hint = submit(HINT, evidence=v)
    full = p.resolve_memory(submit(FULL, evidence=v), matched=False)
    assert hint.reason == full.reason == reason
    assert (hint.action, hint.reason, hint.execution_id) == (full.action, full.reason, full.execution_id)
    assert hint.action not in {"check_memory", "retrieve_memory", "inspect_trace", "retry"}


@pytest.mark.parametrize("policy", [HINT, FULL])
def test_trace_n_boundary_pass_reset_and_unavailable_hold(policy):
    state = trace_decision(policy, version()).next_state
    assert state.trace_failed_versions == 1
    unavailable = submit(policy, state, 2, verdict="unavailable")
    assert unavailable.next_state.trace_failed_versions == 1
    second = trace_decision(policy, version(3), state=unavailable.next_state)
    assert second.reason == "cause_unresolved"
    assert trace_decision(policy, version(4), state=second.next_state, verdict="passed").next_state.trace_failed_versions == 0
    # No permanent local retry: a concrete, locally classified cause reaches n.
    assert second.action == "request"
    assert trace_decision(policy, replace(version(3), progress=True), state=state).reason == "progress"


@pytest.mark.parametrize("op,verdict", [("run", "passed"), ("run", "unavailable"), ("submit", "passed"), ("submit", "failed")])
def test_agent_initiated_after_any_execution(op, verdict):
    policy = "agent_initiated_help"
    state = event(policy, p.initial_state(policy), "execution", "e000001", op=op, eval_verdict=verdict).next_state
    assert event(policy, state, "ask_help").action == "request"
    assert state.pending_ask is None


def test_agent_initiated_interval_budget_concurrency_and_probe():
    policy = "agent_initiated_help"
    params = {"min_new_executions": 2}
    state = p.initial_state(policy, params)
    assert event(policy, state, "ask_help", parameters=params).reason == "no_completed_execution"
    state = event(policy, state, "execution", "e000001", parameters=params).next_state
    assert event(policy, state, "ask_help", parameters=params).reason == "new_execution_required"
    state = event(policy, state, "execution", "e000002", parameters=params).next_state
    assert event(policy, state, "ask_help", parameters=params, remaining=0).reason == "assistance_budget_exhausted"
    assert event(policy, state, "ask_help", parameters=params, pending=True).reason == "request_already_pending"
    assert event(policy, state, "ask_help", parameters=params).action == "request"
    state = event(policy, state, "help_dispatched", "e000002", parameters=params).next_state
    state = event(policy, state, "execution", "e000002", parameters=params).next_state
    assert state.completed_execution_count == 2
    assert event(policy, state, "ask_help", parameters=params).reason == "new_execution_required"
    with pytest.raises(ValueError):
        event(policy, state, "execution", "e000003", op="plan_dry_run", parameters=params)


@pytest.mark.parametrize("reason,allowed", [(reason, reason in {p.TerminationReason.RUN_END, p.TerminationReason.NEXT_SUBMIT, p.TerminationReason.SESSION_FAILURE}) for reason in p.TerminationReason])
def test_fallback_hard_stops_are_free(reason, allowed):
    pending = submit("reactive_help").next_state.pending_ask
    result = p.forced_fallback(pending, reason)
    assert result.can_attempt is allowed
    assert result.forced_fallback_blocked == (None if allowed else reason.value)
    assert not result.charged
    assert not p.forced_fallback(None, reason).can_attempt
    assert p.forced_fallback(None, reason).forced_fallback_blocked is None


def test_probe_only_evidence_does_not_trigger_stuck_or_primary():
    sig = p.failure_signature("ik_motion_planning", code={"source": "planner", "value": "BLOCKED"})
    probes = tuple(p.ProbeEvidence(f"p{i:06}", i, sig) for i in range(1, 4))
    v = version(executions=[execution(4)], probes=probes)
    assert len(v.rejected_probes) == 3
    assert dict(v.probe_histogram) == {sig.signature_id: 3}
    assert v.probe_only_signatures == (sig.signature_id,)
    assert v.stuck_in_version == 1
    assert v.primary_signature == execution(4).signature.signature_id
    assert submit(HINT, evidence=v).reason == "no_trace_trigger"
    # Even identical execution/probe signatures do not add together.
    same = replace(probes[0], signature=execution(4).signature)
    v = version(executions=[execution(4)], probes=[replace(same, probe_id=f"p{i:06}") for i in range(1, 4)])
    assert v.stuck_in_version == 1 and v.probe_only_signatures == ()
    only = version(executions=[], probes=probes)
    assert only.primary_signature is None and only.stuck_in_version == 0
    assert only.representative_execution_id is None


def test_multi_execution_window_dedup_ties_progress_and_code_sha():
    a, b, c = execution(1), execution(2, code="OTHER"), execution(3)
    v = version(executions=[a, b, a, c])
    assert v.stuck_in_version == 2 and len(v.executions) == 3
    assert v.representative_execution_id == "e000003"
    tie = version(executions=[a, b])
    assert tie.primary_signature == b.signature.signature_id
    same_sequence = version(executions=[a, replace(b, sequence=1)])
    assert same_sequence.primary_signature == min(a.signature.signature_id, b.signature.signature_id)
    with pytest.raises(ValueError, match="conflicting"):
        version(executions=[a, replace(a, passed_steps=9)])
    with pytest.raises(ValueError, match="window"):
        version(2, executions=[a], previous=[v])
    prior = version(executions=[execution(1, passed=2)])
    repeat = version(2, executions=[execution(2, passed=2)], previous=[prior], sha="b")
    assert repeat.repeated_across_versions and repeat.code_changed
    progress = version(3, executions=[execution(3, passed=3)], previous=[prior, repeat])
    assert progress.progress and not progress.repeated_across_versions
    low = version(4, executions=[execution(4, passed=2)], previous=[prior, repeat, progress])
    assert not low.progress
    after_pass = version(2, previous=[replace(prior, eval_verdict="passed")])
    assert not after_pass.repeated_across_versions
    empty = version(2, executions=[], previous=[prior], verdict="unavailable")
    assert empty.vague and not empty.executions and empty.primary_signature is None


def test_acceptance_sequence_window_including_invalid_submit():
    prior = p.build_version_evidence("v0001", (execution(1, sequence=2),), (), "a" * 64, (),
                                     submit_sequence=2)
    with pytest.raises(ValueError, match="window"):
        p.build_version_evidence("v0002", (execution(2, sequence=1),), (), "a" * 64, (prior,),
                                 submit_sequence=5)
    probe = p.ProbeEvidence("p000001", 6, execution().signature)
    with pytest.raises(ValueError, match="window"):
        p.build_version_evidence("v0002", (), (probe,), "a" * 64, (prior,), submit_sequence=5)
    # Invalid accepted submit closes its window without inventing an execution.
    empty = p.build_version_evidence("v0002", (), (), "a" * 64, (prior,), submit_sequence=5)
    assert empty.submit_sequence == 5 and not empty.executions


def test_probe_filtering_dedup_and_window_boundary():
    probe = p.ProbeEvidence("p000001", 1, execution().signature)
    v = version(probes=[probe, probe, replace(probe, probe_id="p000002", outcome="timeout"),
                        replace(probe, probe_id="p000003", outcome="validation_error"),
                        replace(probe, probe_id="p000004", op="ik_check")])
    assert len(v.rejected_probes) == 1
    with pytest.raises(ValueError, match="window"):
        version(2, probes=[probe], previous=[v])


def test_blank_withheld_unknown_does_not_merge():
    for stdout, stderr in [("", ""), ("[withheld]", "[artifact withheld by public screening]\n")]:
        e = execution(stdout=stdout, stderr=stderr, code=None)
        assert e.signature.signature_id is None
        v = version(executions=[e, replace(e, execution_id="e000002"), replace(e, execution_id="e000003")])
        assert v.vague and v.stuck_in_version == 0
        assert v.execution_histogram == ()
        assert not version(2, executions=[execution(4, code=None)], previous=[v]).repeated_across_versions
    generic = execution(trace=(dict(event_type="execution.finished", status="failed",
                                    error={"type": "TaskOutcomeFailure"}),),
                        stderr="TaskOutcomeFailure: task unfinished")
    assert generic.signature.signature_id is None


def test_planner_association_code_precedence_and_natural_reason():
    diagnostic = dict(event_id="failure", accepted=False, error_code="P19")
    e = execution(planner=(diagnostic,))
    assert e.signature.code == {"source": "trace", "value": "IK7"}
    e = execution(code=None, planner=(diagnostic,))
    assert e.signature.code == {"source": "planner", "value": "P19"}
    e = execution(code=None, planner=(dict(accepted=False, reason="not_reachable"),))
    assert e.signature.code is None and e.unassociated_planner_codes == ("not_reachable",)
    e = execution(code=None, planner=(dict(diagnostic, error_code=None, reason="goal is not reachable"),))
    assert e.signature.signature_id is None
    e = execution(trace=(dict(event_id="failure", status="failed", error={"type": "CustomError7"}),))
    assert e.signature.code == {"source": "trace.error.type", "value": "CustomError7"}


def test_first_failure_passed_steps_safety_all_events_and_input_unchanged():
    trace = (dict(event_type="execution.started", status="completed"),
             dict(event_type="sdk.call", status="ok"),
             dict(event_type="sdk.call", status="rejected", operation="solve_ik", error={"code": "IK7"}),
             dict(event_type="sdk.call", status="failed", error={"type": "CollisionError"}))
    frozen = json.dumps(trace, sort_keys=True)
    e = execution(trace=trace)
    assert e.stage == "ik_motion_planning" and e.passed_steps == 1 and e.safety_interrupt
    assert json.dumps(trace, sort_keys=True) == frozen
    precheck = execution(trace=(dict(status="rejected", operation="motion_plan", error={"type": "CollisionError"}),))
    assert not precheck.safety_interrupt and precheck.stage == "ik_motion_planning"


@pytest.mark.parametrize("left,right", [
    ("joint7 failed at [1, 2, -3e-2] after 20 ms", "joint9 failed at [4, 5, 9] after 99 ms"),
    ("load ./models/a.xml", "load ../assets/b.xml"),
    ("load models/a.xml", "load config/b.xml"),
    ("load /opt/models/a.xml", "load /srv/data/b.xml"),
    (r"load C:\\data\\a.xml", r"load D:\\other\\b.xml"),
    ("bad https://example.org/a", "bad file://data/b"),
    ("address 0xAbC1", "address 0x123f"),
    ("\x1b[31m关节７ 错误\x1b[0m", "关节9 错误"),
])
def test_signature_normalization_equivalence(left, right):
    a = p.failure_signature("sandbox_runtime", stderr="ValueError: " + left)
    b = p.failure_signature("sandbox_runtime", stderr="ValueError: " + right)
    assert a.signature_id == b.signature_id
    assert a.exception["type"] == "ValueError"


def test_normalization_division_stack_and_structured_digits():
    assert p.normalize_message("ratio 8 / 2") == "ratio <number> / <number>"
    assert p.normalize_message("ratio 8/2 and x/y") == "ratio <number>/<number> and x/y"
    assert p.normalize_message('File "code.py", line 8\nBAD 9') == "bad <number>"
    first = p.failure_signature("unknown", code={"source": "trace", "value": "E7"}, stderr="Custom7Error: bad 1")
    other = p.failure_signature("unknown", code={"source": "trace", "value": "E8"}, stderr="Custom8Error: bad 2")
    assert first.signature_id != other.signature_id
    assert first.exception["type"] == "Custom7Error"
    assert first.code["value"] == "E7"
    canonical = {"stage": first.stage, "code": first.code, "exception": first.exception}
    assert first.signature_id == hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_exception_chain_double_stream_priority_and_broker_error():
    signature = p.failure_signature("unknown", stderr="ValueError: first\nDuring handling...\npkg.FinalError: second 1",
                                    stdout="RuntimeError: later 2")
    assert signature.exception["type"] == "pkg.FinalError"
    assert signature.selection_basis == "stderr_priority"
    fallback = p.failure_signature("unknown", stderr="no exception", stdout="execution_error: RuntimeError: bad 2")
    assert fallback.exception["type"] == "RuntimeError" and fallback.selection_basis == "stdout_fallback"


@pytest.mark.parametrize("text", ["native_success: 1", "ValueError: /data/reward7.xml", "ValueError: hidden detail", "ValueError: ｒｅｗａｒｄ 1"])
def test_forbidden_is_screened_before_normalization(text):
    with pytest.raises(ValueError):
        p.failure_signature("unknown", stderr=text)
    with pytest.raises(ValueError):
        execution(stdout=text)


def test_all_input_and_output_boundaries_screen_public_fields():
    with pytest.raises(ValueError):
        execution(trace=(dict(status="failed", error={"native_success": True}),))
    with pytest.raises(ValueError):
        version(probes=[p.ProbeEvidence("p000001", 1, p.FailureSignature("bad", "unknown", None,
                                     {"type": "ValueError", "normalized_message": "reward"}, "stdout_fallback"))])
    with pytest.raises(ValueError):
        p.question_for_agent("cause_unresolved", {"failure_type": "reward"})


@pytest.mark.parametrize("path", ["/etc/passwd", "../executions/e000001/trace.json", "executions/e000001/../../a", "executions/e000001/./a", r"executions/e000001/..\a", "ws/executions/e000001/trace.json"])
def test_reference_paths_reject_escape(path):
    with pytest.raises(ValueError):
        p.ArtifactRef(path, "a" * 64)


def test_refs_missing_withheld_hash_and_no_filesystem_access(monkeypatch):
    refs = (p.ArtifactRef("executions/e000001/trace.json", "a" * 64),
            p.ArtifactRef("executions/e000001/stderr.txt", status="withheld"),
            p.ArtifactRef("executions/e000001/stdout.txt", status="missing"))
    with pytest.raises(ValueError):
        p.ArtifactRef("executions/e000001/trace.json")
    def forbidden(*a, **kw):
        raise AssertionError("pure layer attempted file access")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(Path, "open", forbidden)
    e = execution(artifacts=refs)
    assert e.artifacts == refs
    v = version(executions=[e])
    assert submit(HINT, evidence=v).action == "continue"
    assert p.question_for_agent("cause_unresolved", {"failure_type": "grasp"}).startswith("What change to the grasp call")
    assert len(e.log_tail) <= 2000


def test_path_or_symlink_input_is_not_read(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("native_success")
    link = tmp_path / "trace.json"
    link.symlink_to(outside)
    for path in (link, outside, "../outside.txt"):
        with pytest.raises(ValueError, match="already-read"):
            p.summarize_execution("e000001", path)


def test_bounded_logs_keep_source_labels_and_truncation():
    e = execution(stdout="x" * 4000, stderr="y" * 4000)
    assert len(e.log_tail) <= 2000 and e.log_truncated
    assert e.log_tail.startswith("stdout:\n") and "\nstderr:\n" in e.log_tail


def compat_snapshot():
    """Freeze complete outputs, including context, refs, approval and budget rules."""
    base = dict(run_id="golden", attempt_index=1, failure_index=0, consecutive_failures=1,
                assistance_remaining=3, evidence_count=1, has_hypothesis=True)
    packet = dict(trace_id="t", events=[dict(event_id="f", event_type="sdk.call", status="failed",
                                            operation="solve_ik", error={"type": "IKError"})])
    assessment = assess_trace(packet).artifact()
    from benchmarks.attention_harness.core.assessment import TraceAssessment
    assessment = TraceAssessment(**assessment)
    cases = [{}, {"consecutive_failures": 2}, {"assistance_remaining": 0, "consecutive_failures": 2},
             {"pending_requests": 1, "consecutive_failures": 2}, {"unsafe": True}, {"approval_required": True},
             {"matching_memory_count": 1}, {"attempt_index": 0, "demo_available": True},
             {"evidence_count": 0, "has_hypothesis": False}, {"assessment": assessment},
             {"assessment": replace(assessment, repeated_failure=True, code_changed=True)},
             {"assessment": replace(assessment, risk="safety")},
             {"trace_evidence": False, "consecutive_failures": 2}]
    core = []
    old = []
    for policy in POLICY_IDS:
        params = dict(target_request_count=1, total_failure_slots=2, seed=101) if policy == RANDOM else {}
        instance = build_policy(policy, **params)
        core.append((policy, type(instance).__name__, [asdict(instance.decide(PolicyContext(**(base | case)))) for case in cases]))
        feedback = dict(execution_status="failed", public_failure=True, public_error="IKError",
                        action_trace=[dict(status="failed", operation="solve_ik", error={"type": "IKError"})])
        for verdict in (False, True, None):
            for flags in ({}, {"help_remaining": 0}, {"unsafe": True}, {"approval_required": True}):
                old.append(decide(policy, params if policy == RANDOM else None, 2, feedback, 2,
                                  eval_review={"passed": verdict}, eval_available=verdict is not None,
                                  **({"help_remaining": 3} | flags)))
        old.append(decide(policy, params if policy == RANDOM else None, 1, feedback, 1,
                          help_remaining=3, demo_available=True, diagnostic_fallback=True))
    encode = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return encode(core), encode(old)


def test_core_and_dev_help_golden_unchanged():
    assert POLICY_IDS == ("autonomous", "demo_first", "reactive_help", "retry_k_then_ask",
                          "budget_matched_random_escalation", "trace_aware_hint_only", "full_trace_aware_attention_planner")
    assert compat_snapshot() == ("d35e790079f9373f40334b1ae50890e1cf6f1f7dab209368b980f7b432f7ec63",
                                 "0eceaebcf2bac99640d901d80a5331c2c2ef6d2276b4922679afec240677da4a")
    root = Path(__file__).resolve().parents[1] / "protocol/v2/review_packages"
    for folder in ("seven_primary_dev_2026-09-29", "seven_primary_guidance_v1_1_dev_2026-10-01"):
        assert json.loads((root / folder / "retry_k.json").read_text())["k"] == 2
    delivery = Path("/home/truares/Documents/Codex/2026-10-09/new-chat/outputs/robocasa365/configs/retry_k.json")
    assert json.loads(delivery.read_text()) == {"k": 1}
