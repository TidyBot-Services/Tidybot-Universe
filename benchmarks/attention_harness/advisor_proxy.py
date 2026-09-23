"""Reproducible low-intelligence Advisor prompt and cache identity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


ADVISOR_MODEL = "parcc/GLM"
ADVISOR_MAX_TOKENS = 512
ADVISOR_REASONING_EFFORT = "low"
ADVISOR_SYSTEM_PROMPT = """You are the fixed TidyBot AdvisorProxy.
Use only the supplied trace packet, public camera evidence, failure history, and
agent hypothesis. Never request or infer simulator object poses, hidden success
state, segmentation IDs, or privileged physics state. Do not execute robot code.

Return exactly one JSON object with these keys:
  schema_version: "attentionbench.advisor-advice.v1"
  request_type: the supplied request_type
  diagnosis: a concise explanation grounded in the visible trace
  guidance: one actionable instruction for the next attempt
  caution: a concise safety or uncertainty note
  confidence: a number from 0.0 to 1.0
Do not wrap the JSON in Markdown and do not add other keys."""

FORBIDDEN_TRACE_KEYS = {
    "cube_pos",
    "cubea_pos",
    "cubeb_pos",
    "done",
    "evaluator_authoritative",
    "evaluator_verdict",
    "is_success",
    "object_pose",
    "object_poses",
    "native_success",
    "oracle_state",
    "reward",
    "segmentation_id",
    "simulator_state",
    "success",
    "task_completed",
}

FORBIDDEN_TRACE_MARKERS = (
    "api_key",
    "credential",
    "oracle",
    "password",
    "privileged",
    "secret",
    "token",
)


def _check_public(value: Any, path: str = "trace_packet") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in FORBIDDEN_TRACE_KEYS or any(
                marker in normalized for marker in FORBIDDEN_TRACE_MARKERS
            ):
                raise ValueError(f"privileged AdvisorProxy field is forbidden: {path}.{key}")
            _check_public(nested, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _check_public(nested, f"{path}[{index}]")


def build_advisor_request(
    *,
    request_type: str,
    trace_packet: Mapping[str, Any],
    public_images: Sequence[str] = (),
) -> dict[str, Any]:
    if request_type not in {"hint", "approval", "interrupt"}:
        raise ValueError("request_type must be hint, approval, or interrupt")
    _check_public(trace_packet)
    for image in public_images:
        if not isinstance(image, str) or not image.startswith("data:image/png;base64,"):
            raise ValueError("Advisor image must be a public PNG data URL")
        if len(image) > 2_000_000:
            raise ValueError("Advisor image exceeds the 2 MB request limit")
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": ADVISOR_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"request_type": request_type, "trace_packet": trace_packet},
                sort_keys=True,
                separators=(",", ":"),
            ),
        },
    ]
    if public_images:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Public initial and final camera frames, in order."},
                    *(
                        {"type": "image_url", "image_url": {"url": value}}
                        for value in public_images
                    ),
                ],
            }
        )
    return {
        "model": ADVISOR_MODEL,
        "temperature": 0.0,
        "max_tokens": ADVISOR_MAX_TOKENS,
        "reasoning_effort": ADVISOR_REASONING_EFFORT,
        "messages": messages,
    }


def advisor_cache_key(request: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
