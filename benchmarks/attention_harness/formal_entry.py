"""Shared fail-closed M1 preflight and canonical formal entry identity."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .attention_modes import AssistanceMode
from .core.policies import POLICY_IDS, build_policy
from .demo_prior import verify_demo_prior
from .formal_runner_boundary import FormalRunRequest
from .robocasa_native.formal_runner import _config as robocasa_config
from .robocasa_native.tasks import get_robocasa_task
from .robosuite_memory.formal_runner import _config as robosuite_config
from .seed_guard import validate_seed
from robosuite_sim.tasks import get_task as get_robosuite_task


def _read_approved(path: Path | None, digest: str | None, name: str) -> bytes | None:
    if (path is None) != (digest is None):
        raise ValueError(f"{name} and approved SHA-256 must be supplied together")
    if path is None:
        return None
    if not path.is_file():
        raise ValueError(f"{name} is missing")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"{name} differs from approved SHA-256")
    return raw


def inspect_formal_entry(
    *, suite: str, task_id: str, seed: int, policy_id: str,
    code: Path, approved_policy_sha256: str,
    config: Path, approved_config_sha256: str,
    max_attempts: int, assistance_credits: int, token_limit: int,
    assistance_mode: AssistanceMode | str,
    human_deadline_seconds: float, overall_deadline_seconds: float,
    demo_prior: Path | None = None, approved_demo_sha256: str | None = None,
    policy_config: Path | None = None,
    approved_policy_config_sha256: str | None = None,
) -> tuple[dict[str, Any], dict[str, int] | None]:
    if suite == "robosuite":
        get_robosuite_task(task_id)
    elif suite == "robocasa":
        get_robocasa_task(task_id)
    else:
        raise ValueError("unsupported formal suite")
    if validate_seed(seed, allow_heldout=False) != "dev":
        raise ValueError("formal task entry requires a development seed")
    if policy_id not in POLICY_IDS:
        raise ValueError("unknown Attention policy")
    mode = AssistanceMode(assistance_mode)
    for name, value, minimum, maximum in (
        ("max_attempts", max_attempts, 1, 10),
        ("assistance_credits", assistance_credits, 0, 10),
        ("token_limit", token_limit, 0, 100000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"invalid {name}")
    for name, value, minimum in (
        ("human_deadline_seconds", human_deadline_seconds, 1),
        ("overall_deadline_seconds", overall_deadline_seconds, 30),
    ):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not minimum <= value <= 600):
            raise ValueError(f"invalid {name}")
    request = FormalRunRequest(
        suite=suite, task_id=task_id, seed=seed, policy_code_path=code,
        policy_sha256=approved_policy_sha256, config_path=config,
        config_sha256=approved_config_sha256, artifact_root=Path("."),
        overall_deadline_seconds=overall_deadline_seconds,
    )
    request.validate()
    (robosuite_config if suite == "robosuite" else robocasa_config)(request)
    demo = _read_approved(demo_prior, approved_demo_sha256, "demo prior")
    if policy_id == "demo_first":
        if demo is None:
            raise ValueError("demo_first requires an approved fixed demo")
        verify_demo_prior(demo.decode("utf-8"), suite=suite, task_id=task_id,
                          approved_sha256=approved_demo_sha256)
    elif demo is not None:
        raise ValueError("only demo_first may receive a demo prior")
    random_raw = _read_approved(policy_config, approved_policy_config_sha256,
                                "policy config")
    parsed = None
    if random_raw is not None:
        parsed = json.loads(random_raw)
        if not isinstance(parsed, dict):
            raise ValueError("policy config must be a JSON object")
    if policy_id == "budget_matched_random_escalation":
        if parsed is None:
            raise ValueError("random escalation requires approved pre-registration")
        if (set(parsed) != {"target_request_count", "total_failure_slots", "seed"}
                or any(isinstance(parsed[key], bool) or not isinstance(parsed[key], int)
                       for key in parsed)
                or parsed["total_failure_slots"] != max_attempts - 1
                or parsed["seed"] != seed
                or isinstance(parsed["target_request_count"], bool)
                or not isinstance(parsed["target_request_count"], int)
                or parsed["target_request_count"] > assistance_credits):
            raise ValueError("random pre-registration disagrees with seed or budget")
        build_policy(policy_id, **parsed)
    elif parsed is not None:
        if policy_id != "retry_k_then_ask" or set(parsed) != {"k"}:
            raise ValueError("policy config is not allowed for selected policy")
        build_policy(policy_id, **parsed)
    identity = {
        "schema_version": "attentionbench.formal-entry-lock.v1",
        "suite": suite, "task_id": task_id, "seed": seed,
        "perception_mode": "sim_gt", "execution_target": f"{suite}_sim",
        "attention_policy": policy_id,
        "approved_policy_sha256": approved_policy_sha256,
        "approved_config_sha256": approved_config_sha256,
        "approved_demo_sha256": approved_demo_sha256,
        "approved_policy_config_sha256": approved_policy_config_sha256,
        "max_attempts": max_attempts, "assistance_credits": assistance_credits,
        "token_limit": token_limit, "assistance_mode": mode.value,
        "human_deadline_seconds": float(human_deadline_seconds),
        "overall_deadline_seconds": float(overall_deadline_seconds),
    }
    identity["sha256"] = hashlib.sha256(json.dumps(identity, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return identity, parsed
