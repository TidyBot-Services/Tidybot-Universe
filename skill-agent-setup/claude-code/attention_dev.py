"""Bounded model Dev for opt-in AttentionBench generated-policy graph nodes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from attention_eval import REPO_ROOT, ParccClient
from benchmarks.attention_harness.robocasa_native.policy_sandbox import (
    GeneratedPolicyError, validate_generated_policy,
)


def generate_policy(config: dict[str, Any], *, graph_dir: Path,
                    client: ParccClient | None = None, model: str = "parcc/GLM") -> dict[str, Any]:
    source_name = config.get("generated_policy_file")
    if not isinstance(source_name, str) or not source_name.endswith(".py"):
        raise ValueError("generated policy path is missing")
    source = (REPO_ROOT / source_name).resolve()
    allowed = (REPO_ROOT / "benchmarks/attention_harness/protocol/v2/policies").resolve()
    if not source.is_relative_to(allowed):
        raise ValueError("generated policy path escapes the development policy directory")
    if source.exists():
        raise FileExistsError("Dev policy already exists; one-shot generation will not overwrite it")
    if config.get("runner_boundary") not in {"generated_sandbox", "formal"} or config.get("allow_generated_sandbox") is not True:
        raise ValueError("bounded Dev requires explicit sandbox opt-in")
    task = config.get("task")
    if task not in {"cube_lift", "cube_stack", "counter_to_sink", "counter_to_cab"}:
        raise ValueError("unsupported smoke task")
    task_hint = (
        "For counter_to_sink, the task target name varies by scene and may be `yogurt`; "
        "do not assume `boxed_drink`. First open the gripper, then inspect "
        "sensors.find_objects() and attempt a conservative approach to the target if found. "
        "Do not claim task completion without moving the object. "
        if task == "counter_to_sink" else
        "For counter_to_cab, the target object is `condiment_bottle`; locate it with "
        "sensors.find_objects(['condiment_bottle']) and open the gripper before attempting "
        "a conservative approach. Do not claim task completion without moving the object. "
        if task == "counter_to_cab" else ""
    )
    prompt = (
        f"Write a short Python robot strategy for {config['suite']} task {task}. "
        "The simulator provides GT object names and world-frame positions through "
        "the public SDK. Return ONLY executable Python source, no Markdown. "
        "Allowed imports: `from robot_sdk import sensors, arm, gripper`. "
        "Allowed calls: sensors.find_objects(), arm.move_to_position(x,y,z), "
        "gripper.open(), gripper.close(). `find_objects()` returns a list of "
        "dicts with name and position=[x,y,z] (if no target, do nothing). "
        "For cube_lift, locate cube, open the gripper, move above the cube, "
        "move down near it, close, then raise. Use at most six SDK calls. "
        + task_hint +
        "Use exact `obj.get('name') == 'cube'` matching; do not call `.lower()` "
        "or any method other than `.get()`. No other imports, files, network, "
        "evaluator access, loops, helpers, or private state. Do not use hidden object pose. "
        "If you have a specific hypothesis about a possible failure, add one "
        "`# Hypothesis: ...` source comment; otherwise omit it."
    )
    model_client = client or ParccClient(timeout_seconds=75, max_attempts=1)
    messages = [{"role": "user", "content": prompt}]
    usage: dict[str, int] = {}
    responses: list[dict[str, Any]] = []
    provider_attempts = 0
    raw_path = graph_dir / "dev_generation_responses.json"

    def save_responses() -> None:
        raw_path.write_text(json.dumps({"prompt": prompt, "responses": responses},
                                       indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                            encoding="utf-8")

    for format_attempt in range(1, 3):
        try:
            response = model_client.chat(
                model=model, messages=messages,
                max_tokens=3072, temperature=0.0, reasoning_effort="low",
            )
        except Exception as exc:
            save_responses()
            (graph_dir / "dev_generation_failure.json").write_text(json.dumps({
                "model": model, "format_attempt": format_attempt,
                "error": f"{type(exc).__name__}: {exc}",
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "responses_artifact": str(raw_path.resolve()),
            }, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            raise
        attempts = response.attempts
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts != 1:
            raise ValueError("bounded Dev requires one provider attempt per format call")
        provider_attempts += attempts
        responses.append({"format_attempt": format_attempt, "content": response.content,
                          "usage": response.usage, "provider_attempts": attempts,
                          "request_id": getattr(response, "request_id", None),
                          "latency_seconds": getattr(response, "latency_seconds", None)})
        save_responses()
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = response.usage.get(key)
            if isinstance(value, int):
                usage[key] = usage.get(key, 0) + value
        code = response.content.strip()
        if code.startswith("```python"):
            code = code[len("```python"):].strip()
        elif code.startswith("```"):
            code = code[3:].strip()
        if code.endswith("```"):
            code = code[:-3].strip()
        code += "\n"
        try:
            validate_generated_policy(code)
            break
        except GeneratedPolicyError as exc:
            if format_attempt == 2:
                (graph_dir / "dev_generation_failure.json").write_text(json.dumps({
                    "model": model, "format_attempt": format_attempt,
                    "error": f"{type(exc).__name__}: {exc}",
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "responses_artifact": str(raw_path.resolve()),
                }, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
                raise
            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": (
                f"The sandbox rejected this code: {exc}. Return corrected Python "
                "source only, with no imports except robot_sdk and no method "
                "calls except `.get()`."
            )})
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(code, encoding="utf-8")
    # A hypothesis exists only when the actual Dev response included this
    # comment. Do not infer a diagnosis from the generated policy.
    match = re.search(r"(?m)^# Hypothesis: (.{1,500})$", code)
    hypothesis = match.group(1).strip() if match else "unknown"
    result = {
        "schema_version": "attentionbench.bounded-dev-generation.v1",
        "model": model, "source": str(source),
        "sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
        "usage": usage, "format_attempts": format_attempt,
        "provider_attempts": provider_attempts,
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "hypothesis": hypothesis,
        "responses_artifact": str(raw_path.resolve()),
        "responses_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
    }
    (graph_dir / "dev_generation.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result
