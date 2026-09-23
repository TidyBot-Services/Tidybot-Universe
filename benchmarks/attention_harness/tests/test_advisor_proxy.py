from __future__ import annotations

import pytest

from benchmarks.attention_harness.advisor_proxy import (
    ADVISOR_MODEL,
    advisor_cache_key,
    build_advisor_request,
)


def test_advisor_request_and_cache_key_are_deterministic() -> None:
    packet = {"failure": "missed grasp", "camera_frame_sha256": "abc"}
    first = build_advisor_request(request_type="hint", trace_packet=packet)
    second = build_advisor_request(
        request_type="hint",
        trace_packet={"camera_frame_sha256": "abc", "failure": "missed grasp"},
    )
    assert first["model"] == ADVISOR_MODEL
    assert advisor_cache_key(first) == advisor_cache_key(second)


@pytest.mark.parametrize(
    "packet",
    [
        {"native_success": True},
        {"cubeA_pos": [0.0, 0.0, 0.0]},
        {"evidence": {"object_pose": [0.0, 0.0, 0.0]}},
        {"history": [{"simulator_state": {}}]},
    ],
)
def test_advisor_rejects_oracle_fields(packet) -> None:
    with pytest.raises(ValueError, match="privileged"):
        build_advisor_request(request_type="hint", trace_packet=packet)
