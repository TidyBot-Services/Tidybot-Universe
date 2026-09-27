"""Opt-in bridge from a Skill DAG node to the v2 AttentionBench dev runner.

This intentionally accepts only an operator-approved importable policy.  The
v2 seven-policy CLI executes trusted Python in-process, so an agent-generated
script must not be sent here merely because it exists on disk.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


_POLICY_REF = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
_SUITES = {"robocasa", "robosuite"}


def build_attention_command(config: dict[str, Any], *, repo_root: Path) -> list[str]:
    """Validate an explicit graph-node contract and build a shell-free command."""
    suite = config.get("suite")
    task = config.get("task")
    boundary = config.get("runner_boundary", "trusted_dev")
    if boundary not in {"trusted_dev", "generated_sandbox", "formal"}:
        raise ValueError("unsupported Skill DAG runner boundary")
    if boundary == "trusted_dev" and config.get("approved_trusted_policy") is not True:
        raise ValueError("trusted_dev requires operator-approved policy")

    seed = config.get("seed")
    attention_policy = config.get("attention_policy")
    policy_ref = config.get("robot_policy")
    if suite not in _SUITES:
        raise ValueError("AttentionBench suite must be robocasa or robosuite")
    if not isinstance(task, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", task):
        raise ValueError("invalid AttentionBench task")
    if isinstance(seed, bool) or not isinstance(seed, int) or not 101 <= seed <= 125:
        raise ValueError("AttentionBench bridge accepts development seeds 101-125 only")
    if not isinstance(attention_policy, str):
        raise ValueError("missing attention policy")
    # Import here so the legacy Orchestrator can still load without the v2 venv.
    from benchmarks.attention_harness.core.policies import POLICY_IDS
    if attention_policy not in POLICY_IDS:
        raise ValueError("unknown AttentionBench decision policy")
    if boundary == "formal":
        return _build_formal_command(config, repo_root=repo_root)
    if boundary == "trusted_dev" and (
        not isinstance(policy_ref, str) or not _POLICY_REF.fullmatch(policy_ref)
    ):
        raise ValueError("robot_policy must be an importable module:function")
    root = repo_root.resolve()
    if not (root / "benchmarks" / "attention_harness" / "sim_gt_attention_cli.py").is_file():
        raise ValueError("AttentionBench runner missing from repo_root")
    artifact_root = Path(config.get("artifact_root", root / "artifacts" / "attentionbench-v2-attention"))
    store_path = Path(config.get("store_path", artifact_root / "attention_memory.sqlite3"))
    if not artifact_root.is_absolute():
        artifact_root = root / artifact_root
    if not store_path.is_absolute():
        store_path = root / store_path
    max_attempts = config.get("max_attempts", 3)
    credits = config.get("assistance_credits", 1)
    if (isinstance(max_attempts, bool) or not isinstance(max_attempts, int)
            or not 1 <= max_attempts <= 10):
        raise ValueError("max_attempts must be 1-10")
    if (isinstance(credits, bool) or not isinstance(credits, int) or not 0 <= credits <= 10):
        raise ValueError("assistance_credits must be 0-10")
    cmd = [
        sys.executable, "-m", "benchmarks.attention_harness.sim_gt_attention_cli",
        "--suite", suite, "--task", task, "--seed", str(seed),
        "--attention-policy", attention_policy,
        "--artifact-root", str(artifact_root.resolve()),
        "--store-path", str(store_path.resolve()),
        "--max-attempts", str(max_attempts),
        "--assistance-credits", str(credits),
    ]
    if boundary == "generated_sandbox":
        if config.get("allow_generated_sandbox") is not True:
            raise ValueError("generated_sandbox requires explicit graph opt-in")
        source_name = config.get("generated_policy_file")
        if not isinstance(source_name, str) or not source_name.endswith(".py"):
            raise ValueError("generated_policy_file must be a Python file")
        source = (root / source_name).resolve()
        allowed = (root / "benchmarks" / "attention_harness" / "protocol" / "v2" / "policies").resolve()
        if not source.is_relative_to(allowed) or not source.is_file():
            raise ValueError("generated policy must be an existing file under protocol/v2/policies")
        from benchmarks.attention_harness.robocasa_native.policy_sandbox import validate_generated_policy
        validate_generated_policy(source.read_text(encoding="utf-8"))
        cmd.extend(("--generated-policy-file", str(source)))
    else:
        cmd.extend(("--robot-policy", policy_ref))
    if suite == "robocasa":
        # An operator attests that the configured Agent Server is simulator-only.
        if config.get("confirm_simulator_agent") is not True:
            raise ValueError("RoboCasa bridge requires simulator Agent Server confirmation")
        cmd.append("--confirm-simulator-agent")
    for key, flag in (("sim_url", "--sim-url"), ("agent_url", "--agent-url"),
                      ("camera_name", "--camera-name"), ("hypothesis", "--hypothesis")):
        value = config.get(key)
        if value is not None:
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"invalid {key}")
            cmd.extend((flag, value))
    if config.get("record_replay") is True:
        cmd.append("--record-replay")
    return cmd


async def run_attention_job(config: dict[str, Any], *, repo_root: Path) -> dict[str, Any]:
    """Run one v2 dev sequence and return its persisted summary, including failures."""
    cmd = build_attention_command(config, repo_root=repo_root)
    if config.get("runner_boundary", "trusted_dev") == "trusted_dev":
        _verify_approved_policy(config, repo_root=repo_root)
    timeout = 330 * int(config.get("max_attempts", 3)) + 60
    process = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(repo_root.resolve()),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        raise TimeoutError("AttentionBench development sequence exceeded its deadline") from None
    if not stdout:
        raise RuntimeError(f"AttentionBench runner produced no summary: {stderr.decode(errors='replace')[-600:]}")
    try:
        result = json.loads(stdout)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("AttentionBench runner returned invalid JSON") from exc
    if (not isinstance(result, dict)
            or result.get("schema_version") != "attentionbench.sim-gt-attention-run.v1"
            or result.get("suite") != config["suite"]
            or result.get("task_id") != config["task"]
            or result.get("seed") != config["seed"]
            or result.get("formal_eligible") is not False
            or not isinstance(result.get("native_success"), bool)):
        raise RuntimeError("AttentionBench runner returned a mismatched summary")
    expected_boundary = config.get("runner_boundary", "trusted_dev")
    if (result.get("runner_boundary") or {}).get("mode") != expected_boundary:
        raise RuntimeError("AttentionBench runner boundary did not match graph")
    if expected_boundary == "generated_sandbox":
        source = Path(config["generated_policy_file"])
        if not source.is_absolute():
            source = repo_root / source
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        if result.get("generated_policy", {}).get("sha256") != digest:
            raise RuntimeError("generated policy changed during Harness execution")
    if expected_boundary == "formal":
        if (result.get("approved_policy_sha256") != config.get("approved_policy_sha256")
                or result.get("approved_config_sha256") != config.get("approved_config_sha256")
                or not result.get("attempts")):
            raise RuntimeError("formal run approval or attempt evidence mismatch")
        for attempt in result["attempts"]:
            formal = attempt.get("formal_runner_result", {})
            if (formal.get("boundary_checked") is not True
                    or formal.get("policy_sha256") != config["approved_policy_sha256"]
                    or formal.get("config_sha256") != config["approved_config_sha256"]
                    or formal.get("run_id") != attempt.get("attention_trace", {}).get("run_id")
                    or formal.get("attempt_id") != attempt.get("attention_trace", {}).get("attempt_id")):
                raise RuntimeError("formal attempt evidence mismatch")
    artifact = Path(result.get("artifact_dir", "")) / "attention_run.json"
    if not artifact.is_file() or json.loads(artifact.read_text()) != result:
        raise RuntimeError("AttentionBench persisted artifact does not match runner output")
    if expected_boundary == "formal":
        from attention_eval import build_eval_packet
        build_eval_packet(artifact)  # Verify every formal artifact before Memory/UI receive the run.
    # Exit 1 is the CLI's expected unsuccessful-task outcome. Other codes fail closed.
    if process.returncode not in (0, 1) or (process.returncode == 0) != result["native_success"]:
        raise RuntimeError(f"AttentionBench runner exit/result mismatch: {process.returncode}")
    return result


def _build_formal_command(config: dict[str, Any], *, repo_root: Path) -> list[str]:
    root = repo_root.resolve()
    if config.get("approved_generated_policy") is not True:
        raise ValueError("formal runner requires explicit post-Dev policy approval")
    source_name = config.get("generated_policy_file")
    if not isinstance(source_name, str) or not source_name.endswith(".py"):
        raise ValueError("formal policy source is missing")
    source = (root / source_name).resolve()
    allowed = (root / "benchmarks/attention_harness/protocol/v2/policies").resolve()
    if not source.is_relative_to(allowed) or not source.is_file():
        raise ValueError("formal policy must exist under protocol/v2/policies")
    config_name = config.get("formal_config_file")
    if not isinstance(config_name, str) or not config_name.endswith(".json"):
        raise ValueError("formal simulator config is missing")
    formal_config = (root / config_name).resolve()
    if not formal_config.is_relative_to(root / "benchmarks/attention_harness/protocol/v2"):
        raise ValueError("formal config must be inside protocol/v2")
    for path, field in ((source, "approved_policy_sha256"),
                        (formal_config, "approved_config_sha256")):
        approved = config.get(field)
        if (not path.is_file() or not isinstance(approved, str)
                or not re.fullmatch(r"[0-9a-f]{64}", approved)
                or hashlib.sha256(path.read_bytes()).hexdigest() != approved):
            raise ValueError(f"{field} differs from approved file")
    from benchmarks.attention_harness.robocasa_native.policy_sandbox import validate_generated_policy
    validate_generated_policy(source.read_text(encoding="utf-8"))
    artifact_root = (root / config.get("artifact_root", "artifacts/attentionbench-v2-attention")).resolve()
    store_path = (root / config.get("store_path", str(artifact_root / "attention_memory.sqlite3"))).resolve()
    attempts = config.get("max_attempts", 3)
    credits = config.get("assistance_credits", 1)
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 10:
        raise ValueError("max_attempts must be 1-10")
    if isinstance(credits, bool) or not isinstance(credits, int) or not 0 <= credits <= 10:
        raise ValueError("assistance_credits must be 0-10")
    token_limit = config.get("token_limit", 30_000)
    deadline = config.get("overall_deadline_seconds", 300)
    if isinstance(token_limit, bool) or not isinstance(token_limit, int) or not 1 <= token_limit <= 30_000:
        raise ValueError("token_limit must be 1-30000")
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not 1 <= deadline <= 300:
        raise ValueError("overall_deadline_seconds must be 1-300")
    if not isinstance(config.get("single_glm_call", False), bool):
        raise ValueError("single_glm_call must be boolean")
    cmd = [sys.executable, "-m", "benchmarks.attention_harness.formal_attention_cli",
           "--suite", config["suite"], "--task", config["task"],
           "--seed", str(config["seed"]), "--attention-policy", config["attention_policy"],
           "--code", str(source), "--approved-policy-sha256", config["approved_policy_sha256"],
           "--config", str(formal_config), "--approved-config-sha256", config["approved_config_sha256"],
           "--artifact-root", str(artifact_root), "--store-path", str(store_path),
           "--max-attempts", str(attempts), "--assistance-credits", str(credits),
           "--token-limit", str(token_limit), "--overall-deadline-seconds", str(deadline)]
    if config.get("single_glm_call") is True:
        cmd.append("--single-glm-call")
    roots = (("robosuite", ("service_source_root",))
             if config["suite"] == "robosuite" else
             ("robocasa", ("sim_source_root", "agent_source_root", "task_source_root",
                            "sim_python", "agent_python")))
    for name in roots[1]:
        value = config.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"formal runner requires {name}")
        path = Path(value).resolve()
        if not path.exists():
            raise ValueError(f"formal runner path missing: {name}")
        cmd.extend(("--" + name.replace("_", "-"), str(path)))
    for file_key, digest_key, flag in (
        ("memory_context_file", "approved_memory_context_sha256", "--memory-context"),
        ("memory_source_run_file", "approved_memory_source_sha256", "--memory-source-run"),
        ("demo_prior_file", "approved_demo_sha256", "--demo-prior"),
        ("policy_config_file", "approved_policy_config_sha256", "--policy-config"),
    ):
        name = config.get(file_key)
        if name is None:
            continue
        if not isinstance(name, str):
            raise ValueError(f"invalid {file_key}")
        path = (root / name).resolve()
        expected = config.get(digest_key)
        if (not path.is_file() or not isinstance(expected, str)
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected):
            raise ValueError(f"{file_key} differs from approved digest")
        cmd.extend((flag, str(path)))
        if file_key == "memory_context_file":
            cmd.extend(("--approved-memory-context-sha256", expected))
        if file_key == "memory_source_run_file":
            cmd.extend(("--approved-memory-source-sha256", expected))
    if config["attention_policy"] == "demo_first" and "demo_prior_file" not in config:
        raise ValueError("formal demo_first requires an approved demo prior")
    if config.get("dev_generation_artifact") is not None:
        path = Path(config["dev_generation_artifact"]).resolve()
        generation = json.loads(path.read_text(encoding="utf-8"))
        if (generation.get("sha256") != config["approved_policy_sha256"]
                or generation.get("source") != str(source)):
            raise ValueError("Dev generation receipt differs from approved source")
        cmd.extend(("--dev-generation-artifact", str(path)))
    return cmd

def _verify_approved_policy(config: dict[str, Any], *, repo_root: Path) -> None:
    """Require post-edit approval of the exact importable policy source."""
    policy_ref = config.get("robot_policy", "")
    if not isinstance(policy_ref, str) or not _POLICY_REF.fullmatch(policy_ref):
        raise ValueError("invalid policy reference")
    module = policy_ref.partition(":")[0]
    root = repo_root.resolve()
    source = root.joinpath(*module.split(".")).with_suffix(".py")
    if not source.is_file() or not source.resolve().is_relative_to(root):
        raise ValueError("approved policy source must be a file inside the Universe checkout")
    approved = config.get("approved_policy_sha256")
    if not isinstance(approved, str) or not re.fullmatch(r"[0-9a-f]{64}", approved):
        raise ValueError("post-Dev approved_policy_sha256 is required")
    actual = hashlib.sha256(source.read_bytes()).hexdigest()
    if actual != approved:
        raise ValueError("policy source changed after approval; review the new digest")
