from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmarks.attention_harness.advisor_proxy import build_advisor_request
from benchmarks.attention_harness.parcc_advisor import (
    ADVICE_SCHEMA_VERSION,
    ParccGLMAdvisorTransport,
    parse_advisor_advice,
)
from benchmarks.attention_harness.parcc_client import ParccResponse


def _advice() -> str:
    return json.dumps(
        {
            "schema_version": ADVICE_SCHEMA_VERSION,
            "request_type": "hint",
            "diagnosis": "The visible grasp attempt missed the cube.",
            "guidance": "Align over the cube in RGB-D before closing the gripper.",
            "caution": "The exact cause is uncertain from the trace.",
            "confidence": 0.6,
        }
    )


class FakeParccClient:
    def __init__(self) -> None:
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return ParccResponse(
            model=kwargs["model"],
            content=_advice(),
            reasoning=None,
            latency_seconds=0.25,
            attempts=1,
            usage={"total_tokens": 17},
            request_id="parcc-request-1",
        )


def test_glm_transport_uses_frozen_request_and_preserves_usage() -> None:
    client = FakeParccClient()
    request = build_advisor_request(
        request_type="hint",
        trace_packet={"failure": "missed grasp"},
        public_images=("data:image/png;base64,aGVsbG8=",),
    )
    reply = ParccGLMAdvisorTransport(client)(request)

    assert client.calls[0]["model"] == "parcc/GLM"
    assert client.calls[0]["temperature"] == 0.0
    assert reply.usage["total_tokens"] == 17
    assert reply.latency_seconds == 0.25
    assert parse_advisor_advice(reply.content, request_type="hint").guidance.startswith(
        "Align over"
    )
    protocol_path = Path(__file__).resolve().parents[1] / "protocol/v1/protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    assert protocol["models"]["advisor_proxy"]["model"] == "parcc/GLM"


def test_advice_rejects_extra_fields_and_mismatched_type() -> None:
    value = json.loads(_advice())
    value["native_success"] = True
    with pytest.raises(ValueError, match="keys do not match"):
        parse_advisor_advice(json.dumps(value), request_type="hint")
    with pytest.raises(ValueError, match="request_type does not match"):
        parse_advisor_advice(_advice(), request_type="approval")
