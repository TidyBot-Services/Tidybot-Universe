"""Additional M1–M6 integration contract; the M2 handoff gate stays unchanged."""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from attention_eval import build_eval_packet


def validate_crosschain_handoff(config: dict, result: dict) -> None:
    """Bind every formal attempt to one approved M2 candidate and entry lock."""
    if config.get("m1_m6_crosschain") is not True or config.get("m2_gate") is not True:
        raise ValueError("cross-module handoff requires the M2 approval gate")
    freeze_path = Path(config.get("crosschain_freeze_file", ""))
    if (not freeze_path.is_file()
            or hashlib.sha256(freeze_path.read_bytes()).hexdigest()
               != config.get("crosschain_freeze_sha256")):
        raise ValueError("cross-module freeze is missing or changed")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    suite = config.get("suite")
    spec = freeze.get("suites", {}).get(suite, {})
    budget = freeze.get("per_suite_budget", {})
    if (freeze.get("formal_eligible") is not False
            or freeze.get("attention_policy") != config.get("attention_policy")
            or freeze.get("dev_prompt_profile") != config.get("dev_prompt_profile")
            or bool(spec.get("public_lift_progress_check", False))
               != bool(config.get("public_lift_progress_check", False))
            or spec.get("task_id") != config.get("task")
            or spec.get("seed") != config.get("seed")
            or spec.get("config_sha256") != config.get("approved_config_sha256")
            or config.get("max_attempts") != budget.get("attempts_per_run_max")
            or config.get("assistance_credits") != budget.get("assistance_credits_per_run_max")
            or config.get("token_limit") != budget.get("advisor_tokens_per_run_max")
            or config.get("overall_deadline_seconds") != budget.get("overall_deadline_seconds_per_run")
            or config.get("single_glm_call") is not True):
        raise ValueError("cross-module task or budget differs from frozen acceptance")
    expected = config.get("m2_candidate")
    if not isinstance(expected, dict):
        raise ValueError("cross-module handoff lacks the approved candidate")
    lock_path = Path(expected["entry_lock"])
    lock = result.get("entry_lock")
    if (not isinstance(lock, dict)
            or lock != json.loads(lock_path.read_text(encoding="utf-8"))
            or lock.get("sha256") != expected.get("entry_sha256")
            or lock.get("approved_policy_sha256") != expected.get("source_sha256")
            or lock.get("approved_config_sha256") != expected.get("config_sha256")
            or lock.get("attention_policy") != config.get("attention_policy")
            or lock.get("max_attempts") != config.get("max_attempts")
            or lock.get("assistance_credits") != config.get("assistance_credits")
            or result.get("suite") != expected.get("suite")
            or result.get("task_id") != expected.get("task")
            or result.get("seed") != expected.get("seed")
            or result.get("approved_policy_sha256") != expected.get("source_sha256")
            or result.get("approved_config_sha256") != expected.get("config_sha256")
            or result.get("scheduler_config", {}).get("entry_sha256") != expected.get("entry_sha256")
            or result.get("runner_boundary", {}).get("mode") != "formal"
            or result.get("formal_eligible") is not False):
        raise ValueError("cross-module run differs from approved M1/M2 identity")
    attempts = result.get("attempts")
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= config["max_attempts"]:
        raise ValueError("cross-module attempt count exceeds frozen budget")
    artifact = Path(result.get("artifact_dir", "")) / "attention_run.json"
    if not artifact.is_file() or json.loads(artifact.read_text(encoding="utf-8")) != result:
        raise ValueError("cross-module persisted run differs from Bridge result")
    run_id = f"run:{artifact.parent.name}"
    seen: set[str] = set()
    ordered_ids: list[str] = []
    for index, attempt in enumerate(attempts):
        formal = attempt.get("formal_runner_result") or {}
        trace = attempt.get("attention_trace") or {}
        attempt_id = f"attempt:{artifact.parent.name}:{index}"
        if (trace.get("run_id") != run_id or trace.get("attempt_id") != attempt_id
                or formal.get("run_id") != run_id or formal.get("attempt_id") != attempt_id
                or formal.get("entry_lock") != lock
                or formal.get("policy_sha256") != expected["source_sha256"]
                or formal.get("config_sha256") != expected["config_sha256"]
                or formal.get("boundary_checked") is not True
                or formal.get("formal_eligible") is not False
                or formal.get("native_success") is not attempt.get("native_success")
                or formal.get("status") != attempt.get("status")
                or attempt_id in seen):
            raise ValueError("cross-module formal attempt identity or outcome mismatch")
        seen.add(attempt_id)
        ordered_ids.append(attempt_id)
    for request in result.get("requests", []):
        if request.get("responder") != "advisor_proxy":
            raise ValueError("cross-module request was not answered by the real Advisor proxy")
    # This independently reads all four hashed artifacts, verifies the native
    # evaluator, independent Safety, Service stop and cross-layer identities.
    packet = build_eval_packet(artifact)
    if packet["run_id"] != run_id or [row["attempt_id"] for row in packet["attempts"]] != ordered_ids:
        raise ValueError("cross-module Eval packet identity mismatch")
