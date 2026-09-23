"""PARCC GLM transport and strict response contract for AdvisorProxy."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

from .advisor_proxy import ADVISOR_MODEL
from .core.advisor import AdvisorTransportReply
from .parcc_client import ParccClient


ADVICE_SCHEMA_VERSION = "attentionbench.advisor-advice.v1"
ADVICE_KEYS = {
    "schema_version",
    "request_type",
    "diagnosis",
    "guidance",
    "caution",
    "confidence",
}


@dataclass(frozen=True)
class AdvisorAdvice:
    request_type: str
    diagnosis: str
    guidance: str
    caution: str
    confidence: float
    schema_version: str = ADVICE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.request_type not in {"hint", "approval", "interrupt"}:
            raise ValueError("unsupported Advisor request_type")
        for value, name in (
            (self.diagnosis, "diagnosis"),
            (self.guidance, "guidance"),
            (self.caution, "caution"),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Advisor {name} must be a non-empty string")
            if len(value) > 2000:
                raise ValueError(f"Advisor {name} exceeds 2000 characters")
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence, (int, float)
        ):
            raise TypeError("Advisor confidence must be numeric")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("Advisor confidence must be between zero and one")
        if self.schema_version != ADVICE_SCHEMA_VERSION:
            raise ValueError("unsupported Advisor advice schema")

    def artifact(self) -> dict[str, Any]:
        value = asdict(self)
        value["confidence"] = float(self.confidence)
        return value

    def canonical_json(self) -> str:
        return json.dumps(
            self.artifact(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


def parse_advisor_advice(content: str, *, request_type: str) -> AdvisorAdvice:
    """Validate GLM output before it can enter an Agent retry context."""

    if not isinstance(content, str) or not content.strip():
        raise ValueError("Advisor response is empty")
    candidate = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, re.DOTALL | re.IGNORECASE)
    if fenced:
        candidate = fenced.group(1)
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("Advisor response is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Advisor response must be one JSON object")
    if set(value) != ADVICE_KEYS:
        missing = sorted(ADVICE_KEYS - set(value))
        extra = sorted(set(value) - ADVICE_KEYS)
        raise ValueError(
            f"Advisor response keys do not match schema; missing={missing}, extra={extra}"
        )
    if value["request_type"] != request_type:
        raise ValueError("Advisor response request_type does not match the request")
    return AdvisorAdvice(
        schema_version=value["schema_version"],
        request_type=value["request_type"],
        diagnosis=value["diagnosis"],
        guidance=value["guidance"],
        caution=value["caution"],
        confidence=value["confidence"],
    )


class ParccGLMAdvisorTransport:
    """Adapt PARCC's OpenAI-compatible GLM endpoint to AdvisorProxy."""

    def __init__(self, client: ParccClient | None = None) -> None:
        self.client = client or ParccClient()

    def __call__(self, request: dict[str, Any]) -> AdvisorTransportReply:
        model = str(request.get("model"))
        if model != ADVISOR_MODEL:
            raise ValueError(f"Advisor model must remain frozen to {ADVISOR_MODEL}")
        request_type = _request_type(request)
        response = self.client.chat(
            model=model,
            messages=list(request["messages"]),
            max_tokens=int(request["max_tokens"]),
            temperature=float(request["temperature"]),
            reasoning_effort=str(request["reasoning_effort"]),
        )
        advice = parse_advisor_advice(response.content, request_type=request_type)
        return AdvisorTransportReply(
            content=advice.canonical_json(),
            model=response.model,
            latency_seconds=response.latency_seconds,
            attempts=response.attempts,
            usage=response.usage,
            request_id=response.request_id,
        )


def _request_type(request: dict[str, Any]) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("Advisor request has no messages")
    user = next(
        (
            item
            for item in reversed(messages)
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        ),
        None,
    )
    if not isinstance(user, dict) or not isinstance(user.get("content"), str):
        raise ValueError("Advisor request has no serialized user payload")
    try:
        payload = json.loads(user["content"])
    except json.JSONDecodeError as exc:
        raise ValueError("Advisor request payload is not valid JSON") from exc
    request_type = payload.get("request_type") if isinstance(payload, dict) else None
    if request_type not in {"hint", "approval", "interrupt"}:
        raise ValueError("Advisor request payload has an invalid request_type")
    return request_type
