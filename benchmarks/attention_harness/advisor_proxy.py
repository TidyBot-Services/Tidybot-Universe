"""Reproducible AdvisorProxy prompt and cache identity for D7."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


ADVISOR_MODEL = "parcc/Qwen"
ADVISOR_SYSTEM_PROMPT = """You are the fixed TidyBot AdvisorProxy.
Use only the supplied trace packet, public camera evidence, failure history, and
agent hypothesis. Never request or infer simulator object poses, hidden success
state, segmentation IDs, or privileged physics state. Return one concise,
actionable hint. Do not execute robot code."""

FORBIDDEN_TRACE_KEYS = {
    "cube_pos",
    "cubea_pos",
    "cubeb_pos",
    "object_pose",
    "object_poses",
    "native_success",
    "oracle_state",
    "segmentation_id",
    "simulator_state",
}


def _check_public(value: Any, path: str = "trace_packet") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower()
            if normalized in FORBIDDEN_TRACE_KEYS:
                raise ValueError(f"privileged AdvisorProxy field is forbidden: {path}.{key}")
            _check_public(nested, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _check_public(nested, f"{path}[{index}]")


def build_advisor_request(
    *,
    request_type: str,
    trace_packet: Mapping[str, Any],
) -> dict[str, Any]:
    if request_type not in {"hint", "approval", "interrupt"}:
        raise ValueError("request_type must be hint, approval, or interrupt")
    _check_public(trace_packet)
    return {
        "model": ADVISOR_MODEL,
        "temperature": 0.0,
        "messages": [
            {"role": "system", "content": ADVISOR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"request_type": request_type, "trace_packet": trace_packet},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ],
    }


def advisor_cache_key(request: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        request, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
