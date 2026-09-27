"""Operator-selected, backend-locked formal engineering launches."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .attention_modes import AssistanceMode
from .core.policies import POLICY_IDS
from .core.store import AttentionStore
from .formal_entry import inspect_formal_entry
from .seed_guard import validate_seed


_TASKS = {"robosuite": {"cube_lift", "cube_stack"},
          "robocasa": {"counter_to_sink", "counter_to_cab"}}
_ROOT_FLAGS = {"robosuite": ("service_source_root",),
               "robocasa": ("sim_source_root", "agent_source_root", "task_source_root",
                            "sim_python", "agent_python")}
_SELECTION_KEYS = {"profile_id", "seed", "attention_policy", "execution_target",
                   "assistance_mode", "max_attempts", "assistance_credits", "token_limit",
                   "human_deadline_seconds", "overall_deadline_seconds"}


def load_catalog(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    profiles = raw.get("profiles") if isinstance(raw, dict) else None
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("run catalog requires nonempty profiles")
    seen = set()
    for profile in profiles:
        if not isinstance(profile, dict) or not isinstance(profile.get("id"), str):
            raise ValueError("invalid run profile")
        suite, task = profile.get("suite"), profile.get("task")
        if profile["id"] in seen or task not in _TASKS.get(suite, set()):
            raise ValueError("duplicate or unsupported run profile")
        seen.add(profile["id"])
        if profile.get("execution_target") != f"{suite}_sim":
            raise ValueError("run profile target must be the matching simulator")
        for field in ("code", "config", *_ROOT_FLAGS[suite]):
            value = profile.get(field)
            if not isinstance(value, str) or not Path(value).is_absolute() or not Path(value).exists():
                raise ValueError(f"run profile needs an existing absolute {field}")
        for field, digest_field in (("code", "approved_policy_sha256"),
                                    ("config", "approved_config_sha256")):
            if hashlib.sha256(Path(profile[field]).read_bytes()).hexdigest() != profile.get(digest_field):
                raise ValueError(f"run profile {field} differs from approved digest")
        if "demo_prior" in profile:
            prior = Path(profile["demo_prior"])
            if (not prior.is_absolute() or not prior.is_file()
                    or hashlib.sha256(prior.read_bytes()).hexdigest()
                    != profile.get("approved_demo_sha256")):
                raise ValueError("run profile demo prior differs from approved digest")
        if "random_policy_config" in profile:
            random_config = Path(profile["random_policy_config"])
            if (not random_config.is_absolute() or not random_config.is_file()
                    or hashlib.sha256(random_config.read_bytes()).hexdigest()
                    != profile.get("approved_random_policy_config_sha256")):
                raise ValueError("run profile random policy config differs from approved digest")
        approved = json.loads(Path(profile["config"]).read_text(encoding="utf-8"))
        if (approved.get("suite") != suite or approved.get("task_id") != task
                or isinstance(approved.get("seed"), bool)
                or not isinstance(approved.get("seed"), int)):
            raise ValueError("run profile disagrees with approved simulator config")
        if validate_seed(approved["seed"], allow_heldout=False) != "dev":
            raise ValueError("run profile requires a development seed")
        profile["seed"] = approved["seed"]
    return profiles


def public_options(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    return {"profiles": [{key: item[key] for key in
                          ("id", "suite", "task", "seed", "execution_target")}
                         for item in profiles],
            "attention_policies": list(POLICY_IDS),
            "assistance_modes": [mode.value for mode in AssistanceMode],
            "limits": {"max_attempts": 10, "assistance_credits": 10,
                       "token_limit": 100000, "human_deadline_seconds": 600,
                       "overall_deadline_seconds": 600}}


def launch_formal_run(
    selection: dict[str, Any], *, profiles: list[dict[str, Any]],
    store: AttentionStore, artifact_root: Path,
    popen: Callable[..., Any] = subprocess.Popen,
) -> dict[str, Any]:
    if set(selection) != _SELECTION_KEYS:
        raise ValueError("run selection has missing or unexpected fields")
    profile = next((item for item in profiles if item["id"] == selection["profile_id"]), None)
    if profile is None:
        raise ValueError("unknown run profile")
    if selection["execution_target"] != profile["execution_target"]:
        raise ValueError("execution target disagrees with profile")
    seed = selection["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    validate_seed(seed, allow_heldout=False)
    if seed != profile["seed"]:
        raise ValueError("seed disagrees with approved simulator config")
    policy = selection["attention_policy"]
    if policy not in POLICY_IDS:
        raise ValueError("unknown Attention policy")
    mode = AssistanceMode(selection["assistance_mode"])
    for key, minimum, maximum in (("max_attempts", 1, 10),
                                  ("assistance_credits", 0, 10),
                                  ("token_limit", 0, 100000)):
        value = selection[key]
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"invalid {key}")
    deadline = selection["human_deadline_seconds"]
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not 1 <= deadline <= 600:
        raise ValueError("invalid human deadline")
    overall = selection["overall_deadline_seconds"]
    if isinstance(overall, bool) or not isinstance(overall, (int, float)) or not 30 <= overall <= 600:
        raise ValueError("invalid attempt execution deadline")
    if policy == "demo_first" and "demo_prior" not in profile:
        raise ValueError("demo_first requires a separately approved fixed demo prior")
    if policy == "budget_matched_random_escalation" and "random_policy_config" not in profile:
        raise ValueError("random escalation requires an approved pre-registered target")
    # Recheck after UI rendering: a profile file may have changed meanwhile.
    for field, digest_field in (("code", "approved_policy_sha256"),
                                ("config", "approved_config_sha256")):
        if hashlib.sha256(Path(profile[field]).read_bytes()).hexdigest() != profile[digest_field]:
            raise ValueError(f"approved {field} changed before launch")
    if "demo_prior" in profile and hashlib.sha256(
        Path(profile["demo_prior"]).read_bytes()).hexdigest() != profile["approved_demo_sha256"]:
        raise ValueError("approved demo prior changed before launch")
    if "random_policy_config" in profile and hashlib.sha256(
        Path(profile["random_policy_config"]).read_bytes()).hexdigest() != profile["approved_random_policy_config_sha256"]:
        raise ValueError("approved random policy config changed before launch")
    entry_lock, _ = inspect_formal_entry(
        suite=profile["suite"], task_id=profile["task"], seed=seed,
        policy_id=policy, code=Path(profile["code"]),
        approved_policy_sha256=profile["approved_policy_sha256"],
        config=Path(profile["config"]),
        approved_config_sha256=profile["approved_config_sha256"],
        max_attempts=selection["max_attempts"],
        assistance_credits=selection["assistance_credits"],
        token_limit=selection["token_limit"], assistance_mode=mode,
        human_deadline_seconds=deadline, overall_deadline_seconds=overall,
        demo_prior=Path(profile["demo_prior"]) if policy == "demo_first" else None,
        approved_demo_sha256=profile.get("approved_demo_sha256") if policy == "demo_first" else None,
        policy_config=Path(profile["random_policy_config"])
            if policy == "budget_matched_random_escalation" else None,
        approved_policy_config_sha256=profile.get("approved_random_policy_config_sha256")
            if policy == "budget_matched_random_escalation" else None,
    )
    launch_id = f"launch:{uuid4().hex}"
    cmd = [sys.executable, "-m", "benchmarks.attention_harness.formal_attention_cli",
           "--suite", profile["suite"], "--task", profile["task"],
           "--seed", str(seed), "--attention-policy", policy,
           "--code", profile["code"], "--approved-policy-sha256", profile["approved_policy_sha256"],
           "--config", profile["config"], "--approved-config-sha256", profile["approved_config_sha256"],
           "--artifact-root", str(artifact_root.resolve()), "--store-path", str(store.path.resolve()),
           "--expected-entry-sha256", entry_lock["sha256"],
           "--max-attempts", str(selection["max_attempts"]),
           "--assistance-credits", str(selection["assistance_credits"]),
           "--token-limit", str(selection["token_limit"]),
           "--assistance-mode", mode.value,
           "--human-deadline-seconds", str(deadline)]
    cmd.append("--single-glm-call")
    cmd.extend(("--overall-deadline-seconds", str(overall)))
    for field in _ROOT_FLAGS[profile["suite"]]:
        cmd.extend(("--" + field.replace("_", "-"), profile[field]))
    if policy == "demo_first":
        cmd.extend(("--demo-prior", profile["demo_prior"],
                    "--approved-demo-sha256", profile["approved_demo_sha256"]))
    if policy == "budget_matched_random_escalation":
        cmd.extend(("--policy-config", profile["random_policy_config"],
                    "--approved-policy-config-sha256",
                    profile["approved_random_policy_config_sha256"]))
    artifact_root.mkdir(parents=True, exist_ok=True)
    log_path = artifact_root / f"{launch_id.replace(':', '-')}.log"
    summary_path = artifact_root / f"{launch_id.replace(':', '-')}.summary.json"
    cmd.extend(("--summary-path", str(summary_path.resolve())))
    worker_cmd = [sys.executable, "-m", "benchmarks.attention_harness.ui_launch_worker",
                  "--store-path", str(store.path.resolve()), "--launch-id", launch_id,
                  "--summary-path", str(summary_path.resolve()), "--", *cmd]
    locked = {**selection, "suite": profile["suite"], "task": profile["task"],
              "entry_lock": entry_lock,
              "approved_policy_sha256": profile["approved_policy_sha256"],
              "approved_config_sha256": profile["approved_config_sha256"],
              "approved_demo_sha256": profile.get("approved_demo_sha256") if policy == "demo_first" else None,
              "approved_random_policy_config_sha256": profile.get("approved_random_policy_config_sha256")
                  if policy == "budget_matched_random_escalation" else None,
              "formal_eligible": False}
    store.record_launch(launch_id, locked=locked, log_path=str(log_path.resolve()))
    try:
        with log_path.open("wb") as log:
            process = popen(worker_cmd, stdout=log, stderr=subprocess.STDOUT,
                            cwd=str(Path(__file__).resolve().parents[2]), start_new_session=True)
    except OSError as error:
        store.record_launch_failed(launch_id, reason=f"{type(error).__name__}: {error}"[:300])
        raise
    store.record_launch_started(launch_id, pid=process.pid)
    return {"launch_id": launch_id, "pid": process.pid, "locked": locked,
            "log_path": str(log_path.resolve())}
