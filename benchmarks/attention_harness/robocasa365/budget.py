"""Hash-bound daily run budgets, separate from the frozen simulator contract."""
import hashlib
import json
from pathlib import Path

PHASE1_LIMITS = dict(max_attempts=2, assistance_credits=1, token_limit=8000,
                     human_deadline_seconds=30, overall_deadline_seconds=300)
DAILY_CONDITIONS = dict(schema_version="attentionbench.robocasa365-run-budget.v1",
                        suite="robocasa365", formal_eligible=False, max_attempts=10,
                        assistance_credits=1, token_limit=8000, human_deadline_seconds=30,
                        attempt_wall_seconds=120, whole_case_wall_seconds=300)
HELP9_CONDITIONS = {**DAILY_CONDITIONS,
                    "schema_version": "attentionbench.robocasa365-run-budget.v2",
                    "assistance_credits": 9}


def load_run_budget(path, digest, *, config_sha256, policy_sha256):
    if path is None and digest is None:
        return None
    if path is None or digest is None:
        raise ValueError("RoboCasa365 run budget requires a file and approved digest")
    raw = Path(path).read_bytes()
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("RoboCasa365 run budget differs from approved digest")
    value = json.loads(raw)
    if isinstance(value, dict) and value.get("schema_version") == "attentionbench.openarm365-run-budget.v1":
        from openarm365.budget import validate_run_budget
        return validate_run_budget(value,config_sha256,policy_sha256)
    if isinstance(value, dict) and value.get("schema_version") == "attentionbench.openarm365-formal-run-budget.v2":
        from openarm365_dev_v2.formal_config import validate_formal_run_budget
        return validate_formal_run_budget(value, config_sha256, policy_sha256)
    if not isinstance(value, dict) or set(value) != set(DAILY_CONDITIONS) | {"approved_config_sha256s", "approved_policy_sha256"}:
        raise ValueError("invalid RoboCasa365 daily run budget")
    conditions = {DAILY_CONDITIONS["schema_version"]: DAILY_CONDITIONS,
                  HELP9_CONDITIONS["schema_version"]: HELP9_CONDITIONS}.get(value.get("schema_version"))
    if conditions is None:
        raise ValueError("unsupported RoboCasa365 daily run budget version")
    for key, expected in conditions.items():
        if value[key] != expected or type(value[key]) is not type(expected):
            raise ValueError("RoboCasa365 daily run budget condition mismatch: " + key)
    configs = value["approved_config_sha256s"]
    if (not isinstance(configs, list) or len(configs) != 2 or len(set(configs)) != 2
            or config_sha256 not in configs or value["approved_policy_sha256"] != policy_sha256):
        raise ValueError("RoboCasa365 run budget is not bound to this config/policy")
    return value


def limits_for_budget(value):
    if value is not None and value["schema_version"] == "attentionbench.openarm365-formal-run-budget.v2":
        return {**{key: value[key] for key in ("max_attempts", "assistance_credits", "token_limit", "human_deadline_seconds")},
                "overall_deadline_seconds": value["whole_case_wall_seconds"]}
    if value is not None and value["schema_version"] == "attentionbench.openarm365-run-budget.v1":
        return {key:value[key] for key in PHASE1_LIMITS}
    limits = dict(PHASE1_LIMITS)
    if value is not None:
        limits["max_attempts"] = value["max_attempts"]
        limits["assistance_credits"] = value["assistance_credits"]
    return limits


def validate_budget_random_plans(plans, budget, *, seed):
    """Approve every selectable budget pair before any outcomes are observed."""
    if (budget is None or budget["schema_version"] != HELP9_CONDITIONS["schema_version"]
            or not isinstance(plans, dict)
            or set(plans) != {f"{n}:{c}" for n in range(1, budget["max_attempts"] + 1)
                              for c in range(budget["assistance_credits"] + 1)}):
        raise ValueError("daily random plans require every approved attempt/help budget")
    for key, reference in plans.items():
        if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
            raise ValueError("invalid daily random plan reference")
        path = Path(reference["path"])
        if not path.is_absolute() or not path.is_file():
            raise ValueError("daily random plan requires an existing absolute path")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
            raise ValueError("daily random plan differs from approved digest")
        attempts, credits = map(int, key.split(":"))
        expected = dict(target_request_count=min(credits, attempts - 1),
                        total_failure_slots=attempts - 1, seed=seed)
        value = json.loads(raw)
        if value != expected or any(type(value[k]) is not int for k in expected):
            raise ValueError("daily random plan disagrees with attempt/help budget or seed")
