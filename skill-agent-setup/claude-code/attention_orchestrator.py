"""Opt-in AttentionBench adapter around the existing Skill DAG Orchestrator.

Run this entrypoint instead of agent_orchestrator.py for graphs containing
``entry.attentionbench``. Ordinary Skill DAG entries keep their old pipeline.
The native simulator evaluator is the only success authority; the Eval Agent
provides diagnostics and cannot turn a failure into a pass.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path

import agent_orchestrator as orch
from attention_dev import generate_policy
from attention_eval import diagnose_attention
from attentionbench_bridge import run_attention_job
from dev_memory_bridge import record_dev_memory_result


REPO_ROOT = Path(__file__).resolve().parents[2]
_legacy_done = orch._handle_agent_done
_legacy_prompt = orch._get_system_prompt
_legacy_spawn = orch.spawn_agent


def _attention_config(skill: str) -> dict | None:
    entry = orch._find_entry(skill)
    if entry is None:
        return None
    config = entry.get("attentionbench")
    if config is None:
        return None
    if not isinstance(config, dict):
        raise ValueError("entry.attentionbench must be an object")
    return config


def _dev_prompt(agent_type: str, skill_name: str = "", agent_server_url: str = "") -> str:
    config = _attention_config(skill_name) if skill_name else None
    if agent_type == "dev" and config and config.get("runner_boundary") in {"generated_sandbox", "formal"}:
        policy_path = (REPO_ROOT / str(config.get("generated_policy_file", ""))).resolve()
        return (
            "You are the AttentionBench Dev Agent. Write one Python robot strategy "
            f"only to `{policy_path}` in the Universe checkout. "
            f"The task is {config.get('suite')} / {config.get('task')} with "
            "sim_gt perception. The strategy runs in a separate restricted process. "
            "It may `from robot_sdk import sensors, arm, gripper` and call "
            "sensors.find_objects(), arm.move_to_position(x,y,z), gripper.open(), "
            "gripper.close(). No other imports, files, network, private APIs, "
            "or simulator evaluator access. Keep it short and bounded; no loops "
            "without a small fixed limit. Do not edit graph metadata or other files. "
            "The operator must approve the exact source hash before a formal Harness run. Report the file path "
            "and your intended strategy, then end your turn."
        )
    prompt = _legacy_prompt(agent_type, skill_name, agent_server_url)
    if agent_type != "dev" or config is None:
        return prompt
    return prompt + (
        "\n\n## AttentionBench v2 development contract\n"
        f"This node is an opt-in {config.get('suite')} / {config.get('task')} "
        f"AttentionBench run using {config.get('attention_policy')}. "
        f"The runner imports `{config.get('robot_policy')}` as a trusted "
        "module:function. Work only on the operator-approved policy module; "
        "do not change graph approval fields or claim formal eligibility. "
        "The Harness controls attempts, Advisor requests and Memory access; "
        "policy code may use only the public SDK/context it receives. "
        "After you finish, the Orchestrator will run the v2 development sequence "
        "and return native-outcome plus diagnostic feedback.\n"
    )


async def _diagnose_attention(skill: str, artifact: Path) -> str:
    """Run one tool-free, bounded diagnostic model call over persisted evidence."""
    del skill
    result = await asyncio.to_thread(diagnose_attention, artifact)
    return str(result["diagnosis"])


async def _spawn_attention(skill: str, prompt: str, agent_type: str = "dev",
                           target: dict | None = None) -> str:
    """Dispatch opt-in generated policy Dev through a one-call bounded model path."""
    config = _attention_config(skill) if agent_type == "dev" else None
    if config is None or config.get("runner_boundary") not in {"generated_sandbox", "formal"}:
        return await _legacy_spawn(skill, prompt, agent_type=agent_type, target=target)
    if not orch.dev_mode:
        return ""
    agent_id = f"attention-dev-{uuid.uuid4().hex[:8]}"
    state = orch.AgentState(agent_id=agent_id, skill=skill, agent_type="dev", status="running")
    state.target_name = target["name"] if target else ""
    orch.agents[agent_id] = state

    async def run() -> None:
        try:
            await orch.ws_broadcast_status(skill, agent_id, "running", "Generating bounded policy")
            source = (REPO_ROOT / str(config.get("generated_policy_file", ""))).resolve()
            if config.get("runner_boundary") == "formal" and source.is_file():
                from attentionbench_bridge import _build_formal_command
                _build_formal_command(config, repo_root=REPO_ROOT)
                state.log.append({"text": f"Approved policy {source} ({config['approved_policy_sha256']})",
                                  "role": "dev"})
            else:
                generated = await asyncio.to_thread(
                    generate_policy, config, graph_dir=orch.GRAPH_DIR,
                )
                orch._update_entry(skill, {"dev_generation": generated})
                state.log.append({"text": f"Generated policy {generated['source']} ({generated['sha256']})",
                                  "role": "dev"})
            state.status = "done"
            await orch._handle_agent_done(state)
        except Exception as exc:
            state.status = "error"
            reason = f"Bounded Dev unavailable: {type(exc).__name__}: {exc}"[:600]
            orch._update_entry(skill, {"status": "review", "attentionbench_error": reason})
            await orch.ws_broadcast_agent_msg(skill, reason, "dev")
            await orch.broadcast_full_sync()

    state.task = asyncio.create_task(run())
    return agent_id


async def _handle_attention_done(state) -> None:
    if state.agent_type != "dev":
        await _legacy_done(state)
        return
    try:
        config = _attention_config(state.skill)
    except ValueError as exc:
        orch._update_entry(state.skill, {"status": "review", "attentionbench_error": str(exc)})
        await orch.broadcast_full_sync()
        return
    if config is None:
        await _legacy_done(state)
        return
    if state.skill in orch._skills_in_test_loop:
        return
    orch._update_entry(state.skill, {"status": "evaluating"})
    await orch.broadcast_full_sync()
    try:
        entry_before_run = orch._find_entry(state.skill) or {}
        generation = entry_before_run.get("dev_generation")
        if (isinstance(generation, dict)
                and generation.get("sha256") == config.get("approved_policy_sha256")
                and generation.get("source")
                and (orch.GRAPH_DIR / "dev_generation.json").is_file()):
            config = {**config, "dev_generation_artifact": str(orch.GRAPH_DIR / "dev_generation.json")}
        result = await run_attention_job(config, repo_root=REPO_ROOT)
    except Exception as exc:
        # A missing service, invalid graph contract or timeout is an
        # infrastructure/development failure, never a benchmark result.
        reason = f"AttentionBench runner unavailable: {type(exc).__name__}: {exc}"[:600]
        orch._last_feedback[state.skill] = reason
        orch._update_entry(state.skill, {
            "status": "review", "attentionbench_error": reason,
        })
        await orch.ws_broadcast_agent_msg(state.skill, reason, "evaluator")
        await orch.broadcast_full_sync()
        return

    artifact = Path(result["artifact_dir"]) / "attention_run.json"
    entry = orch._find_entry(state.skill) or {}
    exposure = entry.get("dev_memory_exposure")
    if exposure is not None:
        try:
            if not isinstance(exposure, dict):
                raise ValueError("invalid Dev Memory exposure pointer")
            url = orch.graph_meta.get("memory_service_url")
            if not isinstance(url, str) or not url.strip():
                raise ValueError("Dev Memory Service URL is missing")
            record_dev_memory_result(
                pointer=exposure,
                run_id=f"run:{Path(result['artifact_dir']).name}",
                artifact=artifact, policy_ref=config["robot_policy"],
                repo_root=REPO_ROOT, memory_service_url=url,
            )
        except Exception as exc:
            reason = f"Dev Memory result evidence unavailable: {type(exc).__name__}: {exc}"[:600]
            orch._update_entry(state.skill, {"status": "review", "attentionbench_error": reason})
            await orch.broadcast_full_sync()
            return
    run_link = {
        "artifact": str(artifact), "store": result["store"],
        "run_id": f"run:{Path(result['artifact_dir']).name}",
        "native_success": result["native_success"],
        "formal_eligible": False, "suite": result["suite"],
        "task_id": result["task_id"], "seed": result["seed"],
        "runner_boundary": result.get("runner_boundary", {}).get("mode"),
        "approved_policy_sha256": result.get("approved_policy_sha256"),
        "approved_config_sha256": result.get("approved_config_sha256"),
            "attempt_ids": [item["attention_trace"]["attempt_id"] for item in result.get("attempts", [])],
            "formal_result_refs": [item["formal_runner_result"]["artifact_dir"]
                                   for item in result.get("attempts", []) if "formal_runner_result" in item],
            "advisor_request_ids": [item["request_id"] for item in result.get("requests", [])],
            "memory_use_ids": [memory_id for item in result.get("attempts", [])
                               for memory_id in item.get("memory_ids", [])],
            "decision_count": len(result.get("decisions", [])),
    }
    orch._update_entry(state.skill, {
        "attentionbench_last_run": run_link, "attentionbench_error": "",
    })
    await orch.ws_broadcast_agent_msg(
        state.skill, f"Native outcome: {result['native_success']}; artifact: {artifact}", "test",
    )
    try:
        diagnosis = await _diagnose_attention(state.skill, artifact)
        eval_status = "completed"
    except Exception as exc:
        diagnosis = f"Diagnostic Eval Agent unavailable: {type(exc).__name__}: {exc}"
        eval_status = "failed"
    eval_artifact = artifact.parent / "eval_diagnosis.json"
    if eval_artifact.is_file():
        run_link["eval_diagnosis"] = str(eval_artifact)
    orch._update_entry(state.skill, {
        "attentionbench_last_run": run_link,
        "attentionbench_eval_status": eval_status,
        "attentionbench_error": "" if eval_status == "completed" else diagnosis[:600],
    })
    # The model's text is feedback only; do not parse a pass/fail claim.
    feedback = (
        f"Native success={result['native_success']}; "
        f"stop={result.get('stopped_reason')}; artifact={artifact}. "
        f"Diagnostic: {diagnosis}"
    )[:4000]
    orch._last_feedback[state.skill] = feedback[:500]
    state.log.append({"text": feedback, "role": "evaluator"})
    await orch.ws_broadcast_agent_msg(state.skill, feedback, "evaluator")
    if result["native_success"]:
        if orch.autonomous_mode:
            await orch._confirm_skill_done(state.skill)
        else:
            orch._update_entry(state.skill, {"status": "review"})
            await orch.broadcast_full_sync()
        return
    # A failed native attempt goes to review; existing graph retry/approval
    # controls remain available without silently consuming another sim seed.
    orch._update_entry(state.skill, {"status": "review"})
    await orch.broadcast_full_sync()


def install() -> None:
    orch._handle_agent_done = _handle_attention_done
    orch._get_system_prompt = _dev_prompt
    orch.spawn_agent = _spawn_attention


if __name__ == "__main__":
    install()
    asyncio.run(orch.main())
