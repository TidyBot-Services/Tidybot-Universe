"""Fail-closed M2 graph approval and one-dispatch identity checks."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from benchmarks.attention_harness.formal_entry import inspect_formal_entry


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_task_lock(config: dict, *, graph_dir: Path, repo_root: Path) -> dict:
    root = repo_root.resolve()
    allowed_tasks = {"robosuite": {"cube_lift", "cube_stack"},
                     "robocasa": {"counter_to_sink", "counter_to_cab"}}
    if (config.get("task") not in allowed_tasks.get(config.get("suite"), set())
            or isinstance(config.get("seed"), bool)
            or not isinstance(config.get("seed"), int)
            or not 101 <= config["seed"] <= 125):
        raise ValueError("M2 suite, task or development seed is invalid")
    name = config.get("m2_task_lock_file")
    if not isinstance(name, str):
        raise ValueError("M2 locked task is missing")
    path = Path(name).resolve()
    if not path.is_relative_to(graph_dir.resolve()) or not path.is_file():
        raise ValueError("M2 task lock escapes graph or is missing")
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {key: config.get(key) for key in (
        "suite", "task", "seed", "attention_policy", "generated_policy_file",
        "formal_config_file", "max_attempts", "assistance_credits", "token_limit",
        "human_deadline_seconds", "overall_deadline_seconds")}
    sim_config = (root / config["formal_config_file"]).resolve()
    policy = (root / config["generated_policy_file"]).resolve()
    protocol = root / "benchmarks/attention_harness/protocol/v2"
    if (not sim_config.is_relative_to(protocol)
            or not policy.is_relative_to(protocol / "policies")):
        raise ValueError("M2 locked task path escapes protocol")
    expected["config_sha256"] = digest(sim_config)
    if (data.get("schema_version") != "attentionbench.m2-task-lock.v1"
            or any(data.get(key) != value for key, value in expected.items())
            or data.get("sha256") != config.get("m2_task_lock_sha256")
            or data.get("sha256") != hashlib.sha256(json.dumps(
                {k: v for k, v in data.items() if k != "sha256"},
                sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")).hexdigest()):
        raise ValueError("M2 locked task identity changed")
    return data


def candidate(config: dict, *, graph_dir: Path, repo_root: Path) -> dict:
    root = repo_root.resolve()
    require_task_lock(config, graph_dir=graph_dir, repo_root=root)
    source = (root / config["generated_policy_file"]).resolve()
    sim_config = (root / config["formal_config_file"]).resolve()
    if not source.is_relative_to(root / "benchmarks/attention_harness/protocol/v2/policies"):
        raise ValueError("M2 source path escapes policy directory")
    if not sim_config.is_relative_to(root / "benchmarks/attention_harness/protocol/v2"):
        raise ValueError("M2 config path escapes protocol directory")
    receipt = (graph_dir / "dev_generation.json").resolve()
    if not receipt.is_relative_to(graph_dir.resolve()):
        raise ValueError("M2 receipt path escapes graph")
    generation = json.loads(receipt.read_text(encoding="utf-8"))
    raw_path = Path(generation.get("responses_artifact", "")).resolve()
    if not raw_path.is_relative_to(graph_dir.resolve()) or not raw_path.is_file():
        raise ValueError("M2 raw Dev response path escapes graph or is missing")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    responses = raw.get("responses", [])
    if not isinstance(responses, list) or not 1 <= len(responses) <= 2:
        raise ValueError("M2 raw Dev response count is invalid")
    last = responses[-1].get("content", "").strip()
    if last.startswith("```python"):
        last = last[len("```python"):].strip()
    elif last.startswith("```"):
        last = last[3:].strip()
    if last.endswith("```"):
        last = last[:-3].strip()
    if last + "\n" != source.read_text(encoding="utf-8"):
        raise ValueError("M2 raw Dev response differs from source")
    usage: dict[str, int] = {}
    for response in responses:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = response.get("usage", {}).get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                usage[key] = usage.get(key, 0) + value
    match = re.search(r"(?m)^# Hypothesis: (.{1,500})$", last + "\n")
    hypothesis = match.group(1).strip() if match else "unknown"
    if (generation.get("schema_version") != "attentionbench.bounded-dev-generation.v1"
            or generation.get("source") != str(source)
            or generation.get("sha256") != digest(source)
            or generation.get("model") != "parcc/GLM"
            or generation.get("usage") != usage
            or generation.get("hypothesis") != hypothesis
            or generation.get("format_attempts") != len(responses)
            or generation.get("provider_attempts") != len(responses)
            or any(item.get("provider_attempts") != 1
                   or item.get("format_attempt") != index
                   for index, item in enumerate(responses, 1))
            or generation.get("prompt_sha256") != hashlib.sha256(
                raw.get("prompt", "").encode("utf-8")).hexdigest()
            or generation.get("responses_sha256") != digest(raw_path)):
        raise ValueError("M2 generation receipt differs from source or bounded GLM contract")
    lock, _ = inspect_formal_entry(
        suite=config["suite"], task_id=config["task"], seed=config["seed"],
        policy_id=config["attention_policy"], code=source,
        approved_policy_sha256=digest(source), config=sim_config,
        approved_config_sha256=digest(sim_config),
        max_attempts=config.get("max_attempts", 3),
        assistance_credits=config.get("assistance_credits", 1),
        token_limit=config.get("token_limit", 30000),
        assistance_mode=config.get("assistance_mode", "benchmark_proxy"),
        human_deadline_seconds=config.get("human_deadline_seconds", 60),
        overall_deadline_seconds=config.get("overall_deadline_seconds", 300),
    )
    lock_path = graph_dir / f"m2_entry_lock_{config['suite']}.json"
    if lock_path.exists() and json.loads(lock_path.read_text(encoding="utf-8")) != lock:
        raise ValueError("M2 entry lock changed after generation")
    if not lock_path.exists():
        lock_path.write_text(json.dumps(lock, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                             encoding="utf-8")
    return {
        "schema_version": "attentionbench.m2-candidate.v1",
        "suite": config["suite"], "task": config["task"], "seed": config["seed"],
        "source": str(source), "source_sha256": digest(source),
        "config": str(sim_config), "config_sha256": digest(sim_config),
        "generation_receipt": str(receipt), "generation_sha256": digest(receipt),
        "entry_lock": str(lock_path), "entry_sha256": lock["sha256"],
    }


def require_approval(config: dict, *, graph_dir: Path, repo_root: Path,
                     expected: dict | None = None) -> dict:
    current = candidate(config, graph_dir=graph_dir, repo_root=repo_root)
    if expected is None or current != expected:
        raise ValueError("M2 candidate identity changed")
    approval_name = config.get("m2_approval_file")
    if not isinstance(approval_name, str) or not approval_name:
        raise ValueError("M2 awaits explicit human approval")
    approval_path = Path(approval_name).resolve()
    # The operator ledger lives outside the agent-writable graph and policy tree.
    if approval_path.is_relative_to(repo_root.resolve()) or not approval_path.is_file():
        raise ValueError("M2 approval must be an external operator record")
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if (approval.get("schema_version") != "attentionbench.m2-human-approval.v1"
            or approval.get("decision") != "approve"
            or not isinstance(approval.get("operator"), str)
            or not approval["operator"].strip()
            or not isinstance(approval.get("approved_at"), str)
            or not approval["approved_at"].strip()
            or not isinstance(approval.get("approval_reference"), str)
            or not approval["approval_reference"].strip()
            or any(approval.get(key) != current[key] for key in
                   ("suite", "task", "seed", "source_sha256", "config_sha256",
                    "generation_sha256", "entry_sha256"))):
        raise ValueError("M2 human approval identity mismatch")
    if (config.get("approved_generated_policy") is not True
            or config.get("approved_policy_sha256") != current["source_sha256"]
            or config.get("approved_config_sha256") != current["config_sha256"]):
        raise ValueError("M2 graph approval fields differ from operator record")
    return {"record": str(approval_path), "record_sha256": digest(approval_path),
            "operator": approval["operator"], "approved_at": approval["approved_at"],
            "candidate": current}


def validate_handoff(config: dict, result: dict) -> None:
    """Tie one persisted formal result and each attempt to the M2 approval."""
    expected = config.get("m2_candidate")
    if not isinstance(expected, dict):
        raise ValueError("M2 handoff lacks the frozen candidate")
    lock = result.get("entry_lock")
    if (not isinstance(lock, dict)
            or lock != json.loads(Path(expected["entry_lock"]).read_text(encoding="utf-8"))
            or lock.get("sha256") != expected.get("entry_sha256")
            or lock.get("approved_policy_sha256") != expected.get("source_sha256")
            or lock.get("approved_config_sha256") != expected.get("config_sha256")
            or result.get("suite") != expected.get("suite")
            or result.get("task_id") != expected.get("task")
            or result.get("seed") != expected.get("seed")
            or result.get("approved_policy_sha256") != expected.get("source_sha256")
            or result.get("approved_config_sha256") != expected.get("config_sha256")
            or result.get("scheduler_config", {}).get("entry_sha256")
               != expected.get("entry_sha256")
            or result.get("runner_boundary", {}).get("mode") != "formal"
            or result.get("formal_eligible") is not False):
        raise ValueError("M2 formal result differs from approved lock")
    attempts = result.get("attempts")
    if not isinstance(attempts, list) or len(attempts) != 1:
        raise ValueError("M2 handoff requires exactly one formal attempt")
    attempt = attempts[0]
    formal = attempt.get("formal_runner_result", {})
    trace = attempt.get("attention_trace", {})
    if (formal.get("entry_lock") != lock
            or formal.get("policy_sha256") != expected.get("source_sha256")
            or formal.get("config_sha256") != expected.get("config_sha256")
            or formal.get("run_id") != trace.get("run_id")
            or formal.get("attempt_id") != trace.get("attempt_id")
            or not isinstance(trace.get("run_id"), str)
            or not isinstance(trace.get("attempt_id"), str)):
        raise ValueError("M2 formal attempt differs from approved lock or run identity")
